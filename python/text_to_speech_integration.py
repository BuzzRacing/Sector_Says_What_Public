"""
Text-to-Speech integration with Inworld TTS using Basic Auth.
Handles multiple commentators, queues requests, and saves MP3 files.
"""

import os
import io
import json
import base64
import time
import queue
import threading
import requests
from typing import Optional

# Ensure pydub can find ffmpeg via imageio-ffmpeg bundled binary
try:
    import imageio_ffmpeg
    _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    _ffmpeg_dir = os.path.dirname(_ffmpeg_exe)
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    _ffmpeg_exe = None

from pydub import AudioSegment

# Point pydub directly at the ffmpeg binary
if _ffmpeg_exe:
    AudioSegment.converter = _ffmpeg_exe
    AudioSegment.ffprobe = _ffmpeg_exe
from commentary_team import get_commentators
import race_config
from tts_pronunciation import apply_pronunciation
from tts_expression import apply_expression_markup

# --- Configuration ---
def _tts_cfg():
    return race_config.cfg("power_unit").get("inworld", {})

INWORLD_TTS_URL = "https://api.inworld.ai/tts/v1/voice:stream"  # fallback; runtime uses config
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "sector_said")
os.makedirs(OUTPUT_DIR, exist_ok=True)

INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")
if not INWORLD_API_KEY:
    print("[WARNING] INWORLD_API_KEY environment variable not set. TTS calls will fail.")

# --- Voice Mapping (rebuilt dynamically from config) ---
def _voice_map():
    return {name: info.get("voice_id") for name, info in get_commentators().items() if "voice_id" in info}
INWORLD_VOICE_MAP = _voice_map()  # initial snapshot; use _voice_map() for live reads

# --- Queue system ---
_tts_queue = queue.Queue()
_queue_thread_started = False
_queue_thread_lock = threading.Lock()

AUDIO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "audio", "broadcast"))

# ================================
# STINGS
# ================================
STINGS = {
    "intro": "Sector_Says_What_Intro.mp3",
    "outro": "Sector_Says_What_Outro_Sting.mp3",
}

def play_sting(name: str, core_system=None):
    import race_data_store
    # Always fetch the live FMOD system
    if core_system is None:
        core_system = race_data_store.fmod_core
    if core_system is None:
        print("[STING ERROR] No FMOD core system available")
        return None
    filename = STINGS.get(name)
    if not filename:
        print(f"[STING ERROR] Unknown sting key '{name}'")
        return None
    path = os.path.join(AUDIO_ROOT, "stings", filename)
    try:
        sound = core_system.create_sound(path)
        channel = core_system.play_sound(sound)
        core_system.update()
        return channel  # non-blocking
    except Exception as e:
        print(f"[STING ERROR] FMOD sting failure: {e}")
        return None

# -------------------------------
# Generate TTS stream (MP3)
# -------------------------------
def generate_tts_stream(voice_id: str, text: str, output_path: str, speaking_rate: float = None):
    if not INWORLD_API_KEY:
        raise RuntimeError("INWORLD_API_KEY environment variable not set.")

    tts = _tts_cfg()
    if speaking_rate is None:
        speaking_rate = tts.get("default_speaking_rate", 1.3)

    try:
        text = apply_pronunciation(text, voice_id=voice_id)
    except Exception as e:
        print(f"[TTS PRONUNCIATION] Apply failed for voice_id='{voice_id}': {e}")

    headers = {
        "Authorization": f"Basic {INWORLD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "voice_id": voice_id,
        "model_id": tts.get("model_id", "inworld-tts-1.5-mini"),
        "audio_config": {"audio_encoding": tts.get("audio_encoding", "MP3"), "speaking_rate": speaking_rate},
        "temperature": tts.get("temperature", 0.79),
    }

    tts_url = tts.get("url", INWORLD_TTS_URL)
    timeout = tts.get("timeout", 30)

    # ---- HTTP + error detail ----
    try:
        response = requests.post(tts_url, json=payload, headers=headers, stream=True, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"Inworld TTS request failed (network): {e}") from e

    if not response.ok:
        # Try to capture the error body for debugging (often contains JSON with the real message)
        try:
            body_text = response.text[:800]
        except Exception:
            body_text = "<no body>"
        raise RuntimeError(
            f"Inworld TTS HTTP {response.status_code} for voice_id='{voice_id}': {body_text}"
        )

    # ---- Stream chunks ----
    raw_audio = io.BytesIO()
    chunk_count = 0

    for line in response.iter_lines():
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        audio_base64 = obj.get("result", {}).get("audioContent")
        if audio_base64:
            try:
                raw_audio.write(base64.b64decode(audio_base64))
                chunk_count += 1
            except Exception:
                continue

    if chunk_count == 0:
        raise RuntimeError(
            f"Inworld TTS returned no audio chunks for voice_id='{voice_id}'. "
            "Check text length, voice_id, and API key format."
        )

    if not output_path.lower().endswith(".mp3"):
        output_path = os.path.splitext(output_path)[0] + ".mp3"

    # Inworld streams raw MP3 bytes — write directly, no re-encoding needed
    raw_audio.seek(0)
    with open(output_path, "wb") as f:
        f.write(raw_audio.read())

    return output_path

