# race_data_store.py — patched version with per-car best sector tracking
from typing import Dict
import sector_says_utilities
import race_config
import re
import random

# ---------- Globals ----------
_sector_times: Dict[int, Dict] = {}  # per-car lap tracking
_last_sector_splits: Dict[int, Dict[str, float]] = {}  # last S1/S2/S3 times
_sector_best_per_car: Dict[int, Dict[str, float]] = {}  # NEW: best sector times per car
_sector_best: Dict[str, float] = {"S1": None, "S2": None, "S3": None}
_sector_worst: Dict[str, float] = {"S1": None, "S2": None, "S3": None}
_sector_best_car: Dict[str, int] = {"S1": None, "S2": None, "S3": None}
_sector_worst_car: Dict[str, int] = {"S1": None, "S2": None, "S3": None}
_last_session_flags = 0

def _init_sector_boundaries():
    """Generate randomised sector boundaries from config ranges."""
    marsh = race_config.cfg("marshalling").get("sector_boundaries_range", {})
    s1_range = marsh.get("s1", [0.30, 0.36])
    s2_range = marsh.get("s2", [0.63, 0.69])
    return [0.0, random.uniform(*s1_range), random.uniform(*s2_range), 0.99]

SECTOR_BOUNDARIES = _init_sector_boundaries()
current_weekend_info = None
audio_output_device = 0
current_focus_driver = None
fmod_system = None
fmod_studio = None

# ---------- Starting Grid ----------
_starting_grid: dict[int, int] = {}
_first_grid_recorded: bool = False

FLAG_BITS = {0: "GREEN", 1: "YELLOW", 2: "BLUE", 3: "MEATBALL", 4: "BLACK", 5: "WHITE", 6: "CHECKERED"}

# ==========================================================
# SHARED RACE STATE (GLOBAL SINGLE SOURCE OF TRUTH)
# ==========================================================
green_flag_time = None
last_summary_race_time = 0.0
last_summary_lap_warning = False


# ---------- Session Flags ----------
def get_new_session_flags(ir):
    """Detect newly-set flag bits since last call.

    Caller must freeze the buffer before calling this function.
    """
    global _last_session_flags
    try:
        flags = ir['SessionFlags']
    except Exception:
        return []
    new_flags = []
    for bit_pos, name in FLAG_BITS.items():
        mask = 1 << bit_pos
        active_now = (flags & mask) != 0
        was_active = (_last_session_flags & mask) != 0
        if active_now and not was_active:
            new_flags.append(name)
    _last_session_flags = flags
    return new_flags

def set_current_weekend_info(ir):
    global current_weekend_info
    # Grab the full WeekendInfo section
    weekend_info = ir.get_session_info().get('WeekendInfo', {})
    # Merge in the nested weather data (flattened for convenience)
    weather_info = weekend_info.get('Weather', {})
    current_weekend_info = {
        **weekend_info,
        "Weather": weather_info,
    }
    # Optional debug output
    print("[DEBUG] set_current_weekend_info:")
    print("  Track:", current_weekend_info.get("TrackDisplayName"))
    print("  Skies:", weather_info.get("Skies"))
    print("  Air Temp:", weather_info.get("Temp", {}).get("Value"))
    print("  Humidity:", weather_info.get("RelativeHumidity", {}).get("Value"))

# ---------- Driver Name Cleaning ----------
def clean_driver_name(name: str) -> str:
    if not name:
        return name
    cleaned = re.sub(r"\d+$", "", name).strip()
    return cleaned

