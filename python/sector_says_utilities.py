"""
Telemetry and utility functions for Sector Says What.
Cleaned full-feature version without Windows COM audio enumeration.
"""
import os
import sys
import json
import ctypes
import time
import asyncio
import re
import csv
import tempfile
import shutil
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import race_data_store
import irsdk

# -----------------------------------------
# PRELOAD FMOD CORE DLL EXPLICITLY
# -----------------------------------------
FMOD_DLL_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..",  # up from python/ to project root
    "audio", "fmod", "fmod.dll"
))
try:
    ctypes.windll.LoadLibrary(FMOD_DLL_PATH)
except Exception as e:
    print("[FMOD] ERROR preloading FMOD core DLL:", e)
    
def safe_int(value, default=0):
    """
    Convert value to int safely. Returns default on failure.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ======================================================
# === Constants =======================================
# ======================================================
GREEN_FLAG = 268697604
WHITE_FLAG = 268701580
TELEMETRY_STABILIZE = 3.0

TRKLOC_NOT_IN_WORLD = -1
TRKLOC_OFF_TRACK = 0
TRKLOC_IN_PIT_STALL = 1
TRKLOC_APPROACHING_PITS = 2
TRKLOC_ON_TRACK = 3

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

CURRENT_HOST = "Murray"
LAST_LEADER = None
LAST_FASTEST_DRIVER = None
ON_PIT = set()
LAST_SNAPSHOT = 0.0
LAST_FLAG = None
PREV_POSITIONS, STARTING_GRID = {}, {}
SNAPSHOT_COUNT = 0
_last_elapsed, _last_total = 0.0, 1.0
mid_race_50_triggered = False
laps_led, PREV_POSITIONS, _last_recorded_lap = {}, {}, {}
first_positions_recorded = False
_last_leading_car_idx = None

ENABLE_TTS = True  # can be wired to settings.json elsewhere
commentary_queue = deque()
executor = ThreadPoolExecutor(max_workers=2)

# TTS output folder
TTS_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "sector_said")
os.makedirs(TTS_OUTPUT_DIR, exist_ok=True)

# Base directory for exports
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_EXPORTS_DIR = os.path.join(_BASE_DIR, "sector_said_exports")
os.makedirs(_EXPORTS_DIR, exist_ok=True)
        
# ======================================================
# === iRacing SDK Connection ==========================
# ======================================================
ir = irsdk.IRSDK()

def connect():
    """Simple blocking connect to iRacing."""
    while not ir.is_connected:
        if not ir.is_initialized:
            ir.startup()
        print("Waiting for iRacing to start")
        time.sleep(1)
        
def print_session_info():
    raw = ir["SessionInfo"]
    info = json.loads(raw) if isinstance(raw, str) else raw
    weekend = info.get("WeekendInfo", {})
    track = weekend.get("TrackDisplayName", weekend.get("TrackName", "Unknown"))
    print(f"Track: {track}")
    print(f"Date:  {weekend.get('WeekendOptions', {}).get('Date', 'Unknown')}")

def export_featured_drivers(featured):
    try:
        dst = os.path.join(EXPORT_PATH, "featured_drivers.json")
        with open(dst, "w", encoding="utf-8") as f:
            json.dump({"featured": featured}, f, indent=2)
    except Exception as e:
        print("[EXPORT] Failed to write featured_drivers.json:", e)

def handle_flags():
    global LAST_FLAG
    flags = ir["SessionFlags"]
    if flags != LAST_FLAG:
        if flags & irsdk.Flags.yellow:
            print("Full-course YELLOW")
        elif flags & irsdk.Flags.green:
            print("GREEN flag")
        elif flags & irsdk.Flags.red:
            print("RED flag")
        LAST_FLAG = flags

def connect_iracing():
    """Connect to iRacing or fall back to MockIR.

    Aggressive reconnection: if startup() succeeds but is_connected stays
    False for too long, tear down and rebuild the SDK object so the
    memory-mapped file handle is refreshed.
    """
    global ir
    try:
        import irsdk
    except ImportError:
        print("[UTIL] iRSDK not found — using MockIR.")
        from mock_ir import MockIR
        ir = MockIR()
        return ir

    import sys, time

    attempt = 0
    MAX_POLLS_BEFORE_RESET = 20        # 10 seconds of polling before hard reset
    SLEEP_BETWEEN_POLLS    = 0.5
    dots = 0

    ir = irsdk.IRSDK()

    while True:
        # --- try startup if needed ---
        if not ir.is_initialized:
            ir.startup()

        if ir.is_connected:
            # Verify we can actually read data
            try:
                ir.freeze_var_buffer_latest()
                test_val = ir['SessionNum']
                if test_val is not None:
                    sys.stdout.write("\r[CONN] Connected to iRacing.             \n")
                    sys.stdout.flush()
                    return ir
            except Exception:
                pass  # connected but can't read — fall through to retry

        attempt += 1
        dots = (dots % 5) + 1
        sys.stdout.write(
            f"\r[CONN] Waiting for iRacing{'.' * dots:<5}  (attempt {attempt})"
        )
        sys.stdout.flush()

        # --- hard reset: tear down SDK and rebuild ---
        if attempt % MAX_POLLS_BEFORE_RESET == 0:
            sys.stdout.write(
                f"\n[CONN] No connection after {attempt} attempts — "
                "resetting SDK handle...\n"
            )
            sys.stdout.flush()
            try:
                ir.shutdown()
            except Exception:
                pass
            ir = irsdk.IRSDK()

        time.sleep(SLEEP_BETWEEN_POLLS)


def normalize_driver_name(name: str) -> str:
    """Stronger normalization: strip [PIT], (OUT), punctuation, and collapse spaces."""
    if not name:
        return ""
    # Remove bracket tags like [PIT]
    name = re.sub(r"\[[^\]]*\]", "", name)
    # Remove parenthetical tags like (OUT)
    name = re.sub(r"\([^\)]*\)", "", name)
    # Remove punctuation
    name = re.sub(r"[^a-zA-Z0-9\s]", " ", name)
    return " ".join(name.split()).strip().lower()


# ======================================================
# === Starting Grid Capture ============================
# ======================================================
STARTING_GRID_FILE = os.path.join(_EXPORTS_DIR, "starting_grid.json")

def save_starting_grid(start_grid: dict):
    """Persist the starting grid (CarIdx → GridPosition)."""
    global STARTING_GRID

    try:
        STARTING_GRID = start_grid.copy()
        with open(STARTING_GRID_FILE, "w", encoding="utf-8") as f:
            json.dump(STARTING_GRID, f, indent=2)
    except Exception as e:
        print("[START GRID] ERROR writing starting_grid.json:", e)

def load_starting_grid() -> dict:
    global STARTING_GRID
    try:
        if os.path.exists(STARTING_GRID_FILE):
            with open(STARTING_GRID_FILE, "r", encoding="utf-8") as f:
                STARTING_GRID = json.load(f)
        return STARTING_GRID
    except Exception as e:
        print("[START GRID] ERROR loading:", e)
        return {}

def build_starting_grid(ir_conn):
    try:
        import live_leaderboard
        cars, owner = live_leaderboard.live_leaderboard(ir_conn)
        # iRacing's CarIdxPosition can be 0 for cars iRacing doesn't yet
        # have a race-grid slot for (most often the player in an AI
        # session at the instant GREEN flips). 0 must sort to the BACK,
        # not the front — otherwise that car gets stamped on pole and
        # every ΔPos / "down N from the grid" callout for the race is
        # poisoned downstream. Same fix shape as the race-end DNS sort
        # override in sector_says_engine.py.
        def _sort_key(c):
            p = c.get("LivePos")
            return p if isinstance(p, int) and p > 0 else 9999
        sorted_cars = sorted(cars, key=_sort_key)
        start_grid = {}
        for pos, car in enumerate(sorted_cars, start=1):
            start_grid[str(car["CarIdx"])] = pos
        return start_grid
    except Exception as e:
        print("[START GRID] ERROR building starting grid:", e)
        return {}
    
# ======================================================
# === Safe Variable Access ============================
# ======================================================
def safe_session_var(ir_conn, var_name, default=None):
    """Read a telemetry variable from the already-frozen buffer.

    IMPORTANT: The caller must call ir_conn.freeze_var_buffer_latest()
    once per tick BEFORE calling this function. This function must NOT
    re-freeze the buffer — doing so corrupts reads in tight loops.
    """
    try:
        return ir_conn[var_name]
    except Exception:
        return default


def safe_ir_sessioninfo(ir_conn):
    """Return the parsed SessionInfo dict, or empty dict on failure.

    In pyirsdk, ir["SessionInfo"] returns the YAML session block as a
    dict (already parsed). This wrapper makes it safe to call when the
    SDK is disconnected or the key is missing.
    """
    try:
        raw = ir_conn["SessionInfo"]
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            import yaml
            return yaml.safe_load(raw) or {}
        return {}
    except Exception:
        return {}


def safe_value(obj, key, default=None):
    try:
        return obj.get(key, default)
    except Exception:
        return default
    
# ======================================================
def update_focus_driver_for_new_race(ir, cars):
    """
    Update focus_driver_of_the_day.json inside sector_said_exports
    when a new race begins. Normalizes names to match even if leaderboard
    adds tags like [PIT], (OUT), etc.
    """
    try:
        export_dir = "sector_said_exports"
        fd_path = os.path.join(export_dir, "focus_driver_of_the_day.json")

        # Ensure directory exists
        if not os.path.exists(export_dir):
            os.makedirs(export_dir, exist_ok=True)

        # If missing, create basic structure
        if not os.path.exists(fd_path):
            print("[FDOTD] No existing file — creating default FDOTD structure.")
            fd_data = {
                "Name": None,
                "CarIdx": None,
                "CarNumber": None,
                "UpdatedAt": int(time.time()),
            }
            with open(fd_path, "w", encoding="utf-8") as f:
                json.dump(fd_data, f, indent=2)
            return

        # Load FDOTD file
        with open(fd_path, "r", encoding="utf-8") as f:
            fd_data = json.load(f)

        if not cars:
            print("[FDOTD] No cars provided — cannot update FDOTD.")
            return

        # ------------------------------------------------------------
        # Phase 0 — CustId match (strongest, survives name changes)
        # ------------------------------------------------------------
        match = None
        fd_cust_id = fd_data.get("CustId")
        if fd_cust_id is not None:
            try:
                fd_cust_id = int(fd_cust_id)
                for c in cars:
                    if c.get("CustId") == fd_cust_id:
                        match = c
                        break
            except (TypeError, ValueError):
                pass

        focus_name = fd_data.get("Name")
        if not match and not focus_name:
            print("[FDOTD] FDOTD Name not set — cannot match driver.")
            return

        # ------------------------------------------------------------
        # Phase 1 — strict name match
        # ------------------------------------------------------------
        if not match and focus_name:
            focus_clean = normalize_driver_name(focus_name)
            for c in cars:
                lb_clean = normalize_driver_name(c.get("Name", ""))
                if lb_clean == focus_clean:
                    match = c
                    break

            # Phase 2 — partial name match
            if not match:
                for c in cars:
                    lb_clean = normalize_driver_name(c.get("Name", ""))
                    if focus_clean in lb_clean or lb_clean in focus_clean:
                        match = c
                        print(f"[FDOTD] Partial-match used: '{c.get('Name')}' matches '{focus_name}'")
                        break

        if not match:
            print(f"[FDOTD] No matching driver found for '{focus_name}'.")
            return

        # Pull fresh values
        new_idx = match.get("CarIdx")
        new_num = match.get("CarNumber") or match.get("CarNum") or None

        # Update JSON
        fd_data["CarIdx"] = new_idx
        fd_data["CarNumber"] = new_num
        fd_data["UpdatedAt"] = int(time.time())

        with open(fd_path, "w", encoding="utf-8") as f:
            json.dump(fd_data, f, indent=2)

        #print("--------------------------------------------------------")
        #print("       FOCUS DRIVER UPDATED FOR NEW RACE")
        #print("--------------------------------------------------------")
        #print(f"Name:        {fd_data['Name']}")
        #print(f"CarIdx:      {fd_data['CarIdx']}")
        #print(f"CarNumber:   {fd_data['CarNumber']}")
        #print(f"UpdatedAt:   {fd_data['UpdatedAt']}")
        #print("--------------------------------------------------------")

    except Exception as e:
        print("[FDOTD] ERROR during update:", e)

# ======================================================
# === Timing Helpers ==================================
# ======================================================
def get_elapsed_time(ir_conn):
    """
    Return elapsed session time as a float.
    Handles iRacing API arrays (lists) safely.
    """
    val = safe_session_var(ir_conn, "SessionTime", 0.0)
    if isinstance(val, list):
        return float(val[0]) if val else 0.0
    return float(val)


def get_total_time(ir_conn):
    """
    Return total session time (elapsed + remaining) as a float.
    Handles iRacing API arrays (lists) safely.
    """
    elapsed = get_elapsed_time(ir_conn)

    remain = safe_session_var(ir_conn, "SessionTimeRemain", 0.0)
    if isinstance(remain, list):
        remain = float(remain[0]) if remain else 0.0
    else:
        remain = float(remain)

    return elapsed + remain


def get_race_times(ir_conn):
    try:
        return {
            "elapsed_seconds": get_elapsed_time(ir_conn),
            "total_seconds": get_total_time(ir_conn),
        }
    except Exception:
        return {"elapsed_seconds": 0.0, "total_seconds": 1.0}

def get_race_progress(ir_conn=None):
    global _last_elapsed, _last_total
    if ir_conn:
        t = get_race_times(ir_conn)
        _last_elapsed, _last_total = t["elapsed_seconds"], t["total_seconds"]
    if _last_total <= 0:
        return 0.0
    return min(_last_elapsed / _last_total, 1.0)

# ======================================================
# === Console Lap Time Formatting =====================
# ======================================================
def format_laptime(seconds):
    """
    Format lap time for console display:
      85.432 -> '1:25.432'
      59.991 -> '59.991'
    """
    try:
        s = float(seconds)
        if s <= 0:
            return ""
        mins = int(s // 60)
        secs = s - mins * 60
        if mins > 0:
            return f"{mins}:{secs:06.3f}"
        return f"{secs:06.3f}"
    except Exception:
        return ""

def format_gap_time(seconds):
    """
    Format gap values for leaderboard display.
    Examples:
        0.532 -> '+0.532'
        12.421 -> '12.421'  (if gap to leader)
        0 or None -> ""
    """
    try:
        if seconds is None or seconds <= 0:
            return ""
        s = float(seconds)
        if s < 60:
            return f"+{s:0.3f}"
        mins = int(s // 60)
        secs = s - mins * 60
        return f"{mins}:{secs:06.3f}"
    except Exception:
        return ""

# ======================================================
# --- Speech helpers ---
# ======================================================
_unit_s_re = re.compile(r"(?P<val>\d+(?:[.,]\d+)?)\s*s(?![a-zA-Z])")

def expand_units_for_speech(text: str) -> str:
    """Replace standalone 's' time unit with 'seconds' for TTS clarity."""
    if not isinstance(text, str):
        text = str(text)

    def repl(m):
        val = m.group("val").replace(",", ".")
        return f"{val} seconds"

    return _unit_s_re.sub(repl, text)


# ─── Broadcast-style number speech helpers ────────────────────────
# Motor racing commentators do NOT say "one minute thirty-eight point
# six four zero seconds". They say "a one thirty-eight point six four
# zero" or "a one thirty-eight six four". These helpers emit spelled-
# out word strings so the TTS reads them exactly that way.

_UNITS = ["zero","one","two","three","four","five","six","seven","eight","nine"]
_TEENS = ["ten","eleven","twelve","thirteen","fourteen","fifteen",
          "sixteen","seventeen","eighteen","nineteen"]
_TENS  = ["","","twenty","thirty","forty","fifty","sixty","seventy","eighty","ninety"]

def _spell_int(n: int) -> str:
    """Spell a non-negative integer 0-999 as English words
    (e.g. 38 -> 'thirty-eight', 203 -> 'two oh three' is NOT used;
    this sticks to 'two hundred three' for clarity)."""
    n = int(n)
    if n < 0:
        return "minus " + _spell_int(-n)
    if n < 10:
        return _UNITS[n]
    if n < 20:
        return _TEENS[n - 10]
    if n < 100:
        t, u = divmod(n, 10)
        if u == 0:
            return _TENS[t]
        return f"{_TENS[t]}-{_UNITS[u]}"
    if n < 1000:
        h, rem = divmod(n, 100)
        if rem == 0:
            return f"{_UNITS[h]} hundred"
        return f"{_UNITS[h]} hundred {_spell_int(rem)}"
    return str(n)

def _spell_digits(s: str) -> str:
    """Spell each digit individually — 640 -> 'six four zero'."""
    return " ".join(_UNITS[int(c)] for c in s if c.isdigit())


def voice_format_lap_time(seconds: float) -> str:
    """Broadcast-style lap time: 'a one thirty-eight point six four zero'
    for 98.640s, or 'fifty-seven point four five six' for 57.456s.
    Emits spelled-out words so TTS reads it as a real commentator would."""
    try:
        s = float(seconds)
    except Exception:
        return str(seconds)
    if s <= 0:
        return "no time"
    if s < 60:
        whole = int(s)
        ms = int(round((s - whole) * 1000))
        if ms >= 1000:
            whole += 1
            ms -= 1000
        return f"{_spell_int(whole)} point {_spell_digits(f'{ms:03d}')}"
    mins = int(s // 60)
    remain = s - mins * 60
    whole = int(remain)
    ms = int(round((remain - whole) * 1000))
    if ms >= 1000:
        whole += 1
        ms -= 1000
    if whole >= 60:
        mins += 1
        whole -= 60
    # Broadcast convention: leading-zero seconds are "oh X"
    # (1:03.456 -> "a one oh three point four five six").
    if whole < 10:
        sec_words = f"oh {_UNITS[whole]}"
    else:
        sec_words = _spell_int(whole)
    # "a one thirty-eight point six four zero"
    return (f"a {_spell_int(mins)} {sec_words} "
            f"point {_spell_digits(f'{ms:03d}')}")


def voice_format_speed(kph: float, unit: str = "km/h") -> str:
    """Broadcast-style speed: 'around one eighty-five kilometers per hour'
    for 185.3 km/h. Rounds to the nearest integer because broadcasters
    don't quote fractional km/h on air."""
    try:
        v = float(kph)
    except Exception:
        return str(kph)
    if v <= 0:
        return "no reading"
    rounded = int(round(v))
    unit_words = ("kilometers per hour" if unit.lower() in ("km/h","kph","kmh")
                  else "miles per hour"    if unit.lower() in ("mph",)
                  else unit)
    return f"{_spell_int(rounded)} {unit_words}"


