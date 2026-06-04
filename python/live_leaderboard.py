import re
import time
from typing import List, Dict, Tuple
from colorama import init, Fore, Style
import os
import sector_says_utilities
from sector_says_utilities import export_timingtower_json
import race_data_store
import commentary_team
import collision_tracker
import offtrack_tracker
import sector_says_dotd as dotd
from sector_says_dotd import DEBUG
from sector_says_dotd import dotd
import json

# JSON export helpers
from sector_says_utilities import (
    export_leaderboard_json,
    export_session_meta,
    get_race_times
)

init(autoreset=True)

# Globals
owner_car_details = {}
_last_recorded_lap = 0
_last_leading_car_idx = None
laps_led: Dict[int, int] = {}
STARTING_GRID: Dict[int, int] = {}
first_positions_recorded = False

# Current iRacing session identifier — pulled once per tick from
# WeekendInfo.SubSessionID. Consumers that need to hit the /data API
# for session-scoped lookups (strength of field, event log) read this.
current_subsession_id: int | None = None

# Per-car pit stop state — tracks edges on CarIdxOnPitRoad so we can
# count stops, record entry lap/time, and log completed stops to the DB.
# Shape: { car_idx: {"stops": int, "on_pit": bool,
#                    "entered_lap": int|None, "entered_time": float|None,
#                    "last_stop_lap": int|None, "last_stop_duration": float|None} }
_pit_state: Dict[int, Dict] = {}

# Cached live incident counts parsed from SessionInfo ResultsPositions.
# Refreshed each loop; map car_idx -> int.
_live_incidents: Dict[int, int] = {}
_official_laps_led: Dict[int, int] = {}

# Set of iRacing cust_ids configured as Owner Drivers in race_config.json.
# Loaded once on first call; the Owner flag in each car dict is True when
# the car's CustId is in this set. Keyed off cust_id rather than car_idx
# because iRacing AI sessions don't always slot the player into car_idx 0.
_owner_cust_ids: set | None = None


def _load_owner_cust_ids() -> set:
    """Load owner cust_ids from race_config.json. Cached on first call."""
    global _owner_cust_ids
    if _owner_cust_ids is not None:
        return _owner_cust_ids
    ids: set = set()
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "race_config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        owners = cfg.get("owner_drivers") or {}
        if isinstance(owners, dict):
            for val in owners.values():
                if isinstance(val, dict):
                    cid = val.get("iracing_customer_id")
                    if cid not in (None, ""):
                        try:
                            ids.add(int(cid))
                        except (TypeError, ValueError):
                            pass
        single = cfg.get("owner_driver") or {}
        if isinstance(single, dict):
            cid = single.get("iracing_customer_id")
            if cid not in (None, ""):
                try:
                    ids.add(int(cid))
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        print(f"[LEADERBOARD] owner cust_id load failed: {e}")
    _owner_cust_ids = ids
    return ids


def _is_owner_car(cust_id, car_idx) -> bool:
    """Owner Driver match by iRacing cust_id. AI sessions don't reliably
    place the player at car_idx 0 (for example, owner driver at car_idx=12), so the
    legacy `car_idx == 0` heuristic was flagging the wrong car or none.
    """
    try:
        cid = int(cust_id) if cust_id is not None else None
    except (TypeError, ValueError):
        cid = None
    if cid is not None and cid in _load_owner_cust_ids():
        return True
    # Fallback: legacy car_idx==0 heuristic, used only when cust_id is
    # missing (rare, but happens in some replay/scrub edge cases).
    return cid is None and car_idx == 0

ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')


def _owner_best_fallback(arr_val, car_idx, owner_scalar):
    """CarIdxBestLapTime[0] can stay at -1/0 for the player's own car in
    AI sessions even after valid laps. Fall back to the scalar
    LapBestLapTime (the iRacing-provided owner-only best) when the array
    slot is non-positive and we're looking at the owner car."""
    try:
        v = float(arr_val) if arr_val is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    if v > 0:
        return arr_val
    if car_idx == 0:
        try:
            s = float(owner_scalar or 0.0)
        except (TypeError, ValueError):
            s = 0.0
        if s > 0:
            return s
    return arr_val


