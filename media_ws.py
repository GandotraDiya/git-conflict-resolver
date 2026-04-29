# media_ws.py
import os
import asyncio
import json
import base64
import aiohttp
import websockets
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Config
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")
ELEVEN_WS_URL = os.getenv("ELEVEN_WS_URL", "wss://api.elevenlabs.io/realtime")  # placeholder — check current ElevenLabs realtime ws URL
SAVE_TRANSCRIPT_ENDPOINT = os.getenv("SAVE_TRANSCRIPT_ENDPOINT", "http://127.0.0.1:5000/save-transcript")

# Twilio will connect to this server and send JSON frames. We'll listen on port 8765 by default.
WS_HOST = "0.0.0.0"
WS_PORT = int(os.getenv("MEDIA_WS_PORT", 8765))

# Simple in-memory mapping call_sid -> metadata
active_calls = {}

async def forward_to_elevenlabs(raw_pcm_bytes, call_sid):
    """
    Placeholder function demonstrating sending audio blobs to ElevenLabs.
    The exact format ElevenLabs expects may differ; adapt to their docs.
    Here we show sending via an HTTP multipart POST to a hypothetical API,
    or via a websocket if their realtime API uses websockets.
    For this example we will call a stub that returns text "transcribed text".
    """

    # --- SIMPLE STUB: return a fake transcript after short delay ---
    await asyncio.sleep(0.05)
    # In a real integration: open a websocket to ElevenLabs realtime, send audio frames,
    # listen for 'transcript' messages (json) and then return that text.
    fake_text = "[transcript fragment at " + datetime.utcnow().isoformat() + "]"
    return fake_text

async def save_transcript_to_api(call_sid, role, text):
    # Save transcript by calling Flask endpoint
    async with aiohttp.ClientSession() as session:
        payload = {"call_sid": call_sid, "role": role, "text": text}
        try:
            async with session.post(SAVE_TRANSCRIPT_ENDPOINT, json=payload, timeout=5) as resp:
                if resp.status != 200:
                    print("Failed to save transcript:", await resp.text())
        except Exception as e:
            print("Error saving transcript:", e)

async def handle_twilio_ws(websocket, path):
    """
    Twilio will connect and send JSON messages:
    - { "event": "start", "start": {...} }
    - { "event": "media", "media": { "payload": "<base64 string>" }, "num": ... }
    - { "event": "stop", "stop": {...} }
    We'll decode media payloads (which are 16-bit linear PCM encoded as base64) and forward them.
    """
    print("Twilio Media WS connected")
    call_sid = None

    try:
        async for raw_message in websocket:
            try:
                msg = json.loads(raw_message)
            except Exception:
                # Twilio may send ping frames or binary; ignore
                continue

            evt = msg.get("event")
            if evt == "start":
                # Example start: msg['start'] contains callSid and other metadata
                start = msg.get("start", {})
                call_sid = start.get("callSid", start.get("call_sid") or start.get("callSid"))
                print("Start for Call SID:", call_sid, "start payload:", start)
                active_calls[call_sid] = {"start": start, "ws": websocket}
                # Optionally persist call metadata by calling Flask /calls endpoint if required

            elif evt == "media":
                # Twilio sends media payload base64-encoded raw audio
                media = msg["media"]
                b64 = media.get("payload")
                if not b64:
                    continue

                pcm = base64.b64decode(b64)

                # Forward PCM to ElevenLabs (placeholder)
                transcript_fragment = await forward_to_elevenlabs(pcm, call_sid)

                # Save the transcript fragment (role: 'user')
                if transcript_fragment:
                    await save_transcript_to_api(call_sid, "user", transcript_fragment)

                    # In a real system: also get agent text and audio from ElevenLabs and send back to Twilio
                    # Example: You may synthesize agent audio and instruct Twilio to play it, or stream audio back
                    # via TwiML <Play> or by sending media back on the same Stream connection if supported.

            elif evt == "stop":
                print("Stream stopped for", call_sid)
                # mark call finished, maybe finalize transcripts
                if call_sid in active_calls:
                    del active_calls[call_sid]

            else:
                # other events: "connected", "heartbeat", custom...
                pass

    except websockets.ConnectionClosed:
        print("Twilio WebSocket disconnected for call", call_sid)
    except Exception as e:
        print("Error in Twilio WS handler:", e)

async def main():
    print(f"Starting WebSocket server on ws://{WS_HOST}:{WS_PORT}")
    async with websockets.serve(handle_twilio_ws, WS_HOST, WS_PORT, max_size=None):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
