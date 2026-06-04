from flask import Flask, send_from_directory, request, jsonify, abort
import os, sys, json

# Ensure parent dir is importable for race_config, race_db
_parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

app = Flask(__name__, static_folder=None)

PUBLIC_STATIC_FILES = {
    "broadcast_hud_mockup.html",
    "hud_v2.css",
    "product.html",
    "product.css",
    "product.js",
    "replay.html",
    "replay.js",
    "replay_list.html",
    "replay_list.js",
}
PUBLIC_STATIC_DIRS = {"backgrounds", "commentators", "drivers", "studio", "tracks"}
PUBLIC_STATIC_EXTS = {".css", ".gif", ".html", ".ico", ".jpeg", ".jpg", ".js", ".json", ".png", ".svg", ".webp"}

PUBLIC_API_EXACT = {
    "/api/audio/devices",
    "/api/broadcast/status",
    "/api/health",
    "/api/media/hud-backgrounds",
    "/api/media/logo",
    "/api/media/page-backgrounds",
    "/api/product/summary",
    "/api/replay/list",
    "/api/settings",
}
PUBLIC_API_PREFIXES = (
    "/api/history/races",
    "/api/media/file",
    "/api/replay/",
)


def _is_loopback_request() -> bool:
    addr = (request.remote_addr or "").strip().lower()
    return addr in {"127.0.0.1", "::1", "localhost"} or addr.startswith("127.") or addr.startswith("::ffff:127.")


def _has_admin_token() -> bool:
    expected = os.environ.get("SSW_ADMIN_TOKEN", "").strip()
    if not expected:
        return False
    supplied = request.headers.get("X-SSW-Admin-Token", "") or request.args.get("admin_token", "")
    return supplied == expected


def _is_public_api(path: str) -> bool:
    if path in PUBLIC_API_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_API_PREFIXES)


@app.before_request
def require_admin_for_sensitive_routes():
    path = request.path
    if _is_loopback_request() or _has_admin_token():
        return None
    if path.startswith("/pitwall") and not path.startswith("/pitwall/static/"):
        return jsonify({"error": "Admin access requires localhost or SSW_ADMIN_TOKEN"}), 403
    if path.startswith("/api/") and (
        request.method not in ("GET", "HEAD", "OPTIONS") or not _is_public_api(path)
    ):
        return jsonify({"error": "Admin API requires localhost or SSW_ADMIN_TOKEN"}), 403
    return None

# Register Pit Wall Control Panel blueprint
from pitwall import pitwall
app.register_blueprint(pitwall)

# ---------------------------------------------------------
# CORRECT EXPORT DIRECTORY
# ---------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE, "..", "sector_said_exports")
EXPORT_DIR = os.path.abspath(EXPORT_DIR)
SETTINGS_FILE = os.path.join(EXPORT_DIR, "system_settings.json")
FDOTD_FILE    = os.path.join(EXPORT_DIR, "focus_driver_of_the_day.json")
print("[EXPORT_DIR] Using:", EXPORT_DIR)

# ---------------------------------------------------
# STATIC EXPORTS (dashboard JSON)
# ---------------------------------------------------
@app.route("/sector_said_exports/<path:filename>")
def sector_exports(filename):
    if (
        ".." in filename
        or "/" in filename
        or "\\" in filename
        or os.path.splitext(filename)[1].lower() not in {".json", ".jsonl"}
    ):
        return jsonify({"error": "Invalid export filename"}), 400
    full_path = os.path.join(EXPORT_DIR, filename)
    print("\n[EXPORT DEBUG] Request for:", filename)
    print("[EXPORT DEBUG] Full path:", full_path)
    print("[EXPORT DEBUG] Exists?:", os.path.exists(full_path))
    if not os.path.exists(full_path):
        return jsonify({}), 200  # empty fallback prevents JS errors
    try:
        resp = send_from_directory(EXPORT_DIR, filename, max_age=0)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    except Exception as e:
        print("[EXPORT ERROR]", e)
        return ("Could not load file", 500)

