"""Edge-detect per-driver lap completions and persist one row per
crossing to race_lap_samples.

The replay animation reads these rows to position dots with iRacing-
accurate timing: each (car_idx, lap_number, session_time_s) triple is
the moment that car crossed start/finish, and intervals are derived
from the time spread at each lap boundary.

Call flow:
  - reset(race_id) on race start (new session, no stale state)
  - capture(cars, session_time_s) once per engine tick

We seed state on a car's first appearance without writing a row —
we only record crossings we actually observed, not laps the car
completed before the engine started watching.
"""
from typing import Dict, Tuple

import race_db

_last_recorded: Dict[Tuple[int, int], int] = {}


def reset(race_id: int = None) -> None:
    """Drop cached edge-state. Pass a race_id to clear only that race;
    omit to wipe everything (safe on engine startup)."""
    global _last_recorded
    if race_id is None:
        _last_recorded = {}
    else:
        _last_recorded = {k: v for k, v in _last_recorded.items() if k[0] != race_id}


def capture(cars: list, session_time_s: float) -> int:
    """Write one sample per car that has just ticked over a new lap.
    Returns the number of rows written (0 if none)."""
    rid = race_db.current_race_id()
    if rid is None or not cars:
        return 0

    written = 0
    for car in cars:
        if not isinstance(car, dict):
            continue
        ci_raw = car.get("CarIdx")
        if ci_raw is None:
            continue
        try:
            ci = int(ci_raw)
        except (TypeError, ValueError):
            continue
        try:
            lap = int(car.get("Lap") or 0)
        except (TypeError, ValueError):
            continue

        key = (rid, ci)
        prev = _last_recorded.get(key)
        if prev is None:
            _last_recorded[key] = lap
            continue
        if lap <= prev:
            continue

        try:
            last_time = float(car.get("Last") or 0.0)
            if last_time <= 0:
                last_time = None
        except (TypeError, ValueError):
            last_time = None

        cust_id = car.get("CustId")
        try:
            cust_id = int(cust_id) if cust_id is not None else None
        except (TypeError, ValueError):
            cust_id = None

        try:
            pos = int(car.get("LivePos") or 0) or None
        except (TypeError, ValueError):
            pos = None

        race_db.log_lap_sample(
            car_idx=ci,
            lap_number=lap,
            cust_id=cust_id,
            session_time_s=float(session_time_s) if session_time_s else None,
            lap_time_s=last_time,
            position=pos,
            source="live",
        )
        _last_recorded[key] = lap
        written += 1

    return written
