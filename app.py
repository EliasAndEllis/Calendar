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
TIMEZONE_MAP = {
    "toronto time": "America/Toronto",
    "new york time": "America/New_York",
    "london time": "Europe/London",
    "tokyo time": "Asia/Tokyo",
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

# Your existing parse_input, check_for_duplicate, create_calendar_event, list_recent_events, modify_calendar_event functions remain mostly unchanged
# (Just update them to return values instead of printing, e.g., return event.get('htmlLink') instead of print())

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
    try:
        event_details = parse_input(user_input)
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