def voice_format_gap(gap_seconds: float) -> str:
    """Broadcast-style gap: 'point one two three' for 0.123s,
    'one point two three four' for 1.234s, 'twelve point five' for 12.5s,
    'over a minute' for 60s+. No raw numerals — every digit spelled out."""
    try:
        g = float(gap_seconds)
    except Exception:
        return expand_units_for_speech(str(gap_seconds))
    if g < 0:
        g = -g
    if g < 0.001:
        return "dead level"
    if g < 1.0:
        ms = int(round(g * 1000))
        if ms >= 1000:
            return "one second"
        return f"point {_spell_digits(f'{ms:03d}')}"
    if g < 60:
        whole = int(g)
        ms = int(round((g - whole) * 1000))
        if ms >= 1000:
            whole += 1
            ms -= 1000
        return (f"{_spell_int(whole)} point "
                f"{_spell_digits(f'{ms:03d}')} seconds")
    # 60s+ — gap is no longer a racing gap, describe it as "over a minute"
    mins = int(g // 60)
    remain = int(g - mins * 60)
    if remain == 0:
        return f"{_spell_int(mins)} minute{'s' if mins != 1 else ''}"
    return (f"{_spell_int(mins)} minute{'s' if mins != 1 else ''} "
            f"{_spell_int(remain)} seconds")

def voice_format_irating(ir) -> str:
    """Broadcast-style iRating: 4324 → '4.3K iRating', 987 → '1.0K iRating'.
    Commentators always express iRating in thousands with one decimal."""
    try:
        v = int(ir)
    except Exception:
        return str(ir)
    if v <= 0:
        return "unrated"
    k = v / 1000.0
    return f"{k:.1f}K iRating"


# ======================================================
# === Live Data Updating ==============================
# ======================================================
def get_host_and_energy(elapsed: float, total: float):
    """
    Simple placeholder host/energy selector.
    The more advanced rotation logic can replace this function.
    """
    if total <= 0:
        return CURRENT_HOST, 1.0
    progress = elapsed / total
    # Simple 0–1 scaling as "energy"
    energy = max(0.2, min(1.0, progress * 1.2))
    return CURRENT_HOST, energy

def update_live_data(ir_conn, leaderboard):
    """
    Update high-level race state summary used by other modules.
    """
    global CURRENT_HOST, _last_elapsed, _last_total, laps_led, _last_leading_car_idx
    elapsed, total = get_elapsed_time(ir_conn), get_total_time(ir_conn)
    CURRENT_HOST, energy = get_host_and_energy(elapsed, total)
    remain = safe_session_var(ir_conn, "SessionTimeRemain", 0.0)
    if leaderboard:
        leader_idx = leaderboard[0].get("CarIdx")
        if leader_idx is not None:
            laps_led[leader_idx] = laps_led.get(leader_idx, 0)
            _last_leading_car_idx = leader_idx

    _last_elapsed, _last_total = elapsed, total
    return {
        "elapsed_time": elapsed,
        "total_time": total,
        "session_time_remain": remain,
        "formatted_remain": f"{int(remain // 60)}:{int(remain % 60):02}",
        "current_host": CURRENT_HOST,
        "energy": energy,
        "top_drivers": leaderboard[:3] if leaderboard else [],
        "_last_leading_car_idx": _last_leading_car_idx,
    }

# ======================================================
# === Async Commentary Queue ==========================
# ======================================================
def _play_sync(filepath: str):
    """
    Very simple synchronous playback stub.
    You can replace this with FMOD-based playback if desired.
    """
    print(f"[TTS] Would play audio file: {filepath}")


async def generate_and_save_tts(text, commentator, clip_id=None):
    if not ENABLE_TTS:
        return None
    os.makedirs(TTS_OUTPUT_DIR, exist_ok=True)
    fn = clip_id or f"{commentator}_{int(time.time())}"
    fp = os.path.join(TTS_OUTPUT_DIR, f"{fn}.wav")

    try:
        from text_to_speech_integration import generate_voice  # type: ignore
    except Exception:
        print("[TTS] WARNING: text_to_speech_integration.generate_voice not available.")
        return None

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, generate_voice, text, commentator, fp)
    return fp


