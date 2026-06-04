# ============================================================
# race_triggers.py — Cleaned, Numeric-Safe Single-File Version
# ============================================================
import time
import random
import math
import inspect
import os
import json
import csv
from datetime import datetime
from typing import Dict, Any, List
import threading
import traceback
import sector_says_utilities
import text_to_speech_integration
import commentary_team
import openai_commentary
import race_config
from sector_says_utilities import (export_commentator_images_json,format_sector_time_for_speech,normalize_telemetry_for_ai,safe_session_var,has_flag as has_flag_int,car_has_flag, FLAGS,
)
from commentary_queue import enqueue_commentary
from trigger_utils import try_trigger, can_fire, mark_fired
import race_data_store
from race_data_store import _sector_best, _sector_best_car, clean_driver_name
import collision_tracker
import offtrack_tracker
import sector_says_dotd as dotd
from sector_says_dotd import DEBUG, submit_trigger
from commentary_phrases import phrases, voice_format_lap_time as _voice_format_lap_time
from prompt_builder import get_db_prompt
from live_leaderboard import get_leaderboard_snapshot
from sector_says_dotd import submit_trigger, submit_custom_points
import race_data_store

# GLOBALS & CONSTANTS
DEBUG_MODE = False  # master debug flag

def _race_control():
    return race_config.cfg("race_control")

def _SUMMARY_RACE_INTERVAL():
    # 90s default (was 60s). Race 4 fired 26 summaries in 31 minutes, all
# collapsing to "Stefan leads ... Focus Driver P14 ..." because the gaps
    # were too short for meaningful new state to develop. 90s gives the
    # LLM something fresh to lead with each time and lets other triggers
    # (overtake, fastest_lap, battle) fill the gaps.
    return _race_control().get("summary_race_interval", 90.0)

# Rotating emphasis index for race-summary calls. Each summary nudges
# the LLM to lead with a different angle so we don't stack 25 "Stefan
# leads by Xs over P2 and P3" openings in a row. Cycles through the
# angles defined in _SUMMARY_ANGLES below.
_summary_angle_idx = 0
_SUMMARY_ANGLES = (
    "Lead with the leader's pace and gap to P2.",
    "Lead with a midfield battle (closest gap behind the leaders).",
    "Lead with the Focus Driver's race — their position swing and pace.",
    "Lead with a position-gainer storyline (biggest mover so far).",
    "Lead with the laps-led tally or DOTD voting share.",
    "Lead with the fastest-lap holder and the current race phase.",
)

_last_fastest_lap = None
_last_owner_lap = None
_last_leader_lap_for_dotd = 0
_last_leader_lap_recorded = 0
_last_summary_lap_warning = False
_last_leading_car_idx = None
_last_top10_positions: Dict[int, int] = {}
_last_in_pit: Dict[int, bool] = {}
_last_off_track: Dict[int, bool] = {}
_last_midfield_battle = None
_last_summary_lines = None
_last_summary_race_time = 0.0
_last_summary_phrase = None
_last_pit_entry_time: Dict[int, float] = {}
_last_lead_change_phrase = None
_last_overtake_phrase = None
_last_towed: Dict[int, bool] = {}
_last_goodbye_state = {}
_last_goodbye_lap = {}
_last_collision_count: Dict[int, int] = {}
_last_spin_state: Dict[int, bool] = {}
_last_spin_phrase = None
_last_recovery_phrase = None
_fractional_trigger_done: Dict[str, bool] = {}
_last_fastest_lap_triggered: Dict[int, bool] = {}
_last_position_triggered: Dict[int, bool] = {}
_last_midfield_battle_trigger: Dict[tuple, float] = {}
_last_midfield_battle_time = 0.0
_sector_trigger_done: Dict[str, bool] = {}
_last_surface: Dict[int, int] = {}
_last_pit_phrase = None
def _COOLDOWN():
    return _race_control().get("cooldown", 30)

def _FLAG_COOLDOWN():
    return _race_control().get("flag_cooldown", 30)
trigger_fired = {"white_flag": False,"yellow_flag": False,"blue_flag": False,"green_flag": False,"checkered_flag": False,"focus_driver_summary": False,}
blue_flag_last_report: Dict[int, float] = {}
yellow_flag_last_report: Dict[int, float] = {}
disable_non_checkered_triggers = False
_dotd_initialized = False
_last_closest_battle_time = 0.0
last_phrase_used: Dict[str, int] = {}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE_DIR, "sector_said_exports")
# =============================
# NUMERIC SAFETY HELPERS
def safe_int(val: Any, default: int = 0) -> int:
    """Safely coerce any value to int, avoiding str/int comparison issues."""
    try:
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, int):
            return val
        return int(float(str(val).strip()))
    except Exception:
        return default
def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely coerce any value to float."""
    try:
        if isinstance(val, (float, int)):
            return float(val)
        return float(str(val).strip())
    except Exception:
        return default
def get_lap(car: Dict[str, Any]) -> int:
    return safe_int(car.get("Lap", 0), 0)
def get_pos(car: Dict[str, Any]) -> int:
    return safe_int(car.get("LivePos", car.get("Pos", 99)), 99)
def get_delta_pos(car: Dict[str, Any]) -> int:
    return safe_int(car.get("ΔPos", car.get("DeltaPos", 0)), 0)
def get_gap_ahead(car: Dict[str, Any], default: float = 10.0) -> float:
    return safe_float(car.get("GapAhead", default), default)
def get_gap_to_leader(car: Dict[str, Any], default: float = 0.0) -> float:
    return safe_float(car.get("GapToLeader", default), default)

# =============================
# INTERNAL PHRASE / HOST HELPERS
def _choose_phrase(options: List[str], last_key: str) -> str:
    """Avoid immediate repetition of the same phrase."""
    last = globals().get(last_key)
    choices = [p for p in options if p != last] or options
    chosen = random.choice(choices)
    globals()[last_key] = chosen
    return chosen
def choose_host(trigger: str = None, exclude_host: str = None, ir=None) -> str:
    """Select a host based on current race time / rotation."""
    host = None
    if ir is not None:
        times = sector_says_utilities.get_race_times(ir)
        elapsed = times.get("elapsed_seconds", 0.0)
        total = times.get("total_seconds", 1.0)
        host, _, _ = commentary_team.get_host_and_energy(elapsed, total)
    if exclude_host and host == exclude_host:
        hosts = commentary_team.host_rotation_quarters
        try:
            idx = hosts.index(exclude_host)
            host = hosts[(idx + 1) % len(hosts)]
        except ValueError:
            host = hosts[0]
    return host or commentary_team.host_rotation_quarters[0]
def _build_prompt(trigger: str, phrase: str, host: str,
                  extra_note: str = "", chars: int = None) -> str:
    info = commentary_team.commentators.get(host, {})
    tset = commentary_team.trigger_settings.get(trigger, {})
    char_limit = chars or tset.get("characters", 200)

    tone_note = "This is a family-friendly broadcast — clean language only, no profanity or adult content. "

    return (
        f"Generate a live commentary snippet for {trigger.replace('_', ' ')}: {phrase}. "
        f"{tone_note}"
        f"{extra_note}"
        f"Think like {info.get('think', '')}, use {info.get('voice', '')} voice, "
        f"style {info.get('style', '')}. "
        f"Keep it concise — around {char_limit} characters. "
        f"Just the data, no intro phrases."
    )
def _announce(trigger: str, phrase: str, host: str, clip_id: str,
              session_info: Dict[str, Any], leaderboard: List[Dict[str, Any]]):
    """Build AI prompt and enqueue commentary."""
    info = commentary_team.commentators.get(host, {})
    location = info.get("location", "Studio")
    prompt = _build_prompt(trigger, phrase, host)
    ai_text = openai_commentary.generate_commentary(prompt)
    enqueue_commentary({
        "prompt": ai_text or phrase,
        "commentator": host,
        "location": location,
        "type": trigger,
        "clip_id": clip_id,
    })

# =============================
# MUSIC STINGS
def play_intro_with_sting():
    from text_to_speech_integration import play_sting
    threading.Thread(target=play_sting, args=("intro",), daemon=True).start()
def play_outro_with_sting():
    from text_to_speech_integration import play_sting
    threading.Thread(target=play_sting, args=("outro",), daemon=True).start()

# =============================
# RACE INTRO
# =============================
def start_race_intro(elapsed_time, total_time, ir=None, leaderboard=None):
    from text_to_speech_integration import play_sting

    # ---------------------------------------------------------
    # Early validation
    # ---------------------------------------------------------
    if not ir or not leaderboard:
        print("[INTRO] Missing session or leaderboard data.")
        return

    # ---------------------------------------------------------
    # Weekend info load
    # ---------------------------------------------------------
    weekend_info = getattr(race_data_store, "current_weekend_info", None)
    if not weekend_info:
        weekend_info = sector_says_utilities.print_weekend_info(ir)
        race_data_store.current_weekend_info = weekend_info

    track = weekend_info.get("TrackDisplayName", "Unknown Track")
    city = weekend_info.get("TrackCity", "Unknown City")
    country = weekend_info.get("TrackCountry", "Unknown Country")
    session_type = weekend_info.get("SessionInfo", {}).get("SessionType", "race")
    starters = weekend_info.get("WeekendOptions", {}).get("NumStarters", "?")
    cartype = weekend_info.get("Category", "car")

    track_moisture = sector_says_utilities.describe_track_moisture(weekend_info)

    # ---------------------------------------------------------
    # Host selection
    # ---------------------------------------------------------
    host, energy, location = commentary_team.get_host_and_energy(
        elapsed_time, total_time
    )
    cinfo = commentary_team.commentators.get(host, {})

    # ---------------------------------------------------------
    # Top 3 formatting
    # ---------------------------------------------------------
    def cname(c):
        return clean_driver_name(
            c.get("Name") or c.get("Driver") or "Unknown"
        )

    top3 = [cname(c) for c in leaderboard[:3]] if leaderboard else ["unknown drivers"]

    # ---------------------------------------------------------
    # FDOTD load
    # ---------------------------------------------------------
    fd_path = os.path.join(EXPORT_DIR, "focus_driver_of_the_day.json")
    fd_data = None

    try:
        if os.path.exists(fd_path):
            with open(fd_path, "r", encoding="utf-8") as f:
                fd_data = json.load(f)
    except Exception as e:
        print("[INTRO] Failed to load FDOTD:", e)

    fd_name = "Unknown Driver"
    fd_pos = "?"
    fd_percent = None

    if fd_data:
        target_cust_id = safe_int(fd_data.get("CustId") or fd_data.get("cust_id"), 0)
        target_car_idx = safe_int(fd_data.get("CarIdx"), -1)
        target_car_number = str(fd_data.get("CarNumber", "")).strip()
        target_name = clean_driver_name(fd_data.get("Name", "")).lower().strip()

        match = None

        for c in leaderboard:
            car_idx = safe_int(c.get("CarIdx"), -2)
            cust_id = safe_int(c.get("CustId") or c.get("cust_id"), 0)
            car_num = str(c.get("CarNumber", "")).strip()
            car_name = clean_driver_name(c.get("Name", "")).lower().strip()

            if (
                (target_cust_id and cust_id == target_cust_id)
                or (target_car_idx >= 0 and car_idx == target_car_idx)
                or (target_car_number and car_num == target_car_number)
                or (target_name and car_name == target_name)
            ):
                match = c
                break

        if match:
            fd_name = clean_driver_name(match.get("Name", "Unknown"))

            pos_val = safe_int(match.get("GridPosition") or match.get("grid_position"), 0)
            fd_pos = pos_val if pos_val > 0 else "?"

            fd_percent = safe_float(fd_data.get("DOTDPercent"), None)

    # ---------------------------------------------------------
    # Commentary lines
    # ---------------------------------------------------------
    lines_to_shuffle = [
        f"Track conditions are {track_moisture}.",
        f"Top 3 drivers: {', '.join(top3)} with a total grid of {starters} in the {cartype} class.",
        (
            f"Our focus driver today is {fd_name}, starting in P{fd_pos}. "
            f"Find a famous or interesting fact about {track} in {city}, {country} "
            f"and present it as if announcing it live during the race."
        )
    ]

    random.shuffle(lines_to_shuffle)

    # ---------------------------------------------------------
    # Style config
    # ---------------------------------------------------------
    style_cfg = commentary_team.trigger_settings.get("race_intro", {})

    voice_style = f"{cinfo.get('voice','')}".strip()
    think_style = f"{cinfo.get('think','')}".strip()
    persona_style = f"{cinfo.get('style','')}".strip()

    # ---------------------------------------------------------
    # AI Prompt — try DB template first, fall back to hardcoded
    # ---------------------------------------------------------
    db_prompt = get_db_prompt("race_intro", {
        "track_name": track, "city": city, "country": country,
        "session_type": session_type, "leader": lines_to_shuffle[0] if lines_to_shuffle else "",
        "car_count": str(len(lines_to_shuffle)),
    })
    if db_prompt:
        prompt = db_prompt
    else:
        prompt = (
            f"Generate a {style_cfg.get('style','fast-paced')} introduction by a live race commentator "
            f"for the {session_type} at {track}, {city}, {country}. "
            f"{' '.join(lines_to_shuffle)} "
            f"Think like {think_style}, use a {voice_style} voice, style {persona_style}. "
            f"Tone: dramatic, fast. ~{style_cfg.get('characters', 150)} chars."
        ).strip()

    ai_text = openai_commentary.generate_commentary(prompt)

    # ---------------------------------------------------------
    # Playback & queue
    # ---------------------------------------------------------
    channel = play_sting("intro")
    time.sleep(2)

    enqueue_commentary({
        "prompt": ai_text,
        "source_prompt": prompt,
        "commentator": host,
        "location": location,
        "trigger_name": "Race Introduction",
        "type": "intro",
        "clip_id": f"intro_{int(time.time())}"
    })

# =============================
# FLAG TRIGGERS
# =============================
def white_flag_final_lap(elapsed_time, total_time, ir=None,
                         leaderboard=None, session_flags=None):
    """
    Fires once when the WHITE flag is active.
    Includes DOTD summary, Focus Driver update, gap context, podium framing.
    """
    from sector_says_dotd import get_dotd_summary
    global trigger_fired, disable_non_checkered_triggers

    # ---------------------------------------------------------
    # Safeguards
    # ---------------------------------------------------------
    if trigger_fired.get("white_flag"):
        return

    if not ir or not leaderboard:
        if DEBUG_MODE:
            print("[WHITE FLAG] Missing IR or leaderboard")
        return

    # Resolve session flags safely
    if not isinstance(session_flags, int):
        session_flags = safe_session_var(ir, "SessionFlags", 0) or 0

    if not has_flag_int(session_flags, "WHITE"):
        return

    # ---------------------------------------------------------
    # Freeze fractional triggers
    # ---------------------------------------------------------
    disable_non_checkered_triggers = True
    for key in _fractional_trigger_done.keys():
        _fractional_trigger_done[key] = True

    # ---------------------------------------------------------
    # Weekend info
    # ---------------------------------------------------------
    weekend_info = (
        race_data_store.current_weekend_info
        or sector_says_utilities.print_weekend_info(ir)
        or {}
    )
    track = weekend_info.get("TrackDisplayName", "this track")
    city = weekend_info.get("TrackCity", "")
    country = weekend_info.get("TrackCountry", "")

    # ---------------------------------------------------------
    # Host selection
    # ---------------------------------------------------------
    host, energy, location = commentary_team.get_host_and_energy(
        elapsed_time, total_time
    )

    # ---------------------------------------------------------
    # Leader + Top3
    # ---------------------------------------------------------
    top3 = leaderboard[:3]
    top3_names = [
        clean_driver_name(c.get("Name") or c.get("Driver") or "Unknown")
        for c in top3
    ]
    leader_name = top3_names[0] if top3_names else "the race leader"

    # ---------------------------------------------------------
    # Gap to P2
    # ---------------------------------------------------------
    gap_phrase = ""
    if len(leaderboard) >= 2:
        p2 = leaderboard[1]

        raw_gap = p2.get("GapToLeader")
        if raw_gap in (None, "", 0):
            raw_gap = p2.get("GapAhead")

        gap_val = safe_float(raw_gap, None)
        if isinstance(gap_val, (float, int)) and gap_val is not None:
            try:
                gap_phrase = sector_says_utilities.voice_format_gap(gap_val) or "a small gap"
            except Exception:
                gap_phrase = "a small gap"

    # ---------------------------------------------------------
    # DOTD + FDOTD
    # ---------------------------------------------------------
    dotd_info = get_dotd_summary() or {}
    dotd_top3 = dotd_info.get("top3", [])
    focus_driver = dotd_info.get("owner")

    dotd_lines = []

    # DOTD standings — spell "percent" in words so the LLM doesn't
    # turn it into seconds / gaps.
    if dotd_top3:
        try:
            formatted = ", ".join(
                f"{c['name']} at {safe_float(c.get('percent'), 0.0):.1f} percent"
                for c in dotd_top3
            )
        except Exception:
            formatted = ", ".join(
                f"{c.get('name','?')} at {c.get('percent','?')} percent"
                for c in dotd_top3
            )
        dotd_lines.append(
            f"Driver of the Day voting share (voter percentages, NOT gaps, NOT seconds): {formatted}."
        )

    # FDOTD line
    if focus_driver:
        try:
            pct = safe_float(focus_driver.get("percent"), 0.0)
            pos = focus_driver.get("pos", "?")
            name = focus_driver.get("name", "?")
            dotd_lines.append(
                f"Focus Driver of the Day: {name} ({pct:.1f}%) running in P{pos}."
            )
        except Exception:
            dotd_lines.append(
                f"Focus Driver of the Day: {focus_driver.get('name','?')} running in "
                f"P{focus_driver.get('pos','?')}."
            )

    # ---------------------------------------------------------
    # Build commentary text
    # ---------------------------------------------------------
    parts = [
        f"The white flag is out at {track}. One lap to go."
    ]

    # Leader + gap call
    if gap_phrase:
        rival = clean_driver_name(
            leaderboard[1].get("Name") or leaderboard[1].get("Driver") or "P2"
        )
        parts.append(
            f"{leader_name} leads into the final lap, with {rival} {gap_phrase} behind."
        )
    else:
        parts.append(f"{leader_name} leads into the final lap.")

    # Podium framing
    if len(top3_names) >= 3:
        parts.append(f"Top three: {top3_names[0]}, {top3_names[1]}, and {top3_names[2]}.")
    elif len(top3_names) == 2:
        parts.append(f"Up front it's {top3_names[0]} and {top3_names[1]}.")
    elif len(top3_names) == 1:
        parts.append(f"Up front it's {top3_names[0]}.")

    # Add DOTD lines
    parts.extend(dotd_lines)

    parts.append(
        "Everything is on the line as they complete this final circuit — dramatic and fast commentary tone."
    )

    final_prompt = " ".join(parts)

    # ---------------------------------------------------------
    # Generate AI commentary — try DB template first
    # ---------------------------------------------------------
    rival_name = clean_driver_name(leaderboard[1].get("Name") or leaderboard[1].get("Driver") or "P2") if len(leaderboard) > 1 else ""
    db_p = get_db_prompt("white_flag", {
        "track_name": track, "leader": leader_name, "rival": rival_name,
        "gap": gap_phrase or "", "p1": top3_names[0] if len(top3_names)>0 else "",
        "p2": top3_names[1] if len(top3_names)>1 else "", "p3": top3_names[2] if len(top3_names)>2 else "",
    })
    if db_p:
        final_prompt = db_p
    ai_text = openai_commentary.generate_commentary(final_prompt)

    # ---------------------------------------------------------
    # Dispatch
    # ---------------------------------------------------------
    enqueue_commentary({
        "prompt": ai_text or final_prompt,
        "source_prompt": final_prompt,
        "commentator": host,
        "location": location,
        "type": "white_flag",
        "trigger_name": "White Flag Last Lap",
        "clip_id": f"white_flag_{int(time.time())}",
    })

    trigger_fired["white_flag"] = True

    if DEBUG_MODE:
        print("[WHITE FLAG] Fired final-lap commentary.")
        print("[WHITE FLAG Prompt]", final_prompt)
        print("[WHITE FLAG Output]", ai_text)


# ============================================================
# FOCUS DRIVER FINAL-LAP SUMMARY
# Slots in once between WHITE and CHECKERED so the player gets a
# personal send-off — grid → current position, pace, DOTD vote share,
# clean-vs-incident framing. Gated by the white-flag lull (invariant
# 1.11) AND by the queue's _is_playing flag (invariant 1.5) so it
# follows the white-flag audio without overlap.
# ============================================================
def _official_result_int_for_car(ir, car_idx, field_name):
    """Read an integer field from iRacing race ResultsPositions."""
    if ir is None or car_idx is None:
        return None
    try:
        sinfo = safe_session_var(ir, "SessionInfo", {}) or {}
        sessions = sinfo.get("Sessions", []) if isinstance(sinfo, dict) else []
        for session in sessions:
            if (session.get("SessionType") or "").lower() != "race":
                continue
            for rp in (session.get("ResultsPositions") or []):
                try:
                    if int(rp.get("CarIdx")) != int(car_idx):
                        continue
                    if field_name not in rp:
                        return None
                    return safe_int(rp.get(field_name), 0)
                except (TypeError, ValueError):
                    continue
            break
    except Exception:
        pass
    return None


def _official_laps_led_for_car(ir, car_idx):
    """Read iRacing's official ResultsPositions LapsLed for one car.

    The live leaderboard has a local laps-led tracker for HUD display, but
    final-lap commentary should only speak an official value. If iRacing
    has not populated the field, return None and omit the claim.
    """
    return _official_result_int_for_car(ir, car_idx, "LapsLed")


def _player_incident_count(ir, car_idx):
    """Return the player's live incident count when this car is the player."""
    if ir is None or car_idx is None:
        return None
    try:
        player_idx = safe_session_var(ir, "PlayerCarIdx", None)
        if player_idx is not None and safe_int(player_idx, -1) != safe_int(car_idx, -2):
            return None
    except Exception:
        pass
    try:
        val = safe_session_var(ir, "PlayerCarMyIncidentCount", None)
        if val is None:
            return None
        return safe_int(val, 0)
    except Exception:
        return None


