"""
race_config.py — Central configuration loader for Sector Says What.

Singleton module: all tunable settings live in race_config.json.
Every module calls cfg() at trigger time (not import time) so
hot-reload via the Pit Wall admin panel works instantly.

Zero imports from other project modules (avoids circular imports).
"""

import os
import json
import copy
import threading

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "race_config.json")
_lock = threading.RLock()
_config: dict = {}

# ─── Built-in defaults ────────────────────────────────────────────
# These mirror the values that were previously hardcoded across modules.
# If race_config.json is missing a key, the default is used.

_DEFAULTS: dict = {

    # ── Commentary Team ──────────────────────────────────────────
    "commentary_team": {
        "commentators": {
            "Murray": {
                "voice": "Classic British, excitable",
                "style": "Warm, reflective, poetic",
                "energy": "high-octane hype",
                "location": "Studio",
                "think": "Murray Walker",
                "voice_id": "Ronald",
                "speaking_rate": 1.3
            },
            "Sienna": {
                "voice": "Classic British, excitable",
                "style": "Warm, reflective, poetic",
                "energy": "high-octane",
                "location": "Studio",
                "think": "Clare Balding",
                "voice_id": "Deborah",
                "speaking_rate": 1.3
            },
            "Nigel": {
                "voice": "British, dry, precise",
                "style": "Telemetry breakdowns, pitlane chaos",
                "energy": "calm",
                "location": "PitLane",
                "think": "Ted Kravitz",
                "voice_id": "Mark",
                "speaking_rate": 1.3
            },
            "Jules": {
                "voice": "fast-paced, charismatic",
                "style": "Live reactions, crowd energy",
                "energy": "high",
                "location": "FanZone",
                "think": "Salma Hayek",
                "voice_id": "Wendy",
                "speaking_rate": 1.3
            },
            "Marcus": {
                "voice": "Deep American broadcast tone",
                "style": "Insider scoops, punchy lines, tactical context",
                "energy": "medium",
                "location": "Paddock",
                "think": "Brad Pitt",
                "voice_id": "Edward",
                "speaking_rate": 1.3
            },
            "Clara": {
                "voice": "friendly and bright",
                "style": "Emotive, immersive, fan-centric",
                "energy": "electric, fearless, full of heart",
                "location": "Paddock",
                "think": "Danica Patrick",
                "voice_id": "Wendy",
                "speaking_rate": 1.3
            },
            "Theo": {
                "voice": "Older British male, refined and articulate",
                "style": "Quips, historical parallels, cultural anecdotes",
                "energy": "medium",
                "location": "PitLane",
                "think": "Omar Sy",
                "voice_id": "Craig",
                "speaking_rate": 1.3
            }
        },
        "trigger_settings": {
            "race_intro": {"style": "race intro speech after green flag", "characters": 200},
            "white_flag": {"style": "announcing last lap", "characters": 200, "cooldown": 1.0, "real_time": True},
            "checkered_flag": {"style": "end of the broadcast", "characters": 200, "cooldown": 1.0, "real_time": True},
            "overtake": {"style": "overtake in the race", "characters": 50, "cooldown": 3.0, "real_time": True},
            "fastest_lap": {"style": "new fastest lap in the race", "characters": 80, "real_time": True},
            "fastest_S1": {"style": "new fastest sector 1", "characters": 50, "real_time": True},
            "fastest_S2": {"style": "new fastest sector 2", "characters": 50, "real_time": True},
            "fastest_S3": {"style": "new fastest sector 3", "characters": 50, "real_time": True},
            "leader_change": {"style": "change in the race leader", "characters": 80, "cooldown": 1.0, "real_time": True},
            "race_update": {"style": "overview of race progress", "characters": 180, "cooldown": 1.0, "real_time": True},
            "friendly_goodbye": {"style": "goodbye driver leaving the race", "characters": 80, "cooldown": 1.0, "real_time": True, "host": "Clara"},
            "midfield_battle": {"style": "close battle in the midfield", "characters": 110, "cooldown": 55.0, "real_time": True},
            "pit_entry": {"style": "pitlane entry", "characters": 70, "cooldown": 1.0, "real_time": True, "host": "Nigel", "ambient_file": "pitlane_basic.mp3", "ambient_volume": 0.7},
            "off_track": {"style": "car off track", "characters": 80, "cooldown": 1.0, "real_time": True, "host": "Marcus"},
            "collision_event": {"style": "collision", "characters": 80, "cooldown": 1.0, "real_time": True, "host": "Marcus"},
            "spinning-off_track": {"style": "car off spun off track", "characters": 80, "cooldown": 1.0, "real_time": True, "host": "Marcus"},
            "blue_flag": {"style": "inform driver of faster car approaching", "characters": 80, "cooldown": 1.0, "real_time": True, "host": "Marcus"},
            "towed_car": {"style": "car being towed back", "characters": 80, "cooldown": 1.0, "real_time": True, "host": "Marcus"},
            "focus_driver_of_the_day": {"fractions": [0.32, 0.58, 0.84], "style": "update on owner's car", "characters": 190, "host": "Jules"},
            "fun_fact": {"fractions": [0.45, 0.70], "style": "fun fact", "characters": 150, "host": "Clara", "ambient_file": "fanzone_loop.mp3", "ambient_volume": 0.5},
            "race_prediction": {"fractions": [0.20, 0.50, 0.82], "style": "race prediction", "characters": 150, "host": "Marcus", "ambient_file": "racetrack_loop.mp3", "ambient_volume": 0.6},
            "historical": {"fractions": [0.56, 0.88], "style": "historical", "characters": 150, "host": "Theo", "ambient_file": "studio_loop.mp3", "ambient_volume": 0.4},
            "trivia_time": {"fractions": [0.40, 0.92], "style": "trivia time", "characters": 260, "host": "Clara", "ambient_file": "fanzone_loop.mp3", "ambient_volume": 0.5},
            "speed_fun_fact": {"fractions": [0.66, 0.84], "style": "speed_fun_fact", "characters": 150, "host": "Jules", "ambient_file": "fanzone_loop.mp3", "ambient_volume": 0.5}
        },
        "host_rotation_quarters": ["Sienna", "Clara", "Jules", "Sienna"]
    },

    # ── Race Control (cooldowns & intervals) ─────────────────────
    "race_control": {
        "cooldown": 30,
        "flag_cooldown": 30,
        "midfield_battle_cooldown": 260,
        "summary_race_interval": 90.0
    },

    # ── Stewards' Office (DOTD scoring) ──────────────────────────
    "dotd": {
        "triggers": {
            "become_leader":  {"points": 15,  "min_interval_s": 2, "cap_per_race": 1},
            "lost_lead":      {"points": -5,  "min_interval_s": 0.5},
            "lead_lap":       {"points": 10,  "min_interval_s": 0, "per_lap": True},
            "win_race":       {"points": 100, "min_interval_s": 0},
            "gain_position":  {"points": 10,  "min_interval_s": 0},
            "fastest_lap":    {"points": 50,  "min_interval_s": 1, "cap_per_race": 1},
            "being_passed":   {"points": -5,  "min_interval_s": 0.5}
        },
        "disabled_triggers": {
            "overtake":         {"points": 0,   "min_interval_s": 0.5, "diminishing_window_s": 30},
            "slowest_lap":      {"points": -10, "min_interval_s": 1, "cap_per_race": 1},
            "off_track":        {"points": -5,  "min_interval_s": 1},
            "caused_collision": {"points": -15, "min_interval_s": 1},
            "pit_penalty":      {"points": -20, "min_interval_s": 1},
            "lap_clean":        {"points": 5,   "min_interval_s": 0, "per_lap": True},
            "defended":         {"points": 10,  "min_interval_s": 2},
            "lapped":           {"points": -40, "min_interval_s": 2}
        },
        "cap_per_lap": 300,
        "diminishing": {
            "enabled": True,
            "decay_steps": [1.0, 0.8, 0.6, 0.4]
        }
    },

    # ── Marshalling (detection & track) ──────────────────────────
    "marshalling": {
        "sector_boundaries_range": {
            "s1": [0.30, 0.36],
            "s2": [0.63, 0.69]
        },
        "collision": {
            "cooldown_seconds": 30,
            "off_track_surfaces": [6, 7, 9],
            "compound_yaw_threshold": 2.0,
            "recovery_yaw_threshold": 3.0
        },
        "offtrack": {
            "cooldown_seconds": 30,
            "multi_car_threshold": 3,
            "surfaces": [6, 7, 8, 9],
            "spin_yaw_threshold": 5.0,
            "spin_rpm_threshold": 500
        }
    },

    # ── Power Unit (AI & TTS) ────────────────────────────────────
    "power_unit": {
        "openai": {
            "model": "gpt-4o-mini",
            "max_tokens": 250,
            "temperature": 0.5
        },
        "inworld": {
            "url": "https://api.inworld.ai/tts/v1/voice:stream",
            "model_id": "inworld-tts-1.5-mini",
            "audio_encoding": "MP3",
            "temperature": 0.79,
            "default_speaking_rate": 1.3,
            "timeout": 30,
            "enable_audio_markups": True,
            "enable_emotion_markups": True,
            "sentence_break_ms": 180,
            "max_sentence_breaks": 2,
            "min_chars_for_breaks": 90,
            "trigger_emotions": {
                "race_intro": "happy",
                "white_flag": "surprised",
                "checkered_flag": "happy",
                "overtake": "surprised",
                "leader_change": "surprised",
                "fastest_lap": "happy",
                "collision_event": "surprised",
                "off_track": "surprised",
                "friendly_goodbye": "sad",
                "focus_driver_of_the_day": "happy"
            }
        },
        # iRacing /data API (members-ng.iracing.com) — toggle-only.
        # Credentials live in python/race_secrets.json or the
        # IRACING_EMAIL / IRACING_PASSWORD env vars so they never
        # touch the hot-reloaded config file or the Pit Wall UI.
        "iracing_api": {
            "enabled": False,
            "base_url": "https://members-ng.iracing.com",
            "cache_ttl_hours": 24
        }
    },

    # ── Owner Drivers (multi-profile support) ─────────────────────
    "owner_drivers": {},

    # Legacy single-driver alias (populated from owner_drivers)
    "owner_driver": {
        "display_name": "",
        "nickname": "",
        "team_name": "",
        "bio": "",
        "iracing_customer_id": "",
        "car_number": "",
        "commentary_frequency": "normal",
        "driving_style": "",
        "featured_phrases": [],
        "profile_image": "",
        "is_active": True,
    },

    # ── System ───────────────────────────────────────────────────
    "system": {
        "audio_output_index": 0,
        "enable_tts": True
    }
}


