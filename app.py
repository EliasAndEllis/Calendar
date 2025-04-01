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

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Replace with a random string

SCOPES = ['https://www.googleapis.com/auth/calendar']
# Updated to map city names directly to IANA timezones
CITY_TIMEZONE_MAP = {
    "toronto": "America/Toronto",
    "new york": "America/New_York",
    "london": "Europe/London",
    "tokyo": "Asia/Tokyo",
    "jakarta": "Asia/Jakarta",  # Added for your example
}
VALID_COLOR_IDS = [str(i) for i in range(1, 12)]

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
    parts = user_input.lower().split()
    if len(parts) < 4:
        raise ValueError("Invalid input format. Use: date time timezone event_name")

    # Use dateutil.parser to handle various date formats
    try:
        # Try to parse the first 1-3 parts as a date
        for i in range(1, 4):
            date_str = " ".join(parts[:i])
            try:
                date = parser.parse(date_str)
                if date.year == datetime.datetime.now().year:  # If year wasn't specified
                    date = date.replace(year=datetime.datetime.now().year)
                date_parts = i
                break
            except ValueError:
                continue
        else:
            raise ValueError("Could not parse date. Try formats like 'March 17', '17 Mar', or '03/17'")

        # Extract time
        time_str = parts[date_parts]
        time = parser.parse(time_str, default=datetime.datetime.now()).time()
        event_datetime = datetime.datetime.combine(date.date(), time)

        # Extract city (instead of timezone)
        tz_start = date_parts + 1
        # Check for a two-word city name (e.g., "new york")
        city_str = " ".join(parts[tz_start:tz_start+2]) if parts[tz_start] + " " + parts[tz_start+1] in CITY_TIMEZONE_MAP else parts[tz_start]
        if city_str not in CITY_TIMEZONE_MAP:
            raise ValueError(f"Invalid city. Supported cities: {', '.join(CITY_TIMEZONE_MAP.keys())}")
        timezone = pytz.timezone(CITY_TIMEZONE_MAP[city_str])

        # Localize and convert to UTC
        event_datetime = timezone.localize(event_datetime)
        event_datetime_utc = event_datetime.astimezone(pytz.UTC)

        # Extract event name
        name_start = tz_start + 2 if " ".join(parts[tz_start:tz_start+2]) in CITY_TIMEZONE_MAP else tz_start + 1
        event_name = " ".join(parts[name_start:])
        if not event_name:
            raise ValueError("Event name cannot be empty.")

        return {
            'summary': event_name,
            'start': event_datetime_utc.isoformat(),
            'end': (event_datetime_utc + datetime.timedelta(hours=1)).isoformat(),  # Default 1-hour duration
        }

    except ValueError as e:
        raise ValueError(f"Error parsing input: {str(e)}")

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
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
