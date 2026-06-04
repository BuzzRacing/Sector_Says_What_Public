"""
pitwall.py — Flask Blueprint for the Pit Wall Control Panel.

Admin dashboard served at /pitwall with config management,
race history, broadcast control, database explorer, media gallery, and more.
"""

import os
import sys
import json
import glob
import subprocess
import signal
import time as _time
import hashlib
from urllib.parse import quote
from flask import Blueprint, send_from_directory, jsonify, request

# Ensure parent directory is importable (for race_config, race_db)
_parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import race_config
import race_db
import track_library
import car_library
import iracing_api_adapter

# Paths
_EXPORTS_DIR = os.path.join(_parent, "sector_said_exports")
_ENGINE_SCRIPT = os.path.join(_parent, "sector_says_engine.py")
_PROJECT_ROOT = os.path.abspath(os.path.join(_parent, ".."))

# Broadcast process handle
_engine_process = None

# Path to the system_settings.json that the engine reads at runtime
_SYSTEM_SETTINGS_FILE = os.path.join(_EXPORTS_DIR, "system_settings.json")


def _sync_system_settings(system_data: dict):
    """Keep system_settings.json in sync when config/system is saved.

    The engine reads audio_output_index from this file, so both the
    Pit Wall and Broadcast HUD settings must land here.  After writing,
    trigger FMOD hot-reload so the change takes effect immediately.

    Also stores ``audio_output_name`` (when the caller provided one) so
    a later FMOD re-enumeration with shifted indices can resolve the
    user's intended device by name instead of silently routing to the
    wrong speaker.
    """
    os.makedirs(_EXPORTS_DIR, exist_ok=True)
    existing = {}
    if os.path.exists(_SYSTEM_SETTINGS_FILE):
        try:
            with open(_SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    # Only overwrite audio_output_index if the caller sent one. A PUT
    # that's just toggling enable_tts must not clobber the currently
    # saved device.
    if "audio_output_index" in system_data and system_data["audio_output_index"] is not None:
        try:
            existing["audio_output_index"] = int(system_data["audio_output_index"])
        except (TypeError, ValueError):
            pass
    if system_data.get("audio_output_name"):
        existing["audio_output_name"] = str(system_data["audio_output_name"]).strip()
    with open(_SYSTEM_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    # Hot-reload FMOD so the device switch is immediate
    try:
        from sector_says_utilities import reload_audio_system
        import commentary_queue
        reload_audio_system(commentary_queue)
    except Exception as e:
        print(f"[PITWALL] FMOD hot-reload skipped: {e}")

PITWALL_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pitwall_static")

pitwall = Blueprint(
    "pitwall",
    __name__,
    static_folder=PITWALL_STATIC,
    static_url_path="/pitwall/static",
)


# ─── Admin shell ──────────────────────────────────────────────────

@pitwall.route("/pitwall")
def pitwall_home():
    return send_from_directory(PITWALL_STATIC, "pitwall.html")


@pitwall.route("/pitwall/<path:filename>")
def pitwall_static(filename):
    return send_from_directory(PITWALL_STATIC, filename)


# ─── Config API ───────────────────────────────────────────────────

@pitwall.route("/api/config", methods=["GET"])
def get_config():
    """Return the full config."""
    return jsonify(race_config.cfg())


@pitwall.route("/api/config/defaults", methods=["GET"])
def get_defaults():
    """Return built-in defaults (for Reset to Defaults)."""
    return jsonify(race_config.defaults())


@pitwall.route("/api/config/<section>", methods=["GET"])
def get_config_section(section):
    """Return a single config section."""
    return jsonify(race_config.cfg(section))


@pitwall.route("/api/config/<section>", methods=["PUT"])
def update_config_section(section):
    """Merge updates into a config section and save.

    Pass ``?replace=true`` when the client is sending the complete desired
    state and wants removals (deleted triggers, unchecked flags, sliders
    moved back to 0) to actually land. Default stays deep-merge so partial
    updates don't drop unrelated fields."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected JSON object"}), 400
    replace = request.args.get("replace", "").lower() in ("1", "true", "yes")
    race_config.update_section(section, data, replace=replace)

    # When "system" is saved, sync audio_output_index / name to
    # system_settings.json so the engine (which reads that file) stays
    # in sync with Pit Wall / HUD.
    if section == "system" and (
        "audio_output_index" in data or "audio_output_name" in data
    ):
        try:
            _sync_system_settings(data)
        except Exception as e:
            print(f"[PITWALL] system_settings sync error: {e}")

    return jsonify({"ok": True, "section": section})


@pitwall.route("/api/config/reload", methods=["POST"])
def reload_config():
    """Hot-reload config from disk."""
    race_config.reload()
    return jsonify({"ok": True})


# ─── iRacing /data API ───────────────────────────────────────────

@pitwall.route("/api/iracing/status", methods=["GET"])
def iracing_status():
    """Report whether the /data API integration is wired up.

    The frontend uses this to render a green/red dot in the Power Unit
    panel. Never touches the network — just inspects local state.
    """
    cfg = race_config.cfg("power_unit").get("iracing_api", {}) or {}
    secrets = race_config.secrets("iracing") or {}
    return jsonify({
        "enabled": bool(cfg.get("enabled")),
        "creds_present": bool(secrets.get("email") and secrets.get("password")),
        "auth_locked_out": bool(iracing_api_adapter._auth_failed_this_process),
        "available": iracing_api_adapter.is_available(),
        "cache_ttl_hours": cfg.get("cache_ttl_hours", 24),
    })


@pitwall.route("/api/iracing/reset_lockout", methods=["POST"])
def iracing_reset_lockout():
    """Clear the auth-lockout sentinel after the operator fixes creds."""
    iracing_api_adapter.reset_auth_lockout()
    return jsonify({"ok": True})


@pitwall.route("/api/iracing/test", methods=["GET"])
def iracing_test():
    """Hit a tiny endpoint to confirm auth + connectivity.

    Uses constants_categories — cheap, no rate-limit cost worth worrying
    about, and fails fast if creds are bad. Returns the resulting list
    or an error string the UI can show in a toast.
    """
    if not iracing_api_adapter.is_available():
        return jsonify({
            "ok": False,
            "error": "Not configured. Set iracing_api.enabled and add credentials to race_secrets.json.",
        }), 400
    try:
        cats = iracing_api_adapter.get_constants().get("categories") or []
        return jsonify({"ok": True, "categories_count": len(cats)})
    except Exception as e:
        return jsonify({"ok": False, "error": repr(e)}), 500


# ─── Race History API ─────────────────────────────────────────────

@pitwall.route("/api/history/races", methods=["GET"])
def list_races():
    """Return recent races."""
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(race_db.get_races(limit, offset))


@pitwall.route("/api/history/races/<int:race_id>", methods=["GET"])
def race_detail(race_id):
    """Return full race detail with drivers, commentary, incidents."""
    detail = race_db.get_race_detail(race_id)
    if not detail:
        return jsonify({"error": "Race not found"}), 404
    return jsonify(detail)


@pitwall.route("/api/history/drivers", methods=["GET"])
def driver_careers():
    """Return driver career stats leaderboard."""
    return jsonify(race_db.get_driver_careers())


@pitwall.route("/api/history/drivers/<name>", methods=["GET"])
def driver_career_detail(name):
    """Return single driver career stats."""
    career = race_db.get_driver_career(name)
    if not career:
        return jsonify({"error": "Driver not found"}), 404
    return jsonify(career)


@pitwall.route("/api/history/drivers/<name>/career-replay", methods=["GET"])
def driver_career_replay(name):
    """Return detailed Focus Driver career replay stats."""
    limit = request.args.get("limit", 60, type=int)
    career = race_db.get_driver_career_replay(name, limit=limit)
    if not career:
        return jsonify({"error": "Driver not found"}), 404
    return jsonify(career)


@pitwall.route("/api/history/owner", methods=["GET"])
def owner_history():
    """Return the owner's recent race results."""
    limit = request.args.get("limit", 10, type=int)
    return jsonify(race_db.get_owner_history(limit))


# ─── Race Replay API ─────────────────────────────────────────────

@pitwall.route("/api/replay/import", methods=["POST"])
def replay_import():
    """Import an iRacing eventresult JSON into the saved race archive.

    Accepts the parsed JSON payload as the request body (Content-Type:
    application/json). Auto-merges into an existing race row when the
    subsession_id matches; otherwise inserts a new race. Pass
    ``?force_new=1`` to skip the merge and insert a new row regardless.

    Returns the structured result from ``import_iracing_eventresult`` so
    the UI can render race_id, merge status, and driver-count details.
    """
    try:
        from import_iracing_eventresult import import_eventresult_payload
    except ImportError as e:
        return jsonify({"error": f"Importer module unavailable: {e}"}), 500

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be JSON (the parsed eventresult contents)"}), 400

    force_new = request.args.get("force_new", "").lower() in ("1", "true", "yes")
    try:
        result = import_eventresult_payload(payload, force_new=force_new)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Import failed: {e}"}), 500
    return jsonify(result)


@pitwall.route("/api/replay/list", methods=["GET"])
def replay_list():
    """Enriched race list for the replay-picker page (cards).

    Returns one row per race with per-driver lookups (winner / owner /
    DOTD) flattened plus their iRacing cust_ids so the frontend can
    render headshots from the iracing_cache. dotd_score on races is
    the WINNER'S vote-share percent (e.g. 59.9) — see invariant 1.x
    in CLAUDE.md and the dotd_percent column on race_drivers.
    """
    result = race_db.execute_readonly(
        """
        SELECT r.race_id, r.track_name, r.track_config, r.race_date,
               r.session_type, r.car_count, r.total_laps,
               r.winner_name, r.winner_car_num,
               r.dotd_name, r.dotd_score AS dotd_percent,
               COALESCE(
                 r.owner_finish,
                 (SELECT rd.finish_position FROM race_drivers rd
                    WHERE rd.race_id = r.race_id AND rd.is_owner = 1 LIMIT 1)
               ) AS owner_finish,
               (SELECT COUNT(*) FROM race_drivers rd WHERE rd.race_id = r.race_id) AS drivers_count,
               (SELECT rd.driver_name FROM race_drivers rd
                  WHERE rd.race_id = r.race_id AND rd.is_owner = 1 LIMIT 1) AS owner_name,
               (SELECT rd.grid_position FROM race_drivers rd
                  WHERE rd.race_id = r.race_id AND rd.is_owner = 1 LIMIT 1) AS owner_grid,
               (SELECT rd.cust_id FROM race_drivers rd
                  WHERE rd.race_id = r.race_id AND rd.is_owner = 1 LIMIT 1) AS owner_cust_id,
               (SELECT rd.cust_id FROM race_drivers rd
                  WHERE rd.race_id = r.race_id AND rd.finish_position = 1 LIMIT 1) AS winner_cust_id,
               (SELECT rd.cust_id FROM race_drivers rd
                  WHERE rd.race_id = r.race_id AND rd.driver_name = r.dotd_name LIMIT 1) AS dotd_cust_id
        FROM races r
        ORDER BY r.race_id DESC
        """
    )
    rows = result.get("rows", []) or []
    # Enrich with usable square-card images. Prefer iRacing headshots,
    # then configured owner photos, then deterministic local driver art
    # so replay cards never fall back to an empty badge.
    img_cache = {}
    def _img(cid):
        if not cid:
            return None
        if cid in img_cache:
            return img_cache[cid]
        try:
            blob = race_db.get_cached_driver(int(cid))
            url = blob.get("image_url") if isinstance(blob, dict) else None
        except Exception:
            url = None
        img_cache[cid] = url
        return url

    def _media_url(path):
        if not path:
            return None
        path = str(path).strip().replace("\\", "/")
        if not path:
            return None
        if path.startswith(("http://", "https://", "/")):
            return path
        return "/api/media/file?path=" + quote(path, safe="")

    owner_profiles = race_config.cfg("owner_drivers") or {}
    def _owner_img(name, cid):
        name_l = (name or "").strip().lower()
        try:
            cid_i = int(cid) if cid else None
        except (TypeError, ValueError):
            cid_i = None
        for key, profile in owner_profiles.items():
            if not isinstance(profile, dict):
                continue
            display_l = str(profile.get("display_name") or key or "").strip().lower()
            try:
                profile_cid = int(profile.get("iracing_customer_id") or 0)
            except (TypeError, ValueError):
                profile_cid = 0
            if (cid_i and profile_cid == cid_i) or (name_l and display_l == name_l):
                return _media_url(profile.get("profile_image"))
        return None

    def _default_driver_img(name, cid):
        seed = f"{cid or ''}|{name or ''}".encode("utf-8", "ignore")
        n = (int(hashlib.sha1(seed).hexdigest()[:8], 16) % 10) + 1
        return _media_url(f"python/sector_says_oversee/drivers/default_male_{n:02d}.png")

    def _driver_img(name, cid):
        return _img(cid) or _owner_img(name, cid) or _default_driver_img(name, cid)

    for r in rows:
        r["winner_image"] = _driver_img(r.get("winner_name"), r.get("winner_cust_id"))
        r["owner_image"]  = _driver_img(r.get("owner_name"), r.get("owner_cust_id"))
        r["dotd_image"]   = _driver_img(r.get("dotd_name"), r.get("dotd_cust_id"))
    return jsonify(rows)




# Deterministic livery palette for replay dots (F1 team-style colors)
_REPLAY_LIVERY = [
    "2ecc71", "f5c518", "00d2be", "0090ff", "dc0000",
    "ff8700", "9b59b6", "e91e63", "ff5252", "26d4c8",
    "fbbf24", "06b6d4", "84cc16", "f97316", "a78bfa",
    "ec4899", "10b981", "fcd34d", "60a5fa", "f43f5e",
]


def _career_summary_for_replay(cust_id):
    """Pull the dominant racing-category career row out of the iracing_cache
    blob and flatten it into a small dict the replay HUD can render.

    Returns ``None`` when the cust_id is missing, the cache row is gone,
    or the blob has no usable stats. Picks the category with the most
    starts — same heuristic the commentary dossier uses, kept local
    so the replay route doesn't import race_triggers.
    """
    if not cust_id:
        return None
    try:
        blob = race_db.get_cached_driver(int(cust_id))
    except Exception:
        return None
    if not isinstance(blob, dict):
        return None
    career = blob.get("career") or {}
    stats = career.get("stats") if isinstance(career, dict) else None
    if not isinstance(stats, list) or not stats:
        return None

    best_row = None
    best_starts = -1
    for s in stats:
        if not isinstance(s, dict):
            continue
        starts = s.get("starts") or 0
        if isinstance(starts, int) and starts > best_starts:
            best_starts = starts
            best_row = s
    if not best_row or best_starts <= 0:
        return None

    return {
        "category": (best_row.get("category") or "").strip(),
        "starts": int(best_row.get("starts") or 0),
        "wins": int(best_row.get("wins") or 0),
        "top5": int(best_row.get("top5") or 0),
        "poles": int(best_row.get("poles") or 0),
        "avg_finish": int(round(best_row.get("avg_finish_position") or 0)) or None,
        "image_url": blob.get("image_url"),
    }


def _normalise_replay_lap_samples(
    samples: list[dict],
    plausible_lap_s: float | None,
    time_offset_s: float = 0.0,
) -> list[dict]:
    """Convert raw iRacing CarIdxLap samples into race-relative progress.

    CarIdxLap is a current-lap counter, not completed race laps. Most
    standing-start races first tick from 1 to 2 at the end of lap one. Some
    layouts with separate start/control lines, notably Nordschleife
    Industriefahrten, also emit an early partial first crossing. The replay
    ranker needs comparable race progress, so map each driver's first genuine
    lap crossing to lap 1 and any implausibly early partial crossing to lap 0.
    """
    if not samples:
        return []

    out = [dict(s) for s in samples]
    if time_offset_s and time_offset_s > 0:
        for s in out:
            if isinstance(s.get("t"), (int, float)):
                s["raw_t"] = s["t"]
                s["t"] = max(0.0, float(s["t"]) - time_offset_s)

    first = next(
        (
            s for s in out
            if isinstance(s.get("lap"), int) and isinstance(s.get("t"), (int, float))
        ),
        None,
    )
    if not first:
        return out

    first_lap = first["lap"]
    first_t = float(first["t"])
    early_partial = False
    if plausible_lap_s and plausible_lap_s > 0:
        early_partial = first_t < plausible_lap_s * 0.50

    offset = first_lap if early_partial else first_lap - 1
    for s in out:
        if isinstance(s.get("lap"), int):
            s["raw_lap"] = s["lap"]
            s["lap"] = max(0, s["lap"] - offset)
    return out


def _normalise_replay_pit_stops(
    stops: list[dict],
    time_offset_s: float = 0.0,
) -> list[dict]:
    """Return pit-stop windows on the same race-relative clock as samples."""
    out = [dict(s) for s in stops]
    if time_offset_s and time_offset_s > 0:
        for s in out:
            for key in ("entered_time", "exited_time"):
                if isinstance(s.get(key), (int, float)):
                    s[key] = max(0.0, float(s[key]) - time_offset_s)
    return out


@pitwall.route("/api/replay/<int:race_id>", methods=["GET"])
def replay_race(race_id):
    """Convert a stored race to the racing-replay JSON schema."""
    detail = race_db.get_race_detail(race_id)
    if not detail:
        return jsonify({"error": "Race not found"}), 404

    race = detail["race"]
    drivers = detail["drivers"]

    # Pre-fetch /data API career summaries for any driver with a cust_id.
    # Old races (pre-Phase 1) won't have cust_ids and silently get None.
    career_by_cid: dict[int, dict] = {}
    for d in drivers:
        cid = d.get("cust_id")
        if isinstance(cid, int) and cid and cid not in career_by_cid:
            summary = _career_summary_for_replay(cid)
            if summary:
                career_by_cid[cid] = summary

    # Phase 2 replay timing: per-lap crossings recorded live by the engine.
    # Grouped by car_idx (primary) with a cust_id fallback for rows where
    # car_idx was never populated. Empty for races that predate the capture.
    samples_by_caridx: dict[int, list[dict]] = {}
    samples_by_custid: dict[int, list[dict]] = {}
    try:
        sample_rows = race_db.execute_readonly(
            "SELECT car_idx, cust_id, lap_number, session_time_s, "
            "lap_time_s, position, interval_ms, personal_best, incident "
            f"FROM race_lap_samples WHERE race_id = {int(race_id)} "
            "ORDER BY lap_number ASC, session_time_s ASC"
        ).get("rows", [])
        for row in sample_rows:
            item = {
                "lap": row.get("lap_number"),
                "t": row.get("session_time_s"),
                "lap_time": row.get("lap_time_s"),
                "position": row.get("position"),
                "interval_ms": row.get("interval_ms"),
                "pb": bool(row.get("personal_best")),
                "inc": bool(row.get("incident")),
            }
            ci = row.get("car_idx")
            if isinstance(ci, int):
                samples_by_caridx.setdefault(ci, []).append(item)
            cid = row.get("cust_id")
            if isinstance(cid, int) and cid:
                samples_by_custid.setdefault(cid, []).append(item)
    except Exception as e:
        print(f"[replay] lap_samples load failed: {e}")

    # Per-car pit stops so replay.js can render in-pit cars off the racing
    # line during the actual stop window. Empty array for races with no
    # logged stops (and silently safe for older replays without the column).
    pit_stops_by_caridx: dict[int, list[dict]] = {}
    try:
        pit_rows = race_db.execute_readonly(
            "SELECT car_idx, stop_num, entered_lap, entered_time, "
            "exited_time, duration_s "
            f"FROM race_pit_stops WHERE race_id = {int(race_id)} "
            "ORDER BY entered_time ASC"
        ).get("rows", [])
        for row in pit_rows:
            ci = row.get("car_idx")
            if not isinstance(ci, int):
                continue
            pit_stops_by_caridx.setdefault(ci, []).append({
                "stop_num": row.get("stop_num"),
                "entered_lap": row.get("entered_lap"),
                "entered_time": row.get("entered_time"),
                "exited_time": row.get("exited_time"),
                "duration_s": row.get("duration_s"),
            })
    except Exception as e:
        print(f"[replay] pit_stops load failed: {e}")

    total_laps = race.get("total_laps") or 0
    # iRacing stores 32767 (INT16 max) for time-limited sessions where the
    # lap target is "unlimited". Anything in that range is meaningless to
    # the replay UI — treat it as unknown.
    if total_laps and total_laps > 999:
        total_laps = 0
    max_driver_laps = 0
    for d in drivers:
        laps_done = d.get("laps_complete")
        if isinstance(laps_done, (int, float)) and 0 < laps_done <= 999:
            max_driver_laps = max(max_driver_laps, int(laps_done))
    if max_driver_laps > total_laps:
        total_laps = max_driver_laps
    duration_s = float(race.get("duration_s") or 0.0)
    # Same kind of garbage shows up in duration when a session was abandoned
    # mid-stream — clamp anything over 4 hours.
    if duration_s > 14400:
        duration_s = 0.0
    n = max(len(drivers), 1)

    plausible_lap_s = None
    for d in drivers:
        for key in ("best_lap", "avg_lap"):
            lap_s = d.get(key) or 0
            if lap_s and lap_s > 0 and (plausible_lap_s is None or lap_s < plausible_lap_s):
                plausible_lap_s = float(lap_s)

    sample_time_offset_s = 0.0
    if duration_s and duration_s > 0:
        winner = next((d for d in drivers if d.get("finish_position") == 1), None)
        winner_car_idx = winner.get("car_idx") if winner else None
        winner_samples = samples_by_caridx.get(winner_car_idx, []) if isinstance(winner_car_idx, int) else []
        winner_last_t = max(
            (
                s.get("t") for s in winner_samples
                if isinstance(s.get("t"), (int, float))
            ),
            default=None,
        )
        if winner_last_t and winner_last_t > duration_s:
            offset = float(winner_last_t) - duration_s
            # A positive delta here is the iRacing SessionTime at green.
            # Clamp wild values so abandoned/partial rows do not shove the
            # whole replay clock around.
            if offset < 600:
                sample_time_offset_s = offset

    out_drivers = []
    for i, d in enumerate(drivers):
        # Prefer the real per-driver average lap (imported from eventresult /
        # GrumpyRobot). Fall back to best_lap + finish-position tiebreaker for
        # older races that predate the avg_lap column, and finally to a
        # synthetic gap if we don't even have a best lap.
        avg_lap_s = d.get("avg_lap") or 0.0
        best_lap = d.get("best_lap") or 0.0  # seconds
        if avg_lap_s and avg_lap_s > 0:
            avg_lap_units = int(avg_lap_s * 10000)
        elif best_lap and best_lap > 0:
            avg_lap_units = int(best_lap * 10000) + (d.get("finish_position") or 1) * 200
        else:
            avg_lap_units = 800000 + i * 500

        grid = d.get("grid_position") or (i + 1)
        finish = d.get("finish_position") or (i + 1)
        color = _REPLAY_LIVERY[i % len(_REPLAY_LIVERY)]

        cust_id = d.get("cust_id") if isinstance(d.get("cust_id"), int) else None
        career = career_by_cid.get(cust_id) if cust_id else None

        # Lap-crossing samples keyed by car_idx when available, cust_id
        # otherwise. Imported races have no samples — the client treats
        # an empty list as "fall back to pace-accurate synthesis".
        drv_car_idx = d.get("car_idx")
        drv_samples: list[dict] = []
        if isinstance(drv_car_idx, int) and drv_car_idx in samples_by_caridx:
            drv_samples = samples_by_caridx[drv_car_idx]
        elif cust_id and cust_id in samples_by_custid:
            drv_samples = samples_by_custid[cust_id]
        drv_samples = _normalise_replay_lap_samples(
            drv_samples,
            plausible_lap_s,
            sample_time_offset_s,
        )

        drv_pit_stops: list[dict] = []
        if isinstance(drv_car_idx, int) and drv_car_idx in pit_stops_by_caridx:
            drv_pit_stops = _normalise_replay_pit_stops(
                pit_stops_by_caridx[drv_car_idx],
                sample_time_offset_s,
            )

        laps_done = d.get("laps_complete")
        # laps_complete NULL means old data — assume they raced the full distance
        if laps_done is None:
            laps_done = total_laps
        reason = d.get("reason_out") or ("Running" if laps_done > 0 else "DNS")

        out_drivers.append({
            "name": d.get("driver_name") or f"Driver {i+1}",
            "car_number": str(d.get("car_number") or ""),
            "car_idx": d.get("car_idx") if isinstance(d.get("car_idx"), int) else None,
            "race_start_pos": max(0, grid - 1),
            "race_finish_pos": max(0, finish - 1),
            "race_avg_lap": avg_lap_units,
            "race_laps": laps_done,
            "reason_out": reason,
            "is_us": bool(d.get("is_owner")),
            "livery": {"color1": color},
            # Extra fields for race results table
            "grid_position": grid,
            "finish_position": finish,
            "best_lap": best_lap if best_lap > 0 else None,
            "avg_lap": avg_lap_s if avg_lap_s > 0 else None,
            "laps_led": d.get("laps_led") or 0,
            "incidents": d.get("incidents") or 0,
            "dotd_score": d.get("dotd_score") or 0.0,
            "dotd_percent": d.get("dotd_percent") or 0.0,
            "irating": d.get("irating"),
            "license_level": d.get("license_level"),
            "license_sub_level": d.get("license_sub_level"),
            # Phase 5: iRacing /data API enrichment. Old races without
            # cust_ids carry nulls — frontend hides the career card
            # gracefully when these are absent.
            "cust_id": cust_id,
            "iracing_image_url": (career or {}).get("image_url"),
            "career": career,
            # Phase 2 timing: list of {lap, t, lap_time, position, interval_ms}
            # ordered by lap. Empty for races without live capture; the
            # client then uses race_avg_lap / race_laps for synthesis.
            "lap_samples": drv_samples,
            # Per-driver pit stops {entered_time, exited_time, ...}.
            # Replay.js renders the dot off the racing line + dimmed for
            # the duration of each stop. Empty for races with no pit
            # activity (sprints) or imported races without samples.
            "pit_stops": drv_pit_stops,
        })

    # Look up the track centerline from the library (if mapped)
    track_entry = track_library.find_for(race.get("track_name") or "")

    # Fastest lap across all drivers
    fastest = None
    for d in drivers:
        bl = d.get("best_lap") or 0
        if bl and bl > 0 and (fastest is None or bl < fastest["time_s"]):
            fastest = {
                "driver_name": d.get("driver_name") or "—",
                "car_number": str(d.get("car_number") or ""),
                "time_s": float(bl),
            }

    # Podium — top 3 by finish position. Skip drivers with no finish recorded.
    podium = []
    sorted_drivers = sorted(
        [d for d in drivers if d.get("finish_position")],
        key=lambda d: d.get("finish_position") or 9999,
    )
    for i, d in enumerate(sorted_drivers[:3]):
        idx = drivers.index(d)
        color = _REPLAY_LIVERY[idx % len(_REPLAY_LIVERY)]
        cid = d.get("cust_id") if isinstance(d.get("cust_id"), int) else None
        podium.append({
            "pos": i + 1,
            "name": d.get("driver_name") or "—",
            "car_number": str(d.get("car_number") or ""),
            "color": color,
            "best_lap": float(d.get("best_lap") or 0.0),
            "is_owner": bool(d.get("is_owner")),
            "iracing_image_url": (career_by_cid.get(cid) or {}).get("image_url") if cid else None,
        })

    # Driver of the Day — prefer races.dotd_name/dotd_score; fall back to
    # the highest dotd_percent in race_drivers.
    # races.dotd_score is the WINNER'S vote-share percent (e.g. 59.9),
    # while race_drivers.dotd_score is raw points and race_drivers.dotd_percent
    # is the per-driver vote share. The replay finish card uses the percent.
    dotd = None
    dotd_name = race.get("dotd_name")
    dotd_winner_pct = race.get("dotd_score")  # already a percent
    if dotd_name:
        match = next((d for d in drivers if (d.get("driver_name") or "") == dotd_name), None)
        if match:
            idx = drivers.index(match)
            cid = match.get("cust_id") if isinstance(match.get("cust_id"), int) else None
            dotd = {
                "name": dotd_name,
                "car_number": str(match.get("car_number") or ""),
                "color": _REPLAY_LIVERY[idx % len(_REPLAY_LIVERY)],
                "percent": float(dotd_winner_pct or match.get("dotd_percent") or 0.0),
                "is_owner": bool(match.get("is_owner")),
                "iracing_image_url": (career_by_cid.get(cid) or {}).get("image_url") if cid else None,
            }
        else:
            dotd = {
                "name": dotd_name,
                "car_number": "",
                "color": _REPLAY_LIVERY[0],
                "percent": float(dotd_winner_pct or 0.0),
                "is_owner": False,
                "iracing_image_url": None,
            }
    else:
        scored = [d for d in drivers if (d.get("dotd_percent") or 0) > 0]
        if scored:
            best = max(scored, key=lambda d: d.get("dotd_percent") or 0)
            idx = drivers.index(best)
            cid = best.get("cust_id") if isinstance(best.get("cust_id"), int) else None
            dotd = {
                "name": best.get("driver_name") or "—",
                "car_number": str(best.get("car_number") or ""),
                "color": _REPLAY_LIVERY[idx % len(_REPLAY_LIVERY)],
                "percent": float(best.get("dotd_percent") or 0.0),
                "is_owner": bool(best.get("is_owner")),
                "iracing_image_url": (career_by_cid.get(cid) or {}).get("image_url") if cid else None,
            }

    return jsonify({
        "race_id": race_id,
        "track_name": race.get("track_name") or "Unknown",
        "track_config": race.get("track_config") or "",
        "total_laps": total_laps,
        "duration_s": duration_s,
        "drivers": out_drivers,
        "track": track_entry,  # null if no library match — replay.js falls back to default
        "fastest_lap": fastest,
        "winner_name": race.get("winner_name") or "",
        "podium": podium,
        "dotd": dotd,
    })


# ─── Replay highlight timeline ───────────────────────────────────

def _synthesize_highlight_events(race, drivers, duration_s, owner_car_idx):
    """Build a handful of plausible highlight events from race_drivers stats
    when no commentary_log/incidents rows exist (e.g. GrumpyRobot imports).

    Times are spread evenly across duration_s — they're not real timestamps,
    just storyboard markers so the replay HUD has narrative beats to slow
    down on. Returned dicts match the schema used by the rest of the route.
    """
    out = []
    n = max(len(drivers), 1)

    def _at(frac, headline, body, *, is_owner=False, kind="synth",
            commentator="", car_idx=None):
        return {
            "t": max(2.0, min(duration_s - 5.0, duration_s * frac)),
            "kind": kind,
            "is_owner": bool(is_owner),
            "headline": headline.upper(),
            "body": body,
            "commentator": commentator,
            "duration": 6.0,
            "car_idx": car_idx,
        }

    def _ci(d):
        ci = d.get("car_idx") if d else None
        return ci if isinstance(ci, int) else None

    def _find_by_name(name):
        if not name:
            return None
        key = name.strip().lower()
        return next(
            (d for d in drivers if (d.get("driver_name") or "").strip().lower() == key),
            None,
        )

    # Pole-sitter call
    pole = next((d for d in drivers if (d.get("grid_position") or 0) == 1), None)
    if pole:
        out.append(_at(0.04, "Pole Position",
                       f"{pole['driver_name']} leads them away from P1.",
                       car_idx=_ci(pole)))

    # Owner storyline: one mid-race beat + finish line. Earlier the synth
    # injected up to 3 owner_climb beats which overpopulated the
    # narrative — keep the arc but tell it in fewer cuts.
    owner = next((d for d in drivers if d.get("is_owner")), None)
    owner_gain = 0
    if owner and owner.get("grid_position") and owner.get("finish_position"):
        gp = owner["grid_position"]; fp = owner["finish_position"]
        owner_gain = gp - fp
        name = owner.get("driver_name") or "Owner"
        owner_ci = _ci(owner)
        if owner_gain >= 2:
            mid_pos = gp - int(round(owner_gain / 2))
            out.append(_at(0.45, f"P{mid_pos} — {name}",
                           f"{name} climbs to P{mid_pos}, on the move from P{gp}.",
                           is_owner=True, kind="owner_climb",
                           car_idx=owner_ci))
        elif owner_gain <= -2:
            out.append(_at(0.45, "Tough afternoon",
                           f"{name} slips from P{gp} to P{fp}.",
                           is_owner=True, kind="owner_drop",
                           car_idx=owner_ci))
        out.append(_at(0.95, f"Owner Finish — P{fp}",
                       f"{name} takes the flag in P{fp}.",
                       is_owner=True, kind="owner_finish",
                       car_idx=owner_ci))

    # Biggest mover — only call out when the owner ISN'T already the
    # biggest gainer of the day (otherwise we double up on the same beat).
    movers = [
        d for d in drivers
        if d.get("grid_position") and d.get("finish_position")
        and (not owner or d.get("car_idx") != owner.get("car_idx"))
    ]
    if movers:
        big = max(movers, key=lambda d: (d["grid_position"] - d["finish_position"]))
        gain = big["grid_position"] - big["finish_position"]
        # Skip if owner already gained more — owner_climb covered the arc.
        if gain >= 3 and gain > owner_gain:
            out.append(_at(0.55, f"Charge — {big['driver_name']}",
                           f"{big['driver_name']} carves up the order, P{big['grid_position']} to P{big['finish_position']}.",
                           car_idx=_ci(big)))

    # Fastest lap
    laps_drivers = [d for d in drivers if d.get("best_lap")]
    if laps_drivers:
        fl = min(laps_drivers, key=lambda d: d["best_lap"])
        secs = fl["best_lap"]
        mins = int(secs // 60)
        out.append(_at(0.70, "Fastest Lap",
                       f"{fl['driver_name']} sets the fastest lap — {mins}:{secs - mins*60:06.3f}.",
                       car_idx=_ci(fl)))

    # Driver of the Day — announce on the final lap
    dotd_name = (race.get("dotd_name") or "").strip()
    if dotd_name:
        out.append(_at(0.96, "Driver of the Day",
                       f"{dotd_name} earns Driver of the Day honours.",
                       car_idx=_ci(_find_by_name(dotd_name))))

    # Winner — at the chequered flag
    winner = race.get("winner_name")
    if winner:
        out.append(_at(0.99, "Race Winner",
                       f"{winner} crosses the line first.",
                       car_idx=_ci(_find_by_name(winner))))

    return out


@pitwall.route("/api/replay/<int:race_id>/report", methods=["GET"])
def replay_report(race_id):
    """AI-generated race report (F1 journalist style). Lazy-generated on
    first request, then cached in races.race_report."""
    import race_report as _rr
    regenerate = request.args.get("regenerate") == "1"
    try:
        if regenerate:
            text = _rr.regenerate_race_report(race_id)
        else:
            text = _rr.generate_race_report(race_id)
        return jsonify({"report": text, "generated": regenerate})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@pitwall.route("/api/replay/<int:race_id>/timeline", methods=["GET"])
def replay_timeline(race_id):
    """Highlight timeline for the Race Replay's variable-speed playback.

    Sources, in priority order:
      1. commentary_log rows for this race (preferring is_owner=1).
      2. incidents rows touching the owner car.
      3. Synthesized 'start' (t=0) and 'finish' (t=duration_s) markers.

    Events within MERGE_S seconds of each other are coalesced into one.
    Each event carries a `duration` (slow-window length) derived from its
    text length so chatty events get more screen time.
    """
    MERGE_S = 8.0
    MIN_WIN = 4.0
    MAX_WIN = 12.0

    def window_for(text):
        n = len((text or "").strip())
        if n <= 0:
            return MIN_WIN
        return max(MIN_WIN, min(MAX_WIN, n / 14.0))

    detail = race_db.get_race_detail(race_id)
    if not detail:
        return jsonify({"error": "Race not found"}), 404
    race = detail["race"]
    drivers = detail["drivers"]
    duration_s = float(race.get("duration_s") or 0.0)

    # Identify the owner car_idx + name from race_drivers (NULL-safe).
    # We keep the name lowercased so we can rescue rows where car_idx
    # was never populated by matching the driver's name inside ai_text.
    owner_car_idx = None
    owner_name_lc = ""
    for d in drivers:
        if d.get("is_owner"):
            owner_car_idx = d.get("car_idx")
            owner_name_lc = (d.get("driver_name") or "").strip().lower()
            break

    events = []

    # 1. commentary_log
    try:
        cl = race_db.execute_readonly(
            "SELECT timestamp_s, trigger_type, commentator, ai_text, car_idx, is_owner "
            f"FROM commentary_log WHERE race_id = {int(race_id)} ORDER BY timestamp_s ASC"
        )
        for row in cl.get("rows", []):
            t = float(row.get("timestamp_s") or 0.0)
            body = (row.get("ai_text") or "").strip()
            # Owner detection: explicit is_owner flag, matching car_idx,
            # OR the owner's name appearing in the generated commentary.
            # The name-match rescues historical races where car_idx was
            # never populated on the commentary_log row.
            is_own = bool(row.get("is_owner")) or (
                owner_car_idx is not None and row.get("car_idx") == owner_car_idx
            )
            if not is_own and owner_name_lc and owner_name_lc in body.lower():
                is_own = True
            trig = (row.get("trigger_type") or "moment").replace("_", " ").upper()
            events.append({
                "t": t,
                "kind": "owner_commentary" if is_own else "commentary",
                "is_owner": is_own,
                "car_idx": row.get("car_idx"),
                "headline": trig,
                "body": body,
                "commentator": row.get("commentator") or "",
                "duration": window_for(body),
            })
    except Exception:
        pass  # table may not exist on fresh DBs

    # 2. incidents touching the owner
    try:
        inc = race_db.execute_readonly(
            "SELECT timestamp_s, incident_type, car_idx, other_car_idx, "
            "car_name, other_car_name, description "
            f"FROM incidents WHERE race_id = {int(race_id)} ORDER BY timestamp_s ASC"
        )
        for row in inc.get("rows", []):
            ci = row.get("car_idx")
            oi = row.get("other_car_idx")
            is_own = owner_car_idx is not None and (ci == owner_car_idx or oi == owner_car_idx)
            # Skip non-owner incidents to keep the timeline focused on the user.
            if owner_car_idx is not None and not is_own:
                continue
            t = float(row.get("timestamp_s") or 0.0)
            body = (row.get("description") or "").strip()
            head = (row.get("incident_type") or "incident").replace("_", " ").upper()
            events.append({
                "t": t,
                "kind": "owner_incident" if is_own else "incident",
                "is_owner": is_own,
                "car_idx": ci,
                "other_car_idx": oi,
                "headline": head,
                "body": body,
                "commentator": "",
                "duration": window_for(body),
            })
    except Exception:
        pass

    # ── Normalise real-log timestamps to race-relative seconds ──
    # Must run BEFORE synthesis, otherwise the synth events (already
    # relative) pull the minimum below the epoch threshold and the
    # check gets skipped. Legacy rows store `timestamp_s` as raw
    # time.time() epoch values; the replay page expects 0..duration_s
    # so its cursor can ramp speed correctly.
    try:
        nonzero_ts = [e["t"] for e in events if e["t"] > 0]
        if nonzero_ts:
            min_t = min(nonzero_ts)
            if min_t > 1_000_000_000:
                for e in events:
                    if e["t"] > 0:
                        e["t"] = max(0.0, e["t"] - min_t)
                    if duration_s > 0:
                        e["t"] = min(e["t"], duration_s - 0.5)
    except Exception as _e:
        print(f"[timeline] timestamp normalise failed: {_e}")

    # 3. Synthesized highlights from race_drivers.
    #    If the commentary_log is empty (e.g. GrumpyRobot imports) we
    #    use the full synthesis. If we already have commentary events
    #    but coverage of the owner is thin (< 3 owner events), we ALSO
    #    inject just the owner-arc beats so the replay's Focus Driver
    #    storytelling never feels empty.
    if duration_s > 0 and drivers:
        owner_event_count = sum(1 for e in events if e.get("is_owner"))
        if not events:
            events.extend(_synthesize_highlight_events(race, drivers, duration_s, owner_car_idx))
        elif owner_event_count < 3 and owner_car_idx is not None:
            synth = _synthesize_highlight_events(race, drivers, duration_s, owner_car_idx)
            # Keep only the owner-only beats — we don't want to duplicate
            # generic race events that the real commentary_log already has.
            events.extend([e for e in synth if e.get("is_owner")])

    # Sort + merge near-neighbours.
    events.sort(key=lambda e: e["t"])
    merged = []
    for e in events:
        if merged and (e["t"] - merged[-1]["t"]) < MERGE_S:
            prev = merged[-1]
            # Owner-tagged events outrank generic ones for the merged headline.
            if e["is_owner"] and not prev["is_owner"]:
                prev["headline"] = e["headline"]
                prev["body"] = e["body"] or prev["body"]
                prev["kind"] = e["kind"]
                prev["is_owner"] = True
                prev["car_idx"] = e["car_idx"]
            else:
                # Append a continuation marker to the body so context isn't lost.
                if e["body"] and e["body"] not in prev["body"]:
                    prev["body"] = (prev["body"] + " · " + e["body"]).strip(" ·")
            prev["duration"] = max(prev["duration"], e["duration"])
        else:
            merged.append(dict(e))

    # ── Trim to a sparse, ~8-10-callout broadcast pace ──────────────
    # The replay used to show every commentary_log row that survived the
    # 8s merge — for a 30-min race that's 25-50 popups. Cap to TARGET
    # events using an importance score, then enforce a hard MIN_SPACING
    # so even the kept events breathe. start/finish bookends are added
    # afterwards and never count against the cap.
    TARGET_TOTAL = 10
    MIN_SPACING_S = 25.0

    HIGH_PRIORITY_TRIGGERS = {
        "FASTEST LAP", "LEAD CHANGE", "OVERTAKE", "WIN RACE",
        "RACE WINNER", "DRIVER OF THE DAY", "DOTD",
        "RACE FINISH", "OWNER FINISH",
        "CHEQUERED FLAG", "CHECKERED FLAG",
        "GREEN", "GREEN FLAG", "POLE POSITION", "PRE RACE",
        "RACE SUMMARY",
    }

    def _importance(evt):
        s = 0
        head = (evt.get("headline") or "").upper()
        kind = (evt.get("kind") or "").lower()
        # Owner involvement is the single biggest factor — replay storyline
        # follows the focus driver.
        if evt.get("is_owner"): s += 100
        if "incident" in kind:  s += 60
        if "owner_finish" in kind or "WINNER" in head: s += 80
        if "dotd" in kind or "DRIVER OF THE DAY" in head: s += 70
        if "fastest_lap" in kind or "FASTEST LAP" in head: s += 50
        if any(t in head for t in HIGH_PRIORITY_TRIGGERS): s += 40
        # Commentary that was actually voiced beats synthesized markers.
        if kind in ("commentary", "owner_commentary"): s += 20
        # Longer body = more substantive; cap to avoid runaway scoring.
        s += min(20, len(evt.get("body") or "") // 40)
        return s

    if len(merged) > TARGET_TOTAL:
        # Score, take top N, re-sort by time.
        scored = sorted(
            ((_importance(e), e) for e in merged),
            key=lambda p: (-p[0], p[1]["t"]),
        )
        kept = sorted([e for _, e in scored[:TARGET_TOTAL]], key=lambda e: e["t"])
        merged = kept

    # Enforce minimum spacing — drop the lower-importance event of any
    # too-close pair. One more pass after the cap so trimming itself
    # doesn't cluster events.
    if len(merged) > 1:
        pruned = [merged[0]]
        for evt in merged[1:]:
            prev = pruned[-1]
            if (evt["t"] - prev["t"]) < MIN_SPACING_S:
                if _importance(evt) > _importance(prev):
                    pruned[-1] = evt
                # else: skip the new one
            else:
                pruned.append(evt)
        merged = pruned

    # 3. Synthesized start / finish bookends.
    start_evt = {
        "t": 0.0,
        "kind": "start",
        "is_owner": False,
        "headline": "LIGHTS OUT",
        "body": "Race start.",
        "commentator": "",
        "duration": 4.0,
    }
    if duration_s > 0:
        winner_name = (race.get("winner_name") or "").strip()
        winner_ci = None
        if winner_name:
            wm = next(
                (d for d in drivers if (d.get("driver_name") or "").strip().lower()
                 == winner_name.lower()),
                None,
            )
            if wm and isinstance(wm.get("car_idx"), int):
                winner_ci = wm["car_idx"]
        finish_evt = {
            "t": max(0.0, duration_s - 0.1),
            "kind": "finish",
            "is_owner": False,
            "headline": "CHEQUERED FLAG",
            "body": (winner_name + " takes the win.") if winner_name else "Race complete.",
            "commentator": "",
            "duration": 6.0,
            "car_idx": winner_ci,
        }
        merged = [start_evt] + merged + [finish_evt]
    else:
        merged = [start_evt] + merged

    return jsonify({
        "race_id": race_id,
        "duration_s": duration_s,
        "owner_car_idx": owner_car_idx,
        "events": merged,
    })


# ─── Track Library API ───────────────────────────────────────────

@pitwall.route("/api/tracks", methods=["GET"])
def tracks_list():
    return jsonify(track_library.load_all())


@pitwall.route("/api/tracks/unmapped", methods=["GET"])
def tracks_unmapped():
    """Return distinct race track_names that have no library entry."""
    result = race_db.execute_readonly(
        "SELECT track_name, COUNT(*) AS race_count, MAX(race_date) AS last_seen "
        "FROM races WHERE track_name IS NOT NULL AND track_name != '' "
        "GROUP BY track_name ORDER BY last_seen DESC"
    )
    out = []
    for r in result.get("rows", []):
        if not track_library.find_for(r["track_name"]):
            out.append(r)
    return jsonify(out)


@pitwall.route("/api/tracks/<slug>", methods=["GET"])
def tracks_get(slug):
    t = track_library.get(slug)
    if not t:
        return jsonify({"error": "Not found"}), 404
    return jsonify(t)


@pitwall.route("/api/tracks", methods=["POST"])
def tracks_create():
    data = request.get_json(force=True) or {}
    try:
        saved = track_library.save(data)
        return jsonify(saved)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/tracks/<slug>", methods=["PUT"])
def tracks_update(slug):
    data = request.get_json(force=True) or {}
    data["slug"] = slug
    try:
        saved = track_library.save(data)
        return jsonify(saved)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/tracks/<slug>", methods=["DELETE"])
def tracks_delete(slug):
    ok = track_library.delete(slug)
    return jsonify({"ok": ok})


@pitwall.route("/api/tracks/<slug>/fetch_iracing", methods=["POST"])
def tracks_fetch_iracing(slug):
    """Pull marketing copy + images from iRacing /data API into a track."""
    if not iracing_api_adapter.is_available():
        return jsonify({"error": "iRacing API not available — enable in Power Unit"}), 400
    enriched = track_library.enrich_with_iracing(slug)
    if not enriched:
        return jsonify({"error": "No iRacing match found for this track. Check that ir_names contains a track_name iRacing reports."}), 404
    return jsonify(enriched)


# ─── Car Library API (Garage tab) ────────────────────────────────

@pitwall.route("/api/cars", methods=["GET"])
def cars_list():
    return jsonify(car_library.load_all())


@pitwall.route("/api/cars/<slug>", methods=["GET"])
def cars_get(slug):
    c = car_library.get(slug)
    if not c:
        return jsonify({"error": "Not found"}), 404
    return jsonify(c)


@pitwall.route("/api/cars", methods=["POST"])
def cars_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify(car_library.save(data))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/cars/<slug>", methods=["PUT"])
def cars_update(slug):
    data = request.get_json(force=True) or {}
    data["slug"] = slug
    try:
        return jsonify(car_library.save(data))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/cars/<slug>", methods=["DELETE"])
def cars_delete(slug):
    return jsonify({"ok": car_library.delete(slug)})


@pitwall.route("/api/cars/sync_iracing", methods=["POST"])
def cars_sync_iracing():
    """Pull the full iRacing car catalog + assets into the library."""
    if not iracing_api_adapter.is_available():
        return jsonify({"error": "iRacing API not available — enable in Power Unit"}), 400
    summary = car_library.sync_from_iracing()
    if "error" in summary:
        return jsonify(summary), 400
    return jsonify(summary)


# ─── Database Explorer API ───────────────────────────────────────

@pitwall.route("/api/db/tables", methods=["GET"])
def db_tables():
    """List all tables with row counts."""
    return jsonify(race_db.get_table_names())


@pitwall.route("/api/db/tables/<table>/schema", methods=["GET"])
def db_table_schema(table):
    """Return column info for a table."""
    try:
        return jsonify(race_db.get_table_schema(table))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/db/tables/<table>/rows", methods=["GET"])
def db_table_rows(table):
    """Return paginated rows."""
    try:
        return jsonify(race_db.get_rows(
            table,
            limit=request.args.get("limit", 50, type=int),
            offset=request.args.get("offset", 0, type=int),
            sort=request.args.get("sort"),
            order=request.args.get("order", "DESC"),
        ))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/db/tables/<table>/rows/<pk_val>", methods=["PUT"])
def db_update_row(table, pk_val):
    """Update a row by primary key."""
    try:
        data = request.get_json(force=True)
        pk_col = data.pop("_pk_col", None)
        if not pk_col:
            schema = race_db.get_table_schema(table)
            pk_col = next((c["name"] for c in schema if c["pk"]), "rowid")
        race_db.update_row(table, pk_col, pk_val, data)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/db/tables/<table>/rows", methods=["POST"])
def db_insert_row(table):
    """Insert a new row."""
    try:
        data = request.get_json(force=True)
        rowid = race_db.insert_row(table, data)
        return jsonify({"ok": True, "rowid": rowid})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/db/tables/<table>/rows/<pk_val>", methods=["DELETE"])
def db_delete_row(table, pk_val):
    """Delete a row by primary key."""
    try:
        ok = race_db.delete_row(
            table,
            request.args.get("pk_col", ""),
            pk_val,
        )
        return jsonify({"ok": ok})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@pitwall.route("/api/db/query", methods=["POST"])
def db_query():
    """Execute a read-only SELECT query."""
    data = request.get_json(force=True)
    sql = data.get("sql", "")
    try:
        result = race_db.execute_readonly(sql)
        return jsonify(result)
    except (ValueError, Exception) as e:
        return jsonify({"error": str(e)}), 400


# ─── Broadcast Control API ───────────────────────────────────────

@pitwall.route("/api/broadcast/status", methods=["GET"])
def broadcast_status():
    """Return current broadcast state."""
    state_file = os.path.join(_EXPORTS_DIR, "broadcast_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            # Check if process is actually running
            global _engine_process
            if _engine_process and _engine_process.poll() is not None:
                _engine_process = None
                state["status"] = "idle"
            # Detect frozen engine — alive but not updating
            if state.get("status") in ("pre_race", "racing"):
                state_age = _time.time() - os.path.getmtime(state_file)
                if state_age > 30:
                    state["warning"] = "Engine may be frozen — broadcast_state not updated for 30s"
            return jsonify(state)
        except Exception:
            pass
    return jsonify({
        "status": "idle", "engine_pid": None, "race_id": None,
        "lap": 0, "total_laps": 0, "elapsed_s": 0,
        "car_count": 0, "commentary_count": 0,
        "last_trigger": None, "started_at": None,
        "flags": 0,
    })


@pitwall.route("/api/broadcast/start", methods=["POST"])
def broadcast_start():
    """Launch the race engine as a subprocess."""
    global _engine_process
    if _engine_process and _engine_process.poll() is None:
        return jsonify({"error": "Engine already running", "pid": _engine_process.pid}), 409

    try:
        # Route engine stdout/stderr to DEVNULL — with subprocess.PIPE and
        # no reader, the 64KB pipe buffer fills after ~1 minute of logs
        # and the engine blocks on print(). DEVNULL costs nothing.
        _engine_process = subprocess.Popen(
            [sys.executable, _ENGINE_SCRIPT],
            cwd=_parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        return jsonify({"ok": True, "pid": _engine_process.pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@pitwall.route("/api/broadcast/stop", methods=["POST"])
def broadcast_stop():
    """Stop the race engine."""
    global _engine_process
    killed = False

    # Try subprocess handle first
    if _engine_process and _engine_process.poll() is None:
        try:
            _engine_process.terminate()
            _engine_process.wait(timeout=5)
            killed = True
        except subprocess.TimeoutExpired:
            _engine_process.kill()
            killed = True
        finally:
            _engine_process = None

    # Fallback: kill by PID from broadcast_state.json (handles externally started engines)
    if not killed:
        _engine_process = None
        state_file = os.path.join(_EXPORTS_DIR, "broadcast_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
                pid = state.get("engine_pid")
                if pid and state.get("status") not in (None, "idle"):
                    os.kill(pid, signal.SIGTERM)
                    killed = True
            except (ProcessLookupError, PermissionError, OSError):
                pass  # Process already gone
            except Exception:
                pass

    # Write idle state
    state_file = os.path.join(_EXPORTS_DIR, "broadcast_state.json")
    try:
        with open(state_file, "w") as f:
            json.dump({"status": "idle", "engine_pid": None}, f)
    except Exception:
        pass

    return jsonify({"ok": True, "killed": killed})


@pitwall.route("/api/broadcast/triggers", methods=["GET"])
def broadcast_triggers():
    """Return the live trigger feed."""
    trigger_file = os.path.join(_EXPORTS_DIR, "trigger_stream.json")
    if os.path.exists(trigger_file):
        try:
            with open(trigger_file, "r") as f:
                triggers = json.load(f)
            limit = request.args.get("limit", 50, type=int)
            return jsonify(triggers[-limit:] if isinstance(triggers, list) else [])
        except Exception:
            pass
    return jsonify([])


# ─── Live Telemetry & Health ─────────────────────────────────────

@pitwall.route("/api/telemetry/live", methods=["GET"])
def telemetry_live():
    """Return a snapshot of all key export files for live debugging."""
    result = {}
    for fname in ["broadcast_state.json", "session_info.json",
                   "leaderboard.json", "weekend_info.json",
                   "dotd_top3.json", "trigger_stream.json"]:
        fpath = os.path.join(_EXPORTS_DIR, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    result[fname] = json.load(f)
                result[fname + "_age_s"] = round(
                    _time.time() - os.path.getmtime(fpath), 1
                )
            except Exception:
                result[fname] = {"error": "parse failed"}
        else:
            result[fname] = None
    return jsonify(result)


@pitwall.route("/api/live/pits", methods=["GET"])
def live_pits():
    """Return live pit stop data from the current race."""
    try:
        race_id = race_db.current_race_id()
        if not race_id:
            return jsonify([])
        conn = race_db._conn()
        cur = conn.execute(
            "SELECT * FROM race_pit_stops WHERE race_id = ? ORDER BY lap DESC",
            [race_id],
        )
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@pitwall.route("/api/health", methods=["GET"])
def health():
    """Engine health check — detects frozen or crashed engine."""
    state_file = os.path.join(_EXPORTS_DIR, "broadcast_state.json")
    result = {"engine_alive": False, "broadcast_state_age_s": None,
              "engine_frozen": False, "status": "unknown"}
    if os.path.exists(state_file):
        try:
            age = round(_time.time() - os.path.getmtime(state_file), 1)
            result["broadcast_state_age_s"] = age
            with open(state_file, "r") as f:
                state = json.load(f)
            result["status"] = state.get("status", "unknown")
            result["engine_pid"] = state.get("engine_pid")
            result["flags"] = state.get("flags", 0)
            # Frozen = engine claims to be running but file hasn't updated
            if result["status"] in ("pre_race", "racing") and age > 30:
                result["engine_frozen"] = True
            # Check if process is alive
            global _engine_process
            if _engine_process and _engine_process.poll() is None:
                result["engine_alive"] = True
        except Exception:
            pass
    return jsonify(result)


# ─── Prompt Templates API ────────────────────────────────────────

@pitwall.route("/api/prompts", methods=["GET"])
def list_prompts():
    """Return all prompt templates grouped by trigger type."""
    templates = race_db.get_prompt_templates()
    grouped = {}
    for t in templates:
        tt = t["trigger_type"]
        if tt not in grouped:
            grouped[tt] = []
        grouped[tt].append(t)
    return jsonify(grouped)


@pitwall.route("/api/prompts/trigger-types", methods=["GET"])
def prompt_trigger_types():
    """Return all known trigger types from config."""
    ts = (race_config.cfg("commentary_team") or {}).get("trigger_settings", {})
    return jsonify(sorted(ts.keys()))


@pitwall.route("/api/prompts/<trigger_type>", methods=["GET"])
def get_prompts_for_trigger(trigger_type):
    """Return templates for a specific trigger type."""
    return jsonify(race_db.get_prompt_templates(trigger_type))


@pitwall.route("/api/prompts", methods=["POST"])
def create_prompt():
    """Create a new prompt template."""
    data = request.get_json(force=True)
    tid = race_db.save_prompt_template(
        trigger_type=data.get("trigger_type", ""),
        template_name=data.get("template_name", ""),
        prompt_text=data.get("prompt_text", ""),
        char_limit=data.get("char_limit", 150),
        is_active=data.get("is_active", 1),
    )
    return jsonify({"ok": True, "id": tid})


@pitwall.route("/api/prompts/<int:template_id>", methods=["PUT"])
def update_prompt(template_id):
    """Update a prompt template."""
    data = request.get_json(force=True)
    fields = {}
    for key in ("trigger_type", "template_name", "prompt_text", "char_limit", "is_active"):
        if key in data:
            fields[key] = data[key]
    race_db.save_prompt_template(template_id, **fields)
    return jsonify({"ok": True})


@pitwall.route("/api/prompts/<int:template_id>", methods=["DELETE"])
def delete_prompt(template_id):
    """Delete a prompt template."""
    ok = race_db.delete_prompt_template(template_id)
    return jsonify({"ok": ok})


# ─── Data / Telemetry Exports API ────────────────────────────────

@pitwall.route("/api/data/exports", methods=["GET"])
def list_exports():
    """List all JSON export files with metadata."""
    files = []
    pattern = os.path.join(_EXPORTS_DIR, "*.json")
    for fpath in sorted(glob.glob(pattern)):
        stat = os.stat(fpath)
        files.append({
            "name": os.path.basename(fpath),
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
    return jsonify(files)


@pitwall.route("/api/data/exports/<filename>", methods=["GET"])
def get_export(filename):
    """Return contents of a specific export file."""
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    fpath = os.path.join(_EXPORTS_DIR, filename)
    if not os.path.exists(fpath):
        return jsonify({"error": "File not found"}), 404
    try:
        with open(fpath, "r") as f:
            data = json.load(f)
        return jsonify({"name": filename, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Owner Driver Profile API ───────────────────────────────────

@pitwall.route("/api/owner-drivers", methods=["GET"])
def list_owner_drivers():
    """Return all owner driver profiles."""
    drivers = race_config.cfg().get("owner_drivers", {})
    return jsonify(drivers)


@pitwall.route("/api/owner-drivers/<name>", methods=["PUT"])
def save_owner_driver(name):
    """Create or update an owner driver profile."""
    data = request.get_json(force=True)
    data["display_name"] = data.get("display_name", name)
    drivers = race_config.cfg().get("owner_drivers", {})
    drivers[name] = {**drivers.get(name, {}), **data}
    race_config.update_section("owner_drivers", drivers)
    return jsonify({"ok": True})


@pitwall.route("/api/owner-drivers/<name>", methods=["DELETE"])
def delete_owner_driver(name):
    """Remove an owner driver profile."""
    drivers = dict(race_config.cfg().get("owner_drivers", {}))
    if name not in drivers:
        return jsonify({"error": "Driver not found"}), 404
    del drivers[name]
    race_config.update_section("owner_drivers", drivers)
    return jsonify({"ok": True})


@pitwall.route("/api/owner-drivers/<name>/activate", methods=["POST"])
def activate_owner_driver(name):
    """Set one owner driver as active, deactivate others."""
    drivers = dict(race_config.cfg().get("owner_drivers", {}))
    if name not in drivers:
        return jsonify({"error": "Driver not found"}), 404
    for k in drivers:
        drivers[k]["is_active"] = (k == name)
    race_config.update_section("owner_drivers", drivers)
    return jsonify({"ok": True})


# ─── Commentator Profile API ───────────────────────────────────

@pitwall.route("/api/commentators", methods=["GET"])
def list_commentators():
    """Return all commentator profiles."""
    casters = (race_config.cfg("commentary_team") or {}).get("commentators", {})
    return jsonify(casters)


@pitwall.route("/api/commentators/<name>", methods=["PUT"])
def save_commentator_profile(name):
    """Create or update a commentator profile."""
    data = request.get_json(force=True)
    casters = dict((race_config.cfg("commentary_team") or {}).get("commentators", {}))
    casters[name] = {**casters.get(name, {}), **data}
    race_config.update_section("commentary_team", {"commentators": casters})
    return jsonify({"ok": True})


@pitwall.route("/api/commentators/<name>", methods=["DELETE"])
def delete_commentator(name):
    """Remove a commentator profile."""
    casters = dict((race_config.cfg("commentary_team") or {}).get("commentators", {}))
    if name not in casters:
        return jsonify({"error": "Commentator not found"}), 404
    del casters[name]
    race_config.update_section("commentary_team", {"commentators": casters})
    return jsonify({"ok": True})


@pitwall.route("/api/commentators/<name>/toggle", methods=["POST"])
def toggle_commentator(name):
    """Toggle a commentator's active status."""
    casters = dict((race_config.cfg("commentary_team") or {}).get("commentators", {}))
    if name not in casters:
        return jsonify({"error": "Commentator not found"}), 404
    casters[name]["is_active"] = not casters[name].get("is_active", True)
    race_config.update_section("commentary_team", {"commentators": casters})
    return jsonify({"ok": True, "is_active": casters[name]["is_active"]})


# ─── Commentator Preview API ─────────────────────────────────────

@pitwall.route("/api/commentators/<name>/preview", methods=["POST"])
def preview_commentator(name):
    """Generate a TTS preview for a commentator."""
    try:
        import text_to_speech_integration as tts
    except ImportError:
        return jsonify({"error": "TTS module not available"}), 500

    casters = (race_config.cfg("commentary_team") or {}).get("commentators", {})
    if name not in casters:
        return jsonify({"error": f"Commentator '{name}' not found"}), 404

    c = casters[name]
    data = request.get_json(force=True) if request.is_json else {}
    sample_text = data.get("text", f"And we're back live at the track! This is {name}, ready for action.")

    try:
        clip_path = tts.generate_speech(
            text=sample_text,
            voice_id=c.get("voice_id", ""),
            speaking_rate=c.get("speaking_rate", 1.3),
        )
        if clip_path:
            rel = os.path.relpath(clip_path, _parent)
            return jsonify({"ok": True, "clip": "/" + rel.replace("\\", "/")})
        return jsonify({"error": "TTS returned no audio"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Media Gallery API ───────────────────────────────────────────

# File type classification — images only
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}

# Directories to scan (relative to project root) — image folders only
_MEDIA_SCAN_DIRS = [
    "python/sector_says_oversee/backgrounds",
    "python/sector_says_oversee/commentators",
    "python/sector_says_oversee/drivers",
    "python/sector_says_oversee/studio",
]


def _scan_media_files():
    """Scan image directories for image files. Returns list of file dicts."""
    files = []
    seen = set()

    for rel_dir in _MEDIA_SCAN_DIRS:
        abs_dir = os.path.join(_PROJECT_ROOT, rel_dir)
        if not os.path.isdir(abs_dir):
            continue
        for fname in os.listdir(abs_dir):
            fpath = os.path.join(abs_dir, fname)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _IMAGE_EXT:
                continue
            rel = os.path.relpath(fpath, _PROJECT_ROOT).replace("\\", "/")
            if rel in seen:
                continue
            seen.add(rel)
            stat = os.stat(fpath)
            files.append({
                "name": fname,
                "path": rel,
                "folder": rel_dir,
                "type": "image",
                "ext": ext,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

    return files


@pitwall.route("/api/media/files", methods=["GET"])
def media_list():
    """Return all media files across the project, merged with DB tags."""
    files = _scan_media_files()
    # Merge DB metadata into file entries
    assets = race_db.get_all_media_assets()
    for f in files:
        meta = assets.get(f["path"])
        if meta:
            f["category"] = meta.get("category", "uncategorized")
            f["commentator"] = meta.get("commentator")
            f["location"] = meta.get("location")
            f["description"] = meta.get("description")
            f["tags"] = meta.get("tags")
            f["asset_id"] = meta.get("id")
        else:
            f["category"] = "uncategorized"
            f["commentator"] = None
            f["location"] = None
            f["description"] = None
            f["tags"] = None
            f["asset_id"] = None
    # Extract unique folders for the filter dropdown
    folders = sorted(set(f["folder"] for f in files))
    categories = sorted(set(f["category"] for f in files if f["category"]))
    # Commentator names from config
    casters = list((race_config.cfg("commentary_team") or {}).get("commentators", {}).keys())
    return jsonify({
        "files": files, "folders": folders, "total": len(files),
        "categories": categories, "commentators": casters,
    })


@pitwall.route("/api/media/by-driver/<path:name>", methods=["GET"])
def media_by_driver(name):
    """Return image files tagged with ``name`` in the media_assets.tags
    JSON array. Same shape as /api/media/files['files'] entries so the
    client can reuse the same thumbnail renderer.

    The driver identifier is the free-text display_name written into the
    tag editor (for example, the driver's iRacing display name). Match is
    case-insensitive and token-level (tags=["Lando"] won't pull in a driver
    named "Land")."""
    if not name:
        return jsonify({"files": []})
    assets = race_db.get_media_by_driver(name.strip())
    all_files = {f["path"]: f for f in _scan_media_files()}
    out = []
    for a in assets:
        fpath = a.get("file_path")
        f = all_files.get(fpath)
        if not f:
            # Asset metadata references a file we can't see on disk anymore;
            # skip rather than show a broken thumbnail.
            continue
        merged = dict(f)
        merged["category"] = a.get("category") or "uncategorized"
        merged["commentator"] = a.get("commentator")
        merged["location"] = a.get("location")
        merged["description"] = a.get("description")
        merged["tags"] = a.get("tags")
        merged["asset_id"] = a.get("id")
        out.append(merged)
    out.sort(key=lambda f: f["name"])
    return jsonify({"files": out, "total": len(out), "driver": name})


@pitwall.route("/api/media/file", methods=["GET"])
def media_serve():
    """Serve a media file by its relative path."""
    rel = request.args.get("path", "")
    if not rel or ".." in rel:
        return jsonify({"error": "Invalid path"}), 400
    # Normalize
    rel = rel.replace("\\", "/")
    abs_path = os.path.abspath(os.path.join(_PROJECT_ROOT, rel))
    # Security: ensure within project
    if not abs_path.startswith(os.path.abspath(_PROJECT_ROOT)):
        return jsonify({"error": "Access denied"}), 403
    if not os.path.isfile(abs_path):
        return jsonify({"error": "Not found"}), 404
    directory = os.path.dirname(abs_path)
    filename = os.path.basename(abs_path)
    return send_from_directory(directory, filename)



@pitwall.route("/api/media/upload", methods=["POST"])
def media_upload():
    """Upload a file to a specified folder."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    folder = request.form.get("folder", "python/sector_says_oversee/backgrounds")

    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    # Security checks
    if ".." in folder or ".." in file.filename:
        return jsonify({"error": "Invalid path"}), 400

    dest_dir = os.path.join(_PROJECT_ROOT, folder)
    if not os.path.abspath(dest_dir).startswith(os.path.abspath(_PROJECT_ROOT)):
        return jsonify({"error": "Access denied"}), 403

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, file.filename)
    file.save(dest)

    rel = os.path.relpath(dest, _PROJECT_ROOT).replace("\\", "/")

    # Save media asset metadata if any tag fields provided
    tag_fields = {}
    category = request.form.get("category")
    if category and category != "uncategorized":
        tag_fields["category"] = category
    commentator = request.form.get("commentator")
    if commentator:
        tag_fields["commentator"] = commentator
    location = request.form.get("location")
    if location:
        tag_fields["location"] = location
    description = request.form.get("description")
    if description:
        tag_fields["description"] = description
    custom_tags = request.form.get("tags")
    if custom_tags:
        tag_fields["tags"] = custom_tags

    if tag_fields:
        race_db.save_media_asset(rel, **tag_fields)

    return jsonify({"ok": True, "path": rel, "name": file.filename})


@pitwall.route("/api/media/delete", methods=["DELETE"])
def media_delete():
    """Delete a media file."""
    rel = request.args.get("path", "")
    if not rel or ".." in rel:
        return jsonify({"error": "Invalid path"}), 400

    abs_path = os.path.abspath(os.path.join(_PROJECT_ROOT, rel))
    if not abs_path.startswith(os.path.abspath(_PROJECT_ROOT)):
        return jsonify({"error": "Access denied"}), 403
    if not os.path.isfile(abs_path):
        return jsonify({"error": "Not found"}), 404

    # Don't allow deleting Python source or critical config
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in (".py", ".pyc"):
        return jsonify({"error": "Cannot delete Python source files"}), 403

    try:
        rel = os.path.relpath(abs_path, _PROJECT_ROOT).replace("\\", "/")
        last_error = None
        for delay_s in (0.0, 0.15, 0.35, 0.75):
            if delay_s:
                _time.sleep(delay_s)
            try:
                os.remove(abs_path)
                last_error = None
                break
            except PermissionError as e:
                last_error = e
        if last_error:
            return jsonify({
                "error": (
                    "File is still locked by Windows. Close any preview/download "
                    "using it and try again."
                )
            }), 423
        # Clean up DB metadata
        race_db.delete_media_asset(rel)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@pitwall.route("/api/media/tags", methods=["GET"])
def media_get_tags():
    """Return tags/metadata for a media file by path."""
    rel = request.args.get("path", "")
    if not rel:
        return jsonify({"error": "Missing path"}), 400
    asset = race_db.get_media_asset(rel)
    if not asset:
        return jsonify({
            "file_path": rel, "category": "uncategorized",
            "commentator": None, "location": None,
            "description": None, "tags": None,
        })
    return jsonify(asset)


@pitwall.route("/api/media/tags", methods=["PUT"])
def media_save_tags():
    """Save or update tags/metadata for a media file."""
    data = request.get_json(force=True)
    file_path = data.pop("file_path", None)
    if not file_path:
        return jsonify({"error": "Missing file_path"}), 400

    fields = {}
    for key in ("category", "commentator", "location", "description", "tags"):
        if key in data:
            fields[key] = data[key]

    asset_id = race_db.save_media_asset(file_path, **fields)
    return jsonify({"ok": True, "id": asset_id})


@pitwall.route("/api/media/categories", methods=["GET"])
def media_categories():
    """Return the list of valid media categories."""
    return jsonify(race_db.MEDIA_CATEGORIES)


@pitwall.route("/api/media/logo", methods=["GET"])
def media_logo():
    """Return the path of the current logo (category=logo), if any."""
    logos = race_db.get_media_by_category("logo")
    if logos:
        # Use the most recently updated one
        logo = max(logos, key=lambda r: r.get("updated_at", ""))
        return jsonify({"path": logo["file_path"]})
    return jsonify({"path": None})


@pitwall.route("/api/media/hud-backgrounds", methods=["GET"])
def media_hud_backgrounds():
    """Return list of HUD background image paths for the Race HUD rotation."""
    assets = race_db.get_media_by_category("hud_background")
    paths = [a["file_path"] for a in assets if a.get("file_path")]
    return jsonify({"backgrounds": paths})


@pitwall.route("/api/media/page-backgrounds", methods=["GET"])
def media_page_backgrounds():
    """Return page background assignments {tab_id: image_path}."""
    assets = race_db.get_media_by_category("page_background")
    result = {}
    for a in assets:
        page = a.get("location")  # location field stores the tab id
        if page:
            result[page] = a["file_path"]
    return jsonify(result)


@pitwall.route("/api/media/page-backgrounds", methods=["PUT"])
def set_page_background():
    """Assign an image as the background for a dashboard page."""
    data = request.get_json(force=True)
    page = data.get("page")
    file_path = data.get("file_path")
    if not page:
        return jsonify({"error": "Missing page"}), 400

    if not file_path:
        # Clear: remove page_background entries for this page
        assets = race_db.get_media_by_category("page_background")
        for a in assets:
            if a.get("location") == page:
                race_db.delete_media_asset(a["file_path"])
        return jsonify({"ok": True, "cleared": True})

    # Upsert: remove old assignment for this page, set new one
    assets = race_db.get_media_by_category("page_background")
    for a in assets:
        if a.get("location") == page and a["file_path"] != file_path:
            # Downgrade old bg to uncategorized
            race_db.save_media_asset(a["file_path"], category="uncategorized", location=None)

    race_db.save_media_asset(file_path, category="page_background", location=page)
    return jsonify({"ok": True})


@pitwall.route("/api/media/folders", methods=["GET"])
def media_folders():
    """Return list of media-relevant folders for upload target."""
    folders = []
    for rel_dir in _MEDIA_SCAN_DIRS:
        abs_dir = os.path.join(_PROJECT_ROOT, rel_dir)
        exists = os.path.isdir(abs_dir)
        folders.append({"path": rel_dir, "exists": exists})
    return jsonify(folders)