# ---------- Starting Grid Capture ----------
def _read_quali_grid(ir):
    """Extract authoritative grid from QualifyResultsInfo.

    The qualifying results are the only source that matches what
    iRacing displays on its own pre-race screen. CarIdxPosition is
    unreliable: 0 for several front-of-grid cars at the green-flag
    instant (AI-session quirk), and already scrambled by on-track
    Turn 1 chaos a few seconds later. QualifyResultsInfo is the live
    quali finishing order — populated as soon as the quali session
    finalises, well before green flag.

    Returns {car_idx: position} or {} if quali results aren't
    available (which can happen during the quali session itself, or
    in AI rollout races with no qualifying).
    """
    quali = sector_says_utilities.safe_session_var(ir, "QualifyResultsInfo", {}) or {}
    results = quali.get("Results") if isinstance(quali, dict) else None
    if not isinstance(results, list) or not results:
        return {}
    raw_positions = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        try:
            ci = int(r.get("CarIdx"))
            pos = int(r.get("Position"))
        except (TypeError, ValueError):
            continue
        if pos < 0:
            continue
        raw_positions[ci] = pos

    if not raw_positions:
        return {}

    # iRacing reports QualifyResultsInfo.Position as zero-based in some
    # sessions and one-based in others. Race 19 exposed the failure mode:
    # pole was Position=0, got skipped, and every following grid slot was
    # shifted forward by one.
    offset = 1 if min(raw_positions.values()) == 0 else 0
    grid = {ci: pos + offset for ci, pos in raw_positions.items()}

    expected_car_idxs = _expected_grid_car_idxs(ir)
    if expected_car_idxs:
        missing = expected_car_idxs - set(grid)
        if missing:
            print(
                f"[GRID] Ignoring incomplete QualifyResultsInfo grid: "
                f"missing {len(missing)} car(s) {sorted(missing)[:8]}"
            )
            return {}

    positions = list(grid.values())
    if len(set(positions)) != len(positions):
        print("[GRID] Ignoring QualifyResultsInfo grid with duplicate positions")
        return {}
    return grid


def _expected_grid_car_idxs(ir):
    """Return non-pace race car indexes expected in the grid, if known."""
    try:
        driver_info = sector_says_utilities.safe_session_var(ir, "DriverInfo", {}) or {}
        drivers = driver_info.get("Drivers", []) if isinstance(driver_info, dict) else []
    except Exception:
        return set()

    expected = set()
    for car in drivers:
        if not isinstance(car, dict):
            continue
        if car.get("CarIsPaceCar"):
            continue
        try:
            ci = int(car.get("CarIdx"))
        except (TypeError, ValueError):
            continue
        expected.add(ci)
    return expected


def record_starting_grid(ir):
    global _starting_grid, _first_grid_recorded
    if _first_grid_recorded:
        return

    # Preferred source: QualifyResultsInfo (what iRacing's pre-race
    # screen shows). Falls through to the CarIdxPosition synth grid
    # only if quali results aren't populated yet.
    quali_grid = _read_quali_grid(ir)
    if quali_grid:
        _starting_grid = quali_grid
        _first_grid_recorded = True
        print(f"[GRID] Captured {len(quali_grid)} cars from QualifyResultsInfo")
        return

    drivers_info = sector_says_utilities.safe_session_var(ir, "DriverInfo", {}).get("Drivers", [])
    positions = sector_says_utilities.safe_session_var(ir, "CarIdxPosition", [])
    track_surface = sector_says_utilities.safe_session_var(ir, "CarIdxTrackSurface", [])

    starting_grid = []
    live_sorted = []

    for car_idx, car in enumerate(drivers_info):
        if not isinstance(car, dict):
            continue
        if car.get("CarIsPaceCar", False):
            continue
        if "UserName" in car:
            original_name = car["UserName"]
            car["UserName"] = clean_driver_name(original_name)
            if original_name != car["UserName"]:
                print(f"[DEBUG] Cleaned driver name: '{original_name}' -> '{car['UserName']}'")
        surface = track_surface[car_idx] if car_idx < len(track_surface) else 0
        if surface == sector_says_utilities.TRKLOC_NOT_IN_WORLD:
            continue
        pos = positions[car_idx] if car_idx < len(positions) else None
        if pos is None:
            continue
        # CarIdxPosition=0 means iRacing hasn't slotted this car into the
        # race grid yet (AI sessions love returning 0 for the player at
        # GREEN). Push 0 to the back of the grid sort instead of pole —
        # mirrors the build_starting_grid fix in sector_says_utilities.
        sort_pos = pos if isinstance(pos, int) and pos > 0 else 9999
        live_sorted.append((sort_pos, car_idx))

    live_sorted.sort(key=lambda x: x[0])
    for start_pos, (_, car_idx) in enumerate(live_sorted, start=1):
        starting_grid.append((car_idx, start_pos))

    _starting_grid = {car_idx: pos for car_idx, pos in starting_grid}
    _first_grid_recorded = True
    print(f"[GRID] Captured {len(_starting_grid)} cars from CarIdxPosition fallback (no quali results yet)")


