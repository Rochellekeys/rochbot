import os
import time
import re
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
import gspread
from google.oauth2.service_account import Credentials
from fuzzywuzzy import fuzz
import json
from flask import Flask, request

# Initialize Slack app
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

# Initialize Flask for HTTP requests
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'  # Your service account key file

# Global variables for caching
qa_data = []
last_updated = 0
CACHE_DURATION = 300  # 5 minutes in seconds

def connect_to_sheets():
    """Connect to Google Sheets"""
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        return None

def load_qa_data():
    """Load Q&A data from Google Sheets"""
    global qa_data, last_updated
    
    try:
        client = connect_to_sheets()
        if not client:
            return False
            
        # Open the spreadsheet by name or URL
        sheet = client.open(os.environ.get("GOOGLE_SHEET_NAME")).sheet1
        
        # Get all records
        records = sheet.get_all_records()
        
        # Filter active questions and format data
        qa_data = []
        for record in records:
            if record.get('Active', '').upper() == 'TRUE':
                qa_data.append({
                    'question': str(record.get('Question', '')).strip(),
                    'answer': str(record.get('Answer', '')).strip(),
                    'keywords': str(record.get('Keywords', '')).strip(),
                    'category': str(record.get('Category', '')).strip()
                })
        
        last_updated = time.time()
        print(f"✅ Loaded {len(qa_data)} Q&A records from Google Sheets")
        return True
        
    except Exception as e:
        print(f"❌ Error loading Q&A data: {e}")
        return False

def save_question_request(question, user_id, user_name):
    """Save unanswered question to requests sheet"""
    try:
        client = connect_to_sheets()
        if not client:
            return False
            
        # Open the spreadsheet and get the requests sheet
        spreadsheet = client.open(os.environ.get("GOOGLE_SHEET_NAME"))
        
        try:
            requests_sheet = spreadsheet.worksheet("Requests")
        except:
            # Create requests sheet if it doesn't exist
            requests_sheet = spreadsheet.add_worksheet(title="Requests", rows="1000", cols="6")
            requests_sheet.append_row([
                "Timestamp", "Question", "User ID", "User Name", "Status", "Admin Notes"
            ])
        
        # Add the request
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        requests_sheet.append_row([
            timestamp, question, user_id, user_name, "New", ""
        ])
        
        return True
        
    except Exception as e:
        print(f"❌ Error saving question request: {e}")
        return False

def find_best_answer(user_query):
    """Find the best matching answer using fuzzy matching"""
    if not qa_data:
        return None
    
    query = user_query.lower().strip()
    best_match = None
    best_score = 0
    
    for item in qa_data:
        # Check question similarity
        question_score = fuzz.partial_ratio(query, item['question'].lower())
        
        # Check keywords similarity
        keywords_score = 0
        if item['keywords']:
            keywords_score = fuzz.partial_ratio(query, item['keywords'].lower())
        
        # Check if query words appear in answer
        answer_score = fuzz.partial_ratio(query, item['answer'].lower())
        
        # Calculate overall score
        overall_score = max(question_score, keywords_score, answer_score * 0.7)
        
        if overall_score > best_score and overall_score > 60:  # Minimum confidence threshold
            best_score = overall_score
            best_match = item
    
    return best_match if best_match else None

def refresh_cache_if_needed():
    """Refresh cache if it's expired"""
    global last_updated
    if time.time() - last_updated > CACHE_DURATION:
        load_qa_data()

def create_answer_blocks(question, answer, category=None):
    """Create Slack blocks for answer display"""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{question}*\n\n{answer}"
            }
        }
    ]
    
    if category:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"📂 Category: {category}"
                }
            ]
        })
    
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "Was this helpful? React with ✅ or ❌"
            }
        ]
    })
    
    return blocks

def create_no_answer_blocks(query):
    """Create Slack blocks when no answer is found"""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "🤔 I couldn't find an answer to your question, but I'd love to help!"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Here's what you can do:*\n• Try rephrasing your question\n• Contact IT or HR directly\n• Send us a request and we'll add it to our knowledge base"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📝 Send Request to Admin",
                        "emoji": True
                    },
                    "style": "primary",
                    "action_id": "send_request",
                    "value": query
                }
            ]
        }
    ]

# Handle direct messages and mentions
@app.event("message")
def handle_message(event, say, client):
    # Skip bot messages and threaded replies
    if event.get("subtype") or event.get("thread_ts"):
        return
    
    # Check if it's a DM or mention
    is_dm = event.get("channel_type") == "im"
    is_mention = event.get("text", "").find(f"<@{os.environ.get('SLACK_BOT_USER_ID')}>") != -1
    
    if not is_dm and not is_mention:
        return
    
    # Extract the query
    query = event.get("text", "")
    if is_mention:
        query = re.sub(f"<@{os.environ.get('SLACK_BOT_USER_ID')}>", "", query).strip()
    
    if not query:
        say("👋 Hi there! Ask me any question about the company and I'll do my best to help!\n\n*For example:* \"Where can I find paper for the printer?\"")
        return
    
    # Refresh cache if needed
    refresh_cache_if_needed()
    
    # Search for answer
    result = find_best_answer(query)
    
    if result:
        say(blocks=create_answer_blocks(
            result['question'], 
            result['answer'], 
            result['category']
        ))
    else:
        say(blocks=create_no_answer_blocks(query))

# Handle button clicks for sending requests
@app.action("send_request")
def handle_send_request(ack, body, client):
    ack()
    
    user_id = body["user"]["id"]
    query = body["actions"][0]["value"]
    
    # Open modal for request details
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "question_request_modal",
            "title": {
                "type": "plain_text",
                "text": "Send Question Request"
            },
            "submit": {
                "type": "plain_text",
                "text": "Send Request"
            },
            "close": {
                "type": "plain_text",
                "text": "Cancel"
            },
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Help u
