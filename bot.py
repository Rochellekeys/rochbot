import os
import time
import re
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import gspread
from google.oauth2.service_account import Credentials
from fuzzywuzzy import fuzz
import json

# Initialize Slack app
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

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
                        "text": "Help us improve! Send your question to our team and we'll add it to the knowledge base."
                    }
                },
                {
                    "type": "input",
                    "block_id": "question_input",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "question",
                        "initial_value": query,
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "What's your question?"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Your Question"
                    }
                },
                {
                    "type": "input",
                    "block_id": "context_input",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "context",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Any additional context that might help us answer this?"
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Additional Context (Optional)"
                    },
                    "optional": True
                }
            ]
        }
    )

# Handle question request submission
@app.view("question_request_modal")
def handle_question_request(ack, body, view, client):
    ack()
    
    user_id = body["user"]["id"]
    user_name = body["user"]["name"]
    
    # Extract form data
    question = view["state"]["values"]["question_input"]["question"]["value"]
    context = view["state"]["values"]["context_input"]["context"]["value"] or ""
    
    full_question = f"{question}\n\nContext: {context}" if context else question
    
    # Save to Google Sheets
    if save_question_request(full_question, user_id, user_name):
        # Send confirmation to user
        client.chat_postMessage(
            channel=user_id,
            text="✅ Thanks for your request! We've received your question and will add it to our knowledge base soon."
        )
        
        # Notify admin channel (optional)
        admin_channel = os.environ.get("ADMIN_CHANNEL_ID")
        if admin_channel:
            client.chat_postMessage(
                channel=admin_channel,
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"📝 *New Question Request*\n\n*From:* <@{user_id}>\n*Question:* {question}"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Context:* {context}" if context else "*No additional context provided*"
                        }
                    }
                ]
            )
    else:
        client.chat_postMessage(
            channel=user_id,
            text="❌ Sorry, there was an error submitting your request. Please try again later or contact IT directly."
        )

# Slash command for quick queries
@app.command("/ask")
def handle_ask_command(ack, command, say):
    ack()
    
    query = command["text"].strip()
    if not query:
        say("Usage: `/ask your question here`\n\nExample: `/ask Where is the printer paper?`")
        return
    
    refresh_cache_if_needed()
    result = find_best_answer(query)
    
    if result:
        say(f"*{result['question']}*\n\n{result['answer']}")
    else:
        say(f"🤔 I couldn't find an answer to \"{query}\". Try asking me directly in a DM for more options!")

# Admin refresh command
@app.command("/refresh-kb")
def handle_refresh_command(ack, command, say):
    ack()
    
    if load_qa_data():
        say(f"✅ Knowledge base refreshed! Loaded {len(qa_data)} questions.")
    else:
        say("❌ Failed to refresh knowledge base. Please check the configuration.")

# Load initial data
print("🚀 Starting Rochbot...")
if load_qa_data():
    print("✅ Initial data loaded successfully")
else:
    print("⚠️ Warning: Could not load initial data")

# Start the app
if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