def upgrade_starting_grid_from_quali(ir):
    """Replace the captured grid with QualifyResultsInfo if available.

    Called at race-start to upgrade whatever record_starting_grid
    captured during pre-green to the authoritative quali results, in
    case they only populated after the first live_leaderboard call.
    No-op if QualifyResultsInfo isn't available — the existing
    fallback grid stays.

    Returns True if the grid was upgraded.
    """
    global _starting_grid, _first_grid_recorded
    quali_grid = _read_quali_grid(ir)
    if not quali_grid:
        return False
    _starting_grid = quali_grid
    _first_grid_recorded = True
    return True

def get_starting_grid():
    return _starting_grid

def reset_starting_grid():
    """Wipe the captured grid so the next race re-records it.

    Call this at race-start to make sure leftover state from a previous
    race in the same engine session doesn't leak through. Without this,
    `_first_grid_recorded=True` would stick across races and every later
    race would inherit race 1's grid.
    """
    global _starting_grid, _first_grid_recorded
    _starting_grid = {}
    _first_grid_recorded = False

def seed_starting_grid_from_cars(cars):
    """Explicitly seed the starting grid from a known-good cars list.

    Used at race_db.start_race time so the grid mirrors exactly what the
    DB recorded as grid_position. The lazy capture inside live_leaderboard
    (record_starting_grid) fires the first time live_leaderboard runs —
    which during pre-green is fine, but at the green-flag instant iRacing
    can still report CarIdxPosition=0 for front-of-grid cars, inverting
    the captured order. Seeding explicitly after the warmup loop avoids
    that window.
    """
    global _starting_grid, _first_grid_recorded
    grid = {}
    for c in cars or []:
        if not isinstance(c, dict):
            continue
        try:
            ci = int(c.get("CarIdx"))
            pos = c.get("LivePos") or c.get("Pos")
            pos = int(pos) if pos else None
        except Exception:
            continue
        if ci is None or pos is None or pos <= 0:
            continue
        grid[ci] = pos
    if grid:
        _starting_grid = grid
        _first_grid_recorded = True

def build_pre_race_featured_list(leaderboard, max_count=5):
    """
    Build a Featured Driver of the Day list BEFORE the race starts.
    This list recalculates every loop until the green flag.
    """
    featured = []

    for car in leaderboard:
        # Skip invalid entries
        if not car.get("Name"):
            continue

        # Preliminary score: use ΔPos, Best lap, team/owner, grid position
        score = 0

        # 1. Grid position weight — pole +1, top 5 +0.5
        pos = car.get("LivePos", 99)
        if pos == 1:
            score += 1.0
        elif pos <= 5:
            score += 0.5

        # 2. Owner car gets special handling
        if car.get("Owner", False):
            score += 0.3

        # 3. Best lap quality
        best = car.get("Best", -1)
        if best > 0:
            score += (200 / best)  # fast lap → higher score

        # 4. ΔPos (if qualifying session updates)
        dpos = car.get("ΔPos", 0)
        score += (dpos * 0.1)

        featured.append({
            "CarIdx": car["CarIdx"],
            "Name": car["Name"],
            "CarNumber": car.get("CarNumber"),
            "Score": round(score, 3),
        })

    # Sort by descending score
    featured.sort(key=lambda x: x["Score"], reverse=True)

    # Trim
    return featured[:max_count]


