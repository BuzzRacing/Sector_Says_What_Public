
"""rules_engine.py — central rules & phrasing helpers for Sector Says What

This module is intentionally pure logic: no calls to iRacing, OpenAI, or TTS.

It takes raw data (leaderboard, focus driver record, DOTD stats, flags) and
returns semantic evaluations the rest of the engine can use, such as:

- Which driver is Focus Driver right now?
- How should we describe Driver of the Day and its percentages?
- Is a given event a "big moment" or a minor one?
- How should we speak gaps and thousandths of a second?

These helpers are meant to sit in the middle of the pipeline:

    Data → rules_engine → prompt builder / AI → TTS → playback
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import math

# -----------------------------
# Data containers
# -----------------------------

@dataclass
class DriverSnapshot:
    car_idx: int
    name: str
    position: Optional[int] = None
    gap_ahead: Optional[float] = None  # seconds
    gap_leader: Optional[float] = None  # seconds
    is_focus: bool = False
    is_dotd: bool = False
    dotd_percent: Optional[float] = None

@dataclass
class RaceContext:
    """
    High-level view of the current race state used by the rules layer.
    """
    flags: int
    session_flags_human: List[str]
    focus_driver_name: Optional[str]
    dotd_driver_name: Optional[str]
    dotd_percent: Optional[float]
    cars: List[DriverSnapshot]

# -----------------------------
# Core helpers
# -----------------------------

def find_driver_by_name(cars: List[DriverSnapshot], name: Optional[str]) -> Optional[DriverSnapshot]:
    if not name:
        return None
    name_l = name.lower()
    for c in cars:
        if c.name.lower() == name_l:
            return c
    return None

def is_big_moment_flag(flag: str, car: Optional[DriverSnapshot]) -> bool:
    """Business logic for whether a flag event is a "big" commentary moment.

    This is intentionally opinionated but easy to tweak.
    """
    flag = (flag or "").lower()

    # Green flag is intentionally quiet
    if flag == "green":
        return False

    # Blue flags are only a moment if they involve the Focus Driver.
    if flag == "blue":
        return bool(car and car.is_focus)

    # Meatball/lucky dog-style penalties:
    if flag in ("meatball", "black", "meatball_flag"):
        # Always big for Focus Driver, otherwise only for top 10.
        if car and car.is_focus:
            return True
        if car and car.position is not None and car.position <= 10:
            return True
        return False

    # Yellow is always "worth talking about".
    if flag == "yellow":
        return True

    # White/checkered: handled by dedicated intro/outro triggers.
    if flag in ("white", "checkered"):
        return False

    return False

def choose_flag_commentary_style(flag: str, car: Optional[DriverSnapshot]) -> str:
    """Return a short style label used by the prompt layer.

    The commentary code can map this to host, tone, and character limits.
    """
    flag = (flag or "").lower()
    if flag == "yellow":
        if car and car.position is not None and car.position <= 5:
            return "yellow_front_runner"
        return "yellow_general"
    if flag == "blue":
        return "blue_focus" if car and car.is_focus else "blue_background"
    if flag in ("meatball", "black", "meatball_flag"):
        return "penalty_focus" if car and car.is_focus else "penalty_midfield"
    if flag == "green":
        return "green_quiet"
    return "generic_flag"

# -----------------------------
# Driver of the Day helpers
# -----------------------------

def format_dotd_summary(dotd_name: Optional[str], dotd_percent: Optional[float]) -> str:
    if not dotd_name:
        return "Driver of the Day voting hasn't taken shape yet."
    if dotd_percent is None:
        return f"{dotd_name} currently leads the Driver of the Day voting."
    # Round to nearest whole percent for speech
    pct = int(round(float(dotd_percent)))
    return f"{dotd_name} leads the Driver of the Day voting with about {pct} percent of the vote."

# -----------------------------
# Gap / time phrasing
# -----------------------------

def format_gap_seconds(value: Optional[float]) -> str:
    """Format gaps including thousandths in a way that's nice to speak.

    Examples:
        0.0      → "dead even"
        0.143    → "one hundred and forty three thousandths"
        0.856    → "eight hundred and fifty six thousandths"
        1.234    → "one point two three four seconds"
    """
    if value is None:
        return "no gap information"

    try:
        v = float(value)
    except Exception:
        return "no gap information"

    if abs(v) < 1e-3:
        return "dead even"

    if abs(v) < 1.0:
        # speak as thousandths
        thousandths = int(round(abs(v) * 1000))
        return f"about {thousandths} thousandths of a second"

    # 1 second or more: keep one or three decimals depending on magnitude
    if abs(v) < 10:
        spoken = f"{v:.3f}"
    else:
        spoken = f"{v:.1f}"
    return f"about {spoken} seconds"

# -----------------------------
# Unusual behaviour helpers
# -----------------------------

def is_unusual_pit_entry(car: DriverSnapshot, field_size: int) -> bool:
    """Return True if this pit event is unusual enough to call out.

    Examples:
        - P2 diving into the pits alone under green
        - Anyone in the top 5 pitting off-strategy
    """
    if not car or car.position is None:
        return False

    # Simple first cut: top 5 is always interesting
    if car.position <= 5:
        return True

    # Small fields: top third of the field
    if field_size > 0 and car.position <= max(3, field_size // 3):
        return True

    return False
