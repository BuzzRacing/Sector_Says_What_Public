# collision_tracker.py (cleaned and extended)
from typing import Dict, List
import sector_says_utilities
import commentary_team
import race_config
import sector_says_dotd
import time

# --- Globals ---
_collisions: Dict[int, List[Dict]] = {}        # per-car collision history
_last_contact: Dict[int, int] = {}             # last car contacted, per car to prevent double triggers
_last_incident_time: Dict[int, float] = {}     # cooldown for incidents
DEBUG = True                                   # Set to False for normal operation

# Rolling log of recent on-track incidents — appended by both
# collision_tracker (compound contact + off-track) and offtrack_tracker
# (spins / repeated off-track) — read by the flag handler so a YELLOW
# raised by iRacing can be tied back to the driver who caused it.
# Each entry: {"time": float (wall-clock), "car_idx": int,
# "car_name": str, "kind": str, "detail": str}
_recent_incidents: List[Dict] = []
_RECENT_INCIDENT_TTL_S = 30.0


def record_incident(car_idx: int, car_name: str = "", kind: str = "incident",
                    detail: str = "", when: float = None) -> None:
    """Append an entry to the recent-incident log. Auto-prunes old entries
    so the list stays bounded over a long race."""
    now = when if when is not None else time.time()
    _recent_incidents.append({
        "time": now,
        "car_idx": int(car_idx) if car_idx is not None else None,
        "car_name": car_name or "",
        "kind": kind,
        "detail": detail,
    })
    cutoff = now - _RECENT_INCIDENT_TTL_S
    while _recent_incidents and _recent_incidents[0]["time"] < cutoff:
        _recent_incidents.pop(0)


def get_recent_incidents(within_s: float = 10.0):
    """Return incidents recorded in the last `within_s` seconds, newest
    first. Used by the flag handler to attribute YELLOW raises."""
    cutoff = time.time() - within_s
    return [e for e in reversed(_recent_incidents) if e["time"] >= cutoff]

def _collision_cfg():
    return race_config.cfg("marshalling").get("collision", {})

def _cooldown():
    return _collision_cfg().get("cooldown_seconds", 30)

def _off_track_surfaces():
    return set(_collision_cfg().get("off_track_surfaces", [6, 7, 9]))

def _compound_yaw():
    return _collision_cfg().get("compound_yaw_threshold", 2.0)

def _recovery_yaw():
    return _collision_cfg().get("recovery_yaw_threshold", 3.0)

# -----------------------------
def reset_collisions():
    """Clear all collision and incident data (call at session start)."""
    global _collisions, _last_contact, _last_incident_time
    _collisions = {}
    _last_contact = {}
    _last_incident_time = {}

# -----------------------------
def update_collisions(ir, car_indices):
    """Update per-car collisions and trigger commentary events."""
    global _collisions, _last_contact, _last_incident_time

    # --- Safe telemetry retrieval with fallbacks ---
    timestamp = sector_says_utilities.safe_session_var(ir, "SessionTime") or 0.0
    contacts = sector_says_utilities.safe_session_var(ir, "CarIdxContact") or [0]*100
    yaw_values = sector_says_utilities.safe_session_var(ir, "CarIdxYaw") or [0.0]*100
    speeds = sector_says_utilities.safe_session_var(ir, "CarIdxSpeed") or [0.0]*100
    track_surfaces = sector_says_utilities.safe_session_var(ir, "CarIdxTrackSurface") or [0]*100
    flags = sector_says_utilities.safe_session_var(ir, "CarIdxF2Flags") or [0]*100

    # --- Loop through each car ---
    for idx in car_indices:
        # Safety: skip invalid indices
        if idx >= len(contacts):
            if DEBUG:
                print(f"[DEBUG Collision] Skipping invalid CarIdx {idx}")
            continue

        # --- Collision Detection ---
        contact_car = contacts[idx]
        last = _last_contact.get(idx)

        if contact_car > 0 and last != contact_car:
            _collisions.setdefault(idx, []).append({"time": timestamp, "with": contact_car})
            _last_contact[idx] = contact_car
            commentary_team.trigger_collision(idx, contact_car)
            if DEBUG:
                print(f"[DEBUG Collision] Car {idx} hit Car {contact_car} at {timestamp:.2f}s")

        elif contact_car == 0:
            _last_contact[idx] = None

        # --- Incident Trigger Cooldown ---
        last_incident = _last_incident_time.get(idx, 0)
        if timestamp - last_incident < _cooldown():
            if DEBUG:
                print(f"[DEBUG Incident] Cooldown active for Car {idx}")
            continue  # skip this car

        # --- Gather telemetry for incident detection ---
        yaw = yaw_values[idx]
        speed = speeds[idx]
        surface = track_surfaces[idx]
        flag = flags[idx]

        # --- Individual Incident Detection ---
        # Collision + off-track (grass/sand/dirt)
        if contact_car > 0 and surface in _off_track_surfaces() and abs(yaw) > _compound_yaw():
            commentary_team.trigger_incident(idx, f"Big contact for Car {idx}—they're off in the grass!")
            _last_incident_time[idx] = timestamp
            record_incident(idx, kind="contact_offtrack",
                            detail=f"contact + off-track yaw {abs(yaw):.1f}")
            # Compound signal (contact + off-track + high yaw) = driver lost
            # control into another car. Treat as at-fault for DOTD.
            try:
                sector_says_dotd.submit_trigger(
                    "caused_collision", idx,
                    meta={"with": int(contact_car), "yaw": float(yaw)},
                )
            except Exception as de:
                if DEBUG:
                    print(f"[DOTD] caused_collision submit error: {de}")
            if DEBUG:
                print(f"[DEBUG Incident] Collision + Off-track for Car {idx}")

        # Recovery attempt (car off-track but not in collision)
        elif surface in _off_track_surfaces() and abs(yaw) > _recovery_yaw():
            commentary_team.trigger_incident(idx, f"Car {idx} trying to wrestle it back onto the circuit!")
            _last_incident_time[idx] = timestamp
            record_incident(idx, kind="offtrack_recovery",
                            detail=f"off-track recovery yaw {abs(yaw):.1f}")
            if DEBUG:
                print(f"[DEBUG Incident] Recovery attempt for Car {idx}")

        # Flag reaction (meatball/yellow/blue flags)
        elif flag & 0x28:  # BLUE (0x20) or YELLOW (0x08)
            commentary_team.trigger_incident(idx, f"Mechanical trouble for Car {idx}—meatball flag shown!")
            _last_incident_time[idx] = timestamp
            record_incident(idx, kind="flag_reaction",
                            detail="meatball/yellow flag shown to driver")
            if DEBUG:
                print(f"[DEBUG Incident] Flag reaction for Car {idx}")

    # --- Multi-Car Incident Detection ---
    ot_surfaces = _off_track_surfaces()
    offtrack_cars = [i for i in car_indices if i < len(track_surfaces) and track_surfaces[i] in ot_surfaces]
    flagged_cars = [i for i in car_indices if i < len(flags) and flags[i] & 0x28]  # BLUE or YELLOW

    if len(offtrack_cars) + len(flagged_cars) >= _multi_car_threshold():
        commentary_team.trigger_multi_car_incident(offtrack_cars + flagged_cars)
        if DEBUG:
            print(f"[DEBUG Multi-Car Incident] Cars involved: {offtrack_cars + flagged_cars}")
