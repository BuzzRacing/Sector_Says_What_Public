# offtrack_tracker.py
from typing import Dict, List
import sector_says_utilities
import commentary_team
import race_config
import time

# --- Globals ---
_last_surface: Dict[int, int] = {}          # last known surface per car
_last_offtrack_time: Dict[int, float] = {}  # cooldown timer per car for off-track events
_last_spin_state: Dict[int, bool] = {}      # is car currently spinning
DEBUG = True

def _offtrack_cfg():
    return race_config.cfg("marshalling").get("offtrack", {})

def _cooldown():
    return _offtrack_cfg().get("cooldown_seconds", 30)

def _multi_car_threshold():
    return _offtrack_cfg().get("multi_car_threshold", 3)

def _offtrack_surfaces():
    return tuple(_offtrack_cfg().get("surfaces", [6, 7, 8, 9]))

def _spin_yaw():
    return _offtrack_cfg().get("spin_yaw_threshold", 5.0)

def _spin_rpm():
    return _offtrack_cfg().get("spin_rpm_threshold", 500)

# -----------------------------
def reset_offtrack_data():
    """Reset all tracking info at session start."""
    global _last_surface, _last_offtrack_time, _last_spin_state
    _last_surface = {}
    _last_offtrack_time = {}
    _last_spin_state = {}

# -----------------------------
def update_offtrack_and_spins(ir, car_indices):
    """Detect off-track events, spins, and recoveries."""
    global _last_surface, _last_offtrack_time, _last_spin_state

    timestamp = sector_says_utilities.safe_session_var(ir, "SessionTime") or 0.0
    surfaces = sector_says_utilities.safe_session_var(ir, "CarIdxTrackSurface") or [0]*100
    yaw = sector_says_utilities.safe_session_var(ir, "CarIdxYaw") or [0.0]*100
    rpm = sector_says_utilities.safe_session_var(ir, "CarIdxRPM") or [0.0]*100
    flags = sector_says_utilities.safe_session_var(ir, "CarIdxF2Flags") or [0]*100

    for idx in car_indices:
        if idx >= len(surfaces):
            continue

        cur_surface = surfaces[idx]
        last_surface = _last_surface.get(idx, 0)
        last_offtrack = _last_offtrack_time.get(idx, 0)
        spinning = _last_spin_state.get(idx, False)

        # --- Off-Track Detection ---
        if cur_surface in _offtrack_surfaces() and timestamp - last_offtrack >= _cooldown():
            commentary_team.trigger_offtrack(idx, f"Car {idx} off track on surface {cur_surface}!")
            _last_offtrack_time[idx] = timestamp
            try:
                import collision_tracker
                collision_tracker.record_incident(idx, kind="offtrack",
                                                  detail=f"surface {cur_surface}")
            except Exception:
                pass
            if DEBUG:
                print(f"[DEBUG OffTrack] Car {idx} off track on {cur_surface}")

        # --- Spin Detection ---
        is_spinning = abs(yaw[idx]) > _spin_yaw() and rpm[idx] > _spin_rpm()
        if is_spinning and not spinning:
            commentary_team.trigger_spin(idx, f"Car {idx} is spinning!")
            _last_spin_state[idx] = True
            try:
                import collision_tracker
                collision_tracker.record_incident(idx, kind="spin",
                                                  detail=f"yaw {abs(yaw[idx]):.1f}")
            except Exception:
                pass
            if DEBUG:
                print(f"[DEBUG Spin] Car {idx} started spinning")
        elif not is_spinning and spinning:
            commentary_team.trigger_recovery(idx, f"Car {idx} recovered from spin")
            _last_spin_state[idx] = False
            if DEBUG:
                print(f"[DEBUG Spin] Car {idx} recovered from spin")

        # --- Update last surface ---
        _last_surface[idx] = cur_surface

    # --- Multi-Car Off-Track Check (optional) ---
    offtrack_cars = [i for i in car_indices if i < len(surfaces) and surfaces[i] in _offtrack_surfaces()]
    if len(offtrack_cars) >= _multi_car_threshold():
        commentary_team.trigger_multi_car_offtrack(offtrack_cars)
        if DEBUG:
            print(f"[DEBUG Multi-Car OffTrack] Cars involved: {offtrack_cars}")