async def play_audio(fp):
    if not ENABLE_TTS or not fp:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _play_sync, fp)


async def process_commentary_queue():
    while True:
        if commentary_queue:
            item = commentary_queue.popleft()
            try:
                fp = await generate_and_save_tts(
                    item["prompt"], item["commentator"], item.get("clip_id")
                )
                await play_audio(fp)
            except Exception as e:
                print(f"[ERROR:Async Queue] {e}")
                traceback.print_exc()
        else:
            await asyncio.sleep(0.1)

async def enqueue_and_process():
    asyncio.create_task(process_commentary_queue())

# ======================================================
# === Track / Weekend Info ============================
# ======================================================
def get_track_info(ir_conn):
    """
    Fetches WeekendInfo from iRacing and returns a dict with track/weather info.
    Handles nested weather dictionaries and multiple key options.
    """
    wi = safe_session_var(ir_conn, "WeekendInfo", {}) or {}

    possible_weather_containers = ["Weather", "TrackWeather", "WeatherInfo"]
    nested_weather = {}
    for key in possible_weather_containers:
        if key in wi and isinstance(wi[key], dict):
            nested_weather = wi[key]
            break

    def _get(keys, default="?"):
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            val = None
            if nested_weather and key in nested_weather:
                val = nested_weather[key]
            elif key in wi:
                val = wi[key]

            if isinstance(val, dict) and "Value" in val:
                return val["Value"]
            if val is not None:
                return val
        return default

    track_info = {
        "track_name": wi.get("TrackDisplayName") or wi.get("TrackName", "Unknown Track"),
        "track_config": wi.get("TrackConfigName", ""),
        "track_city": wi.get("TrackCity", ""),
        "track_state": wi.get("TrackState", ""),
        "track_country": wi.get("TrackCountry", ""),
        "track_length": wi.get("TrackLength", "?"),
        "track_temp": _get(
            ["TrackSurfaceTempCrew", "TrackSurfaceTemp", "TrackTempCrew", "TrackTemp"]
        ),
        "air_temp": _get(["TrackAirTemp", "AirTemp", "AirTempCrew"]),
        "humidity": _get(["TrackRelativeHumidity", "RelativeHumidity", "Humidity"]),
        "skies": _get(["TrackSkies", "Skies"]),
    }
    return track_info


def describe_track_moisture(info):
    try:
        raw_precip = info.get("TrackPrecipitation", 0)
        moisture_pct = float(str(raw_precip).replace("%", "").strip())
    except Exception:
        moisture_pct = 0.0

    if moisture_pct <= 0:
        return "dry track; full grip"
    if moisture_pct < 25:
        return "slight sheen"
    if moisture_pct < 60:
        return "visible water"
    if moisture_pct < 85:
        return "standing water"
    return "heavy rain"


def print_weekend_info(ir_conn, max_attempts=10):
    weekend_info, attempts = {}, 0
    while attempts < max_attempts:
        try:
            if getattr(ir_conn, "is_initialized", False) and getattr(
                ir_conn, "is_connected", False
            ):
                weekend_info = ir_conn["WeekendInfo"]
                if weekend_info:
                    break
        except Exception:
            pass
        attempts += 1
        time.sleep(1)
    if not weekend_info:
        print("[WARN] Could not fetch WeekendInfo.")
        return {}
    describe_track_moisture(weekend_info)
    return weekend_info


def wait_for_green_flag_sync(ir_conn):
    while not getattr(ir_conn, "is_connected", False):
        time.sleep(1)
    while True:
        try:
            ir_conn.freeze_var_buffer_latest()
            if ir_conn["SessionFlags"] == GREEN_FLAG:
                start = time.time()
                print(f"Green flag detected at {start}")
                time.sleep(TELEMETRY_STABILIZE)
                return start
        except Exception:
            pass
        time.sleep(0.1)


def get_session_context(ir_conn):
    info = get_track_info(ir_conn)
    wi = safe_session_var(ir_conn, "WeekendInfo", {}) or {}

    possible_weather_containers = ["Weather", "TrackWeather", "WeatherInfo"]
    nested_weather = {}
    for key in possible_weather_containers:
        if key in wi and isinstance(wi[key], dict):
            nested_weather = wi[key]
            break

    def _get(key, default="?"):
        if nested_weather and key in nested_weather:
            return nested_weather.get(key, default)
        if key in wi:
            return wi.get(key, default)
        return safe_session_var(ir_conn, key, default)

    session_context = {
        **info,
        "session_type": safe_session_var(ir_conn, "SessionTypeName", "Race"),
        "session_laps": safe_session_var(ir_conn, "SessionLapsTotal", "?"),
        "session_flags": safe_session_var(ir_conn, "SessionFlags", 0),
        "air_temp": _get("AirTemp", "?"),
        "track_temp": _get("TrackTempCrew", "?"),
        "wind_dir": _get("WindDir", "?"),
        "wind_vel": _get("WindVel", "?"),
        "humidity": _get("Humidity", "?"),
        "skies": _get("Skies", "?"),
    }

    return session_context


# ======================================================
# === Sector / Lap Helpers ============================
# ======================================================
def format_sector_time_for_console(s):
    if s is None:
        return "--"
    try:
        return "--" if str(s).strip() in ["", "--"] else f"{float(s):.3f}"
    except Exception:
        return "--"


def format_sector_time_for_speech(s):
    """Broadcast-style sector / lap time for TTS. Delegates to
    voice_format_lap_time so the whole engine uses one convention."""
    if s is None:
        return "unknown"
    try:
        return voice_format_lap_time(float(s))
    except Exception:
        return "unknown"


def get_car_lap_data(ir_conn):
    """
    Returns a dictionary keyed by CarIdx with core telemetry fields:
    LapDistPct, LastLapTime, and Speed.
    Safe wrapper to avoid crashes if iRacing disconnects.
    """
    data = {}
    try:
        if not ir_conn.is_connected:
            return data

        lap_dist_array = getattr(ir_conn["CarIdxLapDistPct"], "value", [])
        last_lap_array = getattr(ir_conn["CarIdxLastLapTime"], "value", [])
        speed_array = getattr(ir_conn["CarIdxSpeed"], "value", [])

        for car_idx in range(len(lap_dist_array)):
            data[car_idx] = {
                "LapDistPct": float(lap_dist_array[car_idx]),
                "LastLapTime": float(last_lap_array[car_idx]),
                "Speed": float(speed_array[car_idx]),
            }
    except Exception as e:
        print(f"[WARN] get_car_lap_data failed: {e}")
    return data


