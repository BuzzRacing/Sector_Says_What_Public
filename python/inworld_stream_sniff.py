import os
import requests

URL = "https://api.inworld.ai/tts/v1/voice:stream"

auth = os.environ.get("INWORLD_BASIC_AUTH")
if not auth:
    raise RuntimeError("Set INWORLD_BASIC_AUTH first.")
if not auth.lower().startswith("basic "):
    auth = "Basic " + auth

headers = {
    "Authorization": auth,
    "Content-Type": "application/json",
    "Accept": "*/*",
}

payload = {
    "text": "TIK. TIK. TIK.",
    "voice_id": "Wendy",
    "audio_config": {"audio_encoding": "MP3", "speaking_rate": 1.0},
    "model_id": "inworld-tts-1.5-mini",
}

with requests.post(URL, json=payload, headers=headers, stream=True, timeout=60) as r:
    print("STATUS:", r.status_code)
    print("CONTENT-TYPE:", r.headers.get("content-type"))
    r.raise_for_status()

    # Peek first bytes to see if it's raw audio
    first = r.raw.read(64)
    print("FIRST_64_BYTES_HEX:", first.hex())