# ---------------------------------------------------
# API: SOUND CHECK (pre-race broadcast test)
# ---------------------------------------------------
# Writes a request into commentary_requests.json; the engine polls this
# file every tick during the pre-green loop and enqueues a short welcome
# line from the lead commentator. Bridge-file approach is required
# because the engine and dashboard run in separate processes.
@app.post("/api/broadcast/soundcheck")
def api_broadcast_soundcheck():
    import time as _time
    # Only allow during pre_race so a button mash after lights-out can't
    # talk over live commentary.
    bs_path = os.path.join(EXPORT_DIR, "broadcast_state.json")
    status = "idle"
    try:
        with open(bs_path, "r", encoding="utf-8") as f:
            status = (json.load(f) or {}).get("status", "idle")
    except Exception:
        pass
    if status != "pre_race":
        return jsonify({"ok": False, "error": f"Sound check only available pre-race (status={status})"}), 409

    req_file = os.path.join(EXPORT_DIR, "commentary_requests.json")
    existing = {}
    if os.path.exists(req_file):
        try:
            with open(req_file, "r", encoding="utf-8") as f:
                existing = json.load(f) or {}
        except Exception:
            existing = {}
    rid = _time.time()
    existing["soundcheck"] = {"id": rid}
    try:
        os.makedirs(EXPORT_DIR, exist_ok=True)
        tmp = req_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        os.replace(tmp, req_file)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "id": rid})

# ---------------------------------------------------
# API: GET SETTINGS
# ---------------------------------------------------
@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    settings = {
        "audio_output_index": 0,
        "focus-driver-list": None
    }
    # system settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                sysset = json.load(f)
            settings["audio_output_index"] = sysset.get("audio_output_index", 0)
        except:
            pass
    # focus driver
    if os.path.exists(FDOTD_FILE):
        try:
            with open(FDOTD_FILE, "r", encoding="utf-8") as f:
                settings["focus-driver-list"] = json.load(f)
        except:
            pass
    return jsonify(settings)

