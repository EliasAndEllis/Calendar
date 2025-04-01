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
# Use an environment variable for the secret key (set this in Render's dashboard)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # Replace 'your-secret-key-here' with a secure random string

SCOPES = ['https://www.googleapis.com/auth/calendar']
TIMEZONE_MAP = {
    "toronto time": "America/Toronto",
    "new york time": "America/New_York",
    "london time": "Europe/London",
    "tokyo time": "Asia/Tokyo",
}
VALID_COLOR_IDS = [str(i) for i in range(1, 12)]

def get_service():
    """Create a Google Calendar API service object using stored credentials."""
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
    """Convert Google OAuth credentials to a dictionary for session storage."""
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

def list_recent_events(service):
    """Fetch the 10 most recent upcoming events from the user's primary calendar."""
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
    """Parse user input to extract event details (e.g., '03/17 12pm toronto time meeting 1')."""
    parts = user_input.lower().split()
    if len(parts) < 4:
        raise ValueError("Invalid input format. Use: MM/DD HH:MMam/pm timezone event_name [color_id]")

    # Extract date (MM/DD)
    date_str = parts[0]
    if not date_str.startswith('0') and len(date_str.split('/')[0]) == 1:
        date_str = '0' + date_str
    try:
        date = datetime.datetime.strptime(date_str, '%m/%d')
        date = date.replace(year=datetime.datetime.now().year)  # Assume current year
    except ValueError:
        raise ValueError("Invalid date format. Use MM/DD (e.g., 03/17).")

    # Extract time (HH:MMam/pm)
    time_str = parts[1]
    try:
        time = datetime.datetime.strptime(time_str, '%I:%M%p')
    except ValueError:
        raise ValueError("Invalid time format. Use HH:MMam/pm (e.g., 12:00pm).")

    # Combine date and time
    event_datetime = datetime.datetime.combine(date.date(), time.time())

    # Extract timezone
    timezone_str = " ".join(parts[2:4]) if parts[2] + " " + parts[3] in TIMEZONE_MAP else parts[2]
    if timezone_str not in TIMEZONE_MAP:
        raise ValueError(f"Invalid timezone. Supported timezones: {', '.join(TIMEZONE_MAP.keys())}")
    timezone = pytz.timezone(TIMEZONE_MAP[timezone_str])

    # Localize the datetime to the specified timezone
    event_datetime = timezone.localize(event_datetime)

    # Convert to UTC for Google Calendar
    event_datetime_utc = event_datetime.astimezone(pytz.UTC)

    # Extract event name (everything after timezone until color_id or end)
    color_id = None
    if parts[-1] in VALID_COLOR_IDS:
        color_id = parts[-1]
        event_name_parts = parts[4:-1] if " ".join(parts[2:4]) in TIMEZONE_MAP else parts[3:-1]
    else:
        event_name_parts = parts[4:] if " ".join(parts[2:4]) in TIMEZONE_MAP else parts[3:]

    event_name = " ".join(event_name_parts)
    if not event_name:
        raise ValueError("Event name cannot be empty.")

    return {
        'summary': event_name,
        'start': event_datetime_utc.isoformat(),
        'end': (event_datetime_utc + datetime.timedelta(hours=1)).isoformat(),  # Default 1-hour duration
        'colorId': color_id
    }

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
    """Render the main page with a list of upcoming events."""
    if 'credentials' not in session:
        return redirect(url_for('login'))
    service = get_service()
    events = list_recent_events(service) if service else []
    return render_template('index.html', events=events, message=session.pop('message', None))

@app.route('/login')
def login():
    """Initiate Google OAuth login flow."""
    flow = Flow.from_client_secrets_file('credentials.json', SCOPES)
    flow.redirect_uri = url_for('callback', _external=True)
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    """Handle the OAuth callback and store credentials."""
    state = session['state']
    flow = Flow.from_client_secrets_file('credentials.json', SCOPES, state=state)
    flow.redirect_uri = url_for('callback', _external=True)
    flow.fetch_token(authorization_response=request.url)
    session['credentials'] = credentials_to_dict(flow.credentials)
    return redirect(url_for('index'))

@app.route('/create', methods=['POST'])
def create():
    """Create a new calendar event based on user input."""
    service = get_service()
    if not service:
        return redirect(url_for('login'))
    user_input = request.form['event_details']
    try:
        event_details = parse_input(user_input)
        event_link = create_calendar_event(service, event_details)
        session['message'] = f"Event created: {event_link}" if event_link else "Event already exists."
    except ValueError as e:
        session['message'] = f"Error: {e}"
    return redirect(url_for('index'))

@app.route('/modify', methods=['POST'])
def modify():
    """Modify an existing calendar event based on user input."""
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

@app.route('/logout')
def logout():
    """Log the user out by clearing the session."""
    session.pop('credentials', None)
    session.pop('state', None)
    session.pop('message', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
