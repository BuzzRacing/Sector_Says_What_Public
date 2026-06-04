# commentary_queue.py
import os
import time
import threading, random
from collections import deque
from text_to_speech_integration import generate_voice
import sector_says_utilities
from sector_says_utilities import append_trigger_stream_event
from commentary_team import trigger_settings
import re
import json

# GLOBAL — will hold the shared FMOD system once provided
fmod_system = None
commentary_thread_started = False
# True while TTS is generating or audio is playing — triggers should
# not enqueue new commentary while this is set.
_is_playing = False

def cleanup_old_audio(max_age_days=7):
    """Delete MP3 files in sector_said/ older than max_age_days.
    Called at engine startup to prevent audio file buildup."""
    audio_dir = os.path.join(os.path.dirname(__file__), "sector_said")
    if not os.path.isdir(audio_dir):
        return
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for f in os.listdir(audio_dir):
        if not f.lower().endswith(".mp3"):
            continue
        fpath = os.path.join(audio_dir, f)
        try:
            if os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                removed += 1
        except Exception:
            pass
    if removed:
        print(f"[CLEANUP] Removed {removed} audio file(s) older than {max_age_days} days")


def start():
    """
    Called by sector_says_engine after system_settings are loaded.
    Starts the commentary playback thread.

    NOTE:
    - FMOD system should be created in sector_says_utilities.initialize_audio_system()
      and injected via set_fmod_system(core).
    """
    global commentary_thread_started

    # Clean up old audio files from previous races
    cleanup_old_audio()

    # Avoid double-start
    if commentary_thread_started:
        return

    try:
        thread = threading.Thread(
            target=play_commentary_queue,
            name="CommentaryQueue",
            daemon=True
        )
        thread.start()
        commentary_thread_started = True

        if fmod_system is None:
            print("[COMMENTARY] WARNING: start() called before set_fmod_system(). "
                  "Commentary audio will be queued but cannot play until FMOD is set.")
    except Exception as e:
        print("[COMMENTARY] ERROR during commentary start:", e)


def set_fmod_system(system):
    """
    Inject a pre-created, initialized FMOD System instance from elsewhere
    (typically sector_says_utilities.initialize_audio_system()).
    """
    global fmod_system
    fmod_system = system
    try:
        import race_data_store
        race_data_store.fmod_system = system
        race_data_store.fmod_core   = system  # used by stings as well
    except Exception as e:
        print(f"[COMMENTARY] WARNING: Failed to set FMOD system in race_data_store: {e}")

# --------------------------------------------------
# Short visual text helper
# --------------------------------------------------
def create_visual_short_text(full_text, driver_name=None):
    if not full_text:
        return ""
    short = str(full_text).strip()
    if driver_name:
        # Extract last name
        last_name = re.split(r'[\s\-]+', driver_name.strip())[-1]
        # Replace full name with last name (case-insensitive)
        pattern_full = re.compile(re.escape(driver_name), re.IGNORECASE)
        short = pattern_full.sub(last_name, short)
        # Also replace first + last (if parsed differently)
        parts = driver_name.strip().split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            pattern_first_last = re.compile(fr"{re.escape(first)}\s+{re.escape(last)}", re.IGNORECASE)
            short = pattern_first_last.sub(last, short)
    # Truncate to ~50 characters or stop at the first period
    if len(short) > 50:
        if "." in short:
            short = short.split(".")[0]
        if len(short) > 50:
            short = short[:50].rsplit(" ", 1)[0] + "..."
    short = short.strip()
    # Avoid empty crash — if result empty, return placeholder
    if not short:
        return ""
    # Clean & capitalize safely
    short = short[0].upper() + short[1:]
    return short

