from flask import Flask, request, redirect, url_for, render_template, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import os
from dateutil import parser
import datetime
import pytz
import json
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Replace with a random string

SCOPES = ['https://www.googleapis.com/auth/calendar']
VALID_COLOR_IDS = [str(i) for i in range(1, 12)]

# Initialize geocoder and timezone finder
geolocator = Nominatim(user_agent="calendar_agent")
tf = TimezoneFinder()

def get_service():
    creds = None
    if 'credentials' in session:
        creds = Credentials(**session['credentials'])
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            return None
    session['credentials'] = credentials_to_dict(creds)
    return build('calendar', 'v3', credentials=creds)

def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

def list_recent_events(service):
    """Fetch the 10 most recent events from the user's primary calendar."""
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        return events
    except Exception as e:
        print(f"Error fetching events: {e}")
        return []

def parse_input(user_input):
    """Parse user input with flexible date formats to extract event details."""
    # Split by commas to match desired format: "date, time city, event_name"
    parts = [part.strip() for part in user_input.split(',')]
    if len(parts) != 3:
        raise ValueError("Invalid input format. Use: 'date, time city, event title' (e.g., '20th March 2025, 11am jakarta, meeting with Steve')")

    try:
        # Parse date
        date_str = parts[0].lower()
        date = parser.parse(date_str)
        if date.year == datetime.datetime.now().year and "20" not in date_str:  # If year wasn't specified
            date = date.replace(year=datetime.datetime.now().year)

        # Parse time and city
        time_city_str = parts[1].lower().split()
        time_str = time_city_str[0]
        time = parser.parse(time_str, default=datetime.datetime.now()).time()

        # Extract city (could be 1 or 2 words)
        city_str = " ".join(time_city_str[1:])
        # Geocode the city to get latitude and longitude
        try:
            location = geolocator.geocode(city_str, timeout=10)
            if not location:
                raise ValueError(f"Could not find timezone for city: '{city_str}'. Please use a valid city name.")
            latitude, longitude = location.latitude, location.longitude
        except Exception as e:
            raise ValueError(f"Error finding city: '{city_str}'. Please use a valid city name. Error: {str(e)}")

        # Get the IANA timezone from the coordinates
        timezone_str = tf.timezone_at(lat=latitude, lng=longitude)
        if not timezone_str:
            raise ValueError(f"Could not determine timezone for city: '{city_str}' at coordinates ({latitude}, {longitude}).")
        try:
            timezone = pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Invalid timezone found for city: '{city_str}'. Timezone: '{timezone_str}' is not recognized.")

        # Combine date and time, localize, and convert to UTC
        event_datetime = datetime.datetime.combine(date.date(), time)
        event_datetime = timezone.localize(event_datetime)
        event_datetime_utc = event_datetime.astimezone(pytz.UTC)

        # Event name
        event_name = parts[2].strip()
        if not event_name:
            raise ValueError("Event title cannot be empty.")

        return {
            'summary': event_name,
            'start': event_datetime_utc.isoformat(),
            'end': (event_datetime_utc + datetime.timedelta(hours=1)).isoformat(),  # Default 1-hour duration
        }

    except ValueError as e:
        raise ValueError(f"Error parsing input: {str(e)}. Example: '20th March 2025, 11am jakarta, meeting with Steve'")
    except Exception as e:
        raise ValueError(f"Unexpected error while parsing input: {str(e)}. Please try again with a valid format.")

def check_for_duplicate(service, event_details):
    """Check if an event with the same summary, start, and end time already exists."""
    events = service.events().list(
        calendarId='primary',
        timeMin=event_details['start'],
        timeMax=event_details['end'],
        q=event_details['summary']
    ).execute()
    for event in events.get('items', []):
        if (event['summary'] == event_details['summary'] and
            event['start'].get('dateTime') == event_details['start'] and
            event['end'].get('dateTime') == event_details['end']):
            return True
    return False

def create_calendar_event(service, event_details):
    """Create a new event in the user's primary calendar."""
    if check_for_duplicate(service, event_details):
        return None  # Event already exists

    event = {
        'summary': event_details['summary'],
        'start': {'dateTime': event_details['start'], 'timeZone': 'UTC'},
        'end': {'dateTime': event_details['end'], 'timeZone': 'UTC'},
    }
    if event_details.get('colorId'):
        event['colorId'] = event_details['colorId']

    created_event = service.events().insert(calendarId='primary', body=event).execute()
    return created_event.get('htmlLink')

def modify_calendar_event(service, event_id, event_details):
    """Modify an existing event in the user's primary calendar."""
    event = service.events().get(calendarId='primary', eventId=event_id).execute()
    event['summary'] = event_details['summary']
    event['start'] = {'dateTime': event_details['start'], 'timeZone': 'UTC'}
    event['end'] = {'dateTime': event_details['end'], 'timeZone': 'UTC'}
    if event_details.get('colorId'):
        event['colorId'] = event_details['colorId']
    service.events().update(calendarId='primary', eventId=event_id, body=event).execute()

@app.route('/')
def index():
    if 'credentials' not in session:
        return redirect(url_for('login'))
    service = get_service()
    events = list_recent_events(service) if service else []
    return render_template('index.html', events=events, message=session.pop('message', None))

@app.route('/login')
def login():
    flow = Flow.from_client_secrets_file('credentials.json', SCOPES)
    flow.redirect_uri = url_for('callback', _external=True)
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    state = session['state']
    flow = Flow.from_client_secrets_file('credentials.json', SCOPES, state=state)
    flow.redirect_uri = url_for('callback', _external=True)
    flow.fetch_token(authorization_response=request.url)
    session['credentials'] = credentials_to_dict(flow.credentials)
    return redirect(url_for('index'))

@app.route('/create', methods=['POST'])
def create():
    service = get_service()
    if not service:
        return redirect(url_for('login'))
    user_input = request.form['event_details']
    color_id = request.form['color_id']
    try:
        event_details = parse_input(user_input)
        if color_id:  # Only add colorId if a color was selected
            event_details['colorId'] = color_id
        event_link = create_calendar_event(service, event_details)
        session['message'] = f"Event created: {event_link}" if event_link else "Event already exists."
    except ValueError as e:
        session['message'] = f"Error: {e}"
    except Exception as e:
        session['message'] = f"Unexpected error: {str(e)}. Please try again."
    return redirect(url_for('index'))

@app.route('/modify', methods=['POST'])
def modify():
    service = get_service()
    if not service:
        return redirect(url_for('login'))
    event_id = request.form['event_id']
    new_input = request.form['new_details']
    try:
        event_details = parse_input(new_input)
        modify_calendar_event(service, event_id, event_details)
        session['message'] = "Event updated successfully."
    except ValueError as e:
        session['message'] = f"Error: {e}"
    except Exception as e:
        session['message'] = f"Unexpected error: {str(e)}. Please try again."
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