# --- iRacing Flag Definitions (bitmask) ---
FLAGS = {
    "CHECKERED": 0x00000001,
    "WHITE": 0x00000002,
    "GREEN": 0x00000004,
    "YELLOW": 0x00000008,
    "RED": 0x00000010,
    "BLUE": 0x00000020,
    "DEBRIS": 0x00000040,
    "CROSSED": 0x00000080,
    "YELLOW_WAVING": 0x00000100,
    "ONE_TO_GREEN": 0x00000200,
    "GREEN_HELD": 0x00000400,
    "LAPS_10_TO_GO": 0x00000800,
    "LAPS_5_TO_GO": 0x00001000,
    "RANDOM_WAVING": 0x00002000,
    "CAUTION": 0x00004000,
    "CAUTION_WAVING": 0x00008000,
    "BLACK": 0x00010000,
    "DISQUALIFY": 0x00020000,
    "SERVICABLE": 0x00040000,
    "FURLED": 0x00080000,
    "REPAIR": 0x00100000,
    "START_HIDDEN": 0x10000000,
    "START_READY": 0x20000000,
    "START_SET": 0x40000000,
    "START_GO": 0x80000000,
}


#def has_flag(ir_conn, flag_name):
#    try:
#        flags = ir_conn["SessionFlags"]
#        bit = FLAGS.get(flag_name.upper())
#        if bit is None:
#            print(f"[WARN] Unknown flag name: {flag_name}")
#            return False
#        return (flags & bit) == bit
#    except Exception as e:
#        print(f"[ERROR] has_flag failed: {e}")
#        return False
def has_flag(session_flags, flag_name):
    try:
        if not isinstance(session_flags, int):
            return False
        bit = FLAGS.get(flag_name.upper())
        if bit is None:
            print(f"[WARN] Unknown flag name: {flag_name}")
            return False
        return (session_flags & bit) != 0
    except Exception:
        return False

_is_race_active_logged = False   # one-shot debug flag

def is_race_session_active(ir_conn) -> bool:
    """Return True ONLY when the currently-active iRacing session is the
    Race session AND cars are actually racing.

    Practice and Qualify sessions also raise GREEN flags and have
    SessionState=4, which was causing the engine to fire commentary
    for test pit stops, warmup overtakes, and quali laps as if they
    were real race events. This helper is the single source of truth
    for "is it time to start broadcasting?"
    """
    global _is_race_active_logged

    # ---- 1. SessionState must be Racing (4) ----
    try:
        raw_state = safe_session_var(ir_conn, "SessionState", 0)
        session_state = int(raw_state) if raw_state is not None else 0
    except (ValueError, TypeError):
        session_state = 0
    except Exception:
        session_state = 0

    if session_state != 4:
        if not _is_race_active_logged:
            print(f"[RACE-GATE] SessionState = {safe_session_var(ir_conn, 'SessionState', '?')} "
                  f"(type={type(safe_session_var(ir_conn, 'SessionState', '?')).__name__}, need int 4)")
            _is_race_active_logged = True
        # Lap-based fallback — only meaningful for ParadeLaps (3). States 5
        # (Checkered) and 6 (CoolDown) indicate the race is already over;
        # opening the gate there drops us straight into finish-commentary
        # and kills any in-flight intro audio. States 0/1/2 (Invalid,
        # GetInCar, Warmup) are pre-race and must stay closed.
        if session_state == 3:
            try:
                car_laps = ir_conn["CarIdxLapCompleted"] or []
                max_lap = max((lap for lap in car_laps if lap >= 0), default=0)
                if max_lap >= 1:
                    print(f"[RACE-GATE] Fallback override: SessionState=3 (parade) + leader on lap {max_lap+1}")
                    return True
            except Exception:
                pass
        return False

    # Reset one-shot so we log again next time it's not 4
    _is_race_active_logged = False

    # ---- 2a. Fast path: SessionTypeName ----
    try:
        stn = safe_session_var(ir_conn, "SessionTypeName", None)
        if isinstance(stn, str) and stn.strip():
            result = "race" in stn.strip().lower()
            if not result:
                print(f"[RACE-GATE] SessionTypeName = '{stn}' — not a race session")
            return result
    except Exception:
        pass

    # ---- 2b. Parse SessionInfo YAML ----
    try:
        raw = safe_session_var(ir_conn, "SessionInfo", {}) or {}
        if isinstance(raw, str):
            import yaml
            raw = yaml.safe_load(raw) or {}
        sessions = (raw.get("Sessions") if isinstance(raw, dict) else None) or []
        if not sessions:
            print("[RACE-GATE] No Sessions list in SessionInfo")
            # Fall through to lap-based fallback
        else:
            session_num = safe_session_var(ir_conn, "SessionNum", 0) or 0
            try:
                session_num = int(session_num)
            except Exception:
                session_num = 0
            if 0 <= session_num < len(sessions):
                stype = (sessions[session_num].get("SessionType") or "").strip().lower()
                if "race" in stype:
                    return True
                # Explicit non-race result — do NOT fall through to the
                # lap-based fallback. Practice/warmup sessions regularly
                # have cars on lap 2+, which would otherwise be treated
                # as a race.
                if stype:
                    print(f"[RACE-GATE] SessionInfo[{session_num}].SessionType = '{stype}' — not a race session")
                    return False
                print(f"[RACE-GATE] SessionInfo[{session_num}].SessionType = '{stype}'")
            else:
                print(f"[RACE-GATE] SessionNum {session_num} out of range (have {len(sessions)} sessions)")
    except Exception as e:
        print(f"[RACE-GATE] SessionInfo parse error: {e}")

    # ---- 3. FALLBACK: if SessionState==4 and cars are on lap 1+, ----
    #      the race is almost certainly running. This covers edge cases
    #      where SessionTypeName is missing and SessionInfo parsing fails.
    try:
        car_laps = ir_conn["CarIdxLapCompleted"] or []
        max_lap = max((lap for lap in car_laps if lap >= 0), default=0)
        if max_lap >= 1:
            print(f"[RACE-GATE] Fallback: SessionState=4 + leader on lap {max_lap+1} — treating as race")
            return True
    except Exception:
        pass

    return False


def sector_says_utilities_safe_get(ir_conn, key):
    try:
        if isinstance(ir_conn, dict):
            return ir_conn.get(key)
        return getattr(ir_conn, key, None)
    except Exception:
        return None


def car_has_flag(ir_conn, car_idx, flag_name):
    """Attempt to determine if a car has a particular flag by trying common per-car flag vars."""
    try:
        for var in (
            "CarIdxFlags",
            "CarIdxFlag",
            "CarIdxCarFlags",
            "CarIdxDriverFlags",
            "CarIdxIsInPit",
        ):
            vals = sector_says_utilities_safe_get(ir_conn, var)
            if isinstance(vals, (list, tuple)) and car_idx < len(vals):
                bit = FLAGS.get(flag_name.upper())
                if bit is None:
                    return False
                try:
                    return (int(vals[car_idx]) & bit) == bit
                except Exception:
                    continue
        return False
    except Exception as e:
        print(f"[WARN car_has_flag] {e}")
        return False


def compute_average_speed(track_length_km: float, average_lap_seconds: float) -> float:
    """Return average speed in km/h given track length (km) and avg lap (s)."""
    try:
        if track_length_km <= 0 or average_lap_seconds <= 0:
            return 0.0
        laps_per_hour = 3600.0 / float(average_lap_seconds)
        return track_length_km * laps_per_hour
    except Exception:
        return 0.0


# --- Export helpers for dashboard & Unity ---
_EXPORTS_DIR = os.path.join(os.path.dirname(__file__), "sector_said_exports")
os.makedirs(_EXPORTS_DIR, exist_ok=True)

def _write_json(name: str, data: dict):
    """Atomic JSON write — write to temp file then rename so readers
    never see a partially-written file (prevents HUD flicker)."""
    path = os.path.join(_EXPORTS_DIR, name)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # os.replace is atomic on the same filesystem (POSIX & Windows)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[WARN] write_json {name} failed: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass

def export_current_commentator_json(name: str):
    _write_json("current_commentator.json", {"name": name})