# --------------------------------------------------
# Logging for commentary enqueue
# --------------------------------------------------
def _log_commentary_enqueue(item):
    try:
        # Race-relative timestamp for DB persistence — the replay timeline
        # expects `timestamp_s` to be seconds from race start, not raw epoch.
        # Unity/HUD feeds still use the raw epoch for wall-clock ordering.
        wall_ts = int(time.time())
        race_rel_ts = float(wall_ts)
        try:
            import race_data_store as _rds
            gf = getattr(_rds, "green_flag_time", None)
            if gf:
                race_rel_ts = max(0.0, float(wall_ts) - float(gf))
        except Exception:
            pass

        # Build a concise GUI payload
        payload = {
            "ts": wall_ts,
            "trigger": item.get("trigger_name", "unknown"),
            "type": item.get("type") or item.get("trigger_name", "unknown"),
            "text": item.get("prompt") or "",
            "short": create_visual_short_text(item.get("prompt", ""), item.get("driver_name")),
            "commentator": item.get("commentator") or "Murray",
            "location": item.get("location") or "Studio",
            "clip_id": item.get("clip_id") or "none",
            "car_idx": item.get("car_idx"),
            "owner": bool(item.get("owner")),
        }

        # Append to JSONL for Unity tailing
        export_dir = "sector_said_exports"
        os.makedirs(export_dir, exist_ok=True)
        with open(os.path.join(export_dir, "unity_feed.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

        # Persist to SQLite commentary_log for later review
        try:
            import race_db
            race_db.log_commentary(
                trigger_type=item.get("type") or item.get("trigger_name", ""),
                commentator=payload["commentator"],
                location=payload["location"],
                prompt_text=item.get("source_prompt") or item.get("prompt", "") or "",
                ai_text=payload["text"],
                clip_id=payload["clip_id"],
                car_idx=item.get("car_idx"),
                is_owner=bool(item.get("owner")),
                timestamp_s=race_rel_ts,
            )
        except Exception as e:
            print(f"[DB COMMENTARY LOG WARN] {e}")

        # Send to dashboard HUD stream
        try:
            evt = {
                "ts": payload["ts"],
                "type": payload.get("trigger") or "info",
                "trigger_type": payload.get("type") or payload.get("trigger") or "info",
                "desc": payload.get("text", ""),
                "short": payload.get("short", ""),
                "commentator": payload.get("commentator"),
                "location": payload.get("location"),
                "car_idx": payload.get("car_idx"),
                "owner": payload.get("owner"),
                "clip_id": payload.get("clip_id"),
            }
            append_trigger_stream_event(evt)
        except Exception as e:
            print(f"[GUI STREAM WARN] {e}")

    except Exception as e:
        print(f"[COMMENTARY ENQUEUE ERROR] {e}")


# --------------------------------------------------
# FMOD DLL / enums (but NO new System here)
# --------------------------------------------------
from pathlib import Path

# .../Sector_Says_What/python/commentary_queue.py -> project root is one level up from /python
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FMOD_DLL_PATH = str(PROJECT_ROOT / "audio" / "fmod")

try:
    if os.path.isdir(FMOD_DLL_PATH):
        os.add_dll_directory(FMOD_DLL_PATH)
    else:
        print(f"[FMOD PATH WARN] FMOD folder not found: {FMOD_DLL_PATH}")
except Exception as e:
    print(f"[FMOD PATH WARN] {e}")


from pyfmodex.enums import TIMEUNIT
TIMEUNIT_MS = TIMEUNIT.MS

# --------------------------------------------------
# Commentary queue
# --------------------------------------------------
commentary_queue = deque()
_queue_lock = threading.Lock()

def is_commentary_playing():
    """Return True if TTS generation or audio playback is in progress."""
    return _is_playing


def enqueue_commentary(item):
    """Add a commentary item to the queue.

    If commentary is currently being generated or played, OR there is
    already an item waiting in the queue, the new item is silently
    dropped.  This prevents stale commentary from piling up and ensures
    commentators never talk over each other.  The trigger stream and DB
    log still record the event via _log_commentary_enqueue so nothing
    is lost from the record, only from the audio output.

    Items whose prompt is empty or looks like an LLM meta-reply (the
    model asking for data or refusing to answer) are dropped entirely
    — no DB row, no audio — since they are not race content and would
    only confuse downstream consumers (Unity feed, replay timeline).
    """
    global _is_playing
    with _queue_lock:
        # Anti-fabrication layer 3 — drop LLM meta-replies and empties
        # before they touch the DB / Unity feed / TTS path.
        prompt_text = (item.get("prompt") or "").strip()
        try:
            from openai_commentary import is_llm_meta_reply
            if not prompt_text or is_llm_meta_reply(prompt_text):
                print(f"[CQ GATE] BLOCK {item.get('clip_id','?')} "
                      f"empty/meta-reply prompt: {prompt_text[:80]!r}")
                return
        except Exception as e:
            # If the sanitiser import fails, fall back to the empty check
            # so we still block obvious empty prompts.
            if not prompt_text:
                print(f"[CQ GATE] BLOCK {item.get('clip_id','?')} empty prompt")
                return
            print(f"[CQ GATE] sanitiser unavailable: {e}")

        # Always log to trigger stream / DB / Unity feed so the event
        # is recorded even if we skip the audio.
        _log_commentary_enqueue(item)
        clip_id = item.get("clip_id", "?")
        if _is_playing or len(commentary_queue) > 0:
            print(f"[CQ GATE] DROP {clip_id} (is_playing={_is_playing} qlen={len(commentary_queue)})")
            return  # skip audio — commentator is busy or queued
        # Mark busy immediately so subsequent calls within the same
        # tick window are blocked before the playback thread picks up.
        _is_playing = True
        commentary_queue.append(item)
        print(f"[CQ ENQUEUE] ACCEPT {clip_id} (qlen={len(commentary_queue)})")

def dequeue_commentary():
    """Remove and return the latest commentary item."""
    with _queue_lock:
        if commentary_queue:
            return commentary_queue.pop()  # always take the newest
        return None

def clear_old_items():
    """Drop all queued items except the newest."""
    with _queue_lock:
        while len(commentary_queue) > 1:
            commentary_queue.popleft()


# --------------------------------------------------
# FMOD playback helpers
# --------------------------------------------------
def _play_audio_fmod(file_path: str):
    """
    Play an audio file (wav/mp3) synchronously using the shared FMOD system.

    This assumes:
    - fmod_system is a fully initialized pyfmodex System
    - file_path points to a valid audio file.
    """
    global fmod_system

    #print("[CQ DEBUG] _play_audio_fmod called")
    #print(f"[CQ DEBUG]  file_path = {file_path}")
    #print("[CQ DEBUG]  fmod_system =", fmod_system, "type =", type(fmod_system))

    if fmod_system is None:
        print(f"[FMOD ERROR] No FMOD system set in commentary_queue. "
              f"Cannot play: {file_path}")
        return

    if not os.path.exists(file_path):
        print(f"[FMOD ERROR] Missing file: {file_path}")
        return

    try:
        #print("[CQ DEBUG]  creating sound...")
        sound = fmod_system.create_sound(file_path)
        try:
            length_ms = sound.get_length()
            print(f"[CQ DEBUG]  sound length = {length_ms} ms")
        except Exception as e:
            pass #print(f"[CQ WARN] Could not query sound length: {e}")

        #print("[CQ DEBUG]  playing sound...")
        channel = fmod_system.play_sound(sound)
        #print("[CQ DEBUG]  channel =", channel)

        start_time = time.time()
        MAX_PLAY_SECONDS = 60.0

        while True:
            try:
                playing = channel.is_playing
            except Exception as e:
                print(f"[FMOD ERROR] channel.is_playing failed: {e}")
                break

            if not playing:
                #print("[CQ DEBUG]  playback finished (channel not playing).")
                break

            fmod_system.update()
            elapsed = time.time() - start_time
            if elapsed > MAX_PLAY_SECONDS:
                print(f"[FMOD WARN] Playback exceeded {MAX_PLAY_SECONDS} seconds, breaking.")
                break

            time.sleep(0.05)

        try:
            sound.release()
        except Exception as e:
            print(f"[CQ WARN] Failed to release sound: {e}")

    except Exception as e:
        print(f"[FMOD ERROR] Playback failed for {file_path}: {e}")


def play_ambient(file_path: str, volume: float = 0.5, random_start: bool = True):
    """Play an ambient/effect clip on a separate FMOD channel, optionally starting at a random position."""
    global fmod_system

    if fmod_system is None:
        print(f"[FMOD ERROR] No FMOD system set for ambient playback. "
              f"Cannot play ambient: {file_path}")
        return

    if not os.path.exists(file_path):
        print(f"[FMOD ERROR] Ambient file missing: {file_path}")
        return

    try:
        sound = fmod_system.create_sound(file_path)
        channel = fmod_system.play_sound(sound)
        channel.set_volume(volume)

        if random_start:
            length_ms = sound.get_length()
            if length_ms > 1000:  # avoid last ms
                start_ms = random.randint(0, length_ms - 1000)
                channel.set_position(start_ms, TIMEUNIT_MS)
                print(f"[FMOD] Playing ambient from {start_ms}ms: {os.path.basename(file_path)}")
            else:
                print(f"[FMOD] Playing ambient from start (file too short): {os.path.basename(file_path)}")
        else:
            print(f"[FMOD] Playing ambient from start: {os.path.basename(file_path)}")

    except Exception as e:
        print(f"[FMOD ERROR] Ambient playback failed for {file_path}: {e}")


def play_commentary_queue():
    """Continuously play the latest commentary, skipping old queued items sequentially."""
    global _is_playing
    playback_folder = "sector_said"
    os.makedirs(playback_folder, exist_ok=True)
    print(f"[CQ THREAD] play_commentary_queue started (fmod_system={'SET' if fmod_system is not None else 'NONE'})")

    while True:
        # No items? Wait.
        if not commentary_queue:
            time.sleep(0.05)
            continue

        # Drop ALL but the newest item — anything older is stale
        while len(commentary_queue) > 1:
            dequeue_commentary()

        # Take the newest one
        item = dequeue_commentary()
        if not item:
            _is_playing = False
            continue

        # Prepare clean text
        text = sector_says_utilities.sanitize_for_speech(
            item.get("prompt") or item.get("text")
        )
        if not text:
            print(f"[PLAYBACK] Skipping empty text for item {item.get('clip_id')}")
            _is_playing = False
            continue
        text = text.replace("—", ", ")

        commentator = item.get("commentator", "Murray")
        clip_id = item.get("clip_id") or f"{commentator}_{int(time.time())}"

        # Always build a proper .mp3 target path
        file_path = os.path.join(playback_folder, f"{clip_id}.mp3")

        # _is_playing was already set True by enqueue_commentary()
        try:
            # ------------------------------------------------------------------
            # 🔊 0. Check if TTS is enabled in config
            # ------------------------------------------------------------------
            try:
                import race_config
                if not race_config.cfg("system").get("enable_tts", True):
                    print(f"[TTS] Skipped (TTS disabled in config): {clip_id}")
                    _is_playing = False
                    continue
            except Exception:
                pass  # If config unavailable, proceed with TTS

            # ------------------------------------------------------------------
            # 🔊 1. TTS generation
            # ------------------------------------------------------------------
            print(f"[CQ TTS] GEN start {commentator} → {os.path.basename(file_path)}")
            saved_path = generate_voice(text, commentator, file_path, trigger_name=item.get("trigger_name"))
            print(f"[CQ TTS] GEN done {commentator} → {saved_path}")
            # If TTS failed, saved_path will be None.
            if not saved_path:
                print(f"[TTS ERROR] TTS FAILED for {commentator}, clip_id={clip_id}")
                continue
            # Ensure file exists
            if not os.path.exists(saved_path):
                print(f"[TTS ERROR] File not created: {saved_path}")
                continue

            # Ensure file has content
            if os.path.getsize(saved_path) == 0:
                print(f"[TTS ERROR] File empty: {saved_path}")
                continue

            # ------------------------------------------------------------------
            # 🔊 2. FMOD Playback
            # ------------------------------------------------------------------
            print(f"[CQ FMOD] PLAY start {os.path.basename(saved_path)} (fmod_system={'SET' if fmod_system is not None else 'NONE'})")
            _play_audio_fmod(saved_path)
            print(f"[CQ FMOD] PLAY done {os.path.basename(saved_path)}")

            # ------------------------------------------------------------------
            # 🔊 2b. Delete the MP3 after playback — no need to keep it
            # ------------------------------------------------------------------
            try:
                if os.path.exists(saved_path):
                    os.remove(saved_path)
            except Exception as e:
                print(f"[CLEANUP] Could not delete {saved_path}: {e}")

            # ------------------------------------------------------------------
            # 🔊 3. Ambient sting (optional)
            # ------------------------------------------------------------------
            trigger_name = item.get("trigger_name")
            if trigger_name and trigger_name in trigger_settings:
                trigger_info = trigger_settings[trigger_name]
                ambient_file = trigger_info.get("ambient_file")
                ambient_volume = trigger_info.get("ambient_volume", 0.5)

                if ambient_file:
                    ambient_path = os.path.join("audio", "ambienance", ambient_file)
                    play_ambient(ambient_path, volume=ambient_volume, random_start=True)

        except Exception as e:
            print(f"[ERROR:TTS Queue] Could not play {clip_id}: {e}")
        finally:
            # ── Done — open the gate for the next commentary item ──
            _is_playing = False
