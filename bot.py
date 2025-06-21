import os
from flask import Flask

print("🚀 Starting Rochbot Debug Version...")

# Initialize Flask
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health_check():
    print("✅ Health check called")
    return "Rochbot Debug is running! 🤖", 200

@app.route("/slack/events", methods=["POST"])
def slack_events():
    print("📨 Slack event received")
    return "OK", 200

print("✅ Flask app initialized")

if __name__ == "__main__":
    print("🔧 Starting Flask server...")
    port = int(os.environ.get("PORT", 8080))
    print(f"🌐 Listening on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)