def append_trigger_stream_event(ev: dict):
    path = os.path.join(_EXPORTS_DIR, "trigger_stream.json")
    try:
        os.makedirs(_EXPORTS_DIR, exist_ok=True)

        js = {"events": []}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        js = json.loads(content)
                    else:
                        print("[DEBUG] trigger_stream.json was empty — resetting.")
            except json.JSONDecodeError:
                print("[DEBUG] trigger_stream.json corrupted — resetting to empty list.")
                js = {"events": []}

        js.setdefault("events", []).append(ev)

        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(js, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[WARN] append_trigger_stream_event failed: {e}")
        traceback.print_exc()


def export_dotd_top3(top3):
    try:
        # --------------------------------------------------
        # Load leaderboard for position + delta lookup
        # --------------------------------------------------
        leaderboard_path = os.path.join(_EXPORTS_DIR, "leaderboard.json")
        pos_map = {}

        if os.path.exists(leaderboard_path):
            with open(leaderboard_path, "r", encoding="utf-8") as f:
                js = json.load(f)
                for car in js.get("cars", []):
                    name = (car.get("Name") or car.get("Driver") or "").strip().lower()
                    pos_map[name] = {
                        "pos": car.get("LivePos", car.get("Pos", "?")),
                        "delta": car.get("ΔPos", car.get("DeltaPos", "")) or ""
                    }

        # --------------------------------------------------
        # DOTD Top-3
        # --------------------------------------------------
        total_points = sum(p for _, p in top3) or 1.0
        payload = {"top3": []}

        for name, pts in top3[:3]:
            key = name.lower().strip()
            pos_info = pos_map.get(key, {"pos": "?", "delta": ""})

            payload["top3"].append({
                "name": name,
                "points": float(pts),
                "percent": round(100.0 * pts / total_points, 1),
                "pos": pos_info.get("pos", "?"),
                "delta": pos_info.get("delta", "")
            })

        # --------------------------------------------------
        # Load FDOTD — from focus_driver_of_the_day.json
        # --------------------------------------------------
        fdotd = None
        fd_path = os.path.join(_EXPORTS_DIR, "focus_driver_of_the_day.json")

        if os.path.exists(fd_path):
            with open(fd_path, "r", encoding="utf-8") as f:
                fd = json.load(f)

            fd_name = (fd.get("Name") or "").strip()
            fd_key = fd_name.lower()

            # Leaderboard lookup
            pos_info = pos_map.get(fd_key, {"pos": "?", "delta": ""})

            # Default values
            fd_points = 0.0
            fd_percent = 0.0

            # Check if FDOTD is part of the top-3 list
            for name, pts in top3:
                if name.lower().strip() == fd_key:
                    fd_points = float(pts)
                    fd_percent = round(100.0 * pts / total_points, 1)
                    break  # correct match found

            # Build the FDOTD block
            fdotd = {
                "name": fd_name,
                "pos": pos_info.get("pos", "?"),
                "delta": pos_info.get("delta", ""),
                "points": fd_points,
                "percent": fd_percent
            }

        # Always include in payload
        if fdotd:
            payload["fdotd"] = fdotd

        # --------------------------------------------------
        # Summary text
        # --------------------------------------------------
        if payload["top3"]:
            top3_text = ", ".join(
                f'{d["name"]} ({d["percent"]:.1f}%, P{d["pos"]})'
                for d in payload["top3"]
            )
            summary = f"Driver of the Day leaderboard — {top3_text}."
        else:
            summary = "Driver of the Day leaderboard — no data."

        if fdotd:
            if "percent" in fdotd:
                summary += f" Focus Driver of the Day: {fdotd['name']} ({fdotd['percent']:.1f}%, P{fdotd['pos']})."
            else:
                summary += f" Focus Driver of the Day: {fdotd['name']} (P{fdotd['pos']})."

        payload["summary_text"] = summary

        # --------------------------------------------------
        # Save JSON (this completely overwrites old file)
        # --------------------------------------------------
        out_path = os.path.join(_EXPORTS_DIR, "dotd_top3.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        print("[DOTD] Exported dotd_top3.json")
        print("[DOTD] Payload written:", json.dumps(payload, indent=2))

    except Exception as e:
        print(f"[DOTD] export_dotd_top3 failed: {e}")

        
def export_timingtower_json(timingtower):
    timingtower_path = os.path.join(_EXPORTS_DIR, "timingtower.json")
    # --- Normalize timingtower into list of {name, gap} ---
    normalized = []
    for item in timingtower:
        # Case 1 — (name, gap) tuple/list
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            name = str(item[0])
            gap = float(item[1]) if isinstance(item[1], (int, float)) else 0.0
            normalized.append((name, gap))
            continue
        # Case 2 — dict
        if isinstance(item, dict):
            name = item.get("Name") or item.get("Driver") or item.get("name")
            gap = item.get("GapAhead") or item.get("gap") or 0.0
            if name:
                try:
                    gap = float(gap)
                except:
                    gap = 0.0
                normalized.append((str(name), gap))
            continue
        # Case 3 — single string
        if isinstance(item, str):
            normalized.append((item, 0.0))
            continue
        # Unknown type
        print("[timingtower] WARN: unknown entry:", item)
    # --- Fall back if nothing valid ---
    if not normalized:
        print("[timingtower] WARNING: No valid entries found.")
        normalized = [("Unknown", 0.0)]
    # --- Convert normalized list to JSON-ready list ---
    cars = []
    for idx, (name, gap) in enumerate(normalized, start=1):
        cars.append({
            "LivePos": idx,
            "DeltaPos": 0,
            "Name": name,
            "GapAhead": round(float(gap), 3),
            "Owner": name.strip().lower() in ("owner", "you", "mycar")
        })
    # --- Atomic write ---
    fd, tmp_path = tempfile.mkstemp(dir=_EXPORTS_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as tmp:
        json.dump({"cars": cars}, tmp, indent=2, ensure_ascii=False)
        tmp.flush()
        os.fsync(tmp.fileno())
    shutil.move(tmp_path, timingtower_path)

def export_full_leaderboard_csv(leaderboard):
    try:
        filename = f"Leaderboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        output_path = os.path.join(_EXPORTS_DIR, filename)
        os.makedirs(_EXPORTS_DIR, exist_ok=True)
        fieldnames = [
            "CarIdx",
            "CarNumber",
            "Name",
            "Lap",
            "Best",
            "Last",
            "LapDistPct",
            "LapsLed",
            "Owner",
            "ΔPos",
            "LivePos",
            "GapAhead",
            "GapToLeader",
            "TrackSurface",
            "DOTD",
        ]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for car in leaderboard:
                writer.writerow({k: car.get(k, "") for k in fieldnames})

        print(f"[CHECKERED FLAG] Full leaderboard exported: {output_path}")
    except Exception as e:
        print(f"[CHECKERED FLAG] Failed to export full leaderboard CSV: {e}")

UNITY_FEED_PATH = os.path.join("sector_said_exports", "unity_feed.jsonl")
def reset_unity_feed_file():
    """
    Clears the Unity feed log at the start of a race.
    Creates folder if missing, truncates the file safely.
    """
    try:
        export_dir = "sector_said_exports"
        os.makedirs(export_dir, exist_ok=True)

        # Overwrite file with a header line (optional)
        with open(UNITY_FEED_PATH, "w", encoding="utf-8") as fp:
            fp.write("")  # empty (JSONL logs don’t require a header)

        #print("[UNITY_FEED] Reset unity_feed.jsonl for new race.")
    except Exception as e:
        print(f"[UNITY_FEED ERROR] Failed to reset file: {e}")

def export_leaderboard_json(cars, ir_conn=None):
    # ------------------------------------------------------
    # PROTECT AGAINST BAD SNAPSHOTS (critical fix)
    # ------------------------------------------------------
    if not cars or len(cars) < 5:
        print(f"[LEADERBOARD] REFUSED unstable snapshot ({len(cars) if cars else 0} cars)")
        return

    # ------------------------------------------------------
    # Load starting grid
    # ------------------------------------------------------
    grid_path = os.path.join(_EXPORTS_DIR, "starting_grid.json")
    starting_grid = {}
    try:
        if os.path.exists(grid_path):
            with open(grid_path, "r", encoding="utf-8") as f:
                starting_grid = json.load(f)
    except Exception as e:
        print("[LEADERBOARD] Failed to load starting_grid.json:", e)

    # ------------------------------------------------------
    # LIVE LAP TELEMETRY (if available)
    # ------------------------------------------------------
    live_laps = []
    live_pct  = []

    if ir_conn is not None and getattr(ir_conn, "is_connected", False):
        try:
            live_laps = ir_conn.get("CarIdxLapCompleted", [])
            live_pct  = ir_conn.get("CarIdxLapDistPct", [])
        except Exception:
            live_laps = []
            live_pct = []

    # ------------------------------------------------------
    # Collect DOTD raw scores
    # ------------------------------------------------------
    dotd_scores = []
    for car in cars:
        try:
            dotd_scores.append(float(car.get("DOTD", 0.0)))
        except:
            dotd_scores.append(0.0)

    if not dotd_scores:
        dotd_scores = [0.0]

    min_score = min(dotd_scores)
    max_score = max(dotd_scores)
    score_range = max_score - min_score if max_score != min_score else None

    # ------------------------------------------------------
    # Build updated leaderboard
    # ------------------------------------------------------
    updated = []

    for car in cars:
        car_idx = int(car.get("CarIdx"))
        car_idx_str = str(car_idx)

        live_pos = car.get("LivePos")
        grid_pos = starting_grid.get(car_idx_str, live_pos)

        # Position delta
        try:
            if isinstance(grid_pos, int) and isinstance(live_pos, int):
                delta = grid_pos - live_pos
            else:
                delta = 0
        except:
            delta = 0

        car["GridPosition"] = grid_pos
        car["ΔPos"] = delta

        # ------------------------------------------------------
        # REAL LIVE LAP INJECTION (critical fix)
        # ------------------------------------------------------
        try:
            if live_laps and 0 <= car_idx < len(live_laps):
                car["Lap"] = int(live_laps[car_idx])
        except Exception:
            pass
        try:
            if live_pct and 0 <= car_idx < len(live_pct):
                car["LapDistPct"] = float(live_pct[car_idx])
        except Exception:
            pass
        # ------------------------------------------------------
        # DOTD Percent normalized 0–100
        # Drivers with zero completed laps (stuck in pits / DNS) get
        # DOTDPercent=0 — they can't earn Driver of the Day without
        # actually driving. Prevents 94%-in-pit-lane embarrassment.
        # ------------------------------------------------------
        try:
            lap_count = int(car.get("Lap", 0) or 0)
            if lap_count <= 0:
                car["DOTDPercent"] = 0.0
                car["DOTD"] = 0.0
            else:
                score = float(car.get("DOTD", 0.0))
                if score_range is None or score_range == 0:
                    pct = 100.0
                else:
                    pct = ((score - min_score) / score_range) * 100.0
                car["DOTDPercent"] = round(pct, 1)
        except:
            car["DOTDPercent"] = 0.0

        updated.append(car)

    # ------------------------------------------------------
    # FINAL SAFE EXPORT
    # ------------------------------------------------------
    _write_json("leaderboard.json", {"cars": updated})


def export_session_meta(
    progress_percent: float,
    remaining_text: str,
    session_info: dict = None,
    current_commentator: str = None,
    total_cars: int = None,
    ir_conn=None,
):
    try:
        path = os.path.join(_EXPORTS_DIR, "session_info.json")
        s = session_info or {}
        payload_track = {}

        # SAFELY read iRacing vars
        if ir_conn is not None and getattr(ir_conn, "is_connected", False):

            # WeekendInfo (may not exist during session transitions)
            try:
                wi = safe_session_var(ir_conn, "WeekendInfo", {}) or {}
            except:
                wi = {}

            # Track info
            try:
                payload_track = get_track_info(ir_conn) or {}
            except:
                payload_track = {}

            # Merge track info
            try:
                s = {**payload_track, **s}
            except:
                pass

            # Safe precipitation & moisture
            s["track_precipitation"] = wi.get("TrackPrecipitation", "?")
            try:
                s["moisture_desc"] = describe_track_moisture(wi)
            except:
                s["moisture_desc"] = "—"

            # -------------------------------
            # 🔥 CORRECT, LIVE LAP NUMBER
            # -------------------------------
            try:
                # Leader is car with smallest LapDistPct error on CarIdx (drivable cars)
                car_laps = ir_conn.get("CarIdxLapCompleted", [])
                car_valid = [
                    (idx, lap) for idx, lap in enumerate(car_laps)
                    if lap >= 0
                ]

                if car_valid:
                    # leader lap = maximum completed lap + 1 (current lap)
                    max_completed_lap = max(lap for _, lap in car_valid)
                    s["lap_current"] = max_completed_lap + 1
                else:
                    s["lap_current"] = 0

            except Exception:
                # Fallback to less accurate session-level number
                try:
                    s["lap_current"] = safe_session_var(
                        ir_conn, "SessionLapsCompleted", 0
                    )
                except:
                    s["lap_current"] = 0

            # Driver count
            try:
                di = ir_conn.get("DriverInfo", {})
                drivers = di.get("Drivers", [])
                total_cars = len(drivers)
            except:
                pass

        # Safe dictionary getter
        def safe(d, key, default="—"):
            val = d.get(key, default) if isinstance(d, dict) else default
            return val if val not in [None, "", {}] else default

        # Final payload
        payload = {
            "track_name": safe(s, "track_name"),
            "track_city": safe(s, "track_city"),
            "track_country": safe(s, "track_country"),
            "track_temp": safe(s, "track_temp"),
            "air_temp": safe(s, "air_temp"),
            "humidity": safe(s, "humidity"),
            "skies": safe(s, "skies"),
            "track_length": safe(s, "track_length"),
            "precipitation": safe(s, "track_precipitation", "—"),
            "moisture_desc": safe(s, "moisture_desc", "—"),
            "total_cars": total_cars or 0,
            "progress_percent": progress_percent,
            "remaining_text": remaining_text,
            "current_commentator": current_commentator,
            "lap_current": s.get("lap_current", 0),   # <── now accurate
        }

        # Atomic write — prevents HUD from reading partial JSON
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)

    except Exception as e:
        print(f"[WARN] export_session_meta failed: {e}")


def export_weekend_info(ir_conn=None):
    try:
        path = os.path.join(_EXPORTS_DIR, "weekend_info.json")
        s = {}
        wi = {}
        if ir_conn is not None and getattr(ir_conn, "is_connected", False):
            wi = safe_session_var(ir_conn, "WeekendInfo", {}) or {}
            s = get_track_info(ir_conn)
        else:
            wi = {}
        try:
            raw_precip = wi.get("TrackPrecipitation", 0)
            track_moisture = float(str(raw_precip).replace("%", "").strip())
        except Exception:
            track_moisture = 0.0
        def safe(d, key, default="—"):
            val = d.get(key, default) if isinstance(d, dict) else default
            return val if val not in [None, "", {}] else default
        payload = {
            "track_name": safe(s, "track_name"),
            "track_city": safe(s, "track_city"),
            "track_country": safe(s, "track_country"),
            "track_length": safe(s, "track_length"),
            "track_display_name": safe(wi, "TrackDisplayName"),
            "event_type": safe(wi, "EventType"),
            "category": safe(wi, "Category"),
            "weekend_options": safe(wi, "WeekendOptions", {}),
            "track_id": safe(wi, "TrackID"),
            "track_moisture": track_moisture,
            "track_moisture_desc": describe_track_moisture(wi),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        # Intentionally quiet for weekend info
        pass

# ─── Motor-racing speech translation guide ────────────────────────
# Layer 3 of the broadcast-speech stack (see README "Critical Invariants"
# vocabulary):
#   1. normalize_telemetry_for_ai  — pre-LLM, rewrites the data block
#   2. SYSTEM_GROUND_RULES         — tells the LLM the convention
#   3. to_broadcast_speech         — post-LLM, deterministic rewrite
#                                    of any raw numerals the LLM emits
# Layer 3 is the catchall: regardless of what the LLM does, we scan
# its output for raw numerals (lap times, gaps, speeds, distances,
# percentages, iRating, position labels) and rewrite them in the way
# a real motor-racing commentator would say them. Replacements use
# private-area placeholders mid-pass so a lap-time substitution does
# not get partially re-matched by a later gap regex.

# Private-area placeholders to mark already-translated chunks so the
# subsequent regex passes leave them alone. Picked from the unicode
# private-use area so they never appear in real commentary text.
_BROADCAST_OPEN  = ""
_BROADCAST_CLOSE = ""


def _bcast_protect(s: str) -> str:
    return f"{_BROADCAST_OPEN}{s}{_BROADCAST_CLOSE}"


def _bcast_unprotect(text: str) -> str:
    return text.replace(_BROADCAST_OPEN, "").replace(_BROADCAST_CLOSE, "")


# Match patterns that already sit inside a placeholder so we don't
# re-translate previously-rewritten fragments.
_BCAST_INSIDE = re.compile(
    f"{_BROADCAST_OPEN}[^{_BROADCAST_OPEN}{_BROADCAST_CLOSE}]*{_BROADCAST_CLOSE}"
)


def _spell_lap_time_from_match(minutes: int, seconds: int, ms_str: str) -> str:
    """Format a lap-time match as 'a one thirty-six point four six three'.
    Leading-zero seconds become 'oh X' (1:03.456 -> 'a one oh three point
    four five six'). Sub-minute lap times skip the 'a one'."""
    ms_str = (ms_str + "000")[:3]
    if minutes == 0:
        # Sub-minute lap: 'fifty-seven point four five six'
        sec_words = _spell_int(seconds)
        return f"{sec_words} point {_spell_digits(ms_str)}"
    if seconds < 10:
        sec_words = f"oh {_UNITS[seconds]}"
    else:
        sec_words = _spell_int(seconds)
    return f"a {_spell_int(minutes)} {sec_words} point {_spell_digits(ms_str)}"


def _spell_gap_seconds(g: float, frac_digits: str = None,
                       with_seconds_word: bool = True) -> str:
    """Spell a gap in seconds the way a commentator would.

    - 0.008  -> 'eight thousandths'
    - 0.123  -> 'point one two three'
    - 0.500  -> 'half a second'
    - 0.5    -> 'half a second'
    - 1.234  -> 'one point two three four'
    - 1.5    -> 'one and a half'
    - 12.5   -> 'twelve and a half seconds'
    - 45     -> 'forty-five seconds'
    - 90     -> 'a minute and a half' (>= 60s)

    `frac_digits` (when provided) preserves the original input precision so
    '0.9' becomes 'point nine' rather than 'point nine zero zero'. Pass
    None to fall back to 3-digit padding via the float.
    """
    if g < 0:
        g = -g
    if g < 0.001:
        return "dead level"

    # Half-second special case: matches 0.5, 0.50, 0.500, etc.
    def _is_half(frac):
        return frac is not None and frac.lstrip("0").rstrip("0") == "5" and frac.startswith("5")

    if g < 0.05 and (frac_digits is None or len(frac_digits) >= 2):
        # Sub-tenth (with at least 2 fractional digits): 'X thousandths'
        ms = int(round(g * 1000))
        if ms <= 0:
            return "dead level"
        if ms == 1:
            return "one thousandth"
        return f"{_spell_int(ms)} thousandths"
    if g < 1.0:
        if _is_half(frac_digits) or (frac_digits is None and abs(g - 0.5) < 1e-9):
            return "half a second"
        if frac_digits is not None:
            return f"point {_spell_digits(frac_digits)}"
        ms = int(round(g * 1000))
        return f"point {_spell_digits(f'{ms:03d}')}"
    if g < 60:
        whole = int(g)
        # Determine the fractional part to speak, preserving the original
        # decimal precision when the caller provided it.
        if frac_digits is not None:
            frac_norm = frac_digits.rstrip("0")
            if frac_norm == "":
                # 1.0 / 1.00 / 1.000 — say as "one second"
                return f"{_spell_int(whole)} second{'s' if whole != 1 else ''}"
            if _is_half(frac_digits):
                return f"{_spell_int(whole)} and a half seconds" if with_seconds_word \
                       else f"{_spell_int(whole)} and a half"
            body = f"{_spell_int(whole)} point {_spell_digits(frac_digits)}"
        else:
            ms = int(round((g - whole) * 1000))
            if ms >= 1000:
                whole += 1
                ms -= 1000
            if ms == 0:
                return f"{_spell_int(whole)} second{'s' if whole != 1 else ''}"
            if ms == 500:
                return f"{_spell_int(whole)} and a half seconds" if with_seconds_word \
                       else f"{_spell_int(whole)} and a half"
            body = f"{_spell_int(whole)} point {_spell_digits(f'{ms:03d}')}"
        if with_seconds_word:
            body += " seconds"
        return body
    mins = int(g // 60)
    remain = int(round(g - mins * 60))
    if remain == 0:
        return f"{_spell_int(mins)} minute{'s' if mins != 1 else ''}"
    if remain == 30 and mins == 1:
        return "a minute and a half"
    return (f"{_spell_int(mins)} minute{'s' if mins != 1 else ''} "
            f"{_spell_int(remain)} seconds")


# Regex catalog. Order matters: most specific first.
_BCAST_LAP_TIME_COLON = re.compile(r"\b(\d{1,2}):([0-5]\d)\.(\d{1,3})\b")
# Bare-seconds lap time: 2 or 3 digit integer with 3 decimals. Ignore
# matches that are immediately followed by a unit suffix that would
# mean it's not a lap time — including a bare 's' (gap suffix), 'K',
# distance/speed/percent units. Lap times in the SSW data block are
# always emitted without a trailing unit (e.g. '96.463'), so adding 's'
# to the negative lookahead prevents '13.300s' (a gap) being mistaken
# for a lap time and leaving an orphan 's' behind.
_BCAST_LAP_TIME_BARE = re.compile(
    r"(?<![\d.])(\d{2,3})\.(\d{3})(?![\d])"
    r"(?!\s*(?:s\b|sec\b|seconds\b|K\b|km/h|kph|mph|km\b|mi\b|miles\b|kmh|%|iR))"
)
_BCAST_SPEED = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(km/h|kph|kmh|mph)\b",
    re.IGNORECASE,
)
_BCAST_DISTANCE_KM = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*km\b(?!\s*/\s*h)",
    re.IGNORECASE,
)
_BCAST_DISTANCE_MI = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:mi|miles)\b",
    re.IGNORECASE,
)
# Percent: '34.9%' (symbol) OR '34.9 percent' (word, common in
# DOTD-share callouts). Both anchored to '%' or 'percent' explicitly
# so the value isn't mis-translated as a gap by the catch-all later.
_BCAST_PERCENT = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|(?<=\s)percent\b)",
    re.IGNORECASE,
)
_BCAST_IRATING = re.compile(
    r"(?<![\w.])(\d{1,5})\s*(?:iR|iRating)\b",
    re.IGNORECASE,
)
# Reverse form: 'iRating 4324' (label before number).
_BCAST_IRATING_REVERSE = re.compile(
    r"\b(?:iR|iRating)\s+(\d{1,5})\b",
    re.IGNORECASE,
)
# Duplicate 'seconds seconds' cleanup — multi-minute gap output always
# ends in 'seconds', and if the source text also had ' seconds' after
# the digits we end up with a double. Final pass collapses it.
_BCAST_DUP_SECONDS = re.compile(r"\bseconds\s+seconds\b", re.IGNORECASE)
# Position labels: P1..P99 with a word boundary so we don't eat 'P14k'
# or part of a name like 'P14M'.
_BCAST_POSITION = re.compile(r"\bP(\d{1,2})\b")
# Generic gap fallback. Decimals with 1-3 fractional digits, optionally
# attached to an 's'/'sec'/'seconds' suffix WITHOUT consuming any
# preceding whitespace — so '34.9 percent' (already eaten by the
# percent regex above) and '5.0 seconds' (the word stays in place
# after the digits get translated) both work cleanly. Trailing
# whitespace is preserved.
_BCAST_GAP_DECIMAL = re.compile(
    r"(?<![\w.])([+\-]?)(\d+)\.(\d{1,3})(s|sec|secs|seconds)?\b",
    re.IGNORECASE,
)
# Plain integer-second gaps with explicit 's' suffix: '45s' / '7 sec'.
# Whitespace not consumed; only the unit attaches to the digit.
_BCAST_GAP_INT_SECONDS = re.compile(
    r"(?<![\w.])(\d{1,4})(s|sec|secs|seconds)\b",
    re.IGNORECASE,
)