# ─── Deep merge helper ────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    merged = copy.deepcopy(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


# ─── Public API ───────────────────────────────────────────────────

def cfg(section: str | None = None) -> dict:
    """Return the live config dict, or a specific section.

    Called at trigger time so hot-reloaded values take effect immediately.
    """
    with _lock:
        data = _config if _config else _DEFAULTS
        if section == "owner_driver":
            # Return the active owner driver from the multi-profile dict
            drivers = data.get("owner_drivers", {})
            for d in drivers.values():
                if d.get("is_active", True):
                    return d
            # Fallback to legacy single-driver key
            return data.get("owner_driver", _DEFAULTS.get("owner_driver", {}))
        if section:
            return data.get(section, _DEFAULTS.get(section, {}))
        return data


def reload() -> None:
    """Re-read race_config.json from disk and merge over defaults."""
    global _config
    with _lock:
        if os.path.exists(_CONFIG_PATH):
            try:
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    user = json.load(f)
                _config = _deep_merge(_DEFAULTS, user)
                print(f"[CONFIG] Loaded {_CONFIG_PATH}")
            except Exception as e:
                print(f"[CONFIG] Error reading {_CONFIG_PATH}: {e} — using defaults")
                _config = copy.deepcopy(_DEFAULTS)
        else:
            _config = copy.deepcopy(_DEFAULTS)
            # Write defaults to disk so the user has a file to edit
            _write_no_lock()
            print(f"[CONFIG] Created default {_CONFIG_PATH}")