def _owner_last_fallback(arr_val, car_idx, owner_scalar):
    """Same fallback pattern as _owner_best_fallback but for LastLapTime."""
    try:
        v = float(arr_val) if arr_val is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    if v > 0:
        return arr_val
    if car_idx == 0:
        try:
            s = float(owner_scalar or 0.0)
        except (TypeError, ValueError):
            s = 0.0
        if s > 0:
            return s
    return arr_val

# ========== UI Helpers ==========
def pad_visible(s: str, width: int) -> str:
    visible_len = len(ansi_escape.sub('', s))
    return s + ' ' * max(0, width - visible_len)

def _format_delta(delta: int, width: int = 5) -> str:
    s = f"{delta:+d}"
    if delta > 0:
        colored = f"{Fore.GREEN}{s}{Style.RESET_ALL}"
    elif delta < 0:
        colored = f"{Fore.RED}{s}{Style.RESET_ALL}"
    else:
        colored = f"{Fore.WHITE}{s}{Style.RESET_ALL}"
    return ' ' * (width - len(s)) + colored

def _format_gap_seconds(seconds: float) -> str:
    if seconds and seconds > 0:
        return f"+{seconds:6.3f}"
    else:
        return " " * 6

# ========== MAIN FUNCTION ==========
def live_leaderboard(ir) -> Tuple[List[Dict], Dict]:
    global _last_recorded_lap, _last_leading_car_idx, laps_led, STARTING_GRID
    global current_leaderboard_snapshot, owner_car_details, current_subsession_id

    # If not connected, return nothing
    if not getattr(ir, "is_initialized", False) or not getattr(ir, "is_connected", False):
        return [], {}

    # Refresh SubSessionID from WeekendInfo. Session-scoped and rarely
    # changes, but cheap to re-read each tick and keeps the global
    # honest across session transitions.
    try:
        weekend_info = sector_says_utilities.safe_session_var(ir, "WeekendInfo", {}) or {}
        ssid = weekend_info.get("SubSessionID")
        if isinstance(ssid, int) and ssid > 0:
            current_subsession_id = ssid
    except Exception:
        pass

    # Telemetry fetch
    # --- FIX: Unified driver discovery across all session states ---
    drivers_info = sector_says_utilities.safe_session_var(ir, "DriverInfo", {})
    if not isinstance(drivers_info, dict):
        drivers_info = {}
    # Try new-style: DriverInfo → Drivers
    drivers = drivers_info.get("Drivers")
    if not drivers:
        drivers = []
    # Fallback: SessionInfo → Sessions → ResultsPositions (pre-grid, loading)
    if not drivers:
        session_info = sector_says_utilities.safe_ir_sessioninfo(ir)
        sessions = session_info.get("Sessions", []) if session_info else []
        for s in sessions:
            results = s.get("ResultsPositions")
            if results:
                # Convert ResultsPositions into Driver-like dicts
                drivers = [
                    {
                        "CarIdx": r.get("CarIdx"),
                        "UserName": r.get("UserName") or r.get("DriverName"),
                        "CarNumber": r.get("CarNumberRaw") or r.get("CarNumber"),
                        "CarIsPaceCar": False,
                    }
                    for r in results
                    if r.get("CarIdx") is not None
                ]
                break  # Stop at the first session that has results
    # If still no drivers, return early
    if not drivers:
        print("[LEADERBOARD] No drivers found in DriverInfo or SessionInfo.")
        return [], {}

    positions_arr = sector_says_utilities.safe_session_var(ir, "CarIdxPosition", [])
    laps_arr = sector_says_utilities.safe_session_var(ir, "CarIdxLap", [])
    best_laps = sector_says_utilities.safe_session_var(ir, "CarIdxBestLapTime", [])
    last_laps = sector_says_utilities.safe_session_var(ir, "CarIdxLastLapTime", [])
    owner_best_scalar = sector_says_utilities.safe_session_var(ir, "LapBestLapTime", 0.0) or 0.0
    owner_last_scalar = sector_says_utilities.safe_session_var(ir, "LapLastLapTime", 0.0) or 0.0
    lap_dist = sector_says_utilities.safe_session_var(ir, "CarIdxLapDistPct", [])
    track_surface = sector_says_utilities.safe_session_var(ir, "CarIdxTrackSurface", [])
    # New live telemetry: pit-road booleans, iRacing's own timing arrays
    on_pit_road_arr = sector_says_utilities.safe_session_var(ir, "CarIdxOnPitRoad", [])
    est_time_arr    = sector_says_utilities.safe_session_var(ir, "CarIdxEstTime", [])
    f2_time_arr     = sector_says_utilities.safe_session_var(ir, "CarIdxF2Time", [])
    session_time    = sector_says_utilities.safe_session_var(ir, "SessionTime", 0.0) or 0.0

    # ---- Live incidents per driver ----
    # Pulled from SessionInfo → Sessions → ResultsPositions for the active
    # race simsession. iRacing updates these while the race is running.
    global _live_incidents, _official_laps_led
    try:
        raw_sinfo = sector_says_utilities.safe_session_var(ir, "SessionInfo", {}) or {}
        sessions_list = raw_sinfo.get("Sessions", []) if isinstance(raw_sinfo, dict) else []
        incidents_map = {}
        laps_led_map = {}
        for s in sessions_list:
            if (s.get("SessionType") or "").lower() != "race":
                continue
            for rp in (s.get("ResultsPositions") or []):
                ci = rp.get("CarIdx")
                if ci is None:
                    continue
                try:
                    incidents_map[int(ci)] = int(rp.get("Incidents") or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    laps_led_map[int(ci)] = int(rp.get("LapsLed") or 0)
                except (TypeError, ValueError):
                    pass
            break
        if incidents_map:
            _live_incidents = incidents_map
        if laps_led_map:
            _official_laps_led = laps_led_map
    except Exception as e:
        print(f"[LEADERBOARD] live incidents parse failed: {e}")

    cars = []
    owner_car_details = {}

    # Capture starting grid one time. record_starting_grid() flips the
    # module-level _first_grid_recorded flag once it's captured, so this
    # is a true one-shot — the previous version checked the module flag
    # but only wrote a function-attr flag, leaving the gate permanently
    # open and burning a 1-second sleep on every live_leaderboard() call.
    if not race_data_store._first_grid_recorded:
        race_data_store.record_starting_grid(ir)

    STARTING_GRID = race_data_store.get_starting_grid()

    # -----------------------------------------------------
    # DOTD INIT — additive every tick. AI sessions and late-joiners often
    # don't populate DriverInfo.Drivers fully on the first call; if we
    # only init once, later CarIdxes never get a RaceScore and every
    # submit_custom_points/on_trigger silently drops their lap_clean,
    # race_finish, exceeded_expectation events. create_race_scores is
    # now additive (preserves existing scores) so calling every tick is
    # cheap and self-healing.
    # -----------------------------------------------------
    driver_ids = [
        car.get("CarIdx")
        for car in drivers
        if isinstance(car, dict) and car.get("CarIdx") is not None
    ]
    if driver_ids:
        dotd.create_race_scores(driver_ids)

    # -----------------------------------------------------
    # DOTD NAMES — always keep driver_names current
    # -----------------------------------------------------
    for car in drivers:
        if not isinstance(car, dict):
            continue
        car_idx = car.get("CarIdx")
        if car_idx is None:
            continue
        name = (
            car.get("UserName")
            or car.get("AbbrevName")
            or f"Driver {car_idx}"
        )
        dotd.driver_names[car_idx] = name

    # -----------------------------------------------------
    # BUILD CARS LIST — CarIdx must exist
    # -----------------------------------------------------
    cars = []
    for car in drivers:
        if not isinstance(car, dict):
            continue

        car_idx = car.get("CarIdx")
        if car_idx is None:
            continue  # skip unexpected driver entries

        # Basic info
        name = car.get("UserName") or f"Car {car_idx}"
        car_number = car.get("CarNumber", "?")
        surf = track_surface[car_idx] if car_idx < len(track_surface) else 0

        # Skip pace car
        if car.get("CarIsPaceCar"):
            continue

        # Display name — never modify the name, pit status is a separate field
        display_name = name

        # Live position (fallback = car_idx sorted order)
        live_pos = positions_arr[car_idx] if car_idx < len(positions_arr) else (car_idx + 1)

        # Starting grid
        start_pos = STARTING_GRID.get(car_idx, live_pos)

        # iRacing credentials from the YAML DriverInfo record. LicString
        # looks like "A 3.45" — split into class letter + safety rating so
        # HUD consumers don't have to parse it themselves.
        irating = car.get("IRating")
        lic_level = car.get("LicLevel")
        lic_sub_level = car.get("LicSubLevel")
        lic_string = (car.get("LicString") or "").strip()
        # UserID is iRacing's stable customer id — the anchor for any
        # /data API lookups (career stats, recent form, etc). Absent
        # only for AI drivers and the pace car.
        cust_id_raw = car.get("UserID")
        try:
            cust_id = int(cust_id_raw) if cust_id_raw not in (None, "", 0) else None
        except (TypeError, ValueError):
            cust_id = None
        lic_class = ""
        lic_safety = None
        if lic_string:
            parts = lic_string.split()
            if parts:
                lic_class = parts[0]
                if len(parts) > 1:
                    try:
                        lic_safety = float(parts[1])
                    except (TypeError, ValueError):
                        lic_safety = None

        # ---- Pit state edge detection ----
        # CarIdxOnPitRoad gives us a clean boolean per car. Compare to the
        # previous state we cached for this driver; on any transition we
        # either mark them as "entered" (false→true) or log the completed
        # stop to the DB (true→false).
        try:
            on_pit_now = bool(on_pit_road_arr[car_idx]) if car_idx < len(on_pit_road_arr) else False
        except Exception:
            on_pit_now = False
        lap_now = laps_arr[car_idx] if car_idx < len(laps_arr) else 0
        ps = _pit_state.setdefault(car_idx, {
            "stops": 0, "on_pit": False,
            "entered_lap": None, "entered_time": None,
            "last_stop_lap": None, "last_stop_duration": None,
        })
        if on_pit_now and not ps["on_pit"]:
            # Pit entry
            ps["on_pit"] = True
            ps["entered_lap"] = int(lap_now) if lap_now else None
            ps["entered_time"] = float(session_time)
        elif (not on_pit_now) and ps["on_pit"]:
            # Pit exit — completed stop
            ps["on_pit"] = False
            ps["stops"] += 1
            exited = float(session_time)
            entered = ps.get("entered_time")
            if entered is not None:
                ps["last_stop_duration"] = round(exited - entered, 2)
            ps["last_stop_lap"] = ps.get("entered_lap")
            # Persist to DB (safe no-op if no current race_id)
            try:
                import race_db
                race_db.log_pit_stop(
                    car_idx=car_idx,
                    driver_name=name,
                    stop_num=ps["stops"],
                    entered_lap=ps.get("entered_lap") or 0,
                    entered_time=entered or 0.0,
                    exited_time=exited,
                )
            except Exception as e:
                print(f"[PIT] log failed for car {car_idx}: {e}")

        # ---- iRacing-native gap numbers (seconds) ----
        try:
            est_t = float(est_time_arr[car_idx]) if car_idx < len(est_time_arr) else 0.0
        except Exception:
            est_t = 0.0
        try:
            f2_t  = float(f2_time_arr[car_idx]) if car_idx < len(f2_time_arr) else 0.0
        except Exception:
            f2_t  = 0.0

        data = {
            "CarIdx": car_idx,
            "CarNumber": car_number,
            "Name": display_name,
            "CustId": cust_id,
            "IRating": irating if isinstance(irating, int) else None,
            "LicString": lic_string or None,
            "LicClass": lic_class or None,
            "LicSafety": lic_safety,
            "LicLevel": lic_level if isinstance(lic_level, int) else None,
            "LicSubLevel": lic_sub_level if isinstance(lic_sub_level, int) else None,
            # Pit tracking
            "OnPitRoad": on_pit_now,
            "PitStops": ps["stops"],
            "LastPitLap": ps.get("last_stop_lap"),
            "LastPitDuration": ps.get("last_stop_duration"),
            # iRacing native timing (seconds). F2Time is iRacing's own
            # "gap to car ahead / leader" calc; EstTime is the raw
            # "time to complete this lap from current position" figure.
            "EstTime": est_t,
            "F2Time": f2_t,
            # Live incident count from ResultsPositions YAML
            "LiveIncidents": _live_incidents.get(car_idx, 0),
            "Lap": laps_arr[car_idx] if car_idx < len(laps_arr) else 0,
            "Best": _owner_best_fallback(
                best_laps[car_idx] if car_idx < len(best_laps) else None,
                car_idx,
                owner_best_scalar,
            ),
            "Last": _owner_last_fallback(
                last_laps[car_idx] if car_idx < len(last_laps) else None,
                car_idx,
                owner_last_scalar,
            ),
            "LapDistPct": lap_dist[car_idx] if car_idx < len(lap_dist) else 0.0,
            "GridPosition": start_pos,
            "OfficialLapsLed": _official_laps_led.get(car_idx),
            "LapsLed": _official_laps_led.get(car_idx, laps_led.get(car_idx, 0)),
            "Owner": _is_owner_car(cust_id, car_idx),
            "ΔPos": start_pos - live_pos,
            "LivePos": live_pos,
            "GapAhead": 0.0,
            "GapToLeader": 0.0,
            "TrackSurface": surf,
            "DOTD": dotd.get_score(car_idx),
        }

        cars.append(data)

        if car_idx == 0:
            owner_car_details = data

    # -----------------------------------------------------
    # SORT LEADERBOARD — pit cars always at the bottom
    def sort_key(c):
        pit = c["TrackSurface"] in (1, 2, 4)
        lap = int(c.get("Lap", 0) or 0)
        dist = float(c.get("LapDistPct", 0.0) or 0.0)
        # 0 = on track, 1 = in pit → pits always after on-track cars
        # Within each group, sort by lap + distance around track
        return (1 if pit else 0, -lap, -dist)
    cars_sorted = sorted(cars, key=sort_key)
    
    # --- Track laps led ---
    if not hasattr(live_leaderboard, "_laps_led_state"):
        live_leaderboard._laps_led_state = {
            "laps_led": {},
            "last_leading_car_idx": None,
            "last_recorded_lap": 0,
        }

    laps_led_state = live_leaderboard._laps_led_state
    laps_led = laps_led_state["laps_led"]
    _last_leading_car_idx = laps_led_state["last_leading_car_idx"]
    _last_recorded_lap = laps_led_state["last_recorded_lap"]

    try:
        leader_car = cars_sorted[0] if cars_sorted else None
        if leader_car:
            current_leader_idx = leader_car.get("CarIdx")
            current_lap = int(leader_car.get("Lap", 0) or 0)

            # Only increment when a *new lap number* starts
            if current_lap > _last_recorded_lap:
                if _last_leading_car_idx is not None:
                    laps_led[_last_leading_car_idx] = laps_led.get(_last_leading_car_idx, 0) + 1
                    #print(f"[LAPS LED] CarIdx={_last_leading_car_idx} +1 lap led (now {laps_led[_last_leading_car_idx]})")

                laps_led_state["last_recorded_lap"] = current_lap

            laps_led_state["last_leading_car_idx"] = current_leader_idx

        # Attach LapsLed field to all cars for display/export.
        # Include +1 for the current leader's in-progress lap so
        # the HUD card shows a non-zero count from the first lap.
        current_leader_idx = laps_led_state.get("last_leading_car_idx")
        for car in cars_sorted:
            official_ll = car.get("OfficialLapsLed")
            if official_ll is not None:
                try:
                    car["LapsLed"] = int(official_ll)
                except (TypeError, ValueError):
                    car["LapsLed"] = 0
                continue
            ll = laps_led.get(car.get("CarIdx"), 0)
            if car.get("CarIdx") == current_leader_idx:
                ll += 1  # credit the lap currently being led
            car["LapsLed"] = ll

    except Exception as e:
        print(f"[WARN] Laps-led tracking failed: {e}")

        
    # ── RacePos: true race position ignoring pit-sort ──────────
    # The sort_key above pushes pit-road cars to the bottom of the
    # visual timing tower, which is correct for the HUD but creates
    # fake position drops in the trigger system.  RacePos is based
    # purely on (lap, LapDistPct) — the same data the Relative box
    # uses — so a car pitting retains its approximate race position.
    def race_pos_key(c):
        lap = int(c.get("Lap", 0) or 0)
        dist = float(c.get("LapDistPct", 0.0) or 0.0)
        return (-lap, -dist)
    cars_by_race_pos = sorted(cars_sorted, key=race_pos_key)
    _race_pos_lookup = {}
    for rp, car in enumerate(cars_by_race_pos, 1):
        _race_pos_lookup[car["CarIdx"]] = rp

    # Attach live pos and update DOTD
    # After the chequered flag, iRacing's CarIdxPosition (already stored
    # in each car's LivePos at build time) reflects the official finish
    # order.  The sort-based enumerate is only useful for the visual
    # timing tower *during* the race — at race end it corrupts positions
    # because pit-road cars get pushed to the bottom of the sort.
    session_flags = sector_says_utilities.safe_session_var(ir, "SessionFlags", 0) or 0
    race_over = bool(session_flags & 0x00000001)  # CHECKERED bit
    for pos, car in enumerate(cars_sorted, 1):
        if not race_over:
            car["LivePos"] = pos
        # else: keep the iRacing-native CarIdxPosition set at build time
        # RacePos: true race position based on lap + LapDistPct, not
        # affected by pit-road sort.  Used by check_position_changes()
        # for overtake detection so pitting doesn't cascade fake alerts.
        car["RacePos"] = _race_pos_lookup.get(car["CarIdx"], car["LivePos"])
        effective_pos = car["LivePos"]
        start_pos = STARTING_GRID.get(car["CarIdx"], effective_pos)
        car["ΔPos"] = start_pos - effective_pos
        car["DOTD"] = dotd.get_score(car["CarIdx"])
        
    # -------------------------------------------------------------
    # --- Update DOTD metadata (names, positions, deltas) ----------
    # -------------------------------------------------------------
    if cars_sorted:
        dotd.driver_names = {c["CarIdx"]: c.get("Name", f"Car {c['CarIdx']}") for c in cars_sorted}
        dotd.driver_positions = {c["CarIdx"]: c.get("LivePos") or c.get("Pos") for c in cars_sorted}
        dotd.driver_deltas = {}
        for c in cars_sorted:
            delta_val = c.get("ΔPos") or 0
            if delta_val > 0:
                delta = f"+{int(delta_val)}"
            elif delta_val < 0:
                delta = f"{int(delta_val)}"
            else:
                delta = ""
            dotd.driver_deltas[c["CarIdx"]] = delta

        # -----------------------------------------------------
        # GAP COMPUTATION
        #   - On-track cars form the main chain
        #   - Pit cars stay at the bottom but still get GapToLeader
        # -----------------------------------------------------
        on_track = [c for c in cars_sorted if c["TrackSurface"] not in (1, 2, 4)]
        pit_cars = [c for c in cars_sorted if c["TrackSurface"] in (1, 2, 4)]

        # Field-wide average lap time — used as the fallback scale
        # factor when iRacing's native F2Time is unavailable.  Using a
        # single median prevents wildly wrong gaps for cars that haven't
        # set a timed lap (the previous per-car default of 60s was way
        # too low for circuits like Mugello where a lap is ~100s).
        all_bests = [float(c.get("Best") or 0) for c in on_track + pit_cars if (c.get("Best") or 0) > 0]
        field_avg_lap = sorted(all_bests)[len(all_bests) // 2] if all_bests else 90.0

        # Main gap chain for cars on track
        if on_track:
            gaps_order = sorted(on_track, key=lambda c: (-c["Lap"], -c["LapDistPct"]))
            leader = gaps_order[0]

            for idx, car in enumerate(gaps_order):
                if idx == 0:
                    car["GapAhead"] = 0.0
                    car["GapToLeader"] = 0.0
                else:
                    ahead = gaps_order[idx - 1]

                    try:
                        car_lap = float(car.get("Lap", 0) or 0)
                        ahead_lap = float(ahead.get("Lap", 0) or 0)
                        leader_lap = float(leader.get("Lap", 0) or 0)
                        car_dist = float(car.get("LapDistPct", 0.0) or 0.0)
                        ahead_dist = float(ahead.get("LapDistPct", 0.0) or 0.0)
                        leader_dist = float(leader.get("LapDistPct", 0.0) or 0.0)
                    except Exception:
                        car_lap = ahead_lap = leader_lap = 0.0
                        car_dist = ahead_dist = leader_dist = 0.0

                    car["GapAhead"] = max(
                        (ahead_lap - car_lap + ahead_dist - car_dist) * field_avg_lap,
                        0.0,
                    )
                    car["GapToLeader"] = max(
                        (leader_lap - car_lap + leader_dist - car_dist) * field_avg_lap,
                        0.0,
                    )

        # NOTE: F2Time override removed — iRacing's CarIdxF2Time only
        # updates at sector/lap crossings, causing gaps to appear frozen
        # for an entire lap.  The Lap+LapDistPct calculation above updates
        # every tick and provides continuously changing intervals.

        # Pit cars: keep them at the bottom, give them a sensible leader gap
        for car in pit_cars:
            if on_track:
                leader = on_track[0]

                try:
                    car_lap = float(car.get("Lap", 0) or 0)
                    leader_lap = float(leader.get("Lap", 0) or 0)
                    car_dist = float(car.get("LapDistPct", 0.0) or 0.0)
                    leader_dist = float(leader.get("LapDistPct", 0.0) or 0.0)
                except Exception:
                    car_lap = leader_lap = 0.0
                    car_dist = leader_dist = 0.0

                car["GapAhead"] = 0.0  # visually breaks chain; they’re "off sequence"
                car["GapToLeader"] = max(
                    (leader_lap - car_lap + leader_dist - car_dist) * field_avg_lap,
                    0.0,
                )
            else:
                # No on-track cars (weird session state)
                car["GapAhead"] = 0.0
                car["GapToLeader"] = 0.0

    # --- Sectors (safe formatting) ---
    for car in cars_sorted:
        if car["Lap"] > 0:
            s1, s2, s3 = race_data_store.get_sector_summary(car["CarIdx"])

            def fmt_sector(val):
                try:
                    if val is None or val in ("", "--"):
                        return "--"
                    return f"{float(val):.3f}"
                except Exception:
                    return "--"

            car["S1"] = fmt_sector(s1)
            car["S2"] = fmt_sector(s2)
            car["S3"] = fmt_sector(s3)
        else:
            car["S1"] = car["S2"] = car["S3"] = "--"

    # --- Identify Owner Car ---
    owner_car = next((c for c in cars_sorted if c.get("Owner")), None)

    return cars_sorted, owner_car

# ========== Snapshot Export ==========
def create_leaderboard_snapshot(cars_sorted: List[Dict]) -> List[Dict]:
    """
    Build a clean leaderboard snapshot with guaranteed positions 1..X.
    The owner car (CarIdx == 0) keeps its true LivePos if available.
    """
    # Sort by DOTD score first, then LivePos
    sorted_copy = sorted(
        cars_sorted,
        key=lambda c: (-c.get("DOTD", 0), c.get("LivePos", 999))
    )

    snap = []
    for i, car in enumerate(sorted_copy, start=1):
        car_idx = car.get("CarIdx", -1)
        live_pos = car.get("LivePos")
        # Fallback to sequential position if missing
        fallback_pos = i
        # Keep true LivePos only for owner car (CarIdx == 0)
        if car_idx == 0 and isinstance(live_pos, int):
            final_pos = live_pos
        else:
            final_pos = fallback_pos
        snap.append({
            "CarIdx": car_idx,
            "Pos": final_pos,
            "Driver": car.get("Name", "Unknown"),
            "Lap": car.get("Lap", 0),
            "LapsLed": car.get("LapsLed", 0),
            "Best": car.get("Best", "-"),
            "Last": car.get("Last", "-"),
            "ΔPos": car.get("ΔPos", 0),
            "DOTD": car.get("DOTD", 0),
            "GapAhead": car.get("GapAhead", "-"),
            "GapToLeader": car.get("GapToLeader", "-"),
            "S1": car.get("S1", "-"),
            "S2": car.get("S2", "-"),
            "S3": car.get("S3", "-"),
        })
    return snap

from typing import List, Dict

def create_timingtower_snapshot(cars_sorted: List[Dict]) -> List[Dict]:
    snap = []

    for i, car in enumerate(cars_sorted, start=1):
        car_idx = car.get("CarIdx", -1)

        # Live position fallback
        live_pos = car.get("LivePos")
        if isinstance(live_pos, (int, float)):
            final_pos = int(live_pos)
        else:
            final_pos = i

        # Normalize delta
        delta_raw = car.get("ΔPos") or car.get("DeltaPos") or 0
        try:
            delta_pos = int(delta_raw)
        except (ValueError, TypeError):
            delta_pos = 0

        # Normalize gap
        gap_val = car.get("GapAhead")
        try:
            gap_val = round(float(gap_val), 3)
        except (ValueError, TypeError):
            gap_val = 0.0

        # Use consistent driver field
        driver_name = car.get("Driver") or car.get("Name") or f"Car {car_idx}"

        # Append normalized entry
        snap.append({
            "CarIdx": car_idx,
            "LivePos": final_pos,
            "DeltaPos": delta_pos,
            "Driver": driver_name,
            "Lap": int(car.get("Lap", 0)),
            "GapAhead": gap_val,
            "Owner": bool(car.get("Owner")),
        })

    return snap


def get_timingtower_snapshot() -> List[Dict]:
    global current_timingtower_snapshot
    return current_timingtower_snapshot

# ============================================================
# LEADERBOARD SNAPSHOT SYSTEM (Final Patched Version)
# ============================================================
import os
_current_snapshot: List[Dict] = []
_last_good_snapshot: List[Dict] = []
def update_leaderboard_snapshot(cars: List[Dict]):
    """Refresh memory snapshot (called after every stable export)."""
    global _current_snapshot, _last_good_snapshot

    if cars and isinstance(cars, list) and len(cars) >= 5:
        _current_snapshot = [dict(c) for c in cars]
        _last_good_snapshot = [dict(c) for c in cars]
    else:
        print("[SNAPSHOT] Ignoring unstable snapshot update.")

def get_leaderboard_snapshot() -> List[Dict]:
    """Returns stable snapshot with fallback → disk recovery, 
       but DISABLED after green flag."""
    global _current_snapshot, _last_good_snapshot
    import race_data_store

    # --------------------------------------------
    # If we already have a good in-memory snapshot
    # --------------------------------------------
    if len(_current_snapshot) >= 5:
        return _current_snapshot

    if len(_last_good_snapshot) >= 5:
        print("[SNAPSHOT] Using fallback in-memory snapshot.")
        return _last_good_snapshot

    # --------------------------------------------
    # DO NOT restore from disk once race is green
    # --------------------------------------------
    if getattr(race_data_store, "green_flag_time", None):
        # Race is active → stale disk restores are forbidden
        return []

    # --------------------------------------------
    # SAFE DISK RESTORE BEFORE RACE STARTS
    # --------------------------------------------
    try:
        path = os.path.join("sector_said_exports", "leaderboard.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cars = data.get("cars", [])
            if cars:
                print("[SNAPSHOT] Restored snapshot from disk.")
                _current_snapshot = cars
                _last_good_snapshot = cars
                return cars
    except Exception as e:
        print("[SNAPSHOT] Disk restore error:", e)

    return []
