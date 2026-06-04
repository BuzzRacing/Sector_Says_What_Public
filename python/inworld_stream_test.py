import base64
import json
import os
import requests

URL = "https://api.inworld.ai/tts/v1/voice:stream"

auth = os.environ.get("INWORLD_BASIC_AUTH")
if not auth:
    raise RuntimeError("Set INWORLD_BASIC_AUTH first.")
auth = auth.strip()
if not auth.lower().startswith("basic "):
    auth = "Basic " + auth

headers = {
    "Authorization": auth,
    "Content-Type": "application/json",
    "Accept": "*/*",
}

payload = {
    "text": "TIK. TIK. TIK. TIK.\nTIK-TIK-TIK-TIK.\nTIKATIKATIKA—\nWe interrupt with an important announcement.\n",
    "voice_id": "Wendy",
    "audio_config": {"audio_encoding": "MP3", "speaking_rate": 1.5},
    "temperature": 1.1,
    "model_id": "inworld-tts-1.5-mini",
}

def extract_audio_b64(obj):
    if not isinstance(obj, dict):
        return None
    # common placements
    if "audioContent" in obj:
        return obj.get("audioContent")
    if "result" in obj and isinstance(obj["result"], dict):
        r = obj["result"]
        return r.get("audioContent") or r.get("audio_content")
    return None

r = requests.post(URL, json=payload, headers=headers, timeout=120)
r.raise_for_status()

out_path = "output.mp3"
total_bytes = 0
found_any = False

# Try: parse as JSONL / event-stream first (most robust)
text = r.text
lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

# If there's exactly one line, it might be a single JSON object
candidates = []

if len(lines) == 1:
    candidates.append(lines[0])
else:
    # Multiple lines: could be NDJSON or SSE with "data:"
    candidates.extend(lines)

with open(out_path, "wb") as f:
    for line in candidates:
        if line.startswith("data:"):
            line = line[len("data:"):].strip()

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Not JSON on this line; ignore
            continue

        audio_b64 = extract_audio_b64(obj)
        if audio_b64:
            chunk = base64.b64decode(audio_b64)
            f.write(chunk)
            total_bytes += len(chunk)
            found_any = True

if not found_any:
    # Last resort: show a short preview to diagnose without dumping secrets
    preview = text[:500].replace("\r", "\\r").replace("\n", "\\n")
    raise RuntimeError(f"No audioContent found. Response preview: {preview}")

print(f"Wrote {out_path} ({total_bytes} bytes)")