# ---------- Main Update ----------
def update_sector_times(ir, car_indices):
    """
    Update per-car sector times from telemetry, tracking best per-car sectors.
    """
    try:
        now = sector_says_utilities.safe_session_var(ir, "SessionTime", 0.0)
        lap_dist_pct = sector_says_utilities.safe_session_var(ir, "CarIdxLapDistPct", [])
        last_lap_times = sector_says_utilities.safe_session_var(ir, "CarIdxLastLapTime", [])
        laps_arr = sector_says_utilities.safe_session_var(ir, "CarIdxLap", [])

        for idx in car_indices:
            pct = lap_dist_pct[idx] if idx < len(lap_dist_pct) else 0.0
            last_lap = last_lap_times[idx] if idx < len(last_lap_times) else None
            lap = laps_arr[idx] if idx < len(laps_arr) else 0

            car = _sector_times.setdefault(idx, {
                "last_pct": 0.0,
                "last_time": now,
                "next_split": 0,
                "tracking": False,  # True only after we've observed a lap rollover
            })

            # Skip first lap
            if lap <= 1:
                car["last_pct"] = pct
                continue

            # New lap reset
            if pct < car["last_pct"]:
                # Compute S3 from last lap if possible (only when we were
                # fully tracking — otherwise we'd use a partial S1/S2).
                if car["tracking"]:
                    s1 = _last_sector_splits.get(idx, {}).get("S1")
                    s2 = _last_sector_splits.get(idx, {}).get("S2")
                    if s1 is not None and s2 is not None and last_lap is not None:
                        s3 = last_lap - s1 - s2
                        if s3 > 0:
                            _last_sector_splits.setdefault(idx, {})["S3"] = s3

                            # Update per-car best
                            bests = _sector_best_per_car.setdefault(idx, {})
                            if "S3" not in bests or s3 < bests["S3"]:
                                bests["S3"] = s3

                            # Update global best/worst
                            best, worst = _sector_best["S3"], _sector_worst["S3"]
                            if best is None or s3 < best:
                                _sector_best["S3"] = s3
                                _sector_best_car["S3"] = idx
                            if worst is None or s3 > worst:
                                _sector_worst["S3"] = s3
                                _sector_worst_car["S3"] = idx

                # After the first full rollover we can trust sector crossings
                car["tracking"] = True
                car["next_split"] = 0
                car["last_time"] = now
                car["last_pct"] = pct
                continue

            # S1/S2 updates — only once we have a clean lap-start reference
            if not car["tracking"]:
                car["last_pct"] = pct
                continue

            next_split = car["next_split"]
            if next_split < 2 and pct >= SECTOR_BOUNDARIES[next_split + 1]:
                sector_time = now - car["last_time"]
                # Sanity guard — real F1-class sectors never run under 5s
                # at any real circuit. Anything smaller is engine restart
                # jitter or a mid-sector first observation.
                if sector_time is not None and sector_time >= 5.0:
                    sector_name = f"S{next_split+1}"
                    # Update last split
                    _last_sector_splits.setdefault(idx, {})[sector_name] = sector_time
                    # Update per-car best
                    bests = _sector_best_per_car.setdefault(idx, {})
                    if sector_name not in bests or sector_time < bests[sector_name]:
                        bests[sector_name] = sector_time
                    # Update global best/worst
                    best, worst = _sector_best[sector_name], _sector_worst[sector_name]
                    if best is None or sector_time < best:
                        _sector_best[sector_name] = sector_time
                        _sector_best_car[sector_name] = idx
                    if worst is None or sector_time > worst:
                        _sector_worst[sector_name] = sector_time
                        _sector_worst_car[sector_name] = idx
                    # Only advance the split marker and time reference
                    # when the sector time is valid — otherwise we'd
                    # corrupt the next sector's measurement.
                    car["last_time"] = now
                    car["next_split"] += 1
            car["last_pct"] = pct
    except Exception as e:
        print(f"[race_data_store] update_sector_times error: {e}")

# ---------- Helpers ----------
def _format_time(t):
    return f"{t:.3f}" if t is not None else "--"

# ---------- Get Best Sector Summary for Car ----------
def get_sector_summary(car_idx: int, ir=None):
    """
    Returns a tuple of (S1, S2, S3) — the best per-car sector times.
    """
    bests = _sector_best_per_car.get(car_idx, {})
    return tuple(_format_time(bests.get(s)) for s in ("S1", "S2", "S3"))

# ---------- Sector Highlights ----------
def get_sector_highlights():
    return {
        "fastest": dict(_sector_best_car),
        "slowest": dict(_sector_worst_car)
    }

