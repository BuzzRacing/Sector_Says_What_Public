"""Import a race from the GrumpyRobot iRacing API into race_history.db.

Usage:
    python import_grumpyrobot_race.py <race_id>
    python import_grumpyrobot_race.py 84288510 --host http://localhost:5000

Maps the GrumpyRobot per-driver payload to the local races / race_drivers
schema so it shows up in /replays.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "race_history.db")


def fetch(host: str, race_id: int) -> dict:
    url = f"{host.rstrip('/')}/iracing/race/{race_id}/data"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _clean(s: str) -> str:
    """GrumpyRobot serves some strings with U+FFFD replacement chars where
    accents should be. Strip them and collapse the resulting double spaces."""
    if not s:
        return s
    return re.sub(r"\s+", " ", s.replace("\ufffd", "")).strip()


def parse_track(narrative: str) -> tuple[str, str | None]:
    """Narrative starts e.g. 'Week 1, Autódromo José Carlos Pace — Grand Prix.'"""
    if not narrative:
        return ("Unknown Track", None)
    first = narrative.split(".")[0]
    # Strip leading "Week N, "
    first = re.sub(r"^\s*Week\s+\d+\s*,\s*", "", first)
    first = _clean(first)
    # Split on em-dash / en-dash / hyphen with spaces
    for sep in (" — ", " – ", " - "):
        if sep in first:
            t, c = first.split(sep, 1)
            return (t.strip(), c.strip())
    return (first.strip(), None)


def import_race(race_id: int, host: str, db_path: str = DB_PATH) -> None:
    data = fetch(host, race_id)
    drivers = data.get("drivers") or []
    track_name, track_config = parse_track(data.get("narrative", ""))

    finishers = [d for d in drivers if d.get("race_finish_pos")]
    winner = min(finishers, key=lambda d: d["race_finish_pos"]) if finishers else None

    # DOTD heuristic: most laps led, then best finish
    dotd = max(
        drivers,
        key=lambda d: (d.get("race_laps_lead") or 0, -(d.get("race_finish_pos") or 999)),
        default=None,
    )

    # GrumpyRobot stores lap times as 10000ths of a second (870351 -> 87.0351s)
    LAP_UNIT = 10_000.0
    avg_laps = [d["race_avg_lap"] / LAP_UNIT for d in drivers if d.get("race_avg_lap")]
    avg_lap = sum(avg_laps) / len(avg_laps) if avg_laps else None
    total_laps = data.get("laps") or 0
    duration_s = round(avg_lap * total_laps) if (avg_lap and total_laps) else None

    weather_blob = json.dumps(data.get("weather") or {}, ensure_ascii=False)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO races
           (track_name, track_config, session_type, car_count, total_laps,
            race_date, winner_name, winner_car_num, dotd_name, dotd_score,
            owner_finish, owner_car_num, weather, duration_s, config_snapshot)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            track_name,
            track_config,
            "Race",
            data.get("num_drivers") or len(drivers),
            total_laps,
            datetime.now(timezone.utc).isoformat(),
            _clean(winner["name"]) if winner else None,
            winner["car_number"] if winner else None,
            _clean(dotd["name"]) if dotd else None,
            float(dotd.get("race_laps_lead") or 0) if dotd else None,
            None,
            None,
            weather_blob,
            duration_s,
            json.dumps({"imported_from": "grumpyrobot", "subsession_id": race_id}),
        ),
    )
    new_race_id = cur.lastrowid

    for idx, drv in enumerate(drivers):
        cur.execute(
            """INSERT INTO race_drivers
               (race_id, car_idx, car_number, driver_name, grid_position,
                finish_position, best_lap, avg_lap, laps_led, dotd_score,
                is_owner, incidents, irating, license_level, license_sub_level)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_race_id,
                idx,
                drv.get("car_number"),
                _clean(drv.get("name") or ""),
                drv.get("race_start_pos"),
                drv.get("race_finish_pos"),
                (drv["race_best_lap"] / 10_000.0) if drv.get("race_best_lap") else None,
                (drv["race_avg_lap"] / 10_000.0) if drv.get("race_avg_lap") else None,
                drv.get("race_laps_lead") or 0,
                float(drv.get("race_laps_lead") or 0),
                1 if drv.get("is_us") else 0,
                drv.get("race_incidents") or 0,
                drv.get("irating") if isinstance(drv.get("irating"), int) else None,
                drv.get("license_level") if isinstance(drv.get("license_level"), int) else None,
                drv.get("license_sub_level") if isinstance(drv.get("license_sub_level"), int) else None,
            ),
        )

    conn.commit()
    conn.close()
    print(
        f"Imported subsession {race_id} -> race_id={new_race_id}: "
        f"{track_name}{' — ' + track_config if track_config else ''}, "
        f"{len(drivers)} drivers, {total_laps} laps"
        + (f", winner {winner['name']}" if winner else "")
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("race_id", type=int)
    p.add_argument("--host", default=os.environ.get("GRUMPYROBOT_HOST", "http://localhost:5000"))
    p.add_argument("--db", default=DB_PATH)
    args = p.parse_args()
    import_race(args.race_id, args.host, args.db)


if __name__ == "__main__":
    main()