def focus_driver_white_flag_summary(elapsed_time, total_time,
                                    ir=None, leaderboard=None):
    global trigger_fired
    if trigger_fired.get("focus_driver_summary"):
        return
    if not trigger_fired.get("white_flag"):
        return
    if not ir or not leaderboard:
        return

    fd = next((c for c in leaderboard
               if isinstance(c, dict) and c.get("Owner")), None)
    if not fd:
        return

    name = clean_driver_name(fd.get("Name") or fd.get("Driver") or "Focus Driver")
    cur_pos = safe_int(fd.get("LivePos"))
    car_idx = safe_int(fd.get("CarIdx"))

    grid_pos = None
    try:
        grid_map = race_data_store.get_starting_grid() or {}
        grid_pos = grid_map.get(car_idx)
        if grid_pos is None:
            grid_pos = grid_map.get(str(car_idx))
        grid_pos = safe_int(grid_pos, 0) or None
    except Exception:
        grid_pos = None
    if grid_pos is None:
        grid_pos = safe_int(fd.get("GridPosition") or fd.get("grid_position"), 0) or None
    if grid_pos is None and cur_pos:
        delta_fallback = safe_int(fd.get("ΔPos") or fd.get("DeltaPos") or 0)
        grid_pos = cur_pos + delta_fallback
    delta = (grid_pos - cur_pos) if grid_pos and cur_pos else 0

    best_lap = safe_float(fd.get("Best"), 0.0) or 0.0
    official_laps_led = _official_laps_led_for_car(ir, car_idx)
    laps_led = official_laps_led if official_laps_led is not None else 0
    player_incidents = _player_incident_count(ir, car_idx)
    official_incidents = _official_result_int_for_car(ir, car_idx, "Incidents")
    live_incidents = safe_int(fd.get("LiveIncidents") or fd.get("Incidents") or 0)
    if player_incidents is not None:
        incidents = player_incidents
    elif official_incidents is not None:
        incidents = official_incidents
    else:
        incidents = live_incidents

    # DOTD vote share for this car. RaceScore.percent is set by
    # _export_top3 every time a DOTD event fires, so by white-flag time
    # it reflects the current standings.
    fd_percent = 0.0
    try:
        from sector_says_dotd import dotd as _dotd_mgr
        rs = _dotd_mgr.scores.get(car_idx)
        if rs is not None:
            fd_percent = float(getattr(rs, "percent", 0.0) or 0.0)
    except Exception:
        pass

    parts = [f"Final lap for {name}."]

    if grid_pos and cur_pos:
        if delta > 0:
            parts.append(
                f"{name} started P{grid_pos}, currently running P{cur_pos} — "
                f"up {delta} place{'s' if delta != 1 else ''}."
            )
        elif delta < 0:
            parts.append(
                f"{name} started P{grid_pos}, currently P{cur_pos} — "
                f"down {abs(delta)} place{'s' if abs(delta) != 1 else ''}."
            )
        else:
            parts.append(f"{name} started P{grid_pos} and holds P{cur_pos}.")
    elif cur_pos:
        parts.append(f"{name} running in P{cur_pos}.")
    else:
        parts.append(f"A word on {name}.")

    if best_lap > 0:
        try:
            blap_phrase = sector_says_utilities.voice_format_lap_time(best_lap)
            parts.append(f"Best lap: {blap_phrase}.")
        except Exception:
            pass

    if laps_led > 0:
        parts.append(
            f"Led {laps_led} lap{'s' if laps_led != 1 else ''} of the race."
        )

    if incidents == 0:
        parts.append("Clean run — no incidents.")
    elif incidents <= 2:
        parts.append(
            f"Just {incidents} incident point{'s' if incidents != 1 else ''} on the card."
        )
    else:
        parts.append(
            f"{incidents} incident points on the card — a busy afternoon."
        )

    if fd_percent > 0:
        parts.append(
            f"Driver of the Day vote share: {fd_percent:.1f} percent."
        )

    parts.append("One last circuit to bring it home.")

    final_text = " ".join(parts)

    host, energy, location = commentary_team.get_host_and_energy(
        elapsed_time, total_time
    )

    enqueue_commentary({
        "prompt": final_text,
        "source_prompt": final_text,
        "commentator": host,
        "location": location,
        "type": "focus_driver_summary",
        "trigger_name": "Focus Driver Final-Lap Summary",
        "clip_id": f"focus_driver_summary_{int(time.time())}",
    })

    trigger_fired["focus_driver_summary"] = True

    if DEBUG_MODE:
        print("[FD SUMMARY] Fired focus-driver final-lap summary.")
        print("[FD SUMMARY Prompt]", final_prompt)
        print("[FD SUMMARY Output]", ai_text)


# ============================================================
# CHECKERED FLAG
# ============================================================
def checkered_flag_commentary(elapsed_time, total_time,
                              ir=None, leaderboard=None, session_flags=None):
    from sector_says_dotd import get_dotd_summary
    global trigger_fired

    trigger_fired.setdefault("checkered_flag", False)
    if trigger_fired["checkered_flag"]:
        return

    # ---------------------------------------------------------
    # Sanity checks
    # ---------------------------------------------------------
    if not ir or not leaderboard:
        if DEBUG_MODE:
            print("[CHECKERED FLAG] Missing IR or leaderboard.")
        return

    # Resolve flags
    if not isinstance(session_flags, int):
        session_flags = safe_session_var(ir, "SessionFlags", 0) or 0

    if not has_flag_int(session_flags, "CHECKERED"):
        return

    # ---------------------------------------------------------
    # Timing fallback (safe)
    # ---------------------------------------------------------
    elapsed = 0.0
    total = 0.0
    try:
        times = sector_says_utilities.get_race_times(ir)
        if times:
            elapsed = times.get("elapsed_seconds", 0.0)
            total = times.get("total_seconds", 0.0)
    except Exception as e:
        if DEBUG_MODE:
            print(f"[CHECKERED FLAG] Timing fallback: {e}")

    # ---------------------------------------------------------
    # Weekend info
    # ---------------------------------------------------------
    weekend_info = (
        race_data_store.current_weekend_info
        or sector_says_utilities.print_weekend_info(ir)
        or {}
    )

    # Ensure persistent copy is stored
    if race_data_store.current_weekend_info is None:
        race_data_store.set_current_weekend_info(weekend_info)

    # Track metadata
    track = weekend_info.get("TrackDisplayName", "track")
    city = weekend_info.get("TrackCity") or ""
    country = weekend_info.get("TrackCountry") or ""
    session_type = weekend_info.get("SessionInfo", {}).get("SessionType", "race")

    # ---------------------------------------------------------
    # Host info
    # ---------------------------------------------------------
    host, energy, location = commentary_team.get_host_and_energy(
        elapsed_time, total_time
    )

    # ---------------------------------------------------------
    # Podium
    # ---------------------------------------------------------
    def cname(c):
        return clean_driver_name(c.get("Name") or c.get("Driver") or "Unknown")

    top3 = [cname(c) for c in leaderboard[:3]] if leaderboard else []
    winner = top3[0] if top3 else "the race leader"

    # ---------------------------------------------------------
    # DOTD summary
    # ---------------------------------------------------------
    dotd_info = get_dotd_summary() or {}
    dotd_top3 = dotd_info.get("top3", [])
    focus_driver = dotd_info.get("owner")

    dotd_lines = []

    # DOTD standings table — same verbose phrasing as the mid-race
    # block; prevents the LLM from rewriting percent values as seconds.
    if dotd_top3:
        try:
            formatted = ", ".join(
                f"{c['name']} at {safe_float(c.get('percent'), 0.0):.1f} percent"
                for c in dotd_top3
            )
        except Exception:
            formatted = ", ".join(
                f"{c.get('name','?')} at {c.get('percent','?')} percent"
                for c in dotd_top3
            )
        dotd_lines.append(
            f"Driver of the Day voting share (voter percentages, NOT gaps, NOT seconds): {formatted}."
        )

    # FDOTD result
    if focus_driver:
        try:
            pct = safe_float(focus_driver.get("percent"), 0.0)
            pos = focus_driver.get("pos", "?")
            name = focus_driver.get("name", "?")
            dotd_lines.append(
                f"Focus Driver of the Day: {name} ({pct:.1f}%) finishing in P{pos}."
            )
        except Exception:
            dotd_lines.append(
                f"Focus Driver of the Day: {focus_driver.get('name','?')} "
                f"({focus_driver.get('percent','?')}%) finishing in "
                f"P{focus_driver.get('pos','?')}."
            )

    # ---------------------------------------------------------
    # Line pool (cleaned)
    # ---------------------------------------------------------
    finish_lines = [
        f"The checkered flag waves at {track}! {winner} takes the win!",
        f"{winner} crosses the line first — the race is over here at {track}, {city}, {country}!",
        f"The checkered flag flies over {track}! {winner} wins the {session_type}!",
        f"{winner} takes victory at {track}! What a race here in {city}, {country}!",
    ]

    base_line = random.choice(finish_lines)

    # ---------------------------------------------------------
    # Build full prompt
    # ---------------------------------------------------------
    parts = [
        base_line,
        f"A thrilling end to the {session_type} at {track}.",
    ]

    # Podium digest
    if len(top3) >= 3:
        parts.append(f"Top three across the line: {top3[0]}, {top3[1]}, and {top3[2]}.")
    elif len(top3) == 2:
        parts.append(f"Top two finishers: {top3[0]} and {top3[1]}.")
    elif len(top3) == 1:
        parts.append(f"The winner today: {top3[0]}.")

    parts.extend(dotd_lines)

    parts.append(
        "Tone: triumphant, emotional, full of energy — perfect for a live race finish."
    )

    full_prompt = " ".join(parts)

    # ---------------------------------------------------------
    # AI commentary — try DB template first
    # ---------------------------------------------------------
    db_p = get_db_prompt("checkered_flag", {
        "track_name": track, "city": city, "country": country,
        "winner": winner, "session_type": session_type,
        "p1": top3[0] if len(top3)>0 else "", "p2": top3[1] if len(top3)>1 else "",
        "p3": top3[2] if len(top3)>2 else "",
    })
    if db_p:
        full_prompt = db_p
    ai_text = openai_commentary.generate_commentary(full_prompt)

    enqueue_commentary({
        "prompt": ai_text or full_prompt,
        "commentator": host,
        "location": location,
        "type": "checkered_flag",
        "trigger_name": "Checkered Flag",
        "clip_id": f"checkered_flag_{int(time.time())}",
    })

    trigger_fired["checkered_flag"] = True

    if DEBUG_MODE:
        print("[CHECKERED FLAG Prompt]", full_prompt)
        print("[CHECKERED FLAG Output]", ai_text)

