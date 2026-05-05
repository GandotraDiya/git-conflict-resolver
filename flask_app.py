# flask_app.py
import os
import random
import sqlite3
from flask import Flask, request, jsonify, render_template, g, Response
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# Flask 2.3.x can fail on Python 3.14 while auto-detecting the instance path.
# Providing an explicit absolute path avoids that compatibility issue.
app = Flask(
    __name__,
    template_folder="templates",
    instance_path=os.path.abspath("instance"),
)

# Config from env
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBERS = os.getenv("TWILIO_NUMBERS", "")  # comma-separated
PUBLIC_URL = os.getenv("PUBLIC_URL")  # e.g. https://<your-ngrok>.ngrok.io
MEDIA_WS_URL = os.getenv("MEDIA_WS_URL")  # e.g. wss://<your-media-host>.ngrok.io/media

if not PUBLIC_URL:
    print("WARNING: PUBLIC_URL not set. Twilio must reach your /handle-call and /media endpoints.")
if not MEDIA_WS_URL:
    print("WARNING: MEDIA_WS_URL not set. Twilio media streaming will not work until this is configured.")

client = Client(TWILIO_SID, TWILIO_AUTH) if TWILIO_SID and TWILIO_AUTH else None

DB_PATH = "transcripts.db"

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.execute("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_sid TEXT UNIQUE,
        from_number TEXT,
        to_number TEXT,
        start_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""
    CREATE TABLE IF NOT EXISTS transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_sid TEXT,
        role TEXT, -- 'user' or 'agent'
        text TEXT,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

@app.before_request
def setup():
    init_db()

@app.route("/")
def index():
    return render_template("index.html", public_url=PUBLIC_URL)

def pick_random_twilio_number():
    nums = [n.strip() for n in TWILIO_NUMBERS.split(",") if n.strip()]
    return random.choice(nums) if nums else None

@app.route("/start-call", methods=["POST"])
def start_call():
    data = request.get_json()
    phone = data.get("phone")
    if not phone:
        return jsonify({"error":"phone required"}), 400

    twilio_from = pick_random_twilio_number()
    if not client or not twilio_from:
        return jsonify({"error":"Twilio not configured"}), 500

    # Twilio will request this url when the call connects
    handle_call_url = PUBLIC_URL.rstrip("/") + "/handle-call"

    call = client.calls.create(
        to=phone,
        from_=twilio_from,
        url=handle_call_url  # TwiML URL when the call connects
    )

    # Save initial call info (call SID will arrive in webhook once Twilio calls handle-call - we store again then)
    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO calls (call_sid, from_number, to_number) VALUES (?, ?, ?)",
            (call.sid, twilio_from, phone)
        )
        db.commit()
    except Exception as e:
        print("DB insert error:", e)

    return jsonify({"message": f"Calling {phone} from {twilio_from}. Call SID: {call.sid}"}), 200

# Endpoint Twilio hits to get TwiML — this TwiML starts a Media Stream to our WS endpoint
@app.route("/handle-call", methods=["POST","GET"])
def handle_call():
    # Twilio Media Streams require a public wss:// URL. Keep it separately configurable
    # from the Flask app's public HTTP URL so the websocket server can run on another port/host.
    # Also we play a short message to inform user of recording
    media_ws_url = MEDIA_WS_URL or "wss://your-public-host/media"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice">You will be connected to an AI agent and this call will be recorded and transcribed.</Say>
  <Start>
    <Stream url="{media_ws_url}" />
  </Start>
  <Pause length="600"/> <!-- keep call open while media streaming -->
</Response>"""

    return Response(twiml, mimetype="text/xml")

@app.route("/save-transcript", methods=["POST"])
def save_transcript():
    """
    Endpoint used by the websocket server to persist transcripts.
    POST JSON: { "call_sid": "...", "role": "user|agent", "text": "..." }
    """
    j = request.get_json()
    call_sid = j.get("call_sid")
    role = j.get("role", "user")
    text = j.get("text", "")
    db = get_db()
    db.execute("INSERT INTO transcripts (call_sid, role, text) VALUES (?, ?, ?)", (call_sid, role, text))
    db.commit()
    return jsonify({"ok":True})

@app.route("/transcripts/<call_sid>")
def view_transcripts(call_sid):
    db = get_db()
    cur = db.execute("SELECT role, text, ts FROM transcripts WHERE call_sid=? ORDER BY ts", (call_sid,))
    rows = cur.fetchall()
    items = [dict(r) for r in rows]
    return jsonify(items)

@app.route("/calls")
def list_calls():
    db = get_db()
    cur = db.execute("SELECT id, call_sid, from_number, to_number, start_time FROM calls ORDER BY start_time DESC LIMIT 50")
    rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])

if __name__ == "__main__":
    # Run Flask for the UI & API endpoints
    app.run(host="0.0.0.0", port=int(os.getenv("FLASK_PORT", 5009)), debug=True)