def _to_broadcast_lap_time(m: re.Match) -> str:
    mins = int(m.group(1))
    secs = int(m.group(2))
    ms = m.group(3)
    if mins > 9 or secs >= 60:
        # Not a lap time (could be a clock time or unrelated). Bail.
        return m.group(0)
    return _bcast_protect(_spell_lap_time_from_match(mins, secs, ms))


def _to_broadcast_lap_time_bare(m: re.Match) -> str:
    whole = int(m.group(1))
    ms = m.group(2)
    if whole < 10 or whole > 600:
        return m.group(0)
    return _bcast_protect(_spell_lap_time_from_match(0, whole, ms))


def _to_broadcast_speed(m: re.Match) -> str:
    raw = float(m.group(1))
    unit = m.group(2).lower()
    rounded = int(round(raw))
    if unit in ("mph",):
        return _bcast_protect(f"{_spell_int(rounded)} miles an hour")
    return _bcast_protect(f"{_spell_int(rounded)} kilometers an hour")


def _to_broadcast_km(m: re.Match) -> str:
    raw = float(m.group(1))
    if abs(raw - int(raw)) < 0.001:
        body = _spell_int(int(raw))
    else:
        whole = int(raw)
        decs = f"{raw:.3f}".split(".")[1].rstrip("0") or "0"
        body = f"{_spell_int(whole)} point {_spell_digits(decs)}"
    plural = "" if abs(raw - 1.0) < 0.0001 else "s"
    return _bcast_protect(f"{body} kilometer{plural}")