# ---------------------------------------------------
# API: LIST AUDIO DEVICES (FMOD enumeration)
# ---------------------------------------------------
@app.get("/api/audio/devices")
def api_audio_devices():
    devices_path = os.path.join(EXPORT_DIR, "audio_devices.json")
    # Try a live FMOD refresh so the list reflects what's currently plugged in.
    try:
        from sector_says_utilities import fmod_refresh_device_list, export_audio_devices_json
        devices = fmod_refresh_device_list()
        current = 0
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    current = int(json.load(f).get("audio_output_index", 0))
            except: pass
        export_audio_devices_json(devices, current)
    except Exception as e:
        print("[AUDIO] Live refresh failed, falling back to cached file:", e)
    # Return whatever's on disk
    if os.path.exists(devices_path):
        try:
            with open(devices_path, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception as e:
            return jsonify({"AudioDevices": [], "audio_output_index": 0, "error": str(e)}), 200
    return jsonify({"AudioDevices": [], "audio_output_index": 0}), 200

# ---------------------------------------------------
# API: HOT RELOAD AUDIO DEVICE (FMOD LIVE SWITCH)
# ---------------------------------------------------
@app.post("/api/audio/reload")
def api_audio_reload():
    try:
        from sector_says_utilities import reload_audio_system
        import commentary_queue
        core, studio = reload_audio_system(commentary_queue)
        if core is None or studio is None:
            return jsonify({"ok": False, "error": "FMOD reload failed"}), 500
        return jsonify({"ok": True})
    except Exception as e:
        print("[AUDIO] Hot reload error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------------------------------------------------
# API: SAVE SETTINGS
# ---------------------------------------------------
@app.route("/api/settings/save", methods=["POST"])
def api_save_settings():
    data = request.get_json(force=True) or {}
    os.makedirs(EXPORT_DIR, exist_ok=True)
    # Save main system settings
    sys_settings = {
        "audio_output_index": data.get("audio_output_index", 0)
    }
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(sys_settings, f, indent=2)
    # Sync to race_config.json so Pit Wall and HUD stay in agreement
    try:
        import race_config
        race_config.update_section("system", sys_settings)
    except Exception as e:
        print(f"[SETTINGS] race_config sync error: {e}")
    # Save full FDOTD object
    if "focus-driver-list" in data and isinstance(data["focus-driver-list"], dict):
        with open(FDOTD_FILE, "w", encoding="utf-8") as f:
            json.dump(data["focus-driver-list"], f, indent=2)
    return jsonify({"status": "ok"})

# ---------------------------------------------------
# API: SET FULL FOCUS DRIVER (FDOTD)
# ---------------------------------------------------
@app.post("/api/settings/focus_driver")
def api_set_focus_driver():
    data = request.get_json(force=True) or {}

    cust_id = data.get("CustId")
    car_idx = data.get("CarIdx")
    name = data.get("Name")
    num  = data.get("CarNumber")

    lb_path = os.path.join(EXPORT_DIR, "leaderboard.json")
    with open(lb_path, "r", encoding="utf-8") as f:
        cars = json.load(f).get("cars", [])

    driver = None

    if cust_id is not None:
        try:
            cid = int(cust_id)
            driver = next((d for d in cars if d.get("CustId") == cid), None)
        except (TypeError, ValueError):
            pass

    if not driver and car_idx is not None:
        try:
            ci = int(car_idx)
            driver = next((d for d in cars if d.get("CarIdx") == ci), None)
        except (TypeError, ValueError):
            pass

    if not driver and num:
        driver = next((d for d in cars if str(d.get("CarNumber")) == str(num)), None)

    if not driver and name:
        driver = next((d for d in cars if str(d.get("Name")).strip().lower() == str(name).strip().lower()), None)

    if not driver:
        return jsonify({"error": "Driver not found"}), 404

    with open(FDOTD_FILE, "w", encoding="utf-8") as f:
        json.dump(driver, f, indent=2)

    try:
        sys_settings = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                sys_settings = json.load(f) or {}
        clean_name = (driver.get("Name") or "").replace(" [PIT]", "").strip()
        sys_settings["preferred_focus_driver_name"] = clean_name
        drv_cid = driver.get("CustId")
        sys_settings["preferred_focus_driver_cust_id"] = int(drv_cid) if drv_cid else None
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(sys_settings, f, indent=2)
    except Exception as e:
        print(f"[FOCUS] Failed to update system_settings: {e}")

    return jsonify({"ok": True, "driver": driver})


# ---------------------------------------------------
# API: SET DRIVER PROFILE IMAGE
# ---------------------------------------------------
PROFILES_FILE = os.path.join(EXPORT_DIR, "driver_profiles.json")

@app.route("/api/driver-profiles/<car_idx>/image", methods=["PUT"])
def api_set_driver_image(car_idx):
    """Set the profile_image for a driver in driver_profiles.json."""
    data = request.get_json(force=True) or {}
    image_path = data.get("profile_image", "")

    try:
        profiles = {}
        if os.path.exists(PROFILES_FILE):
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                profiles = json.load(f) or {}
        if str(car_idx) not in profiles:
            return jsonify({"error": "Driver not found"}), 404
        profiles[str(car_idx)]["profile_image"] = image_path
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _one_value(con, sql, default=0):
    try:
        row = con.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def _race_pick(con, sql):
    try:
        row = con.execute(sql).fetchone()
        if not row:
            return None
        return {
            "race_id": row["race_id"],
            "track_name": row["track_name"] or "Unknown Track",
            "race_date": row["race_date"] or "",
            "winner_name": row["winner_name"] or "",
            "dotd_name": row["dotd_name"] or "",
            "dotd_percent": row["dotd_score"],
            "owner_finish": row["owner_finish"],
            "total_laps": row["total_laps"] or 0,
            "car_count": row["car_count"] or 0,
        }
    except Exception:
        return None


@app.get("/api/product/summary")
def api_product_summary():
    db_path = os.path.abspath(os.path.join(BASE, "..", "race_history.db"))
    summary = {
        "stats": {
            "races": 0,
            "driver_rows": 0,
            "commentary_lines": 0,
            "lap_samples": 0,
        },
        "examples": {},
        "can_admin_preview": bool(_is_loopback_request() or _has_admin_token()),
        "admin_token_supplied": bool(request.args.get("admin_token")),
    }
    try:
        import sqlite3
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        summary["stats"] = {
            "races": _one_value(con, "SELECT COUNT(*) FROM races"),
            "driver_rows": _one_value(con, "SELECT COUNT(*) FROM race_drivers"),
            "commentary_lines": _one_value(con, "SELECT COUNT(*) FROM commentary_log"),
            "lap_samples": _one_value(con, "SELECT COUNT(*) FROM race_lap_samples"),
        }
        latest_sql = """
            SELECT race_id, track_name, race_date, winner_name, dotd_name, dotd_score,
                   owner_finish, total_laps, car_count
            FROM races
            ORDER BY race_id DESC
            LIMIT 1
        """
        commentary_sql = """
            SELECT r.race_id, r.track_name, r.race_date, r.winner_name, r.dotd_name,
                   r.dotd_score, r.owner_finish, r.total_laps, r.car_count,
                   COUNT(c.id) AS n
            FROM races r
            JOIN commentary_log c ON c.race_id = r.race_id
            GROUP BY r.race_id
            ORDER BY n DESC, r.race_id DESC
            LIMIT 1
        """
        samples_sql = """
            SELECT r.race_id, r.track_name, r.race_date, r.winner_name, r.dotd_name,
                   r.dotd_score, r.owner_finish, r.total_laps, r.car_count,
                   COUNT(s.id) AS n
            FROM races r
            JOIN race_lap_samples s ON s.race_id = r.race_id
            GROUP BY r.race_id
            ORDER BY n DESC, r.race_id DESC
            LIMIT 1
        """
        summary["examples"] = {
            "latest": _race_pick(con, latest_sql),
            "commentary_rich": _race_pick(con, commentary_sql),
            "lap_sample_rich": _race_pick(con, samples_sql),
        }
    except Exception as e:
        summary["error"] = str(e)
    return jsonify(summary)


# ---------------------------------------------------
# STATIC SITE (dashboard)
# ---------------------------------------------------
@app.route("/product")
def product_page():
    return send_from_directory(".", "product.html")

@app.route("/replays")
def replays_page():
    return send_from_directory(".", "replay_list.html")

@app.route("/replay")
@app.route("/replay/<int:race_id>")
def replay_page(race_id=None):
    return send_from_directory(".", "replay.html")

@app.route("/")
def root():
    # Broadcast HUD V2 (the most-updated mockup) is now the live page.
    return send_from_directory(".", "broadcast_hud_mockup.html")
@app.route("/<path:p>")
def static_proxy(p):
    safe = p.replace("\\", "/").strip("/")
    if not safe or safe.startswith("../") or "/../" in safe:
        abort(404)
    ext = os.path.splitext(safe)[1].lower()
    parts = safe.split("/")
    if safe in PUBLIC_STATIC_FILES or (
        len(parts) >= 2
        and parts[0] in PUBLIC_STATIC_DIRS
        and ext in PUBLIC_STATIC_EXTS
    ):
        return send_from_directory(".", safe)
    abort(404)

# ---------------------------------------------------
# SERVER
# ---------------------------------------------------
if __name__ == "__main__":
    # use_reloader=False is critical: with the reloader ON, werkzeug
    # watches every .py file under CWD and restarts the server the moment
    # one changes. While Claude (or any editor) is touching code in the
    # project, an in-flight request — e.g. a Pit Wall eventresult import —
    # gets its connection dropped mid-handler and the browser surfaces
    # "NetworkError when attempting to fetch resource" instead of a 500
    # or a real response. Keep the file-watcher restart loop off.
    host = os.environ.get("SSW_HOST", "127.0.0.1")
    port = int(os.environ.get("SSW_PORT", "8080"))
    debug = os.environ.get("SSW_FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host=host, port=port, debug=debug, use_reloader=False)