# ============================================================
# SHUTDOWN TIMER (3 minutes post-checkered)
# ============================================================
def delayed_shutdown(leaderboard=None):
    print("[CHECKERED FLAG] 3 minutes passed, performing final race shutdown now.")

    # ---------------------------------------------------------
    # Weekend info / safe track name
    # ---------------------------------------------------------
    weekend_info_local = race_data_store.current_weekend_info or {}
    track_name = weekend_info_local.get("TrackDisplayName", "unknown_track")

    safe_track = (
        str(track_name)
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .lower()
    )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    export_dir = "sector_said_exports"
    os.makedirs(export_dir, exist_ok=True)

    # =========================================================
    # 1. EXPORT LEADERBOARD CSV
    # =========================================================
    try:
        leaderboard_filename = f"leaderboard_{timestamp}_{safe_track}.csv"
        leaderboard_path = os.path.join(export_dir, leaderboard_filename)

        # Determine fields safely
        if leaderboard and len(leaderboard) > 0:
            fieldnames = list(leaderboard[0].keys()) + ["DOTD_Score"]
        else:
            leaderboard = []
            fieldnames = ["CarIdx", "DOTD_Score"]

        with open(leaderboard_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for car in leaderboard:
                row = dict(car)
                car_idx = car.get("CarIdx")

                try:
                    score = dotd.get_score(car_idx) if car_idx is not None else 0.0
                except Exception:
                    score = 0.0

                row["DOTD_Score"] = score
                writer.writerow(row)

        print(f"[CHECKERED FLAG] Leaderboard exported: {leaderboard_path}")

    except Exception as e:
        print(f"[CHECKERED FLAG] Failed to export leaderboard CSV: {e}")

    # =========================================================
    # 2. EXPORT DOTD CSV
    # =========================================================
    try:
        dotd_filename = f"dotd_leaderboard_{timestamp}_{safe_track}.csv"
        dotd_path = os.path.join(export_dir, dotd_filename)

        csv_text = dotd.export_csv()

        with open(dotd_path, "w", newline="", encoding="utf-8") as f:
            f.write(csv_text)

        print(f"[CHECKERED FLAG] DOTD leaderboard exported: {dotd_path}")

    except Exception as e:
        print(f"[CHECKERED FLAG] Failed to export DOTD CSV: {e}")

    # =========================================================
    # 3. DO **NOT** start another timer! (prevents infinite loop)
    # =========================================================
    print("[CHECKERED FLAG] Final shutdown complete.")

# ============================================================
# FRACTIONAL TRIGGER ENGINE
# ============================================================
def choose_phrase(trigger_name,leaderboard=None,race_data_store=None,track_info=None,host="Commentator",DEBUG=False):
    # Try database templates first
    ti = track_info or {}
    db_ctx = {
        "track": ti.get("TrackDisplayName", ti.get("track_name", "")),
        "track_name": ti.get("TrackDisplayName", ti.get("track_name", "")),
        "city": ti.get("TrackCity", ti.get("city", "")),
        "country": ti.get("TrackCountry", ti.get("country", "")),
        "track_length": ti.get("TrackLengthDisplay", ti.get("track_length", "")),
    }
    # Add weather if available
    for wk in ("air_temp", "track_temp", "humidity", "skies"):
        if wk in ti:
            db_ctx[wk] = str(ti[wk])
    # Add leaderboard context
    if leaderboard and isinstance(leaderboard, list) and len(leaderboard) > 0:
        leader = leaderboard[0]
        db_ctx["leader"] = clean_driver_name(leader.get("Name", leader.get("Driver", "")))
        db_ctx["standings_summary"] = ", ".join(
            f"P{c.get('LivePos','?')} {clean_driver_name(c.get('Name', c.get('Driver','?')))}"
            for c in leaderboard[:5]
        )
        # Best/last lap from the leader for {best_lap} and speed calculations
        _best_raw = leader.get("Best") or leader.get("Last")
        try:
            if _best_raw not in (None, "", "None", -1, "-1", 0, "0"):
                _best_s = float(_best_raw)
                db_ctx["best_lap"] = _voice_format_lap_time(_best_s)
                _track_len_raw = str(db_ctx.get("track_length", ""))
                import re as _re
                _km_m = _re.search(r"([0-9]+(?:\.[0-9]+)?)\s*km", _track_len_raw.lower())
                _mi_m = _re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mi", _track_len_raw.lower()) if not _km_m else None
                _len_km = float(_km_m.group(1)) if _km_m else (float(_mi_m.group(1)) * 1.60934 if _mi_m else 0)
                if _len_km > 0:
                    db_ctx["avg_speed"] = f"{_len_km / (_best_s / 3600.0):.0f} km/h"
        except Exception:
            pass

    # Add Focus Driver context for DB templates that use {driver},
    # {position}, {gap}, {momentum} placeholders.
    #
    # IMPORTANT: the FDOTD JSON is a stale snapshot taken when the Focus
    # Driver was picked (pre-green, before the starting grid was even
    # recorded). Its LivePos / ΔPos / GapToLeader / OnPitRoad values MUST
    # NOT be used as live data — they'll say "down 20 places from grid"
    # for a driver who started last. Only use the JSON to identify WHO
    # the Focus Driver is; read their live state from the leaderboard.
    try:
        _fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
        _fd = {}
        if os.path.exists(_fd_path):
            with open(_fd_path, "r", encoding="utf-8") as _ff:
                _fd = json.load(_ff) or {}

        _fd_name = clean_driver_name(_fd.get("Name", ""))
        _fd_idx = _fd.get("CarIdx")
        _fd_num = str(_fd.get("CarNumber") or "").strip()
        _fd_name_lower = _fd_name.strip().lower()

        _fd_car = None
        if leaderboard and isinstance(leaderboard, list):
            for _c in leaderboard:
                if not isinstance(_c, dict):
                    continue
                if _fd_idx is not None and _c.get("CarIdx") == _fd_idx:
                    _fd_car = _c
                    break
                if _fd_num and str(_c.get("CarNumber") or "").strip() == _fd_num:
                    _fd_car = _c
                    break
                if _fd_name_lower and clean_driver_name(_c.get("Name", "")).strip().lower() == _fd_name_lower:
                    _fd_car = _c
                    break

        if _fd_name:
            db_ctx["driver"] = _fd_name
            db_ctx["focus_driver"] = _fd_name

        if _fd_car:
            _fd_live_pos = _fd_car.get("LivePos") or _fd_car.get("Pos")
            if _fd_live_pos:
                db_ctx["position"] = str(_fd_live_pos)
            _fd_gap_live = _fd_car.get("GapToLeader")
            try:
                if _fd_gap_live not in (None, "", 0, "0"):
                    db_ctx["gap"] = f"{float(_fd_gap_live):.1f}s"
            except Exception:
                pass
            _fd_delta = _fd_car.get("ΔPos", _fd_car.get("DeltaPos", 0))
            try:
                _fd_delta = int(_fd_delta)
            except Exception:
                _fd_delta = 0
            _on_pit = bool(_fd_car.get("OnPitRoad"))
            if _on_pit:
                db_ctx["momentum"] = "currently on pit road"
            elif _fd_delta > 0:
                db_ctx["momentum"] = f"gaining — up {_fd_delta} places from grid"
            elif _fd_delta < 0:
                db_ctx["momentum"] = f"losing — down {abs(_fd_delta)} places from grid"
            else:
                db_ctx["momentum"] = "holding their grid position"
        else:
            db_ctx.setdefault("position", "?")
            db_ctx.setdefault("gap", "unknown")
            db_ctx["momentum"] = "running their own race"
    except Exception:
        pass
    db_prompt = get_db_prompt(trigger_name, db_ctx)
    if db_prompt:
        # Safety net: if the resolved prompt still contains raw {placeholders}
        # the AI will echo them back verbatim.  Fall through to the Python
        # phrase generator which builds prompts from live data instead.
        import re as _re_check
        if _re_check.search(r"\{[a-z_]+\}", db_prompt):
            if DEBUG:
                print(f"[choose_phrase] DB template for '{trigger_name}' has "
                      f"unresolved placeholders — falling through to phrase generator")
        else:
            if DEBUG:
                print(f"[choose_phrase] Using DB template for '{trigger_name}'")
            return [db_prompt]

    entry = phrases.get(trigger_name)
    if not entry:
        if DEBUG:
            print(f"[choose_phrase] No phrase generator for '{trigger_name}'")
        return []
    candidate_kwargs = {
        "leaderboard": leaderboard,
        "race_data_store_module": race_data_store,
        "race_data_module": race_data_store,
        "race_data_store": race_data_store,
        "track_info": track_info,
        "host": host,
        "DEBUG": DEBUG,
    }
    try:
        sig = inspect.signature(entry)
        accepted = {
            name: candidate_kwargs[name]
            for name in sig.parameters
            if name in candidate_kwargs
        }
        if DEBUG:
            print(f"[choose_phrase] Calling '{trigger_name}' with {list(accepted.keys())}")
        options = entry(**accepted)
    except Exception as e:
        if DEBUG:
            print(f"[choose_phrase] Error generating '{trigger_name}': {e}")
        return []
    if not options:
        if DEBUG:
            print(f"[choose_phrase] '{trigger_name}' returned no options.")
        return []
    if isinstance(options, str):
        options = [options]

    if not isinstance(options, list):
        if DEBUG:
            print(f"[choose_phrase] Unexpected return type: {type(options)}")
        return []

    last_index = last_phrase_used.get(trigger_name, -1)
    next_index = (last_index + 1) % len(options)
    last_phrase_used[trigger_name] = next_index

    chosen = options[next_index]
    if isinstance(chosen, list):
        return chosen

    return [chosen]


def run_fractional_triggers(progress,leaderboard,session_info,state,ir,track_info=None,DEBUG=False):

    EPSILON = 0.001
    events = []
    now = time.time()

    weekend_info = (
        track_info
        or getattr(race_data_store, "current_weekend_info", {})
        or {}
    )
    # Guards
    if not isinstance(leaderboard, list) or len(leaderboard) < 1:
        return events
    if not isinstance(session_info, dict):
        session_info = {}

    # ── Rate limit: fire at most ONE fractional trigger per call ─────
    # When progress jumps (e.g. short AI race), many fractions become
    # eligible at once. Firing them all floods the commentary queue
    # with a dozen back-to-back items.  Pick the single lowest
    # un-fired fraction each tick; the rest will fire on later ticks.
    fired_this_tick = False

    # Iterate triggers
    for trigger_name, settings in commentary_team.trigger_settings.items():
        if fired_this_tick:
            break
        # skip if locked out
        if (disable_non_checkered_triggers
                and trigger_name not in ("checkered_flag", "white_flag")):
            if DEBUG:
                print(f"[FRACTIONAL] Skipping {trigger_name} due to lockout.")
            continue
        fractions = settings.get("fractions")
        if not fractions:
            continue
        for frac in sorted(fractions):
            if fired_this_tick:
                break
            key = f"{trigger_name}_{frac}"
            # already fired?
            if state.get(key, False):
                continue

            # not reached yet?
            if progress + EPSILON < frac:
                continue

            # choose host
            try:
                main_host, energy, location = commentary_team.get_host_and_energy(
                    elapsed_time=session_info.get("ElapsedTime", 0.0),
                    total_time=session_info.get("TotalTime", 1.0),
                )
            except Exception:
                main_host, energy, location = ("Default", 1.0, "Studio")

            configured_host = settings.get("host")
            commentator = configured_host if configured_host else main_host

            commentator_cfg = commentary_team.commentators.get(commentator, {})
            location = commentator_cfg.get("location", location)

            if DEBUG:
                print(f"[FRACTIONAL] Preparing '{trigger_name}' @ {frac}")

            try:
                final_lines = choose_phrase(
                    trigger_name,
                    leaderboard=leaderboard,
                    race_data_store=race_data_store,
                    track_info=weekend_info,
                    host=commentator,
                    DEBUG=DEBUG
                )
            except Exception as e:
                if DEBUG:
                    print(f"[FRACTIONAL] Phrase error for {trigger_name}: {e}")
                final_lines = []

            if not final_lines:
                if DEBUG:
                    print(f"[FRACTIONAL] No phrase options for '{trigger_name}'")
                continue

            phrase_text = " ".join(str(line) for line in final_lines)

            trigger_chars = settings.get("characters")
            if trigger_chars:
                phrase_text += f" Keep commentary to {trigger_chars} characters."

            try:
                normalized_text = normalize_telemetry_for_ai(phrase_text)
            except Exception:
                normalized_text = phrase_text

            try:
                ai_text = openai_commentary.generate_commentary(normalized_text)
            except Exception as e:
                if DEBUG:
                    print(f"[FRACTIONAL] AI error for '{trigger_name}': {e}")
                ai_text = phrase_text
            clip_id = f"{trigger_name}_{int(frac * 100)}_{time.time_ns()}"
            try:
                enqueue_commentary({
                    "prompt": ai_text,
                    "commentator": commentator,
                    "location": location,
                    "type": trigger_name,
                    "trigger_name": trigger_name,
                    "clip_id": clip_id,
                })
                state[key] = now
                events.append({"trigger": trigger_name, "clip_id": clip_id})
                fired_this_tick = True
            except Exception as e:
                if DEBUG:
                    print(f"[FRACTIONAL] enqueue failed for {trigger_name}: {e}")

    return events

# ============================================================
# LEADERBOARD UTILITIES (SELF-CONTAINED + SAFE)
# ============================================================
def _normalize_leaderboard_list(leaderboard):
    if not leaderboard:
        return []
    if isinstance(leaderboard, list):
        return leaderboard
    if isinstance(leaderboard, dict):
        return (
            leaderboard.get("cars")
            or leaderboard.get("Cars")
            or []
        )
    return []

def _find_leader_car(cars):
    if not cars:
        return None
    def safe_pos(c):
        # IMPORTANT: Retrieve raw value WITHOUT "or" chain,
        # because 0 is a valid value and must NOT be skipped.
        raw = c.get("LivePos")
        if raw is None:
            raw = c.get("Position")
        if raw is None:
            raw = c.get("Pos")
        if raw is None:
            raw = c.get("ClassPosition")

        try:
            v = int(raw)
        except:
            return 999999   # invalid

        # Reject nonsense values like negatives or huge spikes
        if v <= 0:
            return 999999
        return v

    # Pick car with smallest valid position
    leader = min(cars, key=lambda c: safe_pos(c))
    pos = safe_pos(leader)

    if pos >= 999999:
        return None

    return leader

def check_periodic_race_summary(leaderboard, ir, session_info,elapsed_time, total_time):
    import race_data_store
    #print("\n===== SUMMARY DEBUG ENTERED =====")
    #print("green_flag_time:", race_data_store.green_flag_time)
    #print("last_summary_time:", race_data_store.last_summary_race_time)
    try:
        # Abort until green flag
        if race_data_store.green_flag_time is None:
            pass #print("[SUMMARY-DEBUG] No green flag time → EXIT")
            return
        cars = _normalize_leaderboard_list(leaderboard)
        leader = _find_leader_car(cars)
        leader_lap = safe_int(leader.get("Lap"), 0)
        #print("[SUMMARY-DEBUG] Leader lap:", leader_lap)
        if leader_lap < 2:
            pass #print("[SUMMARY-DEBUG] lap < 2 → EXIT")
            return
        now = time.time()
        #print("[SUMMARY-DEBUG] Time since last summary:",
              #now - race_data_store.last_summary_race_time)
        # Interval gate
        if now - race_data_store.last_summary_race_time < _SUMMARY_RACE_INTERVAL():
            pass #print("[SUMMARY-DEBUG] Not time yet → EXIT")
            return
        #print("[SUMMARY-DEBUG] TIME THRESHOLD PASSED!")

        # Update shared summary timer
        race_data_store.last_summary_race_time = now
        # -------------------------------------
        # Build summary text
        # -------------------------------------
        #print("[SUMMARY DEBUG] Building race summary...")
        lines = build_race_summary_lines(leaderboard, ir, session_info,elapsed_time, total_time)
        #print("[SUMMARY-DEBUG] Summary lines generated:", lines)
        if not lines:
            pass #print("[SUMMARY-DEBUG] No summary lines → EXIT")
            return
        # Commentary personality
        host, energy, location = commentary_team.get_host_and_energy(elapsed_time, total_time)
        # Character limit from settings (robust)
        summary_settings = commentary_team.trigger_settings.get("race_summary", {})
        try:
            char_limit = int(summary_settings.get("characters", 200))
        except:
            char_limit = 200
        # Flatten lines into a single summary string
        summary_text = " ".join(lines) if isinstance(lines, list) else str(lines)
        # Resolve leader / lap / track name from real telemetry
        leader_name = clean_driver_name(leader.get("Name", "")) if leader else ""
        current_lap = safe_int(leader.get("Lap"), 0) if leader else 0
        total_laps = 0
        try:
            if isinstance(session_info, dict):
                total_laps = safe_int(session_info.get("total_laps")
                                      or session_info.get("SessionLaps"), 0)
        except Exception:
            pass
        # Fall back to live iRacing session vars — the summary was printing
        # "lap X of ." because session_info doesn't carry total_laps.
        if not total_laps and ir is not None:
            try:
                remain = safe_int(safe_session_var(ir, "SessionLapsRemain", 0), 0)
                done = safe_int(safe_session_var(ir, "RaceLaps", 0), 0)
                if remain > 0 and done > 0:
                    total_laps = remain + done
            except Exception:
                pass
        # iRacing uses INT16_MAX (32767) for unlimited/AI races.
        # Treat anything over 999 as unknown.
        if total_laps > 999:
            total_laps = 0
        track_name = ""
        try:
            wpath = os.path.join("sector_said_exports", "weekend_info.json")
            if os.path.exists(wpath):
                with open(wpath, "r", encoding="utf-8") as wf:
                    wdata = json.load(wf)
                track_name = (wdata.get("track_display_name")
                              or wdata.get("track_name") or "")
        except Exception:
            pass
        # Fall back to race_data_store's cached weekend info — the JSON
        # export can lag behind the live session string.
        if not track_name:
            try:
                wi = getattr(race_data_store, "current_weekend_info", {}) or {}
                track_name = (wi.get("TrackDisplayName")
                              or wi.get("TrackName") or "")
            except Exception:
                pass
        # Focus Driver context appended so every race summary has a
        # tie-back to where the FD currently sits, even when the
        # summary body talks about other drivers.
        try:
            fd_ctx = _build_fdotd_context(leaderboard, reference_best_lap=_last_fastest_lap)
        except Exception:
            fd_ctx = ""

        global _summary_angle_idx
        angle = _SUMMARY_ANGLES[_summary_angle_idx % len(_SUMMARY_ANGLES)]
        _summary_angle_idx += 1

        analysis = get_db_prompt("race_update", {
            "leader": leader_name or "the leader",
            "lap": str(current_lap) if current_lap else "",
            "total_laps": str(total_laps) if total_laps else "",
            "track_name": track_name or "the circuit",
            "summary_lines": summary_text + (f" {fd_ctx}" if fd_ctx else ""),
        })
        if not analysis:
            analysis = (
                f"Write a short, broadcast race update: {summary_text} "
                f"{fd_ctx} "
                f"Limit to {char_limit} characters."
            )
        # Append the rotating angle as a final nudge — the DB template
        # may not know about it, so we tack it on so each summary leads
        # with a different beat.
        analysis += f" ANGLE: {angle}"
        normalized_text = normalize_telemetry_for_ai(analysis)
        ai_text = openai_commentary.generate_commentary(normalized_text)
        print(f"Summary: {analysis}")
        #print(f"Summary: {normalized_text}")
        #print(f"Summary: {ai_text}")
        # Enqueue summary commentary
        enqueue_commentary({
            "prompt": ai_text,
            "commentator": host,
            "location": location,
            "trigger_name": "Race Update",
            "type": "race_summary",
            "clip_id": f"race_summary_{int(time.time())}"
        })
        #print("[SUMMARY-DEBUG] QUEUED SUMMARY COMMENTARY!")
    except Exception as e:
        #print("[SUMMARY-ERROR] HARD FAILURE:", e)
        import traceback
        traceback.print_exc()

def build_race_summary_lines(leaderboard, ir, weekend_info,elapsed_time, total_time):
    try:
        if DEBUG_MODE:
            pass #print("[SUMMARY DEBUG] Building race summary...")
        # NORMALIZE LEADERBOARD
        try:
            cars = _normalize_leaderboard_list(leaderboard)
        except Exception:
            if isinstance(leaderboard, dict):
                cars = leaderboard.get("cars", [])
            elif isinstance(leaderboard, list):
                cars = leaderboard
            else:
                cars = []
        if not cars:
            return ["Race summary unavailable — no leaderboard data yet."]
        if DEBUG_MODE:
            pass #print(f"[SUMMARY DEBUG] Cars received: {len(cars)}")
        # SAFE SORTING BY POSITION
        def safe_pos(c):
            try:
                p = get_pos(c)
                return p if (isinstance(p, int) and p > 0) else 9999
            except:
                return 9999
        try:
            sorted_cars = sorted(cars, key=safe_pos)
        except Exception:
            sorted_cars = cars
        top3 = sorted_cars[:3]
        # NAME EXTRACTION
        def name_for_car(c):
            raw = c.get("Name") or c.get("Driver") or "Unknown"
            return clean_driver_name(str(raw)).strip()
        top3_names = [name_for_car(c) for c in top3]
        leader_name = top3_names[0] if top3_names else "the race leader"
        leader_lap_now = safe_int(top3[0].get("Lap"), 0) if top3 else 0

        def position_delta_for_summary(c):
            grid = safe_int(c.get("GridPosition") or c.get("grid_position"), 0)
            pos = safe_pos(c)
            if grid > 0 and 0 < pos < 9999:
                return grid - pos
            return safe_int(c.get("ΔPos", c.get("DeltaPos", 0)), 0)

        def eligible_for_mover_summary(c):
            pos = safe_pos(c)
            if not (0 < pos < 9999):
                return False
            lap = safe_int(c.get("Lap"), 0)
            if leader_lap_now >= 3 and lap > 0 and lap < leader_lap_now - 1:
                return False
            return True
        # GAP TO P2 (SAFE PARSE)
        leader_gap = ""
        if len(top3) > 1:
            gap_raw = top3[1].get("GapAhead")
            try:
                if isinstance(gap_raw, str):
                    gap_val = float(gap_raw.replace("s", "").replace("+", "").strip())
                else:
                    gap_val = safe_float(gap_raw, None)
            except:
                gap_val = None
            if gap_val is not None:
                try:
                    leader_gap = sector_says_utilities.voice_format_gap(gap_val) or "a small margin"
                except Exception:
                    leader_gap = "a small margin"
        # -----------------------------------------------------------
        # WEATHER — Use live session context from utilities
        # -----------------------------------------------------------
        #weather = ""
        #try:
        #    # Pull fresh, merged weather info from utilities
        #    session_ctx = sector_says_utilities.get_session_context(ir)
        #    skies     = session_ctx.get("skies")
        #    track_temp = session_ctx.get("track_temp")
        #    air_temp   = session_ctx.get("air_temp")
        #    humidity   = session_ctx.get("humidity")
        #    wind_vel   = session_ctx.get("wind_vel")
        #    wind_dir   = session_ctx.get("wind_dir")
        #    parts_w = []
        #    if skies not in [None, "?"]:
        #        parts_w.append(str(skies))
        #    if track_temp not in [None, "?"]:
        #        parts_w.append(f"Track {track_temp}")
        #    if air_temp not in [None, "?"]:
        #        parts_w.append(f"Air {air_temp}")
        #    if humidity not in [None, "?"]:
        #        parts_w.append(f"Humidity {humidity}")
        #    if wind_vel not in [None, "?"] and wind_dir not in [None, "?"]:
        #        parts_w.append(f"Wind {wind_vel} {wind_dir}")
        #    weather = ", ".join(parts_w)
        #except Exception as e:
        #    print("[SUMMARY] Weather block error:", e)
        #    weather = ""
        # ==================================================================
        # BUILD BASE SUMMARY PARTS
        # ==================================================================
        parts = []
        parts.append(f"{leader_name} leads the race.")
        if leader_gap:
            parts.append(f"Gap to P2 is {leader_gap}.")
        if len(top3_names) >= 3:
            parts.append(f"Top three: {', '.join(top3_names)}.")
        elif len(top3_names) == 2:
            parts.append(f"Top two: {', '.join(top3_names)}.")
        #if weather:
        #    parts.append(f"Weather: {weather}.")
        # ==================================================================
        # FDOTD — SAFER, NO DUPLICATE LOOKUPS
        # ==================================================================
        try:
            fd_data = None
            try:
                fd_data = _load_focus_driver()
            except:
                fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
                if os.path.exists(fd_path):
                    with open(fd_path, "r", encoding="utf-8") as f:
                        fd_data = json.load(f)
            if fd_data and fd_data.get("Name"):
                fd_name_clean = clean_driver_name(fd_data["Name"]).strip().lower()
                fd_num_clean  = str(fd_data.get("CarNumber", "")).strip()
                fd_pos = None
                for c in sorted_cars:
                    name_match = clean_driver_name(c.get("Name", "")).strip().lower() == fd_name_clean
                    num_match  = str(c.get("CarNumber", "")).strip() == fd_num_clean
                    if name_match or num_match:
                        fd_pos = safe_pos(c)
                        break
                if fd_pos is not None and 1 <= fd_pos < 9999:
                    parts.append(f"{fd_data['Name']} running P{fd_pos}.")
        except Exception as e:
            print("[SUMMARY] FDOTD block error:", e)
        # ==================================================================
        # OPTIONAL SECTIONS
        # ==================================================================
        optional_sections = []
        # ---------------- DOTD STANDINGS --------------------
        try:
            dotd = None
            try:
                dotd = _load_dotd_top3()
            except:
                path = os.path.join("sector_said_exports", "dotd_top3.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        dotd = json.load(f)
            if dotd and "top3" in dotd:
                items = []
                for e in dotd["top3"]:
                    nm = clean_driver_name(e.get("name", "")).strip()
                    pct = safe_float(e.get("percent", 0), 0)
                    if nm:
                        # Spell out "percent" explicitly. Format "34.9%"
                        # kept confusing the LLM into rewriting it as
                        # "34.900 seconds" — percent is DOTD voting
                        # share, not a gap, and must not be spoken as
                        # a time.
                        items.append(f"{nm} at {pct:.1f} percent")
                if items:
                    optional_sections.append(
                        "Driver of the Day voting share (voter percentages, NOT gaps, NOT seconds, NOT lap times): "
                        + ", ".join(items) + "."
                    )
        except Exception as e:
            print("[SUMMARY] DOTD standings error:", e)
        # ---------------- LAPS LED --------------------
        try:
            laps_led_data = []
            for c in sorted_cars:
                nm = name_for_car(c)
                laps_led = safe_int(c.get("LapsLed", 0), 0)
                laps_led_data.append((nm, max(laps_led, 0)))

            leader_nm, leader_laps = max(laps_led_data, key=lambda x: x[1])
            if leader_laps > 0:
                plural = "lap" if leader_laps == 1 else "laps"
                optional_sections.append(
                    f"Laps led: {leader_nm} has led {leader_laps} {plural}."
                )
        except Exception as e:
            print("[SUMMARY] LapsLed block error:", e)
        # ------------------------------------------------------
        # TOP 3 GAINERS + FOCUS DRIVER (Corrected — no variable shadowing)
        # ------------------------------------------------------
        try:
            gains = []
            fd_gain = None
            fd_name = None
            # Load FDOTD info once
            fd_data = None
            try:
                fd_data = _load_focus_driver()
            except Exception:
                fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
                if os.path.exists(fd_path):
                    with open(fd_path, "r", encoding="utf-8") as f:
                        fd_data = json.load(f)
            if fd_data and fd_data.get("Name"):
                fd_name = clean_driver_name(fd_data["Name"]).strip()
            # Collect gains and FD gain
            for c in sorted_cars:
                name = clean_driver_name(c.get("Name", "")).strip()
                if not name:
                    continue
                dp = position_delta_for_summary(c)
                # Gains list
                if dp > 0 and eligible_for_mover_summary(c):
                    gains.append((name, dp))
                # FD tracking
                if fd_name and name == fd_name:
                    fd_gain = dp
            # ---------------------------
            # Build output line
            # ---------------------------
            top_gainers = []
            if gains:
                top_gainers = sorted(gains, key=lambda x: x[1], reverse=True)[:3]
                top3_gainers_str = ", ".join(f"{n} +{p}" for n, p in top_gainers)
                line = f"Biggest gains: {top3_gainers_str}."
            else:
                line = "No position gains recorded."
            # ---------------------------
            # Append Focus Driver
            # ---------------------------
            if fd_name is not None and fd_gain is not None:
                # FD already in top-3? Prevent duplication
                if fd_name not in [n for n, _ in top_gainers]:
                    if fd_gain > 0:
                        fd_str = f"{fd_name} +{fd_gain}"
                    elif fd_gain < 0:
                        fd_str = f"{fd_name} {fd_gain}"
                    else:
                        fd_str = f"{fd_name} ±0"
                    line += f" also {fd_str}."

            optional_sections.append(line)
        except Exception as e:
            print("[SUMMARY] Gains block error:", e)
        # ---------------- BIGGEST LOSSES --------------------
        try:
            losses = []
            for c in sorted_cars:
                nm = name_for_car(c)
                dp = position_delta_for_summary(c)
                if dp < 0 and eligible_for_mover_summary(c):
                    losses.append((nm, dp))
            if losses:
                topl = sorted(losses, key=lambda x: x[1])[:3]
                optional_sections.append(
                    "Losing Ground: " + ", ".join(f"{n} {p}" for n, p in topl) + "."
                )
        except Exception as e:
            print("[SUMMARY] Losses block error:", e)
        # ---------------- FASTEST LAP --------------------
        try:
            fastest = None
            for c in sorted_cars:
                nm = name_for_car(c)
                raw = c.get("BestLapTime") or c.get("LapBestLapTime") or c.get("Best")
                t = safe_float(raw, None)
                if t is None or t <= 0:
                    continue
                if fastest is None or t < fastest[1]:
                    fastest = (nm, t)
            if fastest:
                optional_sections.append(
                    f"Fastest lap: {fastest[0]} with {sector_says_utilities.voice_format_lap_time(fastest[1])}."
                )
        except Exception as e:
            print("[SUMMARY] Fastest lap block error:", e)
        # ==================================================================
        # RANDOM OPTIONALS (CAP 1–2, BUT PRESERVE NATURAL ORDERING)
        # ==================================================================
        if optional_sections:
            take_n = random.randint(1, min(2, len(optional_sections)))
            # mix lightly but preserve relative order for readability
            random.shuffle(optional_sections)
            optional_sections = optional_sections[:take_n]
            parts.extend(optional_sections)
        # ENDING TAG FOR AI
        final_line = " ".join(parts)
        if DEBUG_MODE:
            pass #print("[SUMMARY DEBUG] Final summary line:", final_line)
        return [final_line]
    except Exception as e:
        print(f"[build_race_summary_lines] ERROR: {e}")
        #return ["Race summary is being prepared."]

# Ensure globals exist
_last_fastest_lap = None
_last_fastest_phrase = None
def check_fastest_lap_and_sectors(leaderboard, elapsed_time, total_time, ir=None):
    global _last_fastest_lap, _last_fastest_phrase
    # Basic validation
    if not leaderboard or len(leaderboard) < 2:
        return
    # Get real leader lap (must be Lap >= 2)
    try:
        # Guarantee sorted by LivePos/Pos
        cars_sorted = sorted(
            leaderboard,
            key=lambda c: safe_int(
                c.get("LivePos")
                or c.get("Position")
                or c.get("Pos")
                or 9999
            )
        )
        leader = cars_sorted[0]
    except Exception:
        return
    leader_lap = safe_int(leader.get("Lap", 0))
    if leader_lap < 2:
        return
    # Scan every car for a NEW fastest lap
    for car in leaderboard:
        try:
            idx  = safe_int(car.get("CarIdx"))
            lap  = safe_int(car.get("Lap"))
            name = clean_driver_name(car.get("Name", "Unknown"))
            raw_best = (
                car.get("BestLapTime")
                or car.get("LapBestLapTime")
                or car.get("Best")
                or None
            )
            best = safe_float(raw_best, None)
            if best is None or best <= 0:
                continue
            # New overall FASTEST LAP?
            if _last_fastest_lap is None or best < _last_fastest_lap:
                prev_fastest = _last_fastest_lap
                _last_fastest_lap = best
                try:
                    try:
                        spoken = sector_says_utilities.voice_format_lap_time(best)
                    except Exception:
                        spoken = "a new fastest"
                    base_phrase = (f"Purple lap for {name}! Fastest lap with a {spoken}.")
                    _last_fastest_phrase = base_phrase
                    # Focus Driver context — compare their best lap against
                    # the new purple so the call ties back to the FD every time
                    fd_ctx = _build_fdotd_context(leaderboard, reference_best_lap=best)
                    # Commentary personality
                    host, energy, location = commentary_team.get_host_and_energy(elapsed_time, total_time)
                    char_limit = (commentary_team.trigger_settings.get("fastest_lap", {}).get("characters", 80))
                    # AI prompt — try DB template first
                    analysis = get_db_prompt("fastest_lap", {
                        "driver": name, "lap_time": spoken, "track_name": track_name if 'track_name' in dir() else "",
                    })
                    if not analysis:
                        analysis = (f"Write a short, live-broadcast Fastest Lap: {base_phrase} Limit to {char_limit} characters.")
                    # Append Focus Driver data so the AI can mention where the
                    # FD's pace sits vs the new fastest — only if we have it.
                    if fd_ctx:
                        analysis += f" {fd_ctx}"
                    normalized_text = normalize_telemetry_for_ai(analysis)
                    ai_text = openai_commentary.generate_commentary(normalized_text)
                    enqueue_commentary({
                        "prompt": ai_text,
                        "commentator": host,
                        "location": location,
                        "trigger_name": "Fastest Lap",
                        "type": "fastest_lap",
                        "clip_id": f"fastest_lap_{idx}_{int(time.time())}",
                        "car_idx": idx,
                    })
                except Exception as ce:
                    print("[FAST LAP COMMENTARY ERROR]", ce)
                # DOTD Scoring
                try:
                    submit_trigger(
                        trigger_id="fastest_lap",
                        driver_id=idx,
                        desc=f"{name} sets a new fastest lap",
                        lap=lap,
                        meta={
                            "best_lap_time": best,
                            "prev_fastest": prev_fastest,
                            "position": safe_int(car.get("Position", 0)),
                            "elapsed_time": float(elapsed_time or 0.0),
                            "total_time": float(total_time or 0.0),
                        }
                    )
                except Exception as de:
                    print("[DOTD] fastest_lap submit_trigger error:", de)
        except Exception as e:
            print("[FAST LAP ERROR]", e)

_last_midfield_battle_time = 0
_last_midfield_battle = None
def _MIDFIELD_BATTLE_COOLDOWN():
    return _race_control().get("midfield_battle_cooldown", 260)
def check_midfield_battles(leaderboard, elapsed_time, total_time, ir=None):
    global _last_midfield_battle_time, _last_midfield_battle
    if not isinstance(leaderboard, list) or len(leaderboard) < 6:
        return
    # Require the field to be on lap ≥ 2
    try:
        if not any(
            safe_int(c.get("Lap")) and safe_int(c.get("Lap")) >= 2
            for c in leaderboard
        ):
            return
    except Exception:
        return
    if not can_fire("midfield_battle"):
        return
    now = time.time()
    if now - _last_midfield_battle_time < _MIDFIELD_BATTLE_COOLDOWN():
        return
    # ---------------------------
    # Load FDOTD (optional highlight)
    # ---------------------------
    fd_record = None
    fd_name_clean = ""
    fd_idx = None
    try:
        fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
        if os.path.exists(fd_path):
            with open(fd_path, "r", encoding="utf-8") as f:
                fd_record = json.load(f)
    except Exception as e:
        print("[MIDFIELD DEBUG] FDOTD read error:", e)

    if isinstance(fd_record, dict):
        fd_name_clean = clean_driver_name(fd_record.get("Name", "") or "")
        fd_idx = safe_int(fd_record.get("CarIdx"))

    def safe_pos(car):
        try:
            p = safe_int(car.get("LivePos")) or safe_int(car.get("Pos"))
            return p if p and p > 0 else 999
        except Exception:
            return 999

    # Sort by position
    sorted_cars = sorted(leaderboard, key=lambda c: safe_pos(c))

    closest_pair = None
    min_gap = float("inf")

    for i in range(len(sorted_cars) - 1):
        c1, c2 = sorted_cars[i], sorted_cars[i + 1]
        p1, p2 = safe_pos(c1), safe_pos(c2)

        # Midfield position range
        if not (2 <= p1 <= 15 and 2 <= p2 <= 15):
            continue

        raw_gap = c2.get("GapAhead", None)
        try:
            gap = abs(float(raw_gap)) if raw_gap not in (None, "", "None") else 10.0
        except Exception:
            gap = 10.0

        if 0 < gap < min_gap:
            min_gap = gap
            closest_pair = (c1, c2)

    if not closest_pair:
        return

    if closest_pair == _last_midfield_battle:
        return

    _last_midfield_battle = closest_pair
    _last_midfield_battle_time = now

    c1, c2 = closest_pair
    p = safe_pos(c1)

    d1 = clean_driver_name(c1.get("Name") or "Driver 1")
    d2 = clean_driver_name(c2.get("Name") or "Driver 2")

    try:
        gap_txt = sector_says_utilities.voice_format_gap(min_gap) or "under a second"
    except Exception:
        gap_txt = "under a second"

    analysis = f"Midfield battle for P{p}: {d1} leads {d2} by {gap_txt}."

    # Skill-mismatch colour — iRating + license give the AI an honest
    # read on which driver is the favourite in this fight and whether it's
    # a routine defend or an upset brewing.
    try:
        from commentary_phrases import driver_skill_blurb, _irating_tier
        s1 = driver_skill_blurb(c1)
        s2 = driver_skill_blurb(c2)
        if s1 or s2:
            analysis += f" Skill: {d1} [{s1 or 'unrated'}] vs {d2} [{s2 or 'unrated'}]."
        ir1 = c1.get("IRating") if isinstance(c1.get("IRating"), (int, float)) else None
        ir2 = c2.get("IRating") if isinstance(c2.get("IRating"), (int, float)) else None
        if ir1 and ir2 and abs(ir1 - ir2) >= 1500:
            underdog = d2 if ir1 > ir2 else d1
            favourite = d1 if ir1 > ir2 else d2
            analysis += f" Notable gap — {favourite} is the heavy favourite on paper; {underdog} punching up."

        # Pit & incident context — feeds directly into battle narrative.
        stops1 = int(c1.get("PitStops") or 0)
        stops2 = int(c2.get("PitStops") or 0)
        if stops1 != stops2:
            # Just state the stop count — never call it strategy. Extra
            # stops in this series usually mean damage or a spin recovery,
            # not a planned undercut.
            analysis += f" Stops so far: {d1} {stops1}, {d2} {stops2}."
        inc1 = int(c1.get("LiveIncidents") or 0)
        inc2 = int(c2.get("LiveIncidents") or 0)
        if inc1 or inc2:
            analysis += f" Incidents so far: {d1} {inc1}x, {d2} {inc2}x."
            if max(inc1, inc2) >= 12:
                risky = d1 if inc1 >= inc2 else d2
                analysis += f" {risky} is on thin ice — a big moment would hurt badly."
    except Exception as e:
        print("[MIDFIELD] skill tag failed:", e)

    # FDOTD involvement / positional reference
    try:
        fd_involved = fd_idx is not None and (
            safe_int(c1.get("CarIdx")) == fd_idx
            or safe_int(c2.get("CarIdx")) == fd_idx
        )
    except Exception:
        fd_involved = False
    if fd_involved and fd_name_clean:
        analysis += f" {fd_name_clean} is part of this fight."
    else:
        # FD not in the battle — attach their data so the AI can
        # still weave in "meanwhile the Focus Driver is…" colour.
        fd_ctx = _build_fdotd_context(leaderboard)
        if fd_ctx:
            analysis += f" {fd_ctx}"
    # Assign commentator
    try:
        host, energy, location = commentary_team.get_host_and_energy(
            elapsed_time, total_time
        )
    except Exception:
        host, location = "Murray", "booth"
    char_limit = commentary_team.trigger_settings.get("midfield_battle", {}).get("characters", 80)
    db_p = get_db_prompt("midfield_battle", {
        "driver1": d1, "driver2": d2, "gap": gap_txt, "pos1": str(p), "pos2": str(p+1), "analysis": analysis,
    })
    prompt = db_p if db_p else (f"Write a short, live-broadcast Midfield battle: {analysis} Limit to {char_limit} characters.")
    prompt = normalize_telemetry_for_ai(prompt)
    ai_text = openai_commentary.generate_commentary(prompt)
    try:
        enqueue_commentary({
            "prompt": ai_text,
            "commentator": host,
            "location": location,
            "trigger_name": "Midfield Battle",
            "type": "midfield_battle",
            "clip_id": f"midfield_battle_{int(now)}",
            "car_idx": safe_int(c1.get("CarIdx")),
        })
    except Exception as e:
        print("[MIDFIELD] enqueue failed:", e)

    mark_fired("midfield_battle")

_last_positions = {}

# ============================================================
# CLEAN LAPS / INCIDENT / COMEBACK SCORING (DOTD)
# ============================================================
# Per-car tracker for the scoring triggers that the old pipeline never
# wired up. Runs every tick off the live leaderboard and fires into the
# DOTD manager. State is kept in module-level dicts so it survives
# between ticks and resets naturally on a new race (engine restart).
_dotd_last_lap: Dict[int, int] = {}          # last completed lap we scored
_dotd_last_incidents: Dict[int, int] = {}    # last seen LiveIncidents value
_dotd_incident_penalty: Dict[int, float] = {}   # running penalty we've already submitted
_dotd_clean_streak: Dict[int, int] = {}      # rolling count of consecutive clean laps
_dotd_places_gained_fired: Dict[int, set] = {}  # {car_idx: {5, 10}} milestones fired
_dotd_last_live_pos: Dict[int, int] = {}     # last seen LivePos per car — defended_position needs prev-lap snapshot
_race_end_scored: bool = False               # race-end bonuses fired once per race
_race_finished: bool = False                 # True after the leader takes the chequered flag —
                                              # gates all rolling triggers so cool-down-lap
                                              # position changes don't fire new commentary
_pre_race_show_fired: bool = False            # True after the pre-race show has played for
                                              # this race — fires once at the earliest
                                              # eligible window (quali→race transition OR
                                              # one-to-green, whichever comes first)


def _tiered_incident_penalty(total_incidents: int) -> float:
    """Progressive incident penalty curve:
        1-4   incidents: free (normal racing)
        5-9   incidents: -2 each
        10-14 incidents: -4 each
        15+   incidents: -6 each
    Returns the total penalty (negative) for the given incident count."""
    if total_incidents <= 4:
        return 0.0
    penalty = 0.0
    t1 = min(max(total_incidents - 4, 0), 5)   # band 5-9
    penalty += t1 * -2
    t2 = min(max(total_incidents - 9, 0), 5)   # band 10-14
    penalty += t2 * -4
    t3 = max(total_incidents - 14, 0)          # band 15+
    penalty += t3 * -6
    return penalty


# ============================================================
# PRE-RACE PREDICTION SHOW
# ============================================================
# Fires once per race, in the window between qualifying ending and
# the green flag of the race itself. Two back-to-back segments:
#   1. pre_race_pick  — Nigel's data-driven podium bet ("where's
#                        your money?")
#   2. pre_race_focus — Clara's focus driver preview with realistic
#                        finish range + DOTD path
#
# This is the ONE exception to the pre-race commentary gate — all
# other triggers stay silent until the race session is actually
# racing (SessionState == 4). This function runs when the race
# session is active but NOT yet racing (Warmup, ParadeLaps) OR
# when ONE_TO_GREEN is raised.

def _read_session_yaml(ir):
    """Parse iRacing's SessionInfo YAML into a dict. Handles the
    string-vs-dict ambiguity of pyirsdk."""
    try:
        raw = safe_session_var(ir, "SessionInfo", {}) or {}
        if isinstance(raw, str):
            import yaml
            raw = yaml.safe_load(raw) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _find_race_session(session_info_yaml):
    """Return the (index, session-dict) of the Race session in the
    SessionInfo YAML, or (None, None) if not found."""
    sessions = session_info_yaml.get("Sessions") or []
    for i, s in enumerate(sessions):
        stype = (s.get("SessionType") or "").strip().lower()
        if "race" in stype:
            return i, s
    return None, None


def _build_grid_preview(ir, leaderboard):
    """Build the grid-preview data block for the pre-race show.

    Tries in order:
      1. The Race session's ResultsPositions (final qualified grid)
      2. The previous (Qualify) session's ResultsPositions
      3. Current live positions from the leaderboard (fallback)

    Returns a list of dicts: {name, grid, car_idx, irating, lic_class,
    lic_safety, ir_rank} sorted by grid ascending. Empty list on failure.
    """
    yaml_info = _read_session_yaml(ir)

    # Build a CarIdx -> enriched-data lookup from the live leaderboard
    lb_by_idx = {}
    if isinstance(leaderboard, list):
        for c in leaderboard:
            if not isinstance(c, dict):
                continue
            ci = c.get("CarIdx")
            if ci is None:
                continue
            lb_by_idx[int(ci)] = c

    # Primary: Race session ResultsPositions
    race_idx, race_session = _find_race_session(yaml_info)
    result_positions = None
    if race_session:
        result_positions = race_session.get("ResultsPositions") or None

    # Fallback: previous session (usually Qualify)
    if not result_positions and race_idx is not None and race_idx > 0:
        sessions = yaml_info.get("Sessions") or []
        prev = sessions[race_idx - 1] if race_idx - 1 < len(sessions) else None
        if prev:
            result_positions = prev.get("ResultsPositions") or None

    grid_entries = []
    if result_positions:
        for rp in result_positions:
            try:
                ci = int(rp.get("CarIdx"))
            except Exception:
                continue
            pos = rp.get("Position")
            try:
                pos = int(pos)
            except Exception:
                continue
            lb_car = lb_by_idx.get(ci) or {}
            name = clean_driver_name(
                rp.get("UserName")
                or rp.get("DriverName")
                or lb_car.get("Name")
                or f"Car {ci}"
            )
            grid_entries.append({
                "name": name,
                "grid": pos,
                "car_idx": ci,
                "cust_id": lb_car.get("CustId"),
                "irating": lb_car.get("IRating"),
                "lic_class": lb_car.get("LicClass"),
                "lic_safety": lb_car.get("LicSafety"),
            })

    # Last-ditch fallback: derive from current leaderboard LivePos
    if not grid_entries and lb_by_idx:
        for ci, car in lb_by_idx.items():
            try:
                pos = int(car.get("LivePos") or car.get("Pos") or 999)
            except Exception:
                continue
            if pos >= 999:
                continue
            grid_entries.append({
                "name": clean_driver_name(car.get("Name") or f"Car {ci}"),
                "grid": pos,
                "car_idx": ci,
                "cust_id": car.get("CustId"),
                "irating": car.get("IRating"),
                "lic_class": car.get("LicClass"),
                "lic_safety": car.get("LicSafety"),
            })

    grid_entries.sort(key=lambda e: int(e.get("grid") or 999))

    # Compute iR rank in the field
    rated = [e for e in grid_entries
             if isinstance(e.get("irating"), (int, float)) and e["irating"] > 0]
    ir_sorted = sorted(rated, key=lambda e: -e["irating"])
    rank_by_ci = {e["car_idx"]: i + 1 for i, e in enumerate(ir_sorted)}
    for e in grid_entries:
        e["ir_rank"] = rank_by_ci.get(e["car_idx"])

    return grid_entries


def _resolve_focus_driver_for_preview(grid_entries):
    """Pick the focus driver for the pre-race show.

    Priority:
      1. system_settings.json → preferred_focus_driver_cust_id (strongest)
      2. system_settings.json → preferred_focus_driver_name (fallback)
      3. focus_driver_of_the_day.json → CustId / CarIdx / Name
      4. CarIdx 0 (owner) fallback
    Returns a grid entry dict or None.
    """
    if not grid_entries:
        return None

    # 1 & 2. system_settings preferred driver — cust_id then name
    try:
        settings_path = os.path.join("sector_said_exports", "system_settings.json")
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f) or {}
            pref_cid = settings.get("preferred_focus_driver_cust_id")
            if pref_cid is not None:
                try:
                    pref_cid = int(pref_cid)
                    for e in grid_entries:
                        if e.get("cust_id") == pref_cid:
                            return e
                except (TypeError, ValueError):
                    pass
            pref = (settings.get("preferred_focus_driver_name") or "").strip().lower()
            if pref:
                for e in grid_entries:
                    if (e.get("name") or "").lower().strip() == pref:
                        return e
    except Exception:
        pass

    # 3. focus_driver_of_the_day.json — CustId then CarIdx then Name
    try:
        fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
        if os.path.exists(fd_path):
            with open(fd_path, "r", encoding="utf-8") as f:
                fd = json.load(f) or {}
            fd_cid = fd.get("CustId")
            if fd_cid is not None:
                try:
                    fd_cid = int(fd_cid)
                    for e in grid_entries:
                        if e.get("cust_id") == fd_cid:
                            return e
                except (TypeError, ValueError):
                    pass
            fd_ci = fd.get("CarIdx")
            if fd_ci is not None:
                try:
                    fd_ci = int(fd_ci)
                    for e in grid_entries:
                        if e.get("car_idx") == fd_ci:
                            return e
                except (TypeError, ValueError):
                    pass
            fd_name = (fd.get("Name") or "").strip().lower()
            if fd_name:
                for e in grid_entries:
                    if (e.get("name") or "").lower().strip() == fd_name:
                        return e
    except Exception:
        pass

    # 4. CarIdx 0 owner
    for e in grid_entries:
        if e.get("car_idx") == 0:
            return e

    return None


def check_pre_race_show(ir, leaderboard):
    """Fire the pre-race prediction show in the window between quali
    end and the race green flag. Exception to the check_triggers gate.
    Runs at most once per race; the flag is reset on GREEN raised-edge
    in check_flag_changes."""
    global _pre_race_show_fired
    if _pre_race_show_fired:
        return
    if ir is None:
        return

    # Must be in the Race session row (not Practice/Quali) but NOT
    # yet racing — Warmup (2) or ParadeLaps (3), OR one-to-green
    # flag raised. SessionState values:
    #   0 Invalid  1 GetInCar  2 Warmup  3 ParadeLaps  4 Racing
    #   5 Checkered  6 CoolDown
    try:
        yaml_info = _read_session_yaml(ir)
    except Exception:
        return
    race_idx, race_session = _find_race_session(yaml_info)
    if race_session is None:
        return
    try:
        cur_session_num = int(safe_session_var(ir, "SessionNum", 0) or 0)
    except Exception:
        return
    if cur_session_num != race_idx:
        # We're still in practice or quali — not yet in the race
        # session row. Show can't fire until quali→race transition.
        return

    try:
        session_state = int(safe_session_var(ir, "SessionState", 0) or 0)
    except Exception:
        session_state = 0
    in_prerace_window = session_state in (2, 3)

    # OR the one-to-green flag is raised — catches the case where
    # we joined the race session mid-formation lap or right at lights
    # out without seeing the Warmup/Parade window.
    one_to_green_bit = FLAGS.get("ONE_TO_GREEN") or 0
    try:
        current_flags = int(safe_session_var(ir, "SessionFlags", 0) or 0)
    except Exception:
        current_flags = 0
    if one_to_green_bit and (current_flags & one_to_green_bit):
        in_prerace_window = True

    if not in_prerace_window:
        return

    # Build the grid + pick the focus driver
    grid = _build_grid_preview(ir, leaderboard)
    if not grid or len(grid) < 3:
        print("[PRE-RACE] Grid too thin to predict — skipping show")
        _pre_race_show_fired = True   # don't keep retrying this race
        return

    # Weekend info for track name
    weekend_info = getattr(race_data_store, "current_weekend_info", {}) or {}

    # Total laps from broadcast_state export (fallback to SessionLaps)
    total_laps = 0
    try:
        bs_path = os.path.join("sector_said_exports", "broadcast_state.json")
        if os.path.exists(bs_path):
            with open(bs_path, "r", encoding="utf-8") as f:
                bs = json.load(f) or {}
            total_laps = int(bs.get("total_laps") or 0)
    except Exception:
        pass
    if not total_laps and race_session:
        try:
            total_laps = int(race_session.get("SessionLaps") or 0)
        except Exception:
            total_laps = 0
    if total_laps > 999:
        total_laps = 0

    from commentary_phrases import (
        _classify_race_length,
        _get_track_history_pattern,
        pre_race_pick_phrases,
        pre_race_focus_phrases,
    )
    race_class = _classify_race_length(total_laps)
    track_name = (
        weekend_info.get("TrackDisplayName")
        or weekend_info.get("track_display_name")
        or weekend_info.get("TrackName")
        or weekend_info.get("track_name")
        or ""
    )
    track_history = _get_track_history_pattern(track_name, limit=3)

    # Resolve the focus driver (user preference → FDOTD → CarIdx 0)
    focus = _resolve_focus_driver_for_preview(grid)

    # ── Segment 1: Nigel's pick ──
    pick_host = commentary_team.trigger_settings.get("pre_race_pick", {}).get("host", "Nigel")
    pick_lines = pre_race_pick_phrases(
        grid=grid,
        weekend_info=weekend_info,
        race_class=race_class,
        total_laps=total_laps,
        track_history=track_history,
        host=pick_host,
    )
    pick_prompt = pick_lines if isinstance(pick_lines, str) else (
        pick_lines[0] if isinstance(pick_lines, list) and pick_lines else "No predictions for this race."
    )
    try:
        pick_ai = openai_commentary.generate_commentary(
            normalize_telemetry_for_ai(pick_prompt)
        )
    except Exception as e:
        print(f"[PRE-RACE] pick AI failed: {e}")
        pick_ai = "No predictions for this race."

    pick_info = commentary_team.commentators.get(pick_host, {})
    enqueue_commentary({
        "prompt": pick_ai,
        "commentator": pick_host,
        "location": pick_info.get("location", "Pit Lane"),
        "trigger_name": "Pre-Race Pick",
        "type": "pre_race_pick",
        "clip_id": f"pre_race_pick_{int(time.time())}",
    })

    # ── Segment 2: Clara's focus driver preview ──
    focus_host = commentary_team.trigger_settings.get("pre_race_focus", {}).get("host", "Clara")
    focus_lines = pre_race_focus_phrases(
        grid=grid,
        weekend_info=weekend_info,
        focus_driver=focus,
        race_class=race_class,
        total_laps=total_laps,
        host=focus_host,
    )
    focus_prompt = focus_lines if isinstance(focus_lines, str) else (
        focus_lines[0] if isinstance(focus_lines, list) and focus_lines else "No predictions for this race."
    )
    try:
        focus_ai = openai_commentary.generate_commentary(
            normalize_telemetry_for_ai(focus_prompt)
        )
    except Exception as e:
        print(f"[PRE-RACE] focus AI failed: {e}")
        focus_ai = "No predictions for this race."

    focus_info = commentary_team.commentators.get(focus_host, {})
    enqueue_commentary({
        "prompt": focus_ai,
        "commentator": focus_host,
        "location": focus_info.get("location", "Studio"),
        "trigger_name": "Pre-Race Focus Driver",
        "type": "pre_race_focus",
        "clip_id": f"pre_race_focus_{int(time.time())}",
        "car_idx": focus.get("car_idx") if focus else None,
    })

    _pre_race_show_fired = True
    print(f"[PRE-RACE] Show fired — {len(grid)} drivers, "
          f"focus={focus['name'] if focus else 'none'}, "
          f"class={race_class}")


def score_race_end(leaderboard):
    """One-time race-end bonuses: compares each driver's final finish
    position to their iRating rank in the field (exceeded_expectation
    / underperformed) and awards a flat race_finish bonus to anyone
    who completed the race under their own power.

    Fires at most once per race (_race_end_scored guard)."""
    global _race_end_scored
    if _race_end_scored:
        return
    if not isinstance(leaderboard, list) or len(leaderboard) < 3:
        return

    # Build the iR-ranked expected order. Drivers without an iRating
    # are dropped from the ranking (they can't be judged against a
    # rating they don't have).
    rated = []
    for car in leaderboard:
        if not isinstance(car, dict):
            continue
        ir = car.get("IRating")
        ci = car.get("CarIdx")
        pos = car.get("LivePos") or car.get("Pos")
        if isinstance(ir, (int, float)) and ir > 0 and ci is not None and isinstance(pos, int):
            rated.append((int(ci), int(ir), int(pos), car))

    if len(rated) < 3:
        print("[RACE END] Not enough rated drivers to score expectations")
        _race_end_scored = True
        return

    # Rank by iRating (highest = rank 1)
    rated_sorted = sorted(rated, key=lambda t: -t[1])
    expected_rank = {t[0]: i + 1 for i, t in enumerate(rated_sorted)}

    for ci, ir, finish_pos, car in rated:
        name = car.get("Name") or f"Car {ci}"
        exp = expected_rank[ci]
        delta = exp - finish_pos  # positive = overperformed

        # Clamp to ±5 places so one outlier finish doesn't wreck scoring
        delta_clamped = max(-5, min(5, delta))

        if delta_clamped >= 1:
            pts = delta_clamped * 8
            submit_custom_points(
                "exceeded_expectation", ci, pts,
                meta={"expected_rank": exp, "finish_pos": finish_pos,
                      "delta": delta, "name": name},
            )
            print(f"[RACE END] {name} exceeded expectation: "
                  f"expected P{exp}, finished P{finish_pos} -> +{pts}")
        elif delta_clamped <= -1:
            pts = delta_clamped * 4  # 4 per place, less harsh than overperform reward
            submit_custom_points(
                "underperformed", ci, pts,
                meta={"expected_rank": exp, "finish_pos": finish_pos,
                      "delta": delta, "name": name},
            )
            print(f"[RACE END] {name} underperformed: "
                  f"expected P{exp}, finished P{finish_pos} -> {pts}")

        # Race finish completion bonus — only if still running at the flag
        reason = (car.get("reason_out") or car.get("ReasonOut")
                  or "Running")
        if reason == "Running":
            submit_custom_points(
                "race_finish", ci, 10.0,
                meta={"finish_pos": finish_pos, "name": name},
            )

        # Win bonus — P1 finisher takes the configured win_race points.
        if finish_pos == 1:
            submit_trigger("win_race", ci, meta={"name": name})
            print(f"[RACE END] {name} wins — win_race fired")

    _race_end_scored = True
    print("[RACE END] Scoring complete")


def _build_driver_dossier(cust_id: int | None) -> str:
    """One-line career snippet for a cust_id, pulled from the iRacing
    /data API cache populated by ``iracing_api_warmer``.

    Returns '' when the cust_id is unknown, no cache row exists, or the
    blob is too thin to be useful. Never hits the network — strictly
    cache-only — so it's safe to call from inside a tick.

    Example: "career: 12 wins from 187 starts, 34 podiums (road)"
    """
    if not cust_id:
        return ""
    try:
        import race_db
        blob = race_db.get_cached_driver(int(cust_id))
    except Exception:
        return ""
    if not isinstance(blob, dict):
        return ""

    career = blob.get("career") or {}
    stats = career.get("stats") if isinstance(career, dict) else None
    if not isinstance(stats, list) or not stats:
        return ""

    # Pick the category with the most starts — that's the discipline
    # the driver actually races. Ignores zero-start rows.
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
        return ""

    wins = best_row.get("wins") or 0
    top5 = best_row.get("top5") or 0
    poles = best_row.get("poles") or 0
    avg_finish = best_row.get("avg_finish_position") or 0
    cat = (best_row.get("category") or "").strip().lower() or "racing"

    bits = [f"{wins} wins from {best_starts} starts"]
    if top5:
        bits.append(f"{top5} top-5s")
    if poles:
        bits.append(f"{poles} poles")
    if isinstance(avg_finish, (int, float)) and avg_finish > 0:
        bits.append(f"avg finish P{int(round(avg_finish))}")
    return f"career ({cat}): " + ", ".join(bits) + "."


def _focus_driver_dotd_standing(car_idx) -> dict:
    """Look up the Focus Driver's live DOTD rank + vote share.

    Computes from the in-process sector_says_dotd.dotd.scores (the same
    dict _export_top3 reads) so every driver is covered, not just the
    top 3 that get written to dotd_top3.json. Returns
    {"rank": int, "of": int, "percent": float, "leader_name": str} on
    success, {} when there's no DOTD data yet or the car_idx isn't in
    the scoring table. Never invents.
    """
    try:
        if not isinstance(car_idx, int) or car_idx < 0:
            return {}
        from sector_says_dotd import dotd
        scores = getattr(dotd, "scores", None) or {}
        if not scores:
            return {}
        rows = []
        total = 0.0
        for did, rs in scores.items():
            try:
                pts = max(float(rs.get_score()), 0.0)
            except Exception:
                pts = 0.0
            total += pts
            rows.append((did, pts))
        if total <= 0 or not rows:
            return {}
        rows.sort(key=lambda x: x[1], reverse=True)
        rank = None
        fd_pts = 0.0
        for i, (did, pts) in enumerate(rows, start=1):
            if did == car_idx:
                rank = i
                fd_pts = pts
                break
        if rank is None:
            return {}
        percent = round((fd_pts / total) * 100.0, 1)
        leader_did, _leader_pts = rows[0]
        leader_name = (getattr(dotd, "driver_names", {}) or {}).get(leader_did, "")
        return {
            "rank": rank,
            "of": len(rows),
            "percent": percent,
            "leader_name": leader_name,
        }
    except Exception as e:
        print(f"[FDOTD context] DOTD standing lookup failed: {e}")
        return {}


def _build_fdotd_context(leaderboard, reference_best_lap: float = None) -> str:
    """Return a short factual data snippet about the Focus Driver's
    current state — for injection into commentary prompts so every
    callout can tie back to the Focus Driver when relevant.

    Returns '' when no FDOTD is set, no match in the leaderboard,
    or data is too thin. Never invents anything.

    Example outputs:
      "Focus Driver: Alex Driver P14, +45.2s behind leader, best lap 1:38.640 (+1.215 off fastest)."
      "Focus Driver: Alex Driver P14, +45.2s behind leader, no timed lap yet. career (road): 12 wins from 187 starts, 34 top-5s, avg finish P9."
    """
    try:
        fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
        if not os.path.exists(fd_path):
            return ""
        with open(fd_path, "r", encoding="utf-8") as f:
            fd = json.load(f)
        if not isinstance(fd, dict):
            return ""
        fd_idx = safe_int(fd.get("CarIdx"), -1)
        fd_name_clean = clean_driver_name(fd.get("Name") or "").strip().lower()
        if fd_idx < 0 and not fd_name_clean:
            return ""

        # Find the car in the current leaderboard
        cars = leaderboard if isinstance(leaderboard, list) else []
        fd_car = None
        for c in cars:
            if not isinstance(c, dict):
                continue
            if safe_int(c.get("CarIdx"), -2) == fd_idx:
                fd_car = c
                break
            if clean_driver_name(c.get("Name") or "").strip().lower() == fd_name_clean:
                fd_car = c
                break
        if fd_car is None:
            return ""

        fd_display_name = clean_driver_name(fd_car.get("Name") or fd.get("Name") or "Focus Driver")
        fd_pos = fd_car.get("LivePos") or fd_car.get("Pos") or "?"
        try:
            fd_pos = int(fd_pos)
        except Exception:
            fd_pos = "?"

        # Gap to leader — prefer the value already on the car
        gap_txt = ""
        try:
            gl = fd_car.get("GapToLeader")
            if isinstance(gl, (int, float)) and gl > 0:
                gap_txt = f"+{float(gl):.1f}s behind leader"
        except Exception:
            pass

        # Whether the FD has a timed lap is still useful for the
        # anti-fabrication guard below, even though the lap time itself
        # is no longer surfaced in the context line.
        try:
            fd_best = fd_car.get("Best") or fd_car.get("BestLapTime") or fd_car.get("LapBestLapTime")
            fd_best_f = float(fd_best) if fd_best not in (None, "", "-", "--", 0, "0", -1, "-1") else None
        except Exception:
            fd_best_f = None
        has_timed_lap = bool(fd_best_f and fd_best_f > 0)

        on_pit_now = bool(fd_car.get("OnPitRoad"))

        bits = [f"P{fd_pos}"]
        if gap_txt:
            bits.append(gap_txt)
        if on_pit_now:
            bits.append("currently on pit road")
        line = f"Focus Driver: {fd_display_name} " + ", ".join(bits) + "."
        if not has_timed_lap:
            line += " (Do not claim this driver is gaining, charging, or racing — they have not posted a timed lap yet.)"
        if not on_pit_now:
            line += " (Do NOT say this driver is in the pits, in pit lane, or on pit road — they are out on track.)"

        # DOTD standing — the user has asked that every Focus Driver
        # reference also surface the current DOTD rank and vote share.
        # Pull live from the in-process DOTD scoring table so this
        # works whether or not the FD is in the broadcast top 3.
        dotd_st = _focus_driver_dotd_standing(safe_int(fd_car.get("CarIdx"), -1))
        if dotd_st and isinstance(dotd_st.get("rank"), int):
            rank = dotd_st["rank"]
            of = dotd_st.get("of") or 0
            pct = dotd_st.get("percent") or 0.0
            leader_name = (dotd_st.get("leader_name") or "").strip()
            if rank == 1:
                dotd_phrase = (
                    f"Driver of the Day standing: leading the vote at "
                    f"{pct:.1f} percent (P1 of {of})."
                )
            else:
                trail_bit = (
                    f" — trailing {leader_name}" if leader_name and leader_name.lower() != fd_display_name.lower() else ""
                )
                dotd_phrase = (
                    f"Driver of the Day standing: P{rank} of {of} at "
                    f"{pct:.1f} percent of the vote{trail_bit}."
                )
            line += " " + dotd_phrase
            line += (
                " REQUIRED: state the Focus Driver's Driver of the Day rank "
                "and vote share aloud in the commentary."
            )

        # If the iRacing /data API warmer has cached career stats for
        # this cust_id, append a one-line dossier so commentary can
        # reference real history instead of inventing it.
        dossier = _build_driver_dossier(fd_car.get("CustId"))
        if dossier:
            line += " " + dossier
        return line
    except Exception as e:
        print(f"[FDOTD context] failed: {e}")
        return ""


def _lap_clean_multiplier(car: dict) -> float:
    """SR-aware multiplier for lap_clean:
        Rookie/D or SR<2.0  -> 1.5x (real growth)
        C / SR 2.0-3.49     -> 1.0x (baseline)
        B / SR 3.5-4.49     -> 0.9x (expected)
        A / SR >= 4.5       -> 0.8x (expected)
    Falls back to 1.0x when license data is missing."""
    try:
        sr = float(car.get("LicSafety") or 0.0)
    except Exception:
        sr = 0.0
    lic = (car.get("LicClass") or "").strip().upper()
    if not lic and sr == 0.0:
        return 1.0
    if lic in ("R", "D") or sr < 2.0:
        return 1.5
    if lic == "C" or sr < 3.5:
        return 1.0
    if lic == "B" or sr < 4.5:
        return 0.9
    return 0.8  # A class, SR 4.5+

def _find_pursuer(leaderboard, defender_pos: int):
    """Return the car directly behind `defender_pos` on track (LivePos ==
    defender_pos + 1). Returns None if the pursuer is missing or the
    defender is last."""
    if not isinstance(leaderboard, list) or defender_pos <= 0:
        return None
    target = defender_pos + 1
    for c in leaderboard:
        if not isinstance(c, dict):
            continue
        try:
            if int(c.get("LivePos") or 0) == target:
                return c
        except Exception:
            continue
    return None


def check_clean_scoring(leaderboard, elapsed_time=None, total_time=None, ir=None):
    """Score the per-lap, per-incident, and comeback triggers that the
    legacy pipeline never wired up. Runs every tick; all work is cheap
    and idempotent."""
    if not isinstance(leaderboard, list) or not leaderboard:
        return
    for car in leaderboard:
        if not isinstance(car, dict):
            continue
        ci = car.get("CarIdx")
        if ci is None:
            continue
        try:
            ci = int(ci)
        except Exception:
            continue

        lap = safe_int(car.get("Lap"))
        inc = safe_int(car.get("LiveIncidents"))
        # Use the ΔPos already computed by live_leaderboard (positive =
        # gained places from grid, negative = lost).
        delta_pos = safe_int(car.get("ΔPos") or car.get("DeltaPos"))
        on_pit = bool(car.get("OnPitRoad"))

        prev_lap = _dotd_last_lap.get(ci, -1)
        prev_inc = _dotd_last_incidents.get(ci, inc)

        # ── Tiered incident penalty: recompute the total expected penalty
        # for the driver's current incident count, then submit the delta
        # vs. what we've already applied. This naturally handles the
        # 1-4 free / 5-9 -2 / 10-14 -4 / 15+ -6 curve without firing
        # multiple events.
        if inc != prev_inc:
            target = _tiered_incident_penalty(inc)
            already = _dotd_incident_penalty.get(ci, 0.0)
            delta = target - already
            if delta != 0:
                submit_custom_points(
                    "incident_point", ci, delta,
                    lap=lap, meta={"total_incidents": inc}
                )
                _dotd_incident_penalty[ci] = target
        _dotd_last_incidents[ci] = inc

        # ── Clean lap scoring: fires once per completed lap if no new
        # incident points accrued during that lap AND the driver is
        # actually on track (not sitting in the pits). Points are
        # modulated by the driver's license class/SR — a low-SR driver
        # running clean is worth more than a high-SR driver doing the
        # expected.
        if lap > 0 and lap != prev_lap:
            if prev_lap >= 0 and not on_pit and inc <= prev_inc:
                base = 2.0  # matches race_config.json lap_clean points
                mult = _lap_clean_multiplier(car)
                submit_custom_points(
                    "lap_clean", ci, base * mult,
                    lap=lap, meta={"sr_multiplier": mult}
                )
                streak = _dotd_clean_streak.get(ci, 0) + 1
                _dotd_clean_streak[ci] = streak
                if streak > 0 and streak % 5 == 0:
                    submit_trigger("clean_streak_5", ci, lap=lap,
                                   meta={"streak": streak})
            else:
                # Broken streak — any incident / pit during the lap resets.
                _dotd_clean_streak[ci] = 0

            # ── Defended position: at lap transition, if driver held
            # LivePos and a pursuer was within 1.0s directly behind at
            # the stripe, award defended_position. The 10s
            # min_interval_s in dotd config debounces short-lap tracks.
            cur_pos = safe_int(car.get("LivePos"))
            prev_pos_def = _dotd_last_live_pos.get(ci)
            if (prev_pos_def is not None
                    and cur_pos > 0
                    and cur_pos == prev_pos_def
                    and not on_pit):
                pursuer = _find_pursuer(leaderboard, cur_pos)
                if pursuer is not None and not bool(pursuer.get("OnPitRoad")):
                    try:
                        pursuer_gap = float(pursuer.get("GapAhead") or 0.0)
                    except Exception:
                        pursuer_gap = 0.0
                    if 0 < pursuer_gap <= 1.0:
                        submit_trigger(
                            "defended_position", ci, lap=lap,
                            meta={"pursuer_gap": round(pursuer_gap, 3),
                                  "pursuer_idx": pursuer.get("CarIdx")},
                        )
            _dotd_last_live_pos[ci] = cur_pos if cur_pos > 0 else prev_pos_def

            _dotd_last_lap[ci] = lap

        # ── Places gained milestones — fire once each per race.
        fired = _dotd_places_gained_fired.setdefault(ci, set())
        if delta_pos >= 10 and 10 not in fired:
            submit_trigger("places_gained_10", ci, lap=lap,
                           meta={"delta_pos": delta_pos})
            fired.add(10)
        elif delta_pos >= 5 and 5 not in fired:
            submit_trigger("places_gained_5", ci, lap=lap,
                           meta={"delta_pos": delta_pos})
            fired.add(5)


# ============================================================
# POSITION CHANGES / OVERTAKES (JSON-DRIVEN)
_last_positions = {}  # global store for last known positions
_last_pit_state = {}  # CarIdx → bool: was this car on pit road last tick?
def _load_leaderboard_from_disk():
    try:
        path = os.path.join("sector_said_exports", "leaderboard.json")
        if not os.path.exists(path):
            print("[POSITION] leaderboard.json not found:", os.path.abspath(path))
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cars = data.get("cars", [])
        if not isinstance(cars, list) or not cars:
            print("[POSITION] leaderboard.json has no 'cars' list or it's empty.")
            return None
        return cars
    except Exception as e:
        print("[POSITION] ERROR reading leaderboard.json:", e)
        return None

_position_detection_start_time = None  # set on first call
_pending_overtakes = {}  # car_idx → {"new_pos": int, "ticks": int}
# When a driver last had an overtake callout fired (wall-clock time).
# Used to rate-limit rapid-gain drivers so we don't call 20 overtakes
# in 30 seconds for a comeback drive, while still calling SOMETHING.
_last_overtake_fire_time: dict = {}
OVERTAKE_CONFIRM_TICKS = 2  # hold new position for N ticks before firing

# Per-driver list of (timestamp, fired_position) within the last
# RECENT_FIRE_WINDOW_S seconds. Used to suppress re-announcing the same
# position when a driver bounces between two slots (e.g. P14↔P15
# side-by-side with another car). Cleared on race start.
_recent_overtake_fires: dict = {}
RECENT_FIRE_WINDOW_S = 60.0

def check_position_changes(leaderboard, elapsed_time, total_time):
    global _last_positions, _last_pit_state, _position_detection_start_time, _pending_overtakes
    # ── Grace period: ignore the first 15 seconds of the race ────
    # At race start, all cars have LapDistPct ≈ 0 and Lap = 0/1,
    # so the RacePos sort is essentially random. Every tick produces
    # phantom lead changes and overtakes.  Allow positions to settle
    # before we start detecting changes.
    GRACE_SECONDS = 15
    if _position_detection_start_time is None:
        _position_detection_start_time = time.time()
    if time.time() - _position_detection_start_time < GRACE_SECONDS:
        return
    # Ensure map exists
    if not isinstance(_last_positions, dict):
        _last_positions = {}
    if not isinstance(_last_pit_state, dict):
        _last_pit_state = {}
    # Prefer the in-memory snapshot — the disk path was racing with
    # writers and returning empty lists almost every tick, blinding
    # the overtake detector for entire races. Memory snapshot is the
    # same source all other triggers use.
    if isinstance(leaderboard, dict):
        cars = leaderboard.get("cars") or []
    elif isinstance(leaderboard, list):
        cars = leaderboard
    else:
        cars = []
    if not cars:
        cars = get_leaderboard_snapshot() or []
    if not cars:
        # Last-ditch disk read (legacy path)
        cars = _load_leaderboard_from_disk() or []
    if not isinstance(cars, list) or len(cars) == 0:
        return
    # Load FDOTD
    fd = None
    fd_idx = None
    try:
        fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
        if os.path.exists(fd_path):
            with open(fd_path, "r", encoding="utf-8") as f:
                fd = json.load(f)
        if fd:
            fd_idx = safe_int(fd.get("CarIdx"))
    except Exception as e:
        print("[POSITION] FDOTD read error:", e)

    # ── Build pit-road lookup for this tick ──────────────────────
    # Uses the authoritative OnPitRoad boolean from live_leaderboard
    # (sourced from iRacing's CarIdxOnPitRoad telemetry variable).
    current_pit_state = {}
    for car in cars:
        ci = safe_int(car.get("CarIdx"))
        if ci is not None:
            current_pit_state[ci] = bool(car.get("OnPitRoad", False))

    # Cars that just entered the pit lane this tick (were on track
    # last tick, on pit road now).  We freeze their position to
    # prevent a cascade of false overtakes for everyone "behind".
    just_entered_pit = set()
    for ci, on_pit_now in current_pit_state.items():
        was_on_pit = _last_pit_state.get(ci, False)
        if on_pit_now and not was_on_pit:
            just_entered_pit.add(ci)

    # Set of car indices currently on pit road (this tick).
    cars_in_pit = {ci for ci, v in current_pit_state.items() if v}

    # ── Compute TRUE on-track positions from physical distance ──
    # total_dist = Lap + LapDistPct gives each car's exact progress
    # around the circuit.  Sorting descending (most distance first)
    # yields the real race order — immune to RacePos jitter.
    track_pos_map = {}
    valid_cars = []
    for car in cars:
        ci = safe_int(car.get("CarIdx"))
        if ci is None or ci < 0:
            continue
        try:
            lap = float(car.get("Lap", 0) or 0)
            dist = float(car.get("LapDistPct", 0.0) or 0.0)
        except Exception:
            continue
        valid_cars.append((ci, lap + dist, car))
    valid_cars.sort(key=lambda x: x[1], reverse=True)
    for rank, (ci, _, _) in enumerate(valid_cars, 1):
        track_pos_map[ci] = rank

    updated_positions = dict(_last_positions)
    # MAIN LOOP — CAR POSITION DELTA PROCESSING
    for car in cars:
        car_idx = safe_int(car.get("CarIdx"))
        if car_idx is None or car_idx < 0:
            continue
        name = clean_driver_name(car.get("Name", "Unknown"))
        # Use physical track-position computed above; fall back to
        # RacePos / LivePos only if the car wasn't in our sort.
        new_pos = track_pos_map.get(car_idx)
        if new_pos is None:
            raw_pos = car.get("RacePos", car.get("LivePos", car.get("Pos")))
            try:
                new_pos = int(raw_pos)
            except Exception:
                continue
        # Use authoritative OnPitRoad from live_leaderboard
        in_pit = current_pit_state.get(car_idx, False)
        # Normalize impossible values
        if new_pos <= 0:
            in_pit = True
            new_pos = 50
        # Ignore insane values (teleport spikes)
        if new_pos >= 200:
            continue
        old_pos = _last_positions.get(car_idx)
        # First sighting → store + continue
        if not isinstance(old_pos, int) or old_pos <= 0:
            updated_positions[car_idx] = new_pos
            continue

        # ── Pit-road position freeze ─────────────────────────────
        # If this car is on pit road OR just entered pit road,
        # silently update stored position without firing any
        # overtake/loss triggers.  The leaderboard sort pushes pit
        # cars to the bottom, which looks like a massive position
        # drop — but it's not a real on-track event.
        if in_pit or car_idx in just_entered_pit:
            updated_positions[car_idx] = new_pos
            continue

        # If this car "gained" positions but the car(s) they
        # displaced are actually in the pits, suppress the trigger.
        # Check: did any car currently in pit road have a stored
        # position between old_pos and new_pos (i.e. they were
        # "between" us and we only jumped because they pitted)?
        if new_pos < old_pos:
            # How many of the "passed" positions are pit cars?
            pit_displaced = 0
            for pit_ci in cars_in_pit:
                pit_stored = _last_positions.get(pit_ci)
                if isinstance(pit_stored, int) and new_pos <= pit_stored < old_pos:
                    pit_displaced += 1
            real_delta = (old_pos - new_pos) - pit_displaced
            if real_delta <= 0:
                # Entire position gain was from pit-outs, not overtakes
                updated_positions[car_idx] = new_pos
                continue

        # No movement or position loss → clear any pending confirmation
        if new_pos >= old_pos:
            _pending_overtakes.pop(car_idx, None)
        if new_pos == old_pos:
            updated_positions[car_idx] = new_pos
            continue
        # Delta: positive = gained positions
        delta = old_pos - new_pos
        is_fd = (fd_idx is not None and car_idx == fd_idx)
        # Reject insane NON-PIT spikes. The Focus Driver gets a wider
        # tolerance — a comeback drive from the back is the whole point
        # of a Focus Driver callout, and clamping their delta to +5 was
        # silencing P21→P2 runs entirely.
        spike_limit = 10 if is_fd else 5
        if abs(delta) > spike_limit and not in_pit:
            updated_positions[car_idx] = new_pos
            continue
        # POSITION GAINS (OVERTAKES)
        if delta > 0:
            # Rate-limit: no more than one overtake callout per driver
            # per N seconds. Lead changes bypass the limit so a pass
            # for P1 always fires.
            pass_for_lead = (new_pos == 1 and old_pos == 2)
            OVERTAKE_COOLDOWN_S = 4.0 if is_fd else 8.0
            now_w = time.time()
            last_fire = _last_overtake_fire_time.get(car_idx, 0.0)
            if not pass_for_lead and (now_w - last_fire) < OVERTAKE_COOLDOWN_S:
                # Within cooldown — silently accumulate the gain without
                # a callout, but keep stored position up to date so the
                # NEXT callout reports the true cumulative delta.
                updated_positions[car_idx] = new_pos
                continue

            # Suppress re-announcing a position we already announced
            # within the recent window. P14↔P15 side-by-side jitter used
            # to fire "Focus Driver gains, now P14" three times in 39s in
            # race 4 — once we've called him at P14, only fire again
            # when he reaches a strictly better position (P13 or
            # better) within the window. Window expires automatically.
            if not pass_for_lead:
                recent = [
                    (t, p) for (t, p) in _recent_overtake_fires.get(car_idx, [])
                    if (now_w - t) < RECENT_FIRE_WINDOW_S
                ]
                _recent_overtake_fires[car_idx] = recent
                best_announced = min((p for _, p in recent), default=999)
                if new_pos >= best_announced:
                    updated_positions[car_idx] = new_pos
                    continue

            # Confirmation window for ALL single-position gains, including
            # FD overtakes and pass-for-lead. Single-tick sort jitter at
            # S/F crossings (Lap and LapDistPct telemetry update one tick
            # apart) was producing phantom P2→P1 callouts followed by an
            # immediate "regain the lead" callout 1-3 seconds later, with
            # the FD-context echoing the real P3 a moment after that. The
            # original FD/lead bypass was added so multi-position comeback
            # jumps wouldn't be swallowed — delta >= 2 still bypasses
            # confirmation, which is the case that actually mattered.
            #
            # Relaxed semantics: a driver who keeps improving (Cherif
            # P19→P18→P17 over consecutive ticks) used to never confirm
            # because pending["new_pos"] required EQUALITY across ticks.
            # Now we accept any tick where new_pos has not REGRESSED,
            # so a steady gainer accumulates ticks normally.
            if delta == 1:
                pending = _pending_overtakes.get(car_idx)
                if pending and new_pos <= pending["new_pos"]:
                    pending["ticks"] += 1
                    pending["new_pos"] = new_pos
                else:
                    _pending_overtakes[car_idx] = {"new_pos": new_pos, "ticks": 1}
                if _pending_overtakes[car_idx]["ticks"] < OVERTAKE_CONFIRM_TICKS:
                    updated_positions[car_idx] = new_pos
                    continue
                _pending_overtakes.pop(car_idx, None)
            else:
                _pending_overtakes.pop(car_idx, None)
            if pass_for_lead and not in_pit:
                submit_trigger("become_leader", car_idx)
            # Commentary personality
            host, energy, location = commentary_team.get_host_and_energy(elapsed_time, total_time)

            # ─── LEAD CHANGE: dedicated prompt path ──────────────────
            if pass_for_lead and not in_pit:
                # Look up displaced leader (now at P2) from current cars list
                old_leader = ""
                for c2 in cars:
                    try:
                        if int(c2.get("RacePos", c2.get("LivePos", c2.get("Pos", 0)))) == 2:
                            old_leader = clean_driver_name(c2.get("Name", ""))
                            break
                    except Exception:
                        continue
                # Track name from weekend export
                track_name = ""
                try:
                    wpath = os.path.join("sector_said_exports", "weekend_info.json")
                    if os.path.exists(wpath):
                        with open(wpath, "r", encoding="utf-8") as wf:
                            wdata = json.load(wf)
                        track_name = (wdata.get("track_display_name")
                                      or wdata.get("track_name") or "")
                except Exception:
                    pass
                lc_char_limit = (commentary_team.trigger_settings
                                 .get("leader_change", {}).get("characters", 80))
                analysis = get_db_prompt("leader_change", {
                    "new_leader": name,
                    "old_leader": old_leader or "the previous leader",
                    "track_name": track_name or "the circuit",
                    "driver": name,
                })
                if not analysis:
                    analysis = (f"LEAD CHANGE! {name} takes P1 from "
                                f"{old_leader or 'the previous leader'} at "
                                f"{track_name or 'the circuit'}. Quick, dramatic, "
                                f"under {lc_char_limit} characters.")
                # Focus Driver context — so the lead change can tie back
                # to "and the Focus Driver is currently Px, +Xs behind".
                # Skip if the FD IS the new leader (they're already the
                # subject of the callout).
                if not is_fd:
                    fd_ctx = _build_fdotd_context(cars)
                    if fd_ctx:
                        analysis += f" {fd_ctx}"
                normalized_text = normalize_telemetry_for_ai(analysis)
                ai_text = openai_commentary.generate_commentary(normalized_text)
                if not in_pit:
                    submit_trigger("gain_position", car_idx)
                enqueue_commentary({
                    "prompt": ai_text,
                    "commentator": host,
                    "location": location,
                    "trigger_name": "Lead Change",
                    "type": "lead_change",
                    "clip_id": f"lead_change_{car_idx}_{int(time.time())}",
                    "car_idx": car_idx,
                })
                _fire_now = time.time()
                _last_overtake_fire_time[car_idx] = _fire_now
                _recent_overtake_fires.setdefault(car_idx, []).append((_fire_now, new_pos))
                updated_positions[car_idx] = new_pos
                continue
            # ─── REGULAR OVERTAKE PATH ───────────────────────────────
            # Identify the driver who was just passed (now at old_pos).
            rival_name = ""
            for c2 in cars:
                try:
                    c2_pos = track_pos_map.get(safe_int(c2.get("CarIdx")))
                    if c2_pos == old_pos and safe_int(c2.get("CarIdx")) != car_idx:
                        rival_name = clean_driver_name(c2.get("Name", ""))
                        break
                except Exception:
                    continue
            gained_s = "" if delta == 1 else f" (up {delta} places this move)"
            if is_fd:
                base_phrase = (f"{name} makes an overtake! They climb to P{new_pos}"
                               f"{gained_s}" + (f", passing {rival_name}." if rival_name else "."))
            else:
                base_phrase = (f"{name} gains a position — now up to P{new_pos}"
                               f"{gained_s}" + (f", past {rival_name}." if rival_name else "."))
            char_limit = (commentary_team.trigger_settings.get("overtake", {}).get("characters", 80))
            # AI prompt — try DB template first
            analysis = get_db_prompt("overtake", {
                "driver": name, "new_pos": str(new_pos), "rival": rival_name,
            })
            if not analysis:
                analysis = (f"Write a short, live-broadcast overtake alert: {base_phrase} "
                            f"Limit to {char_limit} characters.")
            # Focus Driver context — appended only if the overtaker
            # is NOT the Focus Driver (otherwise it would be redundant).
            if not is_fd:
                fd_ctx = _build_fdotd_context(cars)
                if fd_ctx:
                    analysis += f" {fd_ctx}"
            normalized_text = normalize_telemetry_for_ai(analysis)
            ai_text = openai_commentary.generate_commentary(normalized_text)
            # DOTD SCORING (DISABLED IN PITS)
            if not in_pit:
                submit_trigger("gain_position", car_idx)
                #submit_trigger("overtake", car_idx)
            # Commentary rules. Top-10 passes always air; for deeper in
            # the field we still call moves that broke into the top-15
            # OR that were made by the Focus Driver.
            allow_commentary = (
                (new_pos <= 15 and not in_pit)
                or is_fd
            )
            if allow_commentary:
                enqueue_commentary({
                    "prompt": ai_text,
                    "commentator": host,
                    "location": location,
                    "trigger_name": "Overtake Alert",
                    "type": "overtake",
                    "clip_id": f"overtake_{car_idx}_{int(time.time())}",
                    "car_idx": car_idx,
                })
                _fire_now = time.time()
                _last_overtake_fire_time[car_idx] = _fire_now
                _recent_overtake_fires.setdefault(car_idx, []).append((_fire_now, new_pos))
        # POSITION LOSS (BEING PASSED / PITTING)
        elif delta < 0:
            # Losing positions *in pits* → NO SCORING
            if not in_pit:
                submit_trigger("being_passed", car_idx)
            # Losing the lead ALWAYS scores
            if old_pos == 1 and new_pos > 1:
                submit_trigger("lost_lead", car_idx)
        # Update stored position
        updated_positions[car_idx] = new_pos
    # Save for next tick
    _last_positions = updated_positions
    _last_pit_state = current_pit_state


def check_lead_lap_dotd(leaderboard, elapsed_time, total_time, ir=None):
    global _last_leader_lap_recorded
    if not leaderboard:
        return
    def safe_int_local(v, default=0):
        try: return int(v)
        except: return default
    # -----------------------------
    # 1. Find leader
    # -----------------------------
    leader = None
    best_pos = 9999
    for c in leaderboard:
        pos = (c.get("LivePos") or c.get("Position") or c.get("Pos")
               or c.get("ClassPosition") or None)
        pos_int = safe_int_local(pos, 9999)
        if 0 < pos_int < best_pos:
            best_pos = pos_int
            leader = c
    if not leader:
        return
    car_idx = leader.get("CarIdx")
    if car_idx is None:
        return
    # -----------------------------
    # 2. Correct way: get completed laps
    # -----------------------------
    leader_lap = 0
    if ir:
        try:
            leader_lap = int(ir['CarIdxLapCompleted'][car_idx])
        except:
            leader_lap = 0
    else:
        # fallback if ir unavailable
        raw_lap = leader.get("Lap") or leader.get("CompletedLaps") or 0
        leader_lap = safe_int_local(raw_lap, 0)
    # -----------------------------
    # 3. Block lap 0 + lap 1
    # -----------------------------
    if leader_lap < 1:
        return
    # -----------------------------
    # 4. Prevent dupes
    # -----------------------------
    if leader_lap <= _last_leader_lap_recorded:
        return
    # -----------------------------
    # 5. Award DOTD
    # -----------------------------
    submit_trigger("lead_lap", car_idx)
    _last_leader_lap_recorded = leader_lap

        
# ============================================================
# PIT ENTRY — UPDATED FOR CURRENT ENGINE
# ============================================================
def check_pit_events(leaderboard, elapsed_time=None, total_time=None, ir=None):
    global _last_in_pit, _last_ever_ontrack, _last_pit_phrase
    if ir is None:
        return
    if not leaderboard:
        print("[PIT DEBUG] leaderboard empty")
        return
    # CarIdxTrackSurface (fallback-safe)
    try:
        track_surface = sector_says_utilities.safe_session_var(
            ir, "CarIdxTrackSurface", []
        ) or []
    except Exception as e:
        print("[PIT DEBUG] ERROR reading CarIdxTrackSurface:", e)
        track_surface = []
    # Prepare globals
    if "_last_in_pit" not in globals():
        _last_in_pit = {}
    if "_last_ever_ontrack" not in globals():
        _last_ever_ontrack = {}
    now = int(time.time())
    # Load FDOTD (for highlight logic)
    fd_record = None
    fd_name = None
    fd_idx = None
    try:
        fd_path = os.path.join("sector_said_exports", "focus_driver_of_the_day.json")
        if os.path.exists(fd_path):
            with open(fd_path, "r", encoding="utf-8") as f:
                fd_record = json.load(f)
        if fd_record:
            fd_name = clean_driver_name(fd_record.get("Name") or "")
            fd_idx = safe_int(fd_record.get("CarIdx"))
    except Exception as e:
        print("[PIT DEBUG] FDOTD read error:", e)
    # Loop over cars
    for car in leaderboard:
        idx = safe_int(car.get("CarIdx"))
        raw_name = car.get("Name", f"Car {idx}")
        name = clean_driver_name(raw_name.replace("[PIT]", "").strip())
        # Fallback-safe surface read
        surface = track_surface[idx] if idx < len(track_surface) else 0
        # Modern pit lane definition:
        # 1 = apron, 2 = pitlane, 4 = pitstall
        in_pit = surface in (1, 2, 4)
        prev_in_pit = _last_in_pit.get(idx, False)
        # Track if a driver has ever been on track
        if surface in (1, 2, 3, 4):
            _last_ever_ontrack[idx] = True
            #print(f"[PIT DEBUG] {name}: surface={surface} (Fallback), ever_on_track=True")
        else:
            pass #print(f"[PIT DEBUG] {name}: surface={surface} (Fallback)")
        # Skip pit entries for cars that never joined the track
        if not _last_ever_ontrack.get(idx, False):
            _last_in_pit[idx] = in_pit
            continue
        # PIT ENTRY TRIGGER
        if in_pit and not prev_in_pit:
            # Rate-limit pit callouts: when an AI race runs a sync pit
            # window you can get 15+ cars pitting in 20 seconds, which
            # drowns out everything else. We always fire the audio for
            # the Focus Driver and top-3 cars; everyone else shares a
            # global 10s cooldown so we call one midfielder per window
            # but not every single one.
            is_focus = (fd_idx == idx or
                        (fd_name and name.lower() == fd_name.lower()))
            try:
                car_pos = safe_int(car.get("LivePos") or car.get("RacePos") or car.get("Pos") or 99)
            except Exception:
                car_pos = 99
            is_headline = is_focus or (car_pos and car_pos <= 3)

            if "_last_pit_call_time" not in globals():
                global _last_pit_call_time
                _last_pit_call_time = 0.0
            PIT_AUDIO_COOLDOWN_S = 10.0
            wall = time.time()
            allow_audio = is_headline or (wall - _last_pit_call_time >= PIT_AUDIO_COOLDOWN_S)

            phrase_options = [
                f"{name} enters the pits.",
                f"{name} heads to pit lane.",
                f"{name} comes in for service.",
                f"{name} dives into the pits.",
            ]
            if is_focus:
                phrase_options.append(f"{name} enters the pits.")
            if "_last_pit_phrase" not in globals():
                _last_pit_phrase = None
            non_repeat = [p for p in phrase_options if p != _last_pit_phrase]
            if not non_repeat:
                non_repeat = phrase_options
            phrase = random.choice(non_repeat)
            _last_pit_phrase = phrase

            host = commentary_team.trigger_settings.get("pit_entry", {}).get("host", "Murray")
            commentator_info = commentary_team.commentators.get(host, {})
            location = commentator_info.get("location", "Pit Lane")
            char_limit = (commentary_team.trigger_settings.get("pit_entry", {}).get("characters", 80))
            analysis = get_db_prompt("pit_entry", {"driver": name})
            if not analysis:
                analysis = (f"Write a short, live-broadcast pit entry: {phrase} Limit to {char_limit} characters.")
            normalized_text = normalize_telemetry_for_ai(analysis)

            if allow_audio:
                ai_text = openai_commentary.generate_commentary(normalized_text)
                enqueue_commentary({
                    "prompt": ai_text,
                    "commentator": host,
                    "location": location,
                    "trigger_name": "pit_entry",
                    "type": "pit_entry",
                    "clip_id": f"pit_entry_{now}_{idx}",
                    "car_idx": idx,
                })
                _last_pit_call_time = wall
        # ---------------------------------------------------
        # STORE LAST STATE
        _last_in_pit[idx] = in_pit

# ============================================================
# FLAG CHANGE DETECTOR
# ============================================================
# iRacing exposes a single SessionFlags bitmask that can contain
# many flags simultaneously (e.g. YELLOW + CAUTION + DEBRIS all at
# once during a full-course caution). We cache the last seen mask
# and, on each tick, find bits that just flipped 0→1 ("raised") or
# 1→0 ("cleared"). Each raised bit we care about fires its own
# commentary trigger, with per-flag cooldowns so repeated full-course
# cautions in a short window don't spam the AI.
#
# Flag → trigger_type mapping. Bits not listed here are ignored on
# purpose (e.g. the START_* bits from the pre-race sequence) or
# handled by older dedicated triggers (WHITE / CHECKERED).
_FLAG_TRIGGER_MAP = [
    # (flag_name, trigger_type, priority, cooldown_s, fallback_phrase)
    ("RED",             "red_flag",       10,  20, "RED FLAG — session halted."),
    ("CHECKERED",       None,              0,   0, None),   # handled elsewhere
    ("WHITE",           None,              0,   0, None),   # handled elsewhere
    ("CAUTION_WAVING",  "caution",         8,  30, "Full-course caution, waving yellows."),
    ("CAUTION",         "caution",         8,  30, "Full-course caution."),
    ("YELLOW_WAVING",   "yellow_flag",     7,  15, "Waving yellow on track — drivers must slow."),
    ("YELLOW",          "yellow_flag",     7,  15, "Yellow flag — local caution."),
    ("DEBRIS",          "debris",          7,  20, "Debris reported on the racing surface."),
    ("BLUE",            "blue_flag",       5,  20, "Blue flag — faster traffic approaching."),
    ("BLACK",           "black_flag",      6,  30, "Black flag issued — driver must report to pit lane."),
    ("DISQUALIFY",      "disqualify",      9, 120, "Disqualification issued."),
    ("REPAIR",          "meatball_repair", 6,  30, "Meatball flag — mandatory repair required."),
    ("CROSSED",         "crossed_flag",    3,  60, "Crossed flags — the field is past the halfway mark."),
    ("ONE_TO_GREEN",    "one_to_green",    5,  60, "One lap to green — the race is about to go."),
    ("GREEN_HELD",      "green_held",      4,  60, "Start is being held."),
    ("GREEN",           "green_flag",      6,  30, "Green flag — racing is underway."),
    ("LAPS_10_TO_GO",   "laps_10_to_go",   4, 300, "Ten laps to go."),
    ("LAPS_5_TO_GO",    "laps_5_to_go",    5, 300, "Five laps to go."),
]

_last_flags_mask: int = 0
_last_flag_fire_time: Dict[str, float] = {}

def check_flag_changes(flags: int, elapsed_time=None, total_time=None, ir=None):
    """Edge-detect session flag changes and fire a commentary trigger for
    each bit that just rose. Uses per-flag cooldowns and skips flags that
    already have their own dedicated trigger path (WHITE, CHECKERED)."""
    global _last_flags_mask, _race_end_scored, _race_finished, _pre_race_show_fired

    if not isinstance(flags, int):
        try:
            flags = int(flags or 0)
        except Exception:
            flags = 0

    prev = _last_flags_mask
    if flags == prev:
        return
    raised = flags & ~prev  # bits that just turned on
    _last_flags_mask = flags

    # ── Race-start / restart: reset practice pit counts ──
    # Practice pit-stop counts were leaking into the race because the
    # edge detector in live_leaderboard ran the whole time. On any
    # GREEN raised-edge (start or restart) clear the per-car pit
    # counters so the race starts clean.
    green_bit = FLAGS.get("GREEN") or 0
    checkered_bit = FLAGS.get("CHECKERED") or 0
    if green_bit and (raised & green_bit):
        # Only reset race state if CHECKERED is NOT currently active.
        # After the chequered flag iRacing can raise GREEN (pit lane /
        # cool-down) — that must NOT reopen the rolling-trigger gate.
        is_post_checkered = bool(checkered_bit and (flags & checkered_bit))
        try:
            if not is_post_checkered:
                import live_leaderboard as _ll
                if hasattr(_ll, "_pit_state"):
                    for ci in list(_ll._pit_state.keys()):
                        _ll._pit_state[ci] = {
                            "stops": 0, "on_pit": False,
                            "entered_lap": None, "entered_time": None,
                            "last_stop_lap": None, "last_stop_duration": None,
                        }
                    print("[PIT] State reset on GREEN flag")
                # Reset position detection grace period so the first
                # 15 seconds of the new race are silent.
                global _position_detection_start_time
                _position_detection_start_time = time.time()
                # New race — let race_end bonuses fire again and re-open
                # the rolling-trigger gate
                _race_end_scored = False
                _race_finished = False
                _pre_race_show_fired = True  # show already aired — don't
                                             # re-fire once we're actually
                                             # past the green flag
                # Reset one-shot flag triggers so the white/checkered
                # callouts and the focus-driver summary can fire fresh
                # on the new race.
                trigger_fired["white_flag"] = False
                trigger_fired["checkered_flag"] = False
                trigger_fired["focus_driver_summary"] = False
            else:
                print("[FLAGS] GREEN raised while CHECKERED active — "
                      "skipping race-state reset (cool-down lap)")
        except Exception as e:
            print(f"[PIT] reset on green failed: {e}")

    # ── Race end: fire rating-delta + race_finish bonuses once,
    #    and flip the rolling-trigger gate so subsequent cool-down
    #    position changes don't fire fresh overtake/lead-change
    #    commentary. iRacing raises CHECKERED when the leader takes
    #    the flag, and from that moment on any "new leader" the
    #    telemetry reports is just the winner slowing while cars
    #    that already finished drive past. ──
    if checkered_bit and (raised & checkered_bit):
        try:
            lb = get_leaderboard_snapshot() or []
            score_race_end(lb)
        except Exception as e:
            print(f"[RACE END] scoring failed: {e}")
        _race_finished = True
        print("[RACE END] Rolling triggers gated off — race is over")

    if raised == 0:
        return

    now_ts = time.time()

    for flag_name, trigger_type, _prio, cooldown, fallback in _FLAG_TRIGGER_MAP:
        if trigger_type is None:
            continue
        bit = FLAGS.get(flag_name)
        if not bit or not (raised & bit):
            continue

        # Cooldown check
        last = _last_flag_fire_time.get(trigger_type, 0.0)
        if now_ts - last < cooldown:
            continue
        _last_flag_fire_time[trigger_type] = now_ts

        # Host / location routing — fall back to a booth voice if not set
        tset = commentary_team.trigger_settings.get(trigger_type, {})
        try:
            host, energy, location = commentary_team.get_host_and_energy(
                elapsed_time or 0.0, total_time or 1.0
            )
        except Exception:
            host = tset.get("host", "Murray")
            location = "booth"
        char_limit = tset.get("characters", 80)

        # DB prompt if available, otherwise use the factual fallback wrapped
        # in a minimal instruction. Ground rules (in openai_commentary) stop
        # the AI from inventing cause, severity, or driver names.
        #
        # For GREEN flags: tell the AI whether there was a preceding caution
        # so it doesn't fabricate "resumes after caution" on a race start.
        # For YELLOW / CAUTION: tie back to a recent on-track incident if
        # one fired in the last ~10 seconds. Without this, every yellow
        # gets the generic "Yellow flag — local caution" line and the
        # broadcast misses the cause-and-effect.
        extra_context = ""
        if flag_name == "GREEN":
            yellow_bit = FLAGS.get("YELLOW") or FLAGS.get("CAUTION") or 0
            had_caution = bool(yellow_bit and (prev & yellow_bit))
            if had_caution:
                extra_context = " This is a restart after a caution period."
            else:
                extra_context = (" This is the race start — there has been "
                                 "no caution. Do NOT mention a caution or restart.")
                # Enrich the race-start GREEN prompt with grid context so
                # the broadcast doesn't open with a generic "Green flag!
                # Drivers accelerate off the line!" — name the pole
                # sitter and P2, give the field size, mention the FD if
                # they're starting from a notable position.
                try:
                    grid_lb = get_leaderboard_snapshot() or []
                    grid_sorted = sorted(
                        grid_lb,
                        key=lambda c: safe_int(
                            c.get("GridPosition") or c.get("LivePos") or 9999
                        ),
                    )
                    pole = grid_sorted[0] if grid_sorted else None
                    p2 = grid_sorted[1] if len(grid_sorted) > 1 else None
                    field = len(grid_sorted)
                    fd_data = None
                    try:
                        fd_data = _load_focus_driver()
                    except Exception:
                        pass
                    fd_text = ""
                    if fd_data and fd_data.get("Name"):
                        fd_name = clean_driver_name(fd_data["Name"]).strip().lower()
                        for c in grid_sorted:
                            if clean_driver_name(c.get("Name", "")).strip().lower() == fd_name:
                                fd_pos = safe_int(c.get("GridPosition") or c.get("LivePos"))
                                if fd_pos and fd_pos > 3:
                                    fd_text = (
                                        f" Focus Driver {fd_data['Name']} "
                                        f"starts P{fd_pos} — note their "
                                        f"position briefly."
                                    )
                                break
                    grid_bits = []
                    if pole and pole.get("Name"):
                        grid_bits.append(
                            f"pole: {clean_driver_name(pole['Name'])}"
                        )
                    if p2 and p2.get("Name"):
                        grid_bits.append(
                            f"P2: {clean_driver_name(p2['Name'])}"
                        )
                    if field:
                        grid_bits.append(f"{field} cars on the grid")
                    if grid_bits:
                        extra_context += (
                            " Grid context: " + ", ".join(grid_bits) + "."
                            + fd_text
                            + " Open the broadcast with names — mention pole "
                            "and P2 by name. Do not invent battles, "
                            "side-by-side action, or grid-row details."
                        )
                except Exception as e:
                    print(f"[FLAG] GREEN grid-context build failed: {e}")
        elif flag_name in ("YELLOW", "YELLOW_WAVING", "CAUTION", "CAUTION_WAVING", "DEBRIS"):
            try:
                import collision_tracker
                recent = collision_tracker.get_recent_incidents(within_s=10.0)
            except Exception:
                recent = []
            if recent:
                # Resolve car_idx -> driver name from the live snapshot.
                lb_snap = get_leaderboard_snapshot() or []
                idx_to_name = {}
                for c in lb_snap:
                    ci = safe_int(c.get("CarIdx"))
                    if ci is not None:
                        idx_to_name[ci] = clean_driver_name(c.get("Name", ""))
                drivers_seen = []
                for e in recent:
                    nm = idx_to_name.get(e.get("car_idx"))
                    if nm and nm not in drivers_seen:
                        drivers_seen.append(nm)
                    if len(drivers_seen) >= 2:
                        break
                if drivers_seen:
                    if len(drivers_seen) == 1:
                        extra_context = (
                            f" Recent incident in the last few seconds: "
                            f"{drivers_seen[0]} had trouble — the most likely "
                            f"cause of the caution. Reference this driver by "
                            f"name; do not invent location or further detail."
                        )
                    else:
                        extra_context = (
                            f" Recent incidents in the last few seconds "
                            f"involve {drivers_seen[0]} and {drivers_seen[1]}. "
                            f"Reference them by name; do not invent location "
                            f"or further detail."
                        )
        db_p = get_db_prompt(trigger_type, {
            "flag": flag_name,
            "char_limit": str(char_limit),
        })
        if db_p:
            prompt = db_p + extra_context
        else:
            prompt = (
                f"{fallback}{extra_context} Report the flag state factually. "
                f"Do not invent the cause, the location on track, or which "
                f"driver is involved unless that information is stated here. "
                f"Under {char_limit} characters."
            )
        prompt = normalize_telemetry_for_ai(prompt)

        try:
            ai_text = openai_commentary.generate_commentary(prompt)
        except Exception as e:
            print(f"[FLAG] {flag_name} AI failed: {e}")
            ai_text = fallback

        try:
            enqueue_commentary({
                "prompt": ai_text,
                "commentator": host,
                "location": location,
                "trigger_name": flag_name.replace("_", " ").title() + " Flag",
                "type": trigger_type,
                "clip_id": f"{trigger_type}_{int(now_ts)}",
            })
            print(f"[FLAG] {flag_name} -> {trigger_type} fired")
        except Exception as e:
            print(f"[FLAG] enqueue failed for {trigger_type}: {e}")


def check_triggers(ir,elapsed_time: float,total_time: float,flags: int,state: Dict[str, Any],session_info: Dict[str, Any],track_info: Dict[str, Any] = None,):
    """Main orchestrator for all commentary triggers."""
    global DEBUG_MODE
    if session_info is None:
        session_info = {}
    if state is None:
        state = {}
    # ── PRE-RACE GATE ─────────────────────────────────────────────
    # Belt-and-braces check: do NOT fire rolling commentary triggers
    # unless the iRacing *Race* session is actually racing
    # (SessionState == 4). Practice and Qualify are silent. The one
    # exception is the pre-race prediction show, which fires ONCE
    # in the Warmup/ParadeLaps window right before green.
    try:
        race_active = sector_says_utilities.is_race_session_active(ir)
    except Exception:
        race_active = False
    if not race_active:
        # Exception: try to run the pre-race show if we're sitting
        # in the race session's grid window (warmup, parade lap, or
        # one-to-green raised). Runs at most once per race.
        try:
            # Pull a fresh snapshot — leaderboard in check_triggers
            # was built after the gate check, but the show needs it
            # too, so we grab it here directly from the module.
            lb_for_show = get_leaderboard_snapshot() or []
            check_pre_race_show(ir, lb_for_show)
        except Exception as e:
            print(f"[TRIGGERS] pre-race show check failed: {e}")
        if DEBUG_MODE:
            print("[TRIGGERS] Not in Race session — silent (pre-race show may have fired)")
        return
    # 1. LEADERBOARD SNAPSHOT
    raw_lb = get_leaderboard_snapshot()
    if isinstance(raw_lb, list):
        leaderboard = raw_lb
    elif raw_lb is None:
        leaderboard = []
    else:
        try:
            leaderboard = list(raw_lb)
        except TypeError:
            if DEBUG_MODE:
                print("[TRIGGERS] Unexpected leaderboard type:", type(raw_lb))
            leaderboard = []
    leaderboard = [c for c in leaderboard if isinstance(c, dict)]
    # Leaderboard diagnostics
    try:
        path = os.path.join("sector_said_exports", "leaderboard.json")
        if not os.path.exists(path):
            print("[DEBUG] leaderboard.json DOES NOT EXIST:",
                  os.path.abspath(path))
        else:
            size = os.path.getsize(path)
            if size < 10:
                print("[DEBUG] leaderboard.json is EMPTY or TOO SMALL.")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as je:
                print("[DEBUG] JSON LOAD ERROR:", je)
    except Exception as dd:
        print("[DEBUG] Snapshot Diagnostic Error:", dd)
    # Reject empty snapshots
    if not leaderboard:
        if DEBUG_MODE:
            print("[TRIGGERS] No valid leaderboard snapshot.")
        return
    if len(leaderboard) < 3:
        if DEBUG_MODE:
            print("[TRIGGERS] Leaderboard too small for triggers.")
        return
    # ---------------------------
    # 2. FLAG TRIGGERS
    # ---------------------------
    # Flag detection ALWAYS runs — this is what sets _race_finished
    # in the first place, and we still want post-checkered flag
    # commentary like the winner callout to fire.
    check_flag_changes(flags, elapsed_time, total_time, ir=ir)
    white_flag_final_lap(elapsed_time, total_time, ir, leaderboard, flags)
    checkered_flag_commentary(elapsed_time, total_time, ir, leaderboard, flags)

    # ── RACE-OVER GATE ───────────────────────────────────────────
    # Once the leader has taken the chequered flag, stop firing
    # rolling race-state triggers. The winner slowing on their
    # cool-down lap while other (already-finished) cars drive past
    # would otherwise generate bogus "new leader" / "overtake" /
    # "midfield battle" commentary. Flag callouts, pit events, and
    # the one-shot race-end bonuses above this line still run.
    if _race_finished:
        if DEBUG_MODE:
            print("[TRIGGERS] Race finished — skipping rolling triggers")
        return

    # ── WHITE-FLAG LULL ──────────────────────────────────────────
    # After the white-flag commentary fires, hold everything until
    # the leader takes the chequered flag. The only lines we want to
    # hear between WF and CK are:
    #   1. The end-of-race summary triggered by checkered_flag_commentary()
    #      above this gate, when the leader actually crosses S/F.
    #   2. ONE focus-driver final-lap summary, slotted in once the
    #      white-flag audio has finished playing (queue idle check
    #      avoids talk-over per invariant 1.5).
    if trigger_fired.get("white_flag"):
        if not trigger_fired.get("focus_driver_summary"):
            try:
                from commentary_queue import is_commentary_playing
                if not is_commentary_playing():
                    focus_driver_white_flag_summary(
                        elapsed_time, total_time, ir, leaderboard
                    )
            except Exception as _e:
                if DEBUG_MODE:
                    print(f"[FD SUMMARY] dispatch failed: {_e}")
        if DEBUG_MODE:
            print("[TRIGGERS] White flag raised — holding rolling triggers until checkered")
        return

    # ---------------------------
    # DOTD lead lap scoring
    # ---------------------------
    try:
        leader = leaderboard[0]
        leader_idx = safe_int(leader.get("CarIdx"))
        leader_lap = safe_int(leader.get("Lap"))
        global _last_leadlap_scored
        if "_last_leadlap_scored" not in globals():
            _last_leadlap_scored = {}
        prev = _last_leadlap_scored.get(leader_idx, -1)
        if leader_lap > 1 and leader_lap != prev:
            # Use DOTD trigger pipeline instead of a non-existent add_event()
            submit_trigger("lead_lap", leader_idx)
            _last_leadlap_scored[leader_idx] = leader_lap
            if DEBUG_MODE:
                print(
                    f"[DOTD] lead_lap awarded to {leader.get('Name')} "
                    f"(Lap {leader_lap})"
                )
    except Exception as e:
        print("[DOTD] lead_lap scoring error:", e)
    # ---------------------------
    # 3. PIT EVENTS
    # ---------------------------
    check_pit_events(leaderboard, elapsed_time, total_time, ir=ir)
    # ---------------------------
    # 4. (Spins/Off-track reserved — currently disabled)
    # ---------------------------
    # check_spins_offtrack(...)
    # ---------------------------
    # 5. FASTEST LAP / SECTORS
    # ---------------------------
    check_lead_lap_dotd(leaderboard, elapsed_time, total_time, ir=ir)
    check_fastest_lap_and_sectors(leaderboard, elapsed_time, total_time, ir=ir)
    # ---------------------------
    # 6. POSITION CHANGES / OVERTAKES
    # ---------------------------
    check_position_changes(leaderboard, elapsed_time, total_time)
    # ---------------------------
    # 6b. DOTD CLEAN LAPS / INCIDENTS / COMEBACK
    # ---------------------------
    check_clean_scoring(leaderboard, elapsed_time, total_time, ir=ir)
    # ---------------------------
    # 7. MIDFIELD BATTLES
    # ---------------------------
    check_midfield_battles(leaderboard, elapsed_time, total_time, ir)
    # ---------------------------
    # 7.5 PERIODIC RACE SUMMARY
    # ---------------------------
    # Gate: skip race summary if a higher-priority trigger (fastest lap,
    # overtake, midfield battle) already enqueued commentary this tick.
    try:
        from commentary_queue import is_commentary_playing
        _skip_summary = is_commentary_playing()
    except Exception:
        _skip_summary = False
    if not _skip_summary:
        check_periodic_race_summary(
            leaderboard, ir, session_info, elapsed_time, total_time
        )
    # ---------------------------
    # 8. FRACTIONAL TRIGGERS
    # ---------------------------
    # Gate: if commentary was just enqueued this tick (by race summary,
    # midfield battle, fastest lap, etc.), skip fractional triggers to
    # prevent two commentary items landing back-to-back in the stream.
    try:
        from commentary_queue import is_commentary_playing
        if is_commentary_playing():
            return
    except Exception:
        pass

    try:
        progress = (
            session_info.get("Progress", 0.0)
            if isinstance(session_info, dict)
            else 0.0
        )
    except Exception:
        progress = 0.0

    run_fractional_triggers(
        progress, leaderboard, session_info, state, ir, track_info
    )