def _to_broadcast_mi(m: re.Match) -> str:
    raw = float(m.group(1))
    if abs(raw - int(raw)) < 0.001:
        body = _spell_int(int(raw))
    else:
        whole = int(raw)
        decs = f"{raw:.3f}".split(".")[1].rstrip("0") or "0"
        body = f"{_spell_int(whole)} point {_spell_digits(decs)}"
    plural = "" if abs(raw - 1.0) < 0.0001 else "s"
    return _bcast_protect(f"{body} mile{plural}")


def _to_broadcast_percent(m: re.Match) -> str:
    raw = float(m.group(1))
    if abs(raw - int(raw)) < 0.001:
        body = _spell_int(int(raw))
    else:
        whole = int(raw)
        decs = f"{raw:.3f}".split(".")[1].rstrip("0") or "0"
        body = f"{_spell_int(whole)} point {_spell_digits(decs)}"
    return _bcast_protect(f"{body} percent")


def _to_broadcast_irating(m: re.Match) -> str:
    return _bcast_protect(voice_format_irating(int(m.group(1))))


def _to_broadcast_position(m: re.Match) -> str:
    n = int(m.group(1))
    return _bcast_protect(f"P {_spell_int(n)}")


def _to_broadcast_gap_decimal(m: re.Match) -> str:
    sign = m.group(1)
    whole = int(m.group(2))
    frac = m.group(3)
    has_seconds = bool(m.group(4))
    g = float(f"{whole}.{frac}")
    body = _spell_gap_seconds(
        g, frac_digits=frac, with_seconds_word=has_seconds,
    )
    if sign == "-":
        body = "minus " + body
    return _bcast_protect(body)


def _to_broadcast_gap_int_seconds(m: re.Match) -> str:
    n = int(m.group(1))
    if n == 0:
        return _bcast_protect("zero seconds")
    if n < 60:
        return _bcast_protect(f"{_spell_int(n)} second{'s' if n != 1 else ''}")
    return _bcast_protect(_spell_gap_seconds(float(n)))


def to_broadcast_speech(text: str) -> str:
    """Rewrite raw numerals in `text` into the way a motor-racing
    commentator would say them. Idempotent on already-translated text.
    Applied as the final step before TTS in sanitize_for_speech."""
    if not isinstance(text, str) or not text:
        return text or ""

    # 1. Lap time with colon (most specific): 1:36.463
    text = _BCAST_LAP_TIME_COLON.sub(_to_broadcast_lap_time, text)
    # 2. Bare-seconds lap time: 96.463 (3 decimals, not followed by a
    #    unit). Skipped if already inside a placeholder.
    def _safe_bare(m):
        # If this match overlaps a placeholder region, leave it alone.
        # The placeholder regex check is implicit via lookbehind on the
        # placeholder character — but the simpler approach: skip if any
        # placeholder char is directly adjacent.
        return _to_broadcast_lap_time_bare(m)
    text = _BCAST_LAP_TIME_BARE.sub(_safe_bare, text)
    # 3. Speeds (km/h, kph, mph)
    text = _BCAST_SPEED.sub(_to_broadcast_speed, text)
    # 4. Distances
    text = _BCAST_DISTANCE_KM.sub(_to_broadcast_km, text)
    text = _BCAST_DISTANCE_MI.sub(_to_broadcast_mi, text)
    # 5. Percent
    text = _BCAST_PERCENT.sub(_to_broadcast_percent, text)
    # 6. iRating (forward and reverse forms)
    text = _BCAST_IRATING.sub(_to_broadcast_irating, text)
    text = _BCAST_IRATING_REVERSE.sub(
        lambda m: _bcast_protect(voice_format_irating(int(m.group(1)))),
        text,
    )
    # 7. Position labels
    text = _BCAST_POSITION.sub(_to_broadcast_position, text)
    # 8. Generic gap-style decimals (catch-all for anything that
    #    looks like a gap: '+0.456', '6.500', '1.2s', etc.)
    text = _BCAST_GAP_DECIMAL.sub(_to_broadcast_gap_decimal, text)
    # 9. Plain integer + 's' (rare but: '7s', '45s')
    text = _BCAST_GAP_INT_SECONDS.sub(_to_broadcast_gap_int_seconds, text)

    # Strip placeholders BEFORE the duplicate cleanup so the cleanup
    # regex can see across the boundary (placeholder chars aren't
    # whitespace, and \s+ won't traverse them).
    text = _bcast_unprotect(text)
    # Collapse 'seconds seconds' duplicates that arise when a
    # multi-minute gap ('one minute thirty-six seconds') is followed
    # by the original text's ' seconds' word.
    text = _BCAST_DUP_SECONDS.sub("seconds", text)
    return text


