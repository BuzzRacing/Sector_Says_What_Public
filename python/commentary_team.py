# commentary_team.py
from typing import Tuple, Dict, Any
import race_config

# --------------------------------------------------------
# Trigger settings — now loaded from race_config.json
# Access via get_trigger_settings() / get_commentators() for live values.
# Direct references kept as properties for backward compatibility.

def get_trigger_settings() -> Dict[str, Dict[str, Any]]:
    return race_config.cfg("commentary_team").get("trigger_settings", {})

def get_commentators(include_inactive: bool = False) -> Dict[str, Dict[str, Any]]:
    all_casters = race_config.cfg("commentary_team").get("commentators", {})
    if include_inactive:
        return all_casters
    return {k: v for k, v in all_casters.items() if v.get("is_active", True)}

def get_host_rotation() -> list:
    return race_config.cfg("commentary_team").get("host_rotation_quarters", ["Sienna", "Clara", "Jules", "Sienna"])

# Module-level attribute proxy via __getattr__ (Python 3.7+)
# Allows: commentary_team.commentators, commentary_team.trigger_settings, etc.
def __getattr__(name):
    if name == "trigger_settings":
        return get_trigger_settings()
    if name == "commentators":
        return get_commentators()
    if name == "host_rotation_quarters":
        return get_host_rotation()
    raise AttributeError(f"module 'commentary_team' has no attribute {name!r}")
def get_host_and_energy(elapsed_time: float, total_time: float) -> tuple[str, str, str]:
    rotation = get_host_rotation()
    casters = get_commentators()
    if total_time is None or total_time <= 0 or elapsed_time is None or elapsed_time < 0:
        host = rotation[0]
        energy = "medium"
        location = casters.get(host, {}).get("location", "Studio")
        return host, energy, location
    # Clamp elapsed
    elapsed = min(max(elapsed_time, 0.0), total_time)
    frac = elapsed / total_time  # 0.0 -> 1.0
    num_hosts = len(rotation)
    # Smooth host rotation using modulo so last host is included in final 10%
    host_pos = frac * num_hosts  # 0.0 -> num_hosts
    host_index = min(int(host_pos), num_hosts - 1)  # ensures last host is picked for frac=1.0
    host = rotation[host_index]
    # Energy: linear increase + final sprint
    base_energy = frac  # 0.0 -> 1.0
    if base_energy <= 0.25:
        energy = "medium"
    elif base_energy <= 0.5:
        energy = "medium-high"
    elif base_energy <= 0.75:
        energy = "high"
    else:
        energy = "very high"
    if frac > 0.9:
        energy = "max hype"
    location = casters.get(host, {}).get("location", "Studio")
    return host, energy, location

def get_next_commentator(current_host: str) -> str:
    """Return next commentator in rotation, default to first if unknown."""
    if current_host not in host_rotation:
        return host_rotation[0]
    idx = host_rotation.index(current_host)
    return host_rotation[(idx + 1) % len(host_rotation)]



# --- Trigger helper API used by trackers ---
import time
def _enqueue_for_host(text: str, trigger_name: str = 'incident', car_idx: int = None):
    try:
        from commentary_queue import enqueue_commentary
        times = __import__('sector_says_utilities').get_race_times(None)
        host, _ = get_host_and_energy(times.get('elapsed_seconds',0.0), times.get('total_seconds',1.0))
        enqueue_commentary({'prompt': text, 'commentator': host, 'trigger_name': trigger_name, 'clip_id': f"{trigger_name}_{int(time.time())}", 'car_idx': car_idx})
    except Exception as e:
        print(f"[commentary_team] _enqueue_for_host error: {e}")

def trigger_incident(car_idx: int, text: str):
    print(f"[TRIGGER] Incident for car {car_idx}: {text}")
    _enqueue_for_host(text, 'incident', car_idx=car_idx)

def trigger_recovery(car_idx: int, text: str):
    print(f"[TRIGGER] Recovery for car {car_idx}: {text}")
    _enqueue_for_host(text, 'recovery', car_idx=car_idx)

def trigger_multi_car_offtrack(car_list):
    print(f"[TRIGGER] Multi-car offtrack: {car_list}")
    _enqueue_for_host(f"Multiple cars off track: {', '.join(map(str,car_list))}", 'multi_offtrack')
