# ---------------------------------------
# EARLY FMOD PATH FIX (must run before pyfmodex imports)
# ---------------------------------------
# EARLY FMOD PATH FIX (must run before pyfmodex imports)
import os, sys
from pathlib import Path

# Force UTF-8 on stdout/stderr so the log can print em-dashes, arrows,
# unicode driver names (Jos�, �zg�r) without cp1252 crashes on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# .../Sector_Says_What/python/sector_says_engine.py -> project root is one level up from /python
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FMOD_PATH = str(PROJECT_ROOT / "audio" / "fmod")

if os.path.isdir(FMOD_PATH):
    os.environ["PATH"] = FMOD_PATH + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(FMOD_PATH)  # best for Windows DLL loading
    except Exception:
        pass
    sys.path.insert(0, FMOD_PATH)
else:
    print(f"[FMOD PATH WARN] FMOD folder not found: {FMOD_PATH}")


import time
import traceback
import subprocess
import json

import race_config
import race_db
import race_triggers
import lap_sample_capture
import sector_says_utilities
import race_data_store
import live_leaderboard
import commentary_queue
import collision_tracker
import offtrack_tracker

from colorama import init
init(autoreset=True)

from race_data_store import build_pre_race_featured_list
from sector_says_utilities import (
    export_weekend_info,
    export_commentator_images_json,
    export_leaderboard_json,
    export_driver_images_json,
    export_driver_photos,
    export_audio_devices_json,
    initialize_audio_system,
    fmod_refresh_device_list,
    export_session_meta,
    export_featured_drivers,
    has_flag,
    update_focus_driver_for_new_race,
    reset_unity_feed_file,
)

# ANSI color codes
GREEN   = "\033[1;32m"
BLUE    = "\033[1;34m"
MAGENTA = "\033[1;35m"
BOLD    = "\033[1m"

# Title and display width
title = "SECTOR SAYS WHAT"
width = 22

# List of colors to cycle through
colors = [GREEN, BLUE, MAGENTA]

# Display flashing banner
for color in colors * 1:
    print("=" * width)
    print(f"{BOLD}{color}{title.center(width)}")
    print("=" * width)
    time.sleep(0.1)

# ---------------------------------------
# PRE-RACE USER SELECTIONS / EXPORTS
# ---------------------------------------
EXPORTS_DIR = os.path.join(os.path.dirname(__file__), "sector_said_exports")
GRID_FILE = os.path.join(EXPORTS_DIR, "race_grid.json")
FDOTD_FILE = os.path.join(EXPORTS_DIR, "focus_driver_of_the_day.json")
SYSTEM_SETTINGS_FILE = os.path.join(EXPORTS_DIR, "system_settings.json")
BROADCAST_STATE_FILE = os.path.join(EXPORTS_DIR, "broadcast_state.json")
COMMENTARY_REQUESTS_FILE = os.path.join(EXPORTS_DIR, "commentary_requests.json")

# Broadcast state tracking
_commentary_count = 0
_last_soundcheck_id = 0.0

# Audio device hot-reload cursor (engine-process FMOD).
# Flask's /api/config/system save lives in a different process, so its
# reload_audio_system() only touches the Flask-process FMOD. The engine
# watches this signature and reloads its own FMOD when the user changes
# audio device in the HUD or Pit Wall. (index, name) tuple; None means
# "not yet seeded — first observation will seed, not fire".
_last_audio_signature = None

def _export_broadcast_state(status, **kwargs):
    """Write broadcast state JSON for the Pit Wall dashboard."""
    state = {
        "status": status,
        "engine_pid": os.getpid(),
        "race_id": race_db.current_race_id(),
        "lap": kwargs.get("lap", 0),
        "total_laps": kwargs.get("total_laps", 0),
        "elapsed_s": kwargs.get("elapsed_s", 0),
        "car_count": kwargs.get("car_count", 0),
        "commentary_count": _commentary_count,
        "last_trigger": kwargs.get("last_trigger"),
        "started_at": kwargs.get("started_at"),
        "flags": kwargs.get("flags", 0),
    }
    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        tmp = BROADCAST_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, BROADCAST_STATE_FILE)
    except Exception:
        pass