# === Speech Text Sanitizer / Normalizer ==============
def sanitize_for_speech(text):
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        text = " ".join(map(str, text))
    text = str(text)
    # Translate raw numerals into broadcast form BEFORE the dash strip
    # — to_broadcast_speech relies on word boundaries and we want it
    # to see the original text. After this, '1:36.463' is already
    # 'a one thirty-six point four six three' so the legacy dash and
    # 's'-unit normalisations only see plain words.
    try:
        text = to_broadcast_speech(text)
    except Exception as e:
        print(f"[SPEECH] to_broadcast_speech failed: {e}")
    text = text.replace("-", " ")
    try:
        return expand_units_for_speech(text)
    except Exception:
        return text

def normalize_telemetry_for_ai(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(
        r"\b(\d{2,3}\.\d{3})\b",
        lambda m: voice_format_lap_time(float(m.group(1))),
        text,
    )
    text = re.sub(
        r"([+\-]?\d+\.\d{1,3})",
        lambda m: voice_format_gap(float(m.group(1))),
        text,
    )
    text = expand_units_for_speech(text)
    return text

# === Commentator Images JSON Export ==================
def export_commentator_images_json(
    images_dir=None,
    export_path=None,
    testing=False,
):
    import race_config

    base_dir = images_dir or os.path.join(
        os.path.dirname(__file__), "sector_says_oversee", "commentators"
    )
    export_path = export_path or os.path.join(
        _EXPORTS_DIR, "commentator_images.json"
    )
    os.makedirs(base_dir, exist_ok=True)
    os.makedirs(os.path.dirname(export_path), exist_ok=True)
    images_report = {}

    # 1. Pull profile_image from config for each commentator (primary photo)
    casters = (race_config.cfg("commentary_team") or {}).get("commentators", {})
    for name, cfg in casters.items():
        if cfg.get("is_active") is False:
            continue
        profile_img = cfg.get("profile_image", "")
        location = cfg.get("location", "Studio")
        if profile_img:
            fname = os.path.basename(profile_img)
            images_report.setdefault(name, {}).setdefault(location, []).insert(0, fname)

    # 2. Also scan directory for additional images (filename convention)
    for file in os.listdir(base_dir):
        if not file.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        name_parts = file.rsplit(".", 1)[0].split("_")
        if len(name_parts) < 3:
            continue
        name = name_parts[0].capitalize()
        location = name_parts[1].capitalize()

        existing = images_report.setdefault(name, {}).setdefault(location, [])
        if file not in existing:
            existing.append(file)

    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(images_report, f, indent=2)
    return export_path

def export_driver_images_json(out_dir="sector_said_exports"):
    driver_dir = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        "sector_says_oversee", "drivers"
    ))
    output_path = os.path.join(out_dir, "driver_images.json")
    mapping = {}
    if not os.path.isdir(driver_dir):
        print("[DRIVER IMG] No driver directory found:", driver_dir)
        return
    for file in os.listdir(driver_dir):
        if not file.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        name = os.path.splitext(file)[0]  
        name = name.replace("_", " ").title()  # alex_driver -> Alex Driver
        mapping.setdefault(name, []).append(f"drivers/{file}")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)
    except Exception as e:
        print("[DRIVER IMG] Failed to export:", e)
        
def export_driver_photos():
    folder = os.path.join(os.path.dirname(__file__), "sector_says_oversee", "drivers")
    out_path = os.path.join(os.path.dirname(__file__), "sector_said_exports", "driver_photos.json")
    photos = {}
    for fname in os.listdir(folder):
        if not fname.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        key = os.path.splitext(fname)[0].replace("_", " ").lower()
        photos[key] = fname
    # fallback default
    if "default" not in photos:
        photos["default"] = "default.png"
    with open(out_path, "w") as f:
        json.dump(photos, f, indent=2)

# === FMOD Audio Device Export / Import ================
AUDIO_DEVICES_FILE = os.path.join(_EXPORTS_DIR, "audio_devices.json")
SYSTEM_SETTINGS_FILE = os.path.join(_EXPORTS_DIR, "system_settings.json")

def export_audio_devices_json(devices, selected_id=0):
    out = {
        "audio_output_index": selected_id,
        "AudioDevices": devices,
    }
    try:
        with open(AUDIO_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
    except Exception as e:
        print(f"[FMOD] ERROR writing {AUDIO_DEVICES_FILE}: {e}")

def get_current_audio_device_index(default=0):
    """Resolve the saved audio output device.

    Prefers ``audio_output_name`` — FMOD re-enumerates indices when
    USB devices plug/unplug, so a stored numeric index can silently
    start pointing at the wrong speaker. If a name is stored and
    FMOD currently lists it, return that device's live index. Only
    fall back to ``audio_output_index`` when no name match is found.
    """
    try:
        if not os.path.exists(SYSTEM_SETTINGS_FILE):
            return default
        with open(SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
            js = json.load(f) or {}
        saved_name = (js.get("audio_output_name") or "").strip()
        saved_idx = js.get("audio_output_index", default)
        if saved_name:
            try:
                live_devs = fmod_refresh_device_list()
            except Exception as e:
                print(f"[AUDIO] Name lookup skipped — FMOD enum failed: {e}")
                live_devs = []
            # Exact match first, then prefix match (the user-facing name
            # often has a suffix like " (USB2.0 Device)" that can change).
            for d in live_devs:
                if (d.get("name") or "").strip() == saved_name:
                    if d["id"] != saved_idx:
                        print(f"[AUDIO] Name match: '{saved_name}' moved from "
                              f"index {saved_idx} -> {d['id']}")
                    return d["id"]
            saved_head = saved_name.split(" (")[0].strip().lower()
            for d in live_devs:
                dev_head = (d.get("name") or "").split(" (")[0].strip().lower()
                if saved_head and dev_head == saved_head:
                    if d["id"] != saved_idx:
                        print(f"[AUDIO] Prefix match: '{saved_head}' moved from "
                              f"index {saved_idx} -> {d['id']}")
                    return d["id"]
            print(f"[AUDIO] Stored device '{saved_name}' not found in current "
                  f"FMOD enumeration — falling back to index {saved_idx}.")
        return saved_idx
    except Exception as e:
        print("[AUDIO] Failed to read system_settings.json:", e)
    return default

# === FMOD Device Enumeration ==========================
def fmod_refresh_device_list():
    """Enumerate audio devices using a throwaway FMOD System.

    Used by the Pit Wall /api/audio/devices endpoint and at startup.
    """
    from pyfmodex.system import System as CoreSystem
    core = CoreSystem()
    core.init()
    count = core.num_drivers
    devices = []
    for index in range(count):
        info = core.get_driver_info(index)
        raw_name = info.name
        name = raw_name.decode("utf-8", errors="ignore") if isinstance(raw_name, bytes) else str(raw_name)
        devices.append({"id": index, "name": name})
    core.close()
    return devices

def choose_valid_device(desired_index, devices):
    ids = [d["id"] for d in devices]
    if desired_index in ids:
        return desired_index
    print(f"[FMOD] Saved device {desired_index} no longer exists. Falling back to 0.")
    return 0

# === FMOD System Initialization =======================
def initialize_audio_system(audio_index: int, commentary_queue_obj=None):
    """Create the FMOD System, enumerate devices from IT, validate the
    requested index, set the driver, and init.

    Returns (core, studio, devices) — devices is the enumerated list
    from the SAME System instance that will be used for playback, so
    indices are guaranteed to match.
    """
    try:
        from pyfmodex.system import System as CoreSystem
        from pyfmodex.studio.system import System as StudioSystem

        core = CoreSystem()

        # Enumerate devices from THIS System (not a throwaway)
        count = core.num_drivers
        devices = []
        for index in range(count):
            info = core.get_driver_info(index)
            raw_name = info.name
            name = raw_name.decode("utf-8", errors="ignore") if isinstance(raw_name, bytes) else str(raw_name)
            devices.append({"id": index, "name": name})

        # Validate the requested device
        valid_index = choose_valid_device(int(audio_index), devices)

        # Set driver BEFORE init
        try:
            core.driver = valid_index
        except Exception as e:
            print(f"[FMOD] Could not set driver {valid_index}: {e}")

        core.init(maxchannels=32)

        # Log selected device
        selected_name = next(
            (d["name"] for d in devices if d["id"] == valid_index),
            "Unknown"
        )
        print(f"[FMOD] Audio device: #{valid_index} \"{selected_name}\"")
        print(f"[FMOD] {len(devices)} devices available:")
        for d in devices:
            marker = " <<" if d["id"] == valid_index else ""
            print(f"  [{d['id']}] {d['name']}{marker}")

        # Studio System
        studio = StudioSystem()
        studio.init()

        # Link into commentary processing
        if commentary_queue_obj is not None:
            commentary_queue_obj.set_fmod_system(core)
        race_data_store.fmod_core = core
        race_data_store.fmod_studio = studio
        return core, studio, devices
    except Exception as e:
        print(f"[FMOD] CRITICAL ERROR during audio init: {e}")
        return None, None, []

# === FMOD Hot-Reloading (Live Device Switching) =======
def reload_audio_system(commentary_queue_obj=None):
    print("[FMOD] Reloading FMOD audio system...")
    try:
        requested_index = get_current_audio_device_index()
        print(f"[FMOD] Requested device: {requested_index}")
        core, studio, devices = initialize_audio_system(requested_index, commentary_queue_obj)
        race_data_store.fmod_core = core
        race_data_store.fmod_studio = studio
        print(f"[FMOD] Reload complete.")
        return core, studio
    except Exception as e:
        print(f"[FMOD] ERROR during reload_audio_system: {e}")
        return None, None

if __name__ == "__main__":
    export_commentator_images_json(testing=True)
    devs = fmod_refresh_device_list()
    current_device = get_current_audio_device_index()
    export_audio_devices_json(devs, selected_id=current_device)
    print("[SELFTEST] sector_says_utilities main complete.")