def save() -> None:
    """Write the current config back to disk."""
    with _lock:
        _write_no_lock()


def _write_no_lock() -> None:
    """Internal: write _config to disk (caller must hold _lock)."""
    data = _config if _config else _DEFAULTS
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[CONFIG] Error writing {_CONFIG_PATH}: {e}")


def update_section(section: str, data: dict, replace: bool = False) -> None:
    """Merge *data* into a config section and save to disk.

    When ``replace=True`` the section is overwritten with ``data`` exactly,
    so keys that have been removed client-side actually leave the file.
    Default is still deep-merge, which is what most sections want (partial
    updates, no risk of dropping unrelated fields)."""
    global _config
    with _lock:
        if not _config:
            _config = copy.deepcopy(_DEFAULTS)
        if replace:
            _config[section] = copy.deepcopy(data)
        elif section in _config and isinstance(_config[section], dict):
            _config[section] = _deep_merge(_config[section], data)
        else:
            _config[section] = copy.deepcopy(data)
        _write_no_lock()


def defaults() -> dict:
    """Return the built-in defaults (for Reset to Defaults)."""
    return copy.deepcopy(_DEFAULTS)


# ─── Secrets (kept out of race_config.json & the Pit Wall UI) ────
#
# race_secrets.json is gitignored and never written by any UI. The
# iRacing /data API client reads it (or the IRACING_EMAIL /
# IRACING_PASSWORD env vars) at auth time. Returning an empty dict
# when missing is fine — the adapter will just stay disabled.

_SECRETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "race_secrets.json")


def secrets(section: str | None = None) -> dict:
    """Return secrets from race_secrets.json, merged with env fallbacks."""
    data: dict = {}
    try:
        if os.path.exists(_SECRETS_PATH):
            with open(_SECRETS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception as e:
        print(f"[CONFIG] Error reading {_SECRETS_PATH}: {e}")
        data = {}

    # Env fallbacks only fill in gaps — file values win if both present.
    iracing = data.get("iracing") or {}
    if not iracing.get("email"):
        env_email = os.environ.get("IRACING_EMAIL")
        if env_email:
            iracing["email"] = env_email
    if not iracing.get("password"):
        env_pw = os.environ.get("IRACING_PASSWORD")
        if env_pw:
            iracing["password"] = env_pw
    if iracing:
        data["iracing"] = iracing

    if section:
        return data.get(section, {})
    return data


# ─── Auto-load on import ─────────────────────────────────────────
reload()