# -------------------------------
# Public synchronous call
# -------------------------------
def generate_voice(
    text: str,
    commentator_name: str,
    output_path: Optional[str] = None,
    trigger_name: Optional[str] = None,
) -> Optional[str]:
    """
    Synchronous wrapper used by commentary_queue.

    Returns:
        str | None: path to the written MP3, or None on failure.
    """
    voice_id = INWORLD_VOICE_MAP.get(commentator_name)

    if not voice_id:
        print(f"[ERROR] No voice_id found for commentator: '{commentator_name}'")
        return None

    if not output_path:
        output_path = os.path.join(OUTPUT_DIR, f"{commentator_name}_{int(time.time())}.mp3")

    try:
        text = apply_expression_markup(text, trigger_name=trigger_name)
        return generate_tts_stream(
            voice_id,
            text,
            output_path,
            speaking_rate=get_commentators().get(commentator_name, {}).get("speaking_rate", 1.3),
        )
    except Exception as e:
        # This is where your 400 errors show up — now with full HTTP body detail.
        print(f"[ERROR] generate_tts_stream failed for '{commentator_name}' ({voice_id}): {e}")
        return None

# -------------------------------
# Queue-based background processing
# -------------------------------
def enqueue_commentary(item: dict):
    """Queue commentary item for background processing."""
    global _queue_thread_started
    text = item.get("prompt") or item.get("text")
    name = item.get("commentator") or item.get("host_override")
    if not text or not name:
        print(f"[ERROR] Missing text/commentator in queue item: {item}")
        return
    _tts_queue.put(item)
    # Start worker thread once (thread-safe)
    with _queue_thread_lock:
        if not _queue_thread_started:
            threading.Thread(target=_process_queue, daemon=True).start()
            _queue_thread_started = True
            print("[TTS] Background queue thread started.")

def _process_queue():
    """Worker thread: processes queued commentary items."""
    while True:
        item = _tts_queue.get()  # blocking get, no timeout
        try:
            text = item.get("prompt") or item.get("text")
            name = item.get("commentator") or item.get("host_override")
            clip_id = item.get("clip_id", f"{name}_{int(time.time())}")
            out_path = os.path.join(OUTPUT_DIR, f"{clip_id}.mp3")
            saved = generate_voice(text, name, out_path, trigger_name=item.get("trigger_name"))
            if not saved:
                print(f"[ERROR:TTS] Failed to generate voice for {name}: {clip_id}")
        except Exception as e:
            print(f"[ERROR:TTS Queue] Exception: {e}")
        finally:
            _tts_queue.task_done()

# -------------------------------
# Direct test
# -------------------------------
if __name__ == "__main__":
    enqueue_commentary({
        "prompt": "Ladies and gentlemen, the green flag is waving! We are live!",
        "commentator": "Murray",
        "clip_id": "test_intro"
    })

    print("[INFO] Waiting for TTS queue to complete...")
    while not _tts_queue.empty():
        time.sleep(0.1)