# ---------------------------------------
# DASHBOARD -> ENGINE BRIDGE (sound check, etc.)
# ---------------------------------------
def _read_commentary_request_file():
    """Return the commentary_requests.json dict, or {} if missing/broken."""
    if not os.path.exists(COMMENTARY_REQUESTS_FILE):
        return {}
    try:
        with open(COMMENTARY_REQUESTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _get_lead_commentator_name():
    """Pick the lead commentator from race_config (first active, or Murray fallback)."""
    try:
        team = race_config.cfg("commentary_team") or {}
        rotation = team.get("host_rotation_quarters") or []
        if rotation:
            return rotation[0]
        commentators = team.get("commentators") or {}
        for name, info in commentators.items():
            if info.get("is_active", True):
                return name
    except Exception:
        pass
    return "Murray"

def _get_track_display_name():
    """Read the current track name from session_info.json, if available."""
    path = os.path.join(EXPORTS_DIR, "session_info.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        name = data.get("track_name") or data.get("TrackDisplayName")
        return name.strip() if isinstance(name, str) and name.strip() else None
    except Exception:
        return None

def _fire_soundcheck():
    """Enqueue a short welcome + sound check line from the lead commentator."""
    commentator = _get_lead_commentator_name()
    track = _get_track_display_name()
    location = "Studio"
    try:
        info = (race_config.cfg("commentary_team") or {}).get("commentators", {}).get(commentator, {})
        location = info.get("location", "Studio")
    except Exception:
        pass

    if track:
        line = (
            f"Sound check from the studio — this is {commentator}, live at {track}. "
            f"Good luck out there, and let's have ourselves a race."
        )
    else:
        line = (
            f"Sound check from the studio — this is {commentator}, the broadcast booth is live. "
            f"Good luck out there, and let's have ourselves a race."
        )

    commentary_queue.enqueue_commentary({
        "prompt": line,
        "commentator": commentator,
        "location": location,
        "trigger_name": "soundcheck",
        "clip_id": f"soundcheck_{int(time.time())}",
    })
    print(f"[SOUNDCHECK] fired by {commentator}: {line}")

def _process_commentary_requests():
    """Check the bridge file for new requests from the dashboard and fire them."""
    global _last_soundcheck_id
    req = _read_commentary_request_file()
    sc = req.get("soundcheck") if isinstance(req, dict) else None
    if not isinstance(sc, dict):
        return
    try:
        rid = float(sc.get("id") or 0)
    except (TypeError, ValueError):
        return
    if rid > _last_soundcheck_id:
        _last_soundcheck_id = rid
        try:
            _fire_soundcheck()
        except Exception as e:
            print(f"[SOUNDCHECK] enqueue failed: {e}")

def _poll_audio_device_reload():
    """Hot-reload the engine's FMOD when the saved audio device changes.

    Pit Wall / HUD saves land in the Flask process and can't reach the
    engine's FMOD instance. Each loop tick we re-read system_settings.json
    and, if the (index, name) pair differs from what we last observed,
    call reload_audio_system() inside the engine process. enable_tts
    changes are ignored so toggling them doesn't churn FMOD mid-race.
    """
    global _last_audio_signature
    try:
        if not os.path.exists(SYSTEM_SETTINGS_FILE):
            return
        with open(SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
            js = json.load(f) or {}
        idx = js.get("audio_output_index")
        try:
            idx = int(idx) if idx is not None else None
        except (TypeError, ValueError):
            idx = None
        sig = (idx, (js.get("audio_output_name") or "").strip())
        if _last_audio_signature is None:
            _last_audio_signature = sig
            return
        if sig != _last_audio_signature:
            print(f"[AUDIO] system_settings changed: {_last_audio_signature} -> {sig}. Hot-reloading FMOD.")
            _last_audio_signature = sig
            sector_says_utilities.reload_audio_system(commentary_queue)
    except Exception as e:
        print(f"[AUDIO] poll error: {e}")

# ---------------------------------------
# AUDIO DEVICE SELECTION (NO WINDOWS ENUM)
# ---------------------------------------
# Default fallback when no user preference is saved. The user's actual
# saved choice (system_settings.audio_output_name) is tried first — see
# _resolve_preferred_audio_device. Multiple physical setups exist on
# this machine (Sector_Says_What, Racing_Commentary, Driving_Coach,
# etc.) and the active one varies, so the saved name must win.
AUDIO_PREFERRED_NAME = "Racing_Commentary"


def _resolve_owner_driver():
    """First active owner from race_config.owner_drivers, or None."""
    owners = (race_config.cfg("owner_drivers") or {})
    for name, info in owners.items():
        if not isinstance(info, dict):
            continue
        if info.get("is_active") is False:
            continue
        display = (info.get("display_name") or name or "").strip()
        cid = info.get("iracing_customer_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if display:
            return display, cid
    return None, None


def _resolve_preferred_audio_device(saved_name: str = None):
    """Resolve the audio device against the live FMOD enumeration.

    Priority:
      1. The user's saved `audio_output_name` from system_settings.json
         (passed in as `saved_name`). The user picked this in the HUD or
         Pit Wall and we should respect it across engine restarts even
         if FMOD re-enumerates indices.
      2. The hardcoded fallback AUDIO_PREFERRED_NAME ("Racing_Commentary").
      3. Device #0.

    Each candidate is matched first by exact (lowercased) name, then by
    prefix match against the head before " (", which absorbs USB suffix
    drift like "Sector_Says_What (4- USB2.0 Device)" reordering.
    """
    try:
        devices = fmod_refresh_device_list() or []
    except Exception as e:
        print(f"[AUDIO] FMOD enum failed at startup: {e}")
        devices = []

    if not devices:
        return 0, ""

    candidates = []
    if saved_name and isinstance(saved_name, str) and saved_name.strip():
        candidates.append(saved_name.strip())
    candidates.append(AUDIO_PREFERRED_NAME)

    for cand in candidates:
        head = cand.strip().lower()
        # Exact match
        for d in devices:
            if (d.get("name") or "").strip().lower() == head:
                if cand != candidates[0]:
                    print(f"[AUDIO] Saved '{candidates[0]}' not found — "
                          f"using fallback '{cand}'.")
                return d["id"], d["name"]
        # Prefix match (USB suffix drift)
        for d in devices:
            prefix = (d.get("name") or "").split(" (")[0].strip().lower()
            if prefix == head:
                if cand != candidates[0]:
                    print(f"[AUDIO] Saved '{candidates[0]}' not found — "
                          f"using fallback '{cand}'.")
                return d["id"], d["name"]

    d0 = devices[0]
    print(f"[AUDIO] No saved/preferred device available "
          f"({', '.join(repr(c) for c in candidates)}) — using "
          f"#{d0['id']} \"{d0['name']}\" instead.")
    return d0["id"], d0["name"]


def load_saved_user_settings():
    # --- Read existing system_settings.json (or empty) ---
    sys_settings = {}
    if os.path.exists(SYSTEM_SETTINGS_FILE):
        try:
            with open(SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
                sys_settings = json.load(f) or {}
        except Exception as e:
            print(f"[SETTINGS] Failed to read {SYSTEM_SETTINGS_FILE}: {e}")
            sys_settings = {}

    # --- Force Owner Driver as Focus Driver ------------------
    owner_name, owner_cid = _resolve_owner_driver()
    if owner_name:
        sys_settings["preferred_focus_driver_name"] = owner_name
        sys_settings["preferred_focus_driver_cust_id"] = owner_cid
        print(f"[FDOTD] Focus Driver forced to Owner: "
              f"'{owner_name}' (cust_id={owner_cid})")

        fd_data = {}
        if os.path.exists(FDOTD_FILE):
            try:
                with open(FDOTD_FILE, "r", encoding="utf-8") as f:
                    fd_data = json.load(f) or {}
            except Exception as e:
                print(f"[FDOTD] Failed to read existing FDOTD: {e}")
                fd_data = {}
        fd_data["Name"] = owner_name
        if owner_cid is not None:
            fd_data["CustId"] = owner_cid
        # Wipe stale CarIdx/CarNumber from a previous race. iRacing slots
        # the player into different CarIdx values across sessions (e.g.
        # CarIdx=12 in one race, CarIdx=0 in the next), and the HUD
        # highlights the focus driver by CarIdx — so a stale value
        # makes the HUD point at the wrong row until the user manually
        # re-selects themselves. The pre-green tick refreshes these
        # below via update_focus_driver_for_new_race.
        fd_data["CarIdx"] = None
        fd_data["CarNumber"] = None
        fd_data["UpdatedAt"] = int(time.time())
        try:
            os.makedirs(EXPORTS_DIR, exist_ok=True)
            with open(FDOTD_FILE, "w", encoding="utf-8") as f:
                json.dump(fd_data, f, indent=2)
            race_data_store.current_focus_driver = fd_data
        except Exception as e:
            print(f"[FDOTD] Failed to write FDOTD: {e}")
    else:
        print("[FDOTD] No owner driver found in race_config — "
              "leaving Focus Driver preferences untouched.")
        if os.path.exists(FDOTD_FILE):
            try:
                with open(FDOTD_FILE, "r", encoding="utf-8") as f:
                    race_data_store.current_focus_driver = json.load(f)
            except Exception:
                pass

    # --- Resolve audio output: user's saved name wins ---------
    # Pre-fix this would always overwrite the user's choice with
    # Racing_Commentary. Now: try the saved name first, only fall back
    # to Racing_Commentary if the saved device isn't present in FMOD.
    saved_audio_name = sys_settings.get("audio_output_name")
    audio_idx, audio_name = _resolve_preferred_audio_device(saved_audio_name)
    sys_settings["audio_output_index"] = int(audio_idx)
    if audio_name:
        sys_settings["audio_output_name"] = audio_name
    race_data_store.audio_output_device = int(audio_idx)
    print(f"[AUDIO] Startup device: #{audio_idx} \"{audio_name or 'Unknown'}\""
          + (f" (saved preference '{saved_audio_name}')" if saved_audio_name else ""))

    # --- Persist back to system_settings.json ----------------
    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        with open(SYSTEM_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(sys_settings, f, indent=2)
    except Exception as e:
        print(f"[SETTINGS] Failed to write {SYSTEM_SETTINGS_FILE}: {e}")

# Load settings BEFORE main()
load_saved_user_settings()
race_db.initialize()
pre_race_featured = []
export_driver_images_json()
export_driver_photos()
core, studio, devices = initialize_audio_system(
    race_data_store.audio_output_device,
    commentary_queue,
)
export_audio_devices_json(devices, race_data_store.audio_output_device)

# Start Dashboard Server
def start_dashboard_server():
    oversee_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sector_says_oversee")
    oversee_path = os.path.join(oversee_dir, "app.py")
    print(f"[OVERSEE] Launching dashboard: {oversee_path}")
    print(f"[OVERSEE] Working dir: {oversee_dir}")
    print(f"[OVERSEE] Exists: {os.path.exists(oversee_path)}")
    try:
        proc = subprocess.Popen(
            [sys.executable, oversee_path],
            cwd=oversee_dir,
        )
        print(f"[OVERSEE] Dashboard server started (PID {proc.pid})")
    except Exception as e:
        print(f"[OVERSEE ERROR] {e}")

last_heartbeat = 0
def heartbeat_refresh(ir):
    """Heartbeat — verify SDK is still returning data.

    Buffer is already frozen by the caller's loop tick.
    """
    ss = ir["SessionState"]
    if ss is None:
        print("[HEARTBEAT] WARNING: SessionState is None — SDK connection may be stale")
        try:
            ir.shutdown()
            ir.startup()
            print("[HEARTBEAT] SDK reconnected")
        except Exception as e:
            print(f"[HEARTBEAT] Reconnect failed: {e}")

# ---------------------------------------
# MAIN ENGINE LOOP
# ---------------------------------------
def main():
    start_dashboard_server()

    _engine_started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    _export_broadcast_state("connecting", started_at=_engine_started_at)

    ir = sector_says_utilities.connect_iracing()
    if ir is None:
        print("[ERROR] Could not connect to iRacing. Exiting.")
        _export_broadcast_state("idle")
        return

    # === STARTUP SELF-TEST ===
    print("=" * 50)
    print("  TELEMETRY SELF-TEST")
    print("=" * 50)
    try:
        ir.freeze_var_buffer_latest()
        for var in ["SessionState", "SessionFlags", "SessionNum", "SessionTime"]:
            val = ir[var]
            print(f"  {var:20s} = {val!r}")
        di = ir["DriverInfo"]
        if isinstance(di, dict):
            drivers = di.get("Drivers", [])
            print(f"  {'Drivers':20s} = {len(drivers)} entries")
        wi = ir["WeekendInfo"]
        if isinstance(wi, dict):
            print(f"  {'Track':20s} = {wi.get('TrackDisplayName', '?')}")
        print("  SELF-TEST PASSED")
    except Exception as e:
        print(f"  SELF-TEST FAILED: {e}")
    print("=" * 50)
    sys.stdout.flush()

    # START COMMENTARY QUEUE
    commentary_queue.start()

    # Reset trackers
    offtrack_tracker.reset_offtrack_data()
    collision_tracker.reset_collisions()

    # Reset Unity feed ONCE at start of engine/race
    try:
        reset_unity_feed_file()
    except Exception as e:
        print("[WARN] reset_unity_feed_file failed:", e)

    # Reset trigger stream so dashboard doesn't choke on stale data
    try:
        ts_path = os.path.join(EXPORTS_DIR, "trigger_stream.json")
        with open(ts_path, "w", encoding="utf-8") as f:
            json.dump({"events": []}, f)
    except Exception as e:
        print("[WARN] trigger_stream reset failed:", e)

    # Reset DOTD top3 so HUD doesn't show last race's leaderboard
    try:
        dotd_path = os.path.join(EXPORTS_DIR, "dotd_top3.json")
        with open(dotd_path, "w", encoding="utf-8") as f:
            json.dump({"top3": [], "summary_text": "", "total_points_all": 0}, f)
    except Exception as e:
        print("[WARN] dotd_top3 reset failed:", e)

    # Export early metadata
    export_commentator_images_json()
    export_weekend_info(ir)

    # =======================================================
    # WAIT FOR ANY CARS TO APPEAR BEFORE PRE-GREEN LOOP
    # =======================================================
    while True:
        cars, _ = live_leaderboard.live_leaderboard(ir)
        if cars:
            break
        print("[LIVE] No cars found — retrying in 5 seconds...")
        time.sleep(5)

    export_leaderboard_json(cars)
    _export_broadcast_state("pre_race", car_count=len(cars) if cars else 0, started_at=_engine_started_at)
    # --- SAFE PRE-RACE META EXPORT (pass ir so track/weather populate) ---
    try:
        export_session_meta(
            progress_percent=0,
            remaining_text="Pre-Race",
            session_info={"mode": "pre_race"},
            ir_conn=ir,
        )
    except Exception as e:
        print("[WARN] Pre-race session_meta export FAILED:", e)
    # ======================================================
    # PRE-GREEN WAIT LOOP — KEEP UPDATING LEADERBOARD
    # =======================================================
    #print("Pre-Race Loop Active.")
    last_hb = time.time()
    _pregreen_tick = 0
    # Seed the soundcheck cursor from whatever's already in the bridge
    # file so a stale request from a previous engine run doesn't fire
    # immediately on the first pre-green tick.
    global _last_soundcheck_id, _last_audio_signature
    try:
        _stale = (_read_commentary_request_file().get("soundcheck") or {}).get("id") or 0
        _last_soundcheck_id = float(_stale)
    except Exception:
        pass
    # Seed audio signature so a stale system_settings mtime from a prior
    # run doesn't immediately trigger a reload on the first pre-green tick.
    try:
        if os.path.exists(SYSTEM_SETTINGS_FILE):
            with open(SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
                _js = json.load(f) or {}
            _idx = _js.get("audio_output_index")
            try:
                _idx = int(_idx) if _idx is not None else None
            except (TypeError, ValueError):
                _idx = None
            _last_audio_signature = (_idx, (_js.get("audio_output_name") or "").strip())
    except Exception:
        _last_audio_signature = None

    print("[ENGINE] Entering pre-green loop...")
    while True:
        _pregreen_tick += 1

        # ── STEP 1: Refresh telemetry buffer ────────────────
        try:
            ir.freeze_var_buffer_latest()
        except Exception as e:
            print(f"[PRE-GREEN] freeze FAILED: {e}")
            # If freeze fails, try reconnecting
            try:
                ir.shutdown()
                ir.startup()
            except Exception:
                pass
            time.sleep(0.5)
            continue

        # ── STEP 2: Read gate variables IMMEDIATELY ─────────
        ss = ir["SessionState"]
        flags = ir["SessionFlags"] or 0

        # Log every tick
        if _pregreen_tick <= 5 or _pregreen_tick % 10 == 0:
            print(f"[PRE-GREEN] tick={_pregreen_tick}  SessionState={ss}  flags={flags:#010x}")
            sys.stdout.flush()

        # ── STEP 3: Gate check ──────────────────────────────
        # Single source of truth: SessionState==4 AND session type is Race.
        # Practice/warmup also hit SessionState==4, which would otherwise
        # open this gate and make the HUD show "racing". (Invariant 1.2.)
        race_active = sector_says_utilities.is_race_session_active(ir)

        if race_active:
            print("=" * 50)
            print("GREEN FLAG DETECTED — STARTING RACE ENGINE")
            print("=" * 50)
            import race_data_store
            race_data_store.green_flag_time = time.time()
            try:
                race_data_store.last_summary_race_time = time.time() - SUMMARY_RACE_INTERVAL
            except NameError:
                race_data_store.last_summary_race_time = time.time() - 60
            race_data_store.last_summary_lap_warning = False

            # Starting grid capture is deferred until AFTER the warmup loop.
            # At the green-flag instant iRacing returns CarIdxPosition=0 for
            # several front-of-grid cars (AI-session quirk). The "0 → push to
            # back" sort would invert the grid, stamping the actual winner at
            # the back. We seed from the same cars list race_db.start_race
            # uses, which is captured after positions settle.

            # Export leaderboard at green
            try:
                cars, _ = live_leaderboard.live_leaderboard(ir)
                if cars:
                    export_leaderboard_json(cars)
                    from live_leaderboard import update_leaderboard_snapshot
                    update_leaderboard_snapshot(cars)
                    update_focus_driver_for_new_race(ir, cars)
                    from driver_profiles import export_driver_profiles
                    export_driver_profiles(cars)
                    try:
                        import iracing_api_warmer
                        iracing_api_warmer.reset()
                        iracing_api_warmer.start(cars)
                    except Exception as e:
                        print("[GREEN] iracing warmer kickoff failed:", e)
            except Exception as e:
                print("[GREEN] ERROR exporting initial leaderboard:", e)

            last_hb = time.time()
            break

        # ── Not racing yet — update leaderboard, flags & grid while waiting ──
        try:
            cars, _ = live_leaderboard.live_leaderboard(ir)
            if cars:
                export_leaderboard_json(cars)
                # Refresh FDOTD CarIdx for the current session. Engine
                # startup wiped CarIdx so the HUD doesn't highlight a
                # stale slot from a previous race; this call resolves
                # the live CarIdx from the leaderboard via CustId match
                # (Phase 0 in update_focus_driver_for_new_race) so
                # practice and qualifying highlight the correct driver,
                # not just the race session.
                try:
                    update_focus_driver_for_new_race(ir, cars)
                except Exception as fe:
                    print("[PRE-GREEN] FDOTD refresh failed:", fe)
        except Exception as e:
            print("[PRE-GREEN] leaderboard error:", e)

        # Export flags to broadcast_state so HUD shows flag atmosphere
        _export_broadcast_state(
            "pre_race",
            car_count=len(cars) if cars else 0,
            started_at=_engine_started_at,
            flags=flags,
        )

        # Handle dashboard-originated requests (sound check, etc.)
        _process_commentary_requests()

        # Hot-reload FMOD if the user switched audio device in HUD / Pit Wall.
        _poll_audio_device_reload()

        try:
            export_session_meta(
                progress_percent=0,
                remaining_text="Pre-Race",
                session_info={"mode": "pre_race"},
                ir_conn=ir,
                total_cars=len(cars) if cars else None,
            )
        except Exception:
            pass

        try:
            if cars:
                os.makedirs(EXPORTS_DIR, exist_ok=True)
                with open(GRID_FILE, "w", encoding="utf-8") as f:
                    json.dump(cars, f, indent=2)
        except Exception as e:
            print("[PRE-GREEN] GRID_FILE write error:", e)

        # Heartbeat
        now = time.time()
        if now - last_hb > 10:
            heartbeat_refresh(ir)
            last_hb = now

        time.sleep(0.5)

    # =======================================================
    # WARM-UP: ENSURE FULL LEADERBOARD BEFORE MAIN LOOP
    # =======================================================
    warmup_start = time.time()
    while True:
        ir.freeze_var_buffer_latest()
        cars, _ = live_leaderboard.live_leaderboard(ir)
        car_count = len(cars) if cars else 0

        if car_count >= 10:
            export_leaderboard_json(cars)
            break

        if time.time() - warmup_start > 4:
            print(f"[RACE] WARNING: Still only {car_count} cars after warmup timeout. Using fallback.")
            export_leaderboard_json(cars or [])
            break

        time.sleep(0.1)

    # =======================================================
    # LOCK STARTING GRID FROM QUALIFYING RESULTS
    # =======================================================
    # Done BEFORE start_race so the DB row, the in-memory grid, and the
    # JSON export all agree. record_starting_grid already attempted a
    # quali capture during pre-green; this call upgrades it if quali
    # only finalised between then and now (common in AI sessions where
    # quali and grid lineup overlap). CarIdxPosition-based capture — the
    # pre-green lazy fallback and the previous cars-list seed — was
    # unreliable: 0 for front-of-grid cars at green-flag instant, then
    # scrambled by Turn 1 chaos a few seconds later. QualifyResultsInfo
    # matches the order iRacing displays on its pre-race screen.
    try:
        upgraded = race_data_store.upgrade_starting_grid_from_quali(ir)
        seeded = race_data_store.get_starting_grid()
        if not seeded:
            # Last-resort fallback: synthesise from cars list LivePos.
            # Only fires for AI rollout races with no qualifying session.
            race_data_store.seed_starting_grid_from_cars(cars)
            seeded = race_data_store.get_starting_grid()
            print(f"[GRID] No quali results — fell back to LivePos seed ({len(seeded)} cars)")
        else:
            src = "QualifyResultsInfo (upgraded)" if upgraded else "QualifyResultsInfo (pre-green)"
            print(f"[GRID] Starting grid locked from {src} ({len(seeded)} cars)")
        # Refresh GridPosition on each car in-place so race_db.start_race
        # writes the upgraded values into race_drivers.grid_position.
        if seeded:
            for c in cars or []:
                if not isinstance(c, dict):
                    continue
                ci = c.get("CarIdx")
                if ci in seeded:
                    c["GridPosition"] = seeded[ci]
            json_grid = {str(ci): pos for ci, pos in seeded.items()}
            sector_says_utilities.save_starting_grid(json_grid)
    except Exception as _e:
        print(f"[GRID] grid lock failed: {_e}")

    # =======================================================
    # LOG RACE START TO SQLITE
    # =======================================================
    try:
        wi = getattr(race_data_store, "current_weekend_info", None) or {}
        if not wi:
            wi = sector_says_utilities.safe_session_var(ir, "WeekendInfo", {}) or {}
            if isinstance(wi, dict) and wi:
                race_data_store.current_weekend_info = wi
        race_db.start_race(
            track_name=wi.get("TrackDisplayName", wi.get("TrackName", "Unknown")),
            track_config=wi.get("TrackConfigName", ""),
            session_type="Race",
            car_count=len(cars) if cars else 0,
            total_laps=int(sector_says_utilities.safe_session_var(ir, "SessionLapsRemain", 0) or 0),
            weather=wi,
            config_snapshot=race_config.cfg(),
            drivers=cars,
        )
        # Fresh per-race edge state for lap-crossing capture.
        lap_sample_capture.reset()
        # Wipe accumulated DOTD scores from any previous race in the same
        # engine session. live_leaderboard re-seeds the score dict from the
        # current driver list on the next tick (additive create_race_scores).
        try:
            import sector_says_dotd
            sector_says_dotd.dotd.reset()
            print("[DOTD] Scores reset for new race")
        except Exception as _e:
            print(f"[DOTD] reset failed: {_e}")
    except Exception as e:
        print(f"[DB] Failed to log race start: {e}")

    # =======================================================
    # RACE INTRO
    # =======================================================
    try:
        times = sector_says_utilities.get_race_times(ir) or {}
        elapsed = times.get("elapsed_seconds", 0.0)
        total   = times.get("total_seconds", 1.0)

        race_triggers.start_race_intro(
            elapsed,
            total,
            ir=ir,
            leaderboard=cars,
        )
    except Exception as e:
        print("[RACE] Failed to start race intro:", e)

    # =======================================================
    # MAIN RACE LOOP
    # =======================================================
    try:
        trigger_state = {}
        session_info = {}
        last_hb = time.time()

        _stale_count = 0
        while True:
            try:
                ir.freeze_var_buffer_latest()
            except Exception as e:
                print(f"[RACE] freeze FAILED: {e}")
                _stale_count += 1
                if _stale_count >= 10:
                    print("[RACE] 10 consecutive freeze failures — reconnecting SDK")
                    try:
                        ir.shutdown()
                        ir.startup()
                        _stale_count = 0
                    except Exception:
                        pass
                time.sleep(0.5)
                continue

            try:
                flags = sector_says_utilities.safe_session_var(ir, "SessionFlags", 0)
            except:
                flags = 0

            # Detect stale SDK — SessionState should never be None mid-race
            ss = ir["SessionState"]
            if ss is None:
                _stale_count += 1
                if _stale_count >= 5:
                    print(f"[RACE] SessionState=None for {_stale_count} ticks — reconnecting SDK")
                    try:
                        ir.shutdown()
                        ir.startup()
                        _stale_count = 0
                    except Exception:
                        pass
                continue
            else:
                _stale_count = 0

            if has_flag(flags, "CHECKERED"):
                print("[RACE_END] Checkered flag — ending loop.")
                break

            # Safe leaderboard
            cars, owner_car = live_leaderboard.live_leaderboard(ir)
            if cars and len(cars) > 5:
                export_leaderboard_json(cars)
                # Update in-memory snapshot for triggers
                try:
                    from live_leaderboard import update_leaderboard_snapshot
                    update_leaderboard_snapshot(cars)
                except Exception as e:
                    print("[SNAPSHOT UPDATE ERROR]", e)

            # Record lap-crossing samples for replay timing. Cheap (reads
            # from the already-frozen buffer via cars) and writes at most
            # one row per car per tick — only when CarIdxLap ticked over.
            try:
                st = sector_says_utilities.safe_session_var(ir, "SessionTime", 0.0)
                lap_sample_capture.capture(cars or [], float(st) if st else 0.0)
            except Exception as e:
                print("[RACE] lap_sample_capture ERROR:", e)

            # Timings
            try:
                times = sector_says_utilities.get_race_times(ir)
            except:
                times = {"total_seconds": 0, "elapsed_seconds": 0}

            try:
                progress = sector_says_utilities.get_race_progress(ir)
            except:
                progress = 0

            elapsed = times.get("elapsed_seconds", 0.0)
            total   = times.get("total_seconds", 1.0)
            remaining = max(0, total - elapsed)

            session_info = {
                "ElapsedTime": elapsed,
                "TotalTime": total,
                "Progress": progress,
            }

            # Broadcast state for Pit Wall
            _export_broadcast_state(
                "racing",
                lap=int(sector_says_utilities.safe_session_var(ir, "RaceLaps", 0) or 0),
                total_laps=int(sector_says_utilities.safe_session_var(ir, "SessionLapsRemain", 0) or 0),
                elapsed_s=round(elapsed, 1),
                car_count=len(cars) if cars else 0,
                started_at=_engine_started_at,
                flags=flags,
            )

            # Meta export
            try:
                export_session_meta(
                    progress_percent=progress * 100,
                    remaining_text=f"{int(remaining // 60)}m {int(remaining % 60):02d}s remaining",
                    ir_conn=ir,
                )
            except Exception as e:
                print("[RACE] export_session_meta ERROR:", e)

            # Hot-reload FMOD if the user switched audio device in HUD / Pit Wall.
            _poll_audio_device_reload()

            # Triggers
            try:
                race_triggers.check_triggers(
                    ir=ir,
                    elapsed_time=elapsed,
                    total_time=total,
                    flags=flags,
                    state=trigger_state,
                    session_info=session_info,
                    track_info=getattr(race_data_store, "current_weekend_info", None),
                )
            except Exception as e:
                print("[RACE] check_triggers ERROR:", e)

            # Sector timing
            try:
                car_indices = [c["CarIdx"] for c in cars] if cars else []
                if car_indices:
                    race_data_store.update_sector_times(ir, car_indices)
            except Exception as e:
                print("[RACE] update_sector_times ERROR:", e)

            # Heartbeat
            now = time.time()
            if now - last_hb > 10:
                heartbeat_refresh(ir)
                last_hb = now

            time.sleep(1)

        _export_broadcast_state("finished", started_at=_engine_started_at, flags=flags)

        # CHECKERED fires the instant the leader crosses S/F, but cars
        # behind are still on the cool-down lap and last-corner passes
        # (e.g. P10 -> P9 on the final corner) won't have registered yet.
        # Wait for SessionInfo.ResultsPositions to stabilise before the
        # snapshot so the saved finish order matches the iRacing results
        # screen. Polls each second; breaks when positions+LapsComplete
        # are unchanged for 3 consecutive ticks or after a timeout.
        try:
            _stable_sig = None
            _stable_count = 0
            _wait_deadline = time.time() + 25.0
            while time.time() < _wait_deadline:
                try:
                    ir.freeze_var_buffer_latest()
                except Exception:
                    pass
                _sinfo_w = sector_says_utilities.safe_session_var(ir, "SessionInfo", {}) or {}
                _sigs = []
                for _s in (_sinfo_w.get("Sessions", []) if isinstance(_sinfo_w, dict) else []):
                    if (_s.get("SessionType") or "").lower() != "race":
                        continue
                    for _rp in (_s.get("ResultsPositions") or []):
                        _sigs.append((
                            _rp.get("CarIdx"),
                            _rp.get("Position"),
                            _rp.get("LapsComplete"),
                        ))
                    break
                _sig = tuple(sorted(_sigs, key=lambda t: (t[0] if t[0] is not None else -1))) if _sigs else None
                if _sig is not None and _sig == _stable_sig:
                    _stable_count += 1
                    if _stable_count >= 3:
                        print(f"[RACE END] ResultsPositions stable after "
                              f"{int(time.time() - (_wait_deadline - 25.0))}s wait.")
                        break
                else:
                    _stable_count = 0
                    _stable_sig = _sig
                time.sleep(1.0)
            else:
                print("[RACE END] ResultsPositions stabilisation timed out at 25s; "
                      "snapshotting current state.")
        except Exception as _e:
            print(f"[RACE END] stabilisation wait failed: {_e}")

        # ─── Race finished (checkered flag) ─── log to SQLite
        try:
            final_cars, _ = live_leaderboard.live_leaderboard(ir)

            # Override LivePos / Best / LapsLed / Lap with iRacing's
            # authoritative SessionInfo.Sessions.ResultsPositions block.
            # The engine's Lap+LapDistPct sort can disagree with iRacing
            # at the S/F line and its per-tick LapsLed tally drifts from
            # iRacing's official count; the YAML is the scored truth.
            #
            # NOTE on indexing: the YAML ResultsPositions.Position is
            # 1-INDEXED (1 = winner). Do NOT +1 it. The JSON export you
            # download from the iRacing results page happens to be
            # 0-indexed, but that's a different surface.
            try:
                import sector_says_utilities as _ssu
                _sinfo = _ssu.safe_session_var(ir, "SessionInfo", {}) or {}
                _sessions = _sinfo.get("Sessions", []) if isinstance(_sinfo, dict) else []
                _official = {}
                for _s in _sessions:
                    if (_s.get("SessionType") or "").lower() != "race":
                        continue
                    for _rp in (_s.get("ResultsPositions") or []):
                        _ci = _rp.get("CarIdx")
                        if _ci is None:
                            continue
                        try:
                            _ci_i = int(_ci)
                        except (TypeError, ValueError):
                            continue
                        entry = {}
                        _pos = _rp.get("Position")
                        if _pos is None:
                            _pos = _rp.get("ClassPosition")
                        try:
                            if _pos is not None:
                                entry["pos"] = int(_pos)
                        except (TypeError, ValueError):
                            pass
                        try:
                            if _rp.get("FastestTime") is not None:
                                ft = float(_rp["FastestTime"])
                                if ft > 0:
                                    entry["best"] = ft
                        except (TypeError, ValueError):
                            pass
                        try:
                            if _rp.get("LapsLed") is not None:
                                entry["laps_led"] = int(_rp["LapsLed"])
                        except (TypeError, ValueError):
                            pass
                        try:
                            if _rp.get("LapsComplete") is not None:
                                entry["laps_complete"] = int(_rp["LapsComplete"])
                        except (TypeError, ValueError):
                            pass
                        reason = _rp.get("ReasonOutStr") or _rp.get("ReasonOut")
                        if reason:
                            entry["reason"] = str(reason)
                        _official[_ci_i] = entry
                    break
                if _official:
                    for _c in (final_cars or []):
                        _ci = _c.get("CarIdx")
                        if _ci in _official:
                            off = _official[_ci]
                            if "pos" in off:
                                _c["LivePos"] = off["pos"]
                            if "best" in off:
                                _c["Best"] = off["best"]
                            if "laps_led" in off:
                                _c["LapsLed"] = off["laps_led"]
                            if "laps_complete" in off:
                                _c["Lap"] = off["laps_complete"]
                            if "reason" in off:
                                _c["ReasonOut"] = off["reason"]
                    final_cars = sorted(
                        final_cars or [],
                        key=lambda c: c.get("LivePos") or 9999,
                    )
                    print(f"[RACE END] Finish order from ResultsPositions: "
                          f"{[(c.get('Name'), c.get('LivePos')) for c in final_cars[:5]]}")
                else:
                    print("[RACE END] ResultsPositions empty — using engine sort")
            except Exception as _e:
                print(f"[RACE END] ResultsPositions override failed: {_e}")

            winner = final_cars[0] if final_cars else {}
            owner = next((c for c in (final_cars or []) if c.get("Owner")), {})
            # Get DOTD winner
            dotd_path = os.path.join(EXPORTS_DIR, "dotd_top3.json")
            dotd_winner_name, dotd_winner_score = None, None
            if os.path.exists(dotd_path):
                with open(dotd_path, "r", encoding="utf-8") as f:
                    dotd_data = json.load(f)
                top = dotd_data.get("top3", [{}])
                if top:
                    dotd_winner_name = top[0].get("name")
                    dotd_winner_score = top[0].get("score") or top[0].get("percent")

            race_duration = time.time() - (race_data_store.green_flag_time or time.time())
            race_db.finish_race(
                winner_name=winner.get("Name"),
                winner_car_num=str(winner.get("CarNumber", "")),
                dotd_name=dotd_winner_name,
                dotd_score=dotd_winner_score,
                owner_finish=owner.get("LivePos"),
                owner_car_num=str(owner.get("CarNumber", "")),
                duration_s=race_duration,
                final_standings=final_cars,
            )

            # ─── Owner car finish summary commentary ───
            # Fire a quick race recap focused on the owner's result.
            try:
                from commentary_queue import enqueue_commentary
                import openai_commentary, commentary_team
                from race_triggers import clean_driver_name

                owner_name = clean_driver_name(owner.get("Name") or "the driver")
                owner_pos_raw = owner.get("LivePos")
                try:
                    owner_pos = int(owner_pos_raw) if owner_pos_raw is not None else None
                except (TypeError, ValueError):
                    owner_pos = None
                owner_grid = owner.get("GridPosition") or owner.get("grid_position")
                winner_name = clean_driver_name(winner.get("Name") or "the race leader")
                car_count = len(final_cars) if final_cars else None

                # If the Owner flag never matched (legacy car_idx==0
                # heuristic missed an AI session where the player isn't
                # at car_idx 0), skip the owner-finish line entirely
                # rather than ship a "Finished P? out of ?" callout.
                if owner_pos is None or not owner_name or owner_name == "the driver":
                    print("[FINISH] Owner not identified at race end; "
                          "skipping owner-finish callout.")
                    raise RuntimeError("owner-finish: no owner identified")

                # Grid delta
                delta_text = ""
                if isinstance(owner_grid, int):
                    gained = owner_grid - owner_pos
                    if gained > 0:
                        delta_text = f"Gained {gained} position{'s' if gained > 1 else ''} from P{owner_grid} on the grid."
                    elif gained < 0:
                        delta_text = f"Lost {abs(gained)} position{'s' if abs(gained) > 1 else ''} from P{owner_grid} on the grid."
                    else:
                        delta_text = f"Held their starting position of P{owner_grid}."

                # Top 3 for context
                top3_names = [clean_driver_name(c.get("Name", "?")) for c in (final_cars or [])[:3]]
                podium_text = ""
                if len(top3_names) >= 3:
                    podium_text = f"Podium: {top3_names[0]}, {top3_names[1]}, {top3_names[2]}."

                # DOTD mention
                dotd_text = f"Driver of the Day: {dotd_winner_name}." if dotd_winner_name else ""

                prompt = (
                    f"Checkered flag! Quick race summary for {owner_name}: "
                    f"finished P{owner_pos} out of {car_count}. "
                    f"{delta_text} Winner: {winner_name}. {podium_text} "
                    f"{dotd_text} "
                    f"Write a concise, upbeat race-end summary in broadcast style, under 180 characters. "
                    f"Focus on {owner_name}'s result and the race highlights."
                )

                host, energy, location = commentary_team.get_host_and_energy(1.0, 1.0)
                ai_text = openai_commentary.generate_commentary(prompt)

                enqueue_commentary({
                    "prompt": ai_text or prompt,
                    "commentator": host,
                    "location": location,
                    "type": "owner_finish",
                    "trigger_name": "Race Finish Summary",
                    "clip_id": f"owner_finish_{int(time.time())}",
                    "car_idx": owner.get("CarIdx"),
                    "owner": True,
                })
                print(f"[FINISH] Owner finish commentary fired: P{owner_pos}")
            except Exception as e:
                print(f"[FINISH] Owner finish commentary failed: {e}")

        except Exception as e:
            print(f"[DB] Failed to log race finish: {e}")

    except KeyboardInterrupt:
        print("[ENGINE] Commentary engine stopped by user.")
    except Exception:
        print("[ERROR] Unexpected exception in race loop:")
        traceback.print_exc()
    finally:
        _export_broadcast_state("idle")


if __name__ == "__main__":
    try:
        main()
    finally:
        _export_broadcast_state("idle")
