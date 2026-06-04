"""
race_db.py — SQLite database layer for Sector Says What.

Stores race history, commentary logs, incidents, and driver career stats.
Uses WAL mode for safe concurrent reads alongside the race engine's writes.
All write operations are serialised through a single threading.Lock.
"""

import os
import json
import math
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "race_history.db")

_write_lock = threading.Lock()
_local = threading.local()

# Current race id (set by start_race, used by log_* helpers)
_current_race_id: Optional[int] = None


# ─── Connection management ────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    """Return a thread-local connection (created on first call per thread)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ─── Schema ───────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    race_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_name       TEXT NOT NULL,
    track_config     TEXT,
    session_type     TEXT,
    car_count        INTEGER,
    total_laps       INTEGER,
    race_date        TEXT NOT NULL,
    winner_name      TEXT,
    winner_car_num   TEXT,
    dotd_name        TEXT,
    dotd_score       REAL,
    owner_finish     INTEGER,
    owner_car_num    TEXT,
    weather          TEXT,
    duration_s       REAL,
    config_snapshot  TEXT,
    race_report      TEXT
);

CREATE TABLE IF NOT EXISTS race_drivers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id          INTEGER NOT NULL REFERENCES races(race_id),
    car_idx          INTEGER,
    car_number       TEXT,
    driver_name      TEXT,
    cust_id          INTEGER,
    grid_position    INTEGER,
    finish_position  INTEGER,
    best_lap         REAL,
    avg_lap          REAL,
    laps_led         INTEGER DEFAULT 0,
    dotd_score       REAL DEFAULT 0,
    dotd_percent     REAL DEFAULT 0,
    is_owner         BOOLEAN DEFAULT 0,
    incidents        INTEGER DEFAULT 0,
    irating          INTEGER,
    license_level    INTEGER,
    license_sub_level INTEGER
);

CREATE TABLE IF NOT EXISTS iracing_cache (
    cust_id          INTEGER PRIMARY KEY,
    last_fetched     INTEGER NOT NULL,
    member_json      TEXT,
    career_json      TEXT,
    recent_json      TEXT,
    bests_json       TEXT,
    image_url        TEXT
);
CREATE INDEX IF NOT EXISTS idx_iracing_cache_fetched ON iracing_cache(last_fetched);

CREATE TABLE IF NOT EXISTS commentary_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id          INTEGER REFERENCES races(race_id),
    timestamp_s      REAL,
    trigger_type     TEXT,
    commentator      TEXT,
    location         TEXT,
    prompt_text      TEXT,
    ai_text          TEXT,
    clip_id          TEXT,
    car_idx          INTEGER,
    is_owner         BOOLEAN DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id          INTEGER REFERENCES races(race_id),
    timestamp_s      REAL,
    incident_type    TEXT,
    car_idx          INTEGER,
    car_name         TEXT,
    other_car_idx    INTEGER,
    other_car_name   TEXT,
    surface          INTEGER,
    yaw              REAL,
    description      TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS driver_career (
    driver_name      TEXT PRIMARY KEY,
    races_entered    INTEGER DEFAULT 0,
    wins             INTEGER DEFAULT 0,
    podiums          INTEGER DEFAULT 0,
    poles            INTEGER DEFAULT 0,
    dotd_wins        INTEGER DEFAULT 0,
    best_finish      INTEGER,
    total_laps_led   INTEGER DEFAULT 0,
    total_incidents  INTEGER DEFAULT 0,
    last_race_date   TEXT
);

CREATE TABLE IF NOT EXISTS prompt_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type    TEXT NOT NULL,
    template_name   TEXT NOT NULL,
    prompt_text     TEXT NOT NULL,
    char_limit      INTEGER DEFAULT 150,
    is_active       BOOLEAN DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_race_drivers_race ON race_drivers(race_id);
CREATE INDEX IF NOT EXISTS idx_commentary_race ON commentary_log(race_id);
CREATE INDEX IF NOT EXISTS idx_incidents_race ON incidents(race_id);
CREATE INDEX IF NOT EXISTS idx_pt_trigger ON prompt_templates(trigger_type);

CREATE TABLE IF NOT EXISTS media_assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT NOT NULL UNIQUE,
    category        TEXT NOT NULL DEFAULT 'uncategorized',
    commentator     TEXT,
    location        TEXT,
    description     TEXT,
    tags            TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_media_path ON media_assets(file_path);
CREATE INDEX IF NOT EXISTS idx_media_category ON media_assets(category);
CREATE INDEX IF NOT EXISTS idx_media_commentator ON media_assets(commentator);

CREATE TABLE IF NOT EXISTS race_pit_stops (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         INTEGER REFERENCES races(race_id),
    car_idx         INTEGER,
    driver_name     TEXT,
    stop_num        INTEGER,
    entered_lap     INTEGER,
    entered_time    REAL,
    exited_time     REAL,
    duration_s      REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pit_stops_race ON race_pit_stops(race_id);

CREATE TABLE IF NOT EXISTS race_lap_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         INTEGER NOT NULL REFERENCES races(race_id),
    cust_id         INTEGER,
    car_idx         INTEGER,
    lap_number      INTEGER NOT NULL,
    session_time_s  REAL,
    lap_time_s      REAL,
    position        INTEGER,
    interval_ms     INTEGER,
    flags           INTEGER,
    incident        BOOLEAN DEFAULT 0,
    personal_best   BOOLEAN DEFAULT 0,
    source          TEXT
);
CREATE INDEX IF NOT EXISTS idx_lap_samples_race ON race_lap_samples(race_id);
CREATE INDEX IF NOT EXISTS idx_lap_samples_race_cust ON race_lap_samples(race_id, cust_id);
CREATE INDEX IF NOT EXISTS idx_lap_samples_race_caridx ON race_lap_samples(race_id, car_idx);
"""


def initialize() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    with _write_lock:
        conn = _conn()
        conn.executescript(_SCHEMA)
        # Lightweight migrations for pre-existing DBs
        existing = {row[1] for row in conn.execute("PRAGMA table_info(race_drivers)").fetchall()}
        for col, ddl in (
            ("avg_lap",           "REAL"),
            ("irating",           "INTEGER"),
            ("license_level",     "INTEGER"),
            ("license_sub_level", "INTEGER"),
            ("cust_id",           "INTEGER"),
            ("laps_complete",     "INTEGER"),
            ("reason_out",        "TEXT"),
            ("dotd_percent",      "REAL DEFAULT 0"),
        ):
            if col not in existing:
                conn.execute(f"ALTER TABLE race_drivers ADD COLUMN {col} {ddl}")
        # Index on cust_id must come after the ALTER above — fresh DBs have
        # the column from _SCHEMA, upgraded DBs only just got it.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_race_drivers_cust ON race_drivers(cust_id)")
        # races table migrations
        races_cols = {row[1] for row in conn.execute("PRAGMA table_info(races)").fetchall()}
        if "race_report" not in races_cols:
            conn.execute("ALTER TABLE races ADD COLUMN race_report TEXT")
        conn.commit()
    print(f"[DB] Initialized {DB_PATH}")


# ─── Race lifecycle ───────────────────────────────────────────────

def start_race(
    track_name: str,
    track_config: str = "",
    session_type: str = "Race",
    car_count: int = 0,
    total_laps: int = 0,
    weather: dict = None,
    config_snapshot: dict = None,
    drivers: list = None,
) -> int:
    """Insert a new race row and its drivers. Returns the race_id."""
    global _current_race_id
    now = datetime.now(timezone.utc).isoformat()

    # SessionLapsRemain returns 32767 (INT16_MAX) for unlimited/time-limited
    # AI races — store it as 0 so downstream "X of Y laps" math behaves.
    # See README "Critical Invariants" for the INT16_MAX lap clamp.
    try:
        if int(total_laps or 0) > 999:
            total_laps = 0
    except (TypeError, ValueError):
        total_laps = 0

    with _write_lock:
        conn = _conn()
        cur = conn.execute(
            """INSERT INTO races
               (track_name, track_config, session_type, car_count, total_laps,
                race_date, weather, config_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track_name,
                track_config or "",
                session_type,
                car_count,
                total_laps,
                now,
                json.dumps(weather) if weather else None,
                json.dumps(config_snapshot) if config_snapshot else None,
            ),
        )
        race_id = cur.lastrowid

        if drivers:
            for d in drivers:
                conn.execute(
                    """INSERT INTO race_drivers
                       (race_id, car_idx, car_number, driver_name, cust_id,
                        grid_position, is_owner)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        race_id,
                        d.get("CarIdx"),
                        str(d.get("CarNumber", "")),
                        d.get("Name", ""),
                        d.get("CustId"),
                        d.get("GridPosition") or d.get("LivePos"),
                        1 if d.get("Owner") else 0,
                    ),
                )

        conn.commit()

    _current_race_id = race_id
    print(f"[DB] Race #{race_id} started — {track_name} ({car_count} cars)")
    return race_id


def finish_race(
    race_id: int = None,
    winner_name: str = None,
    winner_car_num: str = None,
    dotd_name: str = None,
    dotd_score: float = None,
    owner_finish: int = None,
    owner_car_num: str = None,
    duration_s: float = None,
    final_standings: list = None,
) -> None:
    """Update the race row with results and refresh driver_career."""
    rid = race_id or _current_race_id
    if rid is None:
        print("[DB] finish_race called with no active race")
        return

    with _write_lock:
        conn = _conn()

        conn.execute(
            """UPDATE races SET
               winner_name=?, winner_car_num=?, dotd_name=?, dotd_score=?,
               owner_finish=?, owner_car_num=?, duration_s=?
               WHERE race_id=?""",
            (winner_name, winner_car_num, dotd_name, dotd_score,
             owner_finish, owner_car_num, duration_s, rid),
        )

        if final_standings:
            # Real average lap per car_idx, computed from race_lap_samples
            # (invariant 1.13). Replaces the old `best + finish_pos * 0.02`
            # synthetic estimate, which produced averages ~0.02s slower than
            # the best lap regardless of how the driver actually ran.
            avg_by_caridx = {}
            for ci, avg in conn.execute(
                "SELECT car_idx, AVG(lap_time_s) FROM race_lap_samples "
                "WHERE race_id=? AND lap_time_s IS NOT NULL AND lap_time_s > 0 "
                "GROUP BY car_idx",
                (rid,),
            ).fetchall():
                avg_by_caridx[ci] = avg

            # DOTD vote-share percent: each driver's positive points as a
            # fraction of total positive points across the field. Mirrors
            # the calc in sector_says_dotd._export_top3 so the persisted
            # value matches what the live HUD shows.
            total_pos_dotd = sum(
                max((d.get("DOTD", 0) or d.get("dotd_score", 0) or 0), 0.0)
                for d in final_standings
            )

            for idx, d in enumerate(final_standings, 1):
                car_idx = d.get("CarIdx")
                best = d.get("Best") or d.get("best_lap") or 0
                # iRacing omits DNS drivers from ResultsPositions, so their
                # LivePos stays at 0 (CarIdxPosition default for non-
                # participants). Falling back to a literal 1 collided them
                # with the actual winner — use the sorted-list index, since
                # final_cars is already sorted with LivePos=0/None at the
                # bottom (sector_says_engine.py race-end override).
                live_pos = d.get("LivePos")
                fallback_pos = d.get("finish_position")
                if live_pos and live_pos > 0:
                    finish_pos = live_pos
                elif fallback_pos and fallback_pos > 0:
                    finish_pos = fallback_pos
                else:
                    finish_pos = idx

                avg_lap = avg_by_caridx.get(car_idx)
                if avg_lap is None and best and best > 0:
                    # Fallback when lap_sample_capture missed this car (DNS,
                    # disconnect before first crossing). Still synthetic but
                    # we have nothing better.
                    avg_lap = best + finish_pos * 0.02

                laps_complete = d.get("Lap") or d.get("laps_complete") or 0
                reason = "Running" if laps_complete > 0 else "DNS"

                # Per-driver incident count comes from
                # SessionInfo.Sessions[].ResultsPositions[].Incidents, which
                # live_leaderboard exposes as `LiveIncidents`. The legacy
                # `Incidents` key was never populated on the leaderboard
                # dict, so this column was 0 for every driver.
                incidents = d.get("LiveIncidents", d.get("Incidents", 0)) or 0

                raw_dotd = d.get("DOTD", 0) or d.get("dotd_score", 0) or 0
                dotd_pct = round(
                    (max(raw_dotd, 0.0) / total_pos_dotd) * 100.0, 1
                ) if total_pos_dotd > 0 else 0.0

                conn.execute(
                    """UPDATE race_drivers SET
                       finish_position=?, best_lap=?, laps_led=?,
                       dotd_score=?, dotd_percent=?, incidents=?, avg_lap=?,
                       laps_complete=?, reason_out=?
                       WHERE race_id=? AND car_idx=?""",
                    (
                        finish_pos,
                        best,
                        d.get("LapsLed", 0),
                        raw_dotd,
                        dotd_pct,
                        incidents,
                        avg_lap,
                        laps_complete,
                        reason,
                        rid,
                        car_idx,
                    ),
                )

            # Backfill races.total_laps when start_race clamped it to 0 for
            # a timed race (SessionLapsRemain = INT16_MAX, invariant 1.8).
            # By race end the leader's actual lap count is the right value.
            leader_laps = max(
                (d.get("Lap") or d.get("laps_complete") or 0)
                for d in final_standings
            )
            if leader_laps > 0:
                cur_total = conn.execute(
                    "SELECT total_laps FROM races WHERE race_id=?", (rid,)
                ).fetchone()
                if cur_total and (cur_total[0] is None or cur_total[0] == 0):
                    conn.execute(
                        "UPDATE races SET total_laps=? WHERE race_id=?",
                        (leader_laps, rid),
                    )

        conn.commit()

    # Update career aggregates
    _update_careers(rid)
    print(f"[DB] Race #{rid} finished — winner: {winner_name}")


def _update_careers(race_id: int) -> None:
    """Refresh driver_career rows based on this race's results."""
    with _write_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM race_drivers WHERE race_id=?", (race_id,)
        ).fetchall()

        race_row = conn.execute(
            "SELECT * FROM races WHERE race_id=?", (race_id,)
        ).fetchone()
        race_date = race_row["race_date"] if race_row else None
        dotd_winner = race_row["dotd_name"] if race_row else None

        for r in rows:
            name = r["driver_name"]
            if not name:
                continue

            existing = conn.execute(
                "SELECT * FROM driver_career WHERE driver_name=?", (name,)
            ).fetchone()

            fp = r["finish_position"]
            gp = r["grid_position"]
            is_win = 1 if fp == 1 else 0
            is_podium = 1 if fp and fp <= 3 else 0
            is_pole = 1 if gp == 1 else 0
            is_dotd = 1 if name == dotd_winner else 0

            if existing:
                best = existing["best_finish"]
                if fp and (best is None or fp < best):
                    best = fp
                conn.execute(
                    """UPDATE driver_career SET
                       races_entered = races_entered + 1,
                       wins = wins + ?,
                       podiums = podiums + ?,
                       poles = poles + ?,
                       dotd_wins = dotd_wins + ?,
                       best_finish = ?,
                       total_laps_led = total_laps_led + ?,
                       total_incidents = total_incidents + ?,
                       last_race_date = ?
                       WHERE driver_name = ?""",
                    (is_win, is_podium, is_pole, is_dotd, best,
                     r["laps_led"] or 0, r["incidents"] or 0,
                     race_date, name),
                )
            else:
                conn.execute(
                    """INSERT INTO driver_career
                       (driver_name, races_entered, wins, podiums, poles,
                        dotd_wins, best_finish, total_laps_led,
                        total_incidents, last_race_date)
                       VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, is_win, is_podium, is_pole, is_dotd,
                     fp, r["laps_led"] or 0, r["incidents"] or 0,
                     race_date),
                )

        conn.commit()


# ─── Logging helpers ──────────────────────────────────────────────

def log_commentary(
    trigger_type: str = "",
    commentator: str = "",
    location: str = "",
    prompt_text: str = "",
    ai_text: str = "",
    clip_id: str = "",
    car_idx: int = None,
    is_owner: bool = False,
    timestamp_s: float = None,
    race_id: int = None,
) -> None:
    """Insert a commentary log row."""
    rid = race_id or _current_race_id
    with _write_lock:
        try:
            _conn().execute(
                """INSERT INTO commentary_log
                   (race_id, timestamp_s, trigger_type, commentator,
                    location, prompt_text, ai_text, clip_id,
                    car_idx, is_owner)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, timestamp_s, trigger_type, commentator,
                 location, prompt_text, ai_text, clip_id,
                 car_idx, 1 if is_owner else 0),
            )
            _conn().commit()
        except Exception as e:
            print(f"[DB] Commentary log error: {e}")


def log_lap_sample(
    car_idx: int,
    lap_number: int,
    cust_id: int = None,
    session_time_s: float = None,
    lap_time_s: float = None,
    position: int = None,
    interval_ms: int = None,
    flags: int = None,
    incident: bool = False,
    personal_best: bool = False,
    source: str = "live",
    race_id: int = None,
) -> None:
    """Write one lap-crossing row. Called per tick from the engine when a
    driver's CarIdxLap increments. Replay timing is derived from these
    rows — each car's (lap_number, session_time_s) is the authoritative
    moment they crossed the start/finish line."""
    rid = race_id or _current_race_id
    if rid is None:
        return
    with _write_lock:
        try:
            _conn().execute(
                """INSERT INTO race_lap_samples
                   (race_id, cust_id, car_idx, lap_number, session_time_s,
                    lap_time_s, position, interval_ms, flags, incident,
                    personal_best, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, cust_id, car_idx, lap_number, session_time_s,
                 lap_time_s, position, interval_ms, flags,
                 1 if incident else 0, 1 if personal_best else 0, source),
            )
            _conn().commit()
        except Exception as e:
            print(f"[DB] Lap sample error: {e}")


def log_incident(
    incident_type: str,
    car_idx: int = None,
    car_name: str = "",
    other_car_idx: int = None,
    other_car_name: str = "",
    surface: int = None,
    yaw: float = None,
    description: str = "",
    timestamp_s: float = None,
    race_id: int = None,
) -> None:
    """Insert an incident log row."""
    rid = race_id or _current_race_id
    with _write_lock:
        try:
            _conn().execute(
                """INSERT INTO incidents
                   (race_id, timestamp_s, incident_type, car_idx, car_name,
                    other_car_idx, other_car_name, surface, yaw, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, timestamp_s, incident_type, car_idx, car_name,
                 other_car_idx, other_car_name, surface, yaw, description),
            )
            _conn().commit()
        except Exception as e:
            print(f"[DB] Incident log error: {e}")


def log_pit_stop(
    car_idx: int,
    driver_name: str,
    stop_num: int,
    entered_lap: int,
    entered_time: float,
    exited_time: float,
    race_id: int = None,
) -> None:
    """Insert a completed pit stop row. Called on pit-out edge detection."""
    rid = race_id or _current_race_id
    if rid is None:
        return
    duration = None
    try:
        if entered_time is not None and exited_time is not None:
            duration = round(float(exited_time) - float(entered_time), 2)
    except Exception:
        duration = None
    with _write_lock:
        try:
            _conn().execute(
                """INSERT INTO race_pit_stops
                   (race_id, car_idx, driver_name, stop_num,
                    entered_lap, entered_time, exited_time, duration_s)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, car_idx, driver_name, stop_num,
                 entered_lap, entered_time, exited_time, duration),
            )
            _conn().commit()
        except Exception as e:
            print(f"[DB] Pit stop log error: {e}")


# ─── Query helpers (for Pit Wall API) ─────────────────────────────

def get_races(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return recent races, newest first."""
    rows = _conn().execute(
        "SELECT * FROM races ORDER BY race_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def get_race_detail(race_id: int) -> dict | None:
    """Return full race detail including drivers, commentary, incidents."""
    conn = _conn()
    race = conn.execute("SELECT * FROM races WHERE race_id=?", (race_id,)).fetchone()
    if not race:
        return None

    drivers = conn.execute(
        "SELECT * FROM race_drivers WHERE race_id=? ORDER BY finish_position",
        (race_id,),
    ).fetchall()

    commentary = conn.execute(
        "SELECT * FROM commentary_log WHERE race_id=? ORDER BY timestamp_s",
        (race_id,),
    ).fetchall()

    incs = conn.execute(
        "SELECT * FROM incidents WHERE race_id=? ORDER BY timestamp_s",
        (race_id,),
    ).fetchall()

    return {
        "race": dict(race),
        "drivers": [dict(d) for d in drivers],
        "commentary": [dict(c) for c in commentary],
        "incidents": [dict(i) for i in incs],
    }


def get_driver_careers(limit: int = 100) -> list[dict]:
    """Return driver career stats sorted by wins then races."""
    rows = _conn().execute(
        """SELECT * FROM driver_career
           ORDER BY wins DESC, podiums DESC, races_entered DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_driver_career(name: str) -> dict | None:
    """Return single driver career stats."""
    row = _conn().execute(
        "SELECT * FROM driver_career WHERE driver_name=?", (name,)
    ).fetchone()
    return dict(row) if row else None


def get_driver_career_replay(name: str, limit: int = 60) -> dict | None:
    """Return detailed per-race stats for the career replay panel."""
    driver_name = (name or "").strip()
    if not driver_name:
        return None

    conn = _conn()
    rows = conn.execute(
        """
        SELECT
            r.race_id, r.track_name, r.track_config, r.race_date,
            r.car_count, r.total_laps, r.duration_s, r.dotd_name,
            d.driver_name, d.cust_id, d.car_idx, d.car_number,
            d.grid_position, d.finish_position, d.best_lap, d.avg_lap,
            d.laps_led, d.dotd_score, d.dotd_percent, d.is_owner,
            d.incidents, d.irating, d.license_level, d.license_sub_level,
            d.laps_complete, d.reason_out
        FROM race_drivers d
        JOIN races r ON r.race_id = d.race_id
        WHERE d.driver_name = ?
           OR (
                d.cust_id IS NOT NULL
                AND d.cust_id IN (
                    SELECT DISTINCT cust_id
                    FROM race_drivers
                    WHERE driver_name = ? AND cust_id IS NOT NULL
                )
           )
        ORDER BY r.race_date ASC, r.race_id ASC
        LIMIT ?
        """,
        (driver_name, driver_name, max(1, int(limit))),
    ).fetchall()
    if not rows:
        return None

    trend: list[dict] = []
    for row in rows:
        r = dict(row)
        race_id = r.get("race_id")
        cust_id = r.get("cust_id")
        car_idx = r.get("car_idx")

        sample_params: list = [race_id]
        sample_where = ["race_id=?"]
        identity_terms = []
        if cust_id is not None:
            identity_terms.append("cust_id=?")
            sample_params.append(cust_id)
        if car_idx is not None:
            identity_terms.append("car_idx=?")
            sample_params.append(car_idx)
        if identity_terms:
            sample_where.append("(" + " OR ".join(identity_terms) + ")")
        lap_rows = conn.execute(
            f"""
            SELECT lap_time_s
            FROM race_lap_samples
            WHERE {' AND '.join(sample_where)}
              AND lap_time_s IS NOT NULL
              AND lap_time_s > 0
            """,
            tuple(sample_params),
        ).fetchall()
        lap_times = [float(x["lap_time_s"]) for x in lap_rows]
        consistency_s = None
        if len(lap_times) > 1:
            avg = sum(lap_times) / len(lap_times)
            consistency_s = math.sqrt(sum((x - avg) ** 2 for x in lap_times) / len(lap_times))

        pit_params: list = [race_id, r.get("driver_name")]
        pit_terms = ["driver_name=?"]
        if car_idx is not None:
            pit_terms.append("car_idx=?")
            pit_params.append(car_idx)
        pit = conn.execute(
            f"""
            SELECT COUNT(*) AS stops, COALESCE(SUM(duration_s), 0) AS total_time
            FROM race_pit_stops
            WHERE race_id=?
              AND ({' OR '.join(pit_terms)})
            """,
            tuple(pit_params),
        ).fetchone()

        comm_params: list = [race_id]
        comm_terms = ["is_owner=1"]
        if car_idx is not None:
            comm_terms.append("car_idx=?")
            comm_params.append(car_idx)
        commentary = conn.execute(
            f"""
            SELECT COUNT(*) AS calls
            FROM commentary_log
            WHERE race_id=?
              AND ({' OR '.join(comm_terms)})
            """,
            tuple(comm_params),
        ).fetchone()

        grid = r.get("grid_position")
        finish = r.get("finish_position")
        gain = None
        if isinstance(grid, int) and isinstance(finish, int) and grid > 0 and finish > 0:
            gain = grid - finish

        trend.append({
            "race_id": race_id,
            "race_date": r.get("race_date"),
            "track_name": r.get("track_name"),
            "track_config": r.get("track_config"),
            "car_count": r.get("car_count") or 0,
            "total_laps": r.get("total_laps") or 0,
            "duration_s": r.get("duration_s"),
            "driver_name": r.get("driver_name"),
            "car_number": r.get("car_number"),
            "grid_position": grid,
            "finish_position": finish,
            "position_delta": gain,
            "best_lap": r.get("best_lap"),
            "avg_lap": r.get("avg_lap"),
            "laps_led": r.get("laps_led") or 0,
            "dotd_percent": r.get("dotd_percent") or 0,
            "dotd_winner": bool(r.get("dotd_name") == r.get("driver_name")),
            "incidents": r.get("incidents") or 0,
            "irating": r.get("irating"),
            "license_level": r.get("license_level"),
            "license_sub_level": r.get("license_sub_level"),
            "laps_complete": r.get("laps_complete"),
            "reason_out": r.get("reason_out"),
            "lap_sample_count": len(lap_times),
            "lap_consistency_s": consistency_s,
            "pit_stops": int(pit["stops"] or 0) if pit else 0,
            "pit_time_s": float(pit["total_time"] or 0) if pit else 0.0,
            "commentary_calls": int(commentary["calls"] or 0) if commentary else 0,
        })

    finish_rows = [r for r in trend if isinstance(r.get("finish_position"), int) and r["finish_position"] > 0]
    grid_rows = [r for r in trend if isinstance(r.get("grid_position"), int) and r["grid_position"] > 0]
    gain_rows = [r for r in trend if isinstance(r.get("position_delta"), int)]
    ir_rows = [r for r in trend if isinstance(r.get("irating"), int) and r["irating"] > 0]
    sample_rows = [r for r in trend if r.get("lap_sample_count", 0) > 0]

    def avg(values):
        values = [v for v in values if isinstance(v, (int, float))]
        return (sum(values) / len(values)) if values else None

    summary = {
        "races": len(trend),
        "wins": sum(1 for r in finish_rows if r["finish_position"] == 1),
        "podiums": sum(1 for r in finish_rows if r["finish_position"] <= 3),
        "poles": sum(1 for r in grid_rows if r["grid_position"] == 1),
        "best_finish": min((r["finish_position"] for r in finish_rows), default=None),
        "avg_finish": avg([r.get("finish_position") for r in finish_rows]),
        "avg_grid": avg([r.get("grid_position") for r in grid_rows]),
        "net_positions": sum((r.get("position_delta") or 0) for r in gain_rows),
        "comeback_races": sum(1 for r in gain_rows if (r.get("position_delta") or 0) > 0),
        "total_incidents": sum((r.get("incidents") or 0) for r in trend),
        "avg_incidents": avg([r.get("incidents") for r in trend]),
        "clean_races": sum(1 for r in trend if (r.get("incidents") or 0) == 0),
        "total_laps_led": sum((r.get("laps_led") or 0) for r in trend),
        "dotd_wins": sum(1 for r in trend if r.get("dotd_winner")),
        "best_dotd_percent": max((r.get("dotd_percent") or 0 for r in trend), default=0),
        "total_pit_stops": sum((r.get("pit_stops") or 0) for r in trend),
        "sample_races": len(sample_rows),
        "latest_irating": ir_rows[-1]["irating"] if ir_rows else None,
        "irating_delta": (ir_rows[-1]["irating"] - ir_rows[0]["irating"]) if len(ir_rows) > 1 else None,
    }

    tracks_by_name: dict[str, list[dict]] = {}
    for r in trend:
        tracks_by_name.setdefault(r.get("track_name") or "Unknown Track", []).append(r)
    tracks = []
    for track_name, track_rows in tracks_by_name.items():
        track_finish = [r for r in track_rows if isinstance(r.get("finish_position"), int)]
        tracks.append({
            "track_name": track_name,
            "races": len(track_rows),
            "best_finish": min((r["finish_position"] for r in track_finish), default=None),
            "avg_finish": avg([r.get("finish_position") for r in track_finish]),
            "avg_gain": avg([r.get("position_delta") for r in track_rows]),
            "total_incidents": sum((r.get("incidents") or 0) for r in track_rows),
            "best_dotd_percent": max((r.get("dotd_percent") or 0 for r in track_rows), default=0),
        })
    tracks.sort(key=lambda t: (-t["races"], t["avg_finish"] if t["avg_finish"] is not None else 999))

    moments = []
    if finish_rows:
        best = min(finish_rows, key=lambda r: (r["finish_position"], -int(r.get("race_id") or 0)))
        moments.append({
            "label": "Best Finish",
            "value": f"P{best['finish_position']}",
            "race_id": best["race_id"],
            "track_name": best["track_name"],
        })
    positive_gains = [r for r in gain_rows if (r.get("position_delta") or 0) > 0]
    if positive_gains:
        comeback = max(positive_gains, key=lambda r: (r["position_delta"], int(r.get("race_id") or 0)))
        moments.append({
            "label": "Biggest Comeback",
            "value": f"+{comeback['position_delta']}",
            "race_id": comeback["race_id"],
            "track_name": comeback["track_name"],
        })
    dotd_best = max(trend, key=lambda r: (r.get("dotd_percent") or 0, int(r.get("race_id") or 0)))
    if (dotd_best.get("dotd_percent") or 0) > 0:
        moments.append({
            "label": "Best DOTD Vote",
            "value": f"{dotd_best['dotd_percent']:.1f}%",
            "race_id": dotd_best["race_id"],
            "track_name": dotd_best["track_name"],
        })
    clean = [r for r in trend if (r.get("incidents") or 0) == 0]
    if clean:
        recent_clean = clean[-1]
        moments.append({
            "label": "Latest Clean Race",
            "value": f"Race {recent_clean['race_id']}",
            "race_id": recent_clean["race_id"],
            "track_name": recent_clean["track_name"],
        })
    consistency = [r for r in trend if isinstance(r.get("lap_consistency_s"), float)]
    if consistency:
        tightest = min(consistency, key=lambda r: r["lap_consistency_s"])
        moments.append({
            "label": "Tightest Pace Window",
            "value": f"{tightest['lap_consistency_s']:.2f}s",
            "race_id": tightest["race_id"],
            "track_name": tightest["track_name"],
        })

    return {
        "driver_name": rows[-1]["driver_name"] or driver_name,
        "summary": summary,
        "trend": trend,
        "tracks": tracks,
        "moments": moments,
    }


# ─── iRacing /data API cache ─────────────────────────────────────

def get_cached_driver(cust_id: int, max_age_hours: float = 24.0) -> dict | None:
    """Return cached /data API blob for a driver, or None if missing/stale.

    Each column holding a JSON string is parsed back into a dict/list.
    Callers tolerate None — fall back to whatever they had before.
    """
    if not cust_id:
        return None
    row = _conn().execute(
        "SELECT * FROM iracing_cache WHERE cust_id=?", (int(cust_id),)
    ).fetchone()
    if not row:
        return None
    age_s = (datetime.now(timezone.utc).timestamp()) - (row["last_fetched"] or 0)
    if age_s > max_age_hours * 3600:
        return None
    out = {
        "cust_id": row["cust_id"],
        "last_fetched": row["last_fetched"],
        "image_url": row["image_url"],
    }
    for key in ("member_json", "career_json", "recent_json", "bests_json"):
        raw = row[key]
        if raw:
            try:
                out[key.replace("_json", "")] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                out[key.replace("_json", "")] = None
        else:
            out[key.replace("_json", "")] = None
    return out


def set_cached_driver(
    cust_id: int,
    member: dict | None = None,
    career: dict | None = None,
    recent: dict | None = None,
    bests: dict | None = None,
    image_url: str | None = None,
) -> None:
    """Upsert a /data API blob for a driver. Silent on error."""
    if not cust_id:
        return
    now = int(datetime.now(timezone.utc).timestamp())
    with _write_lock:
        try:
            _conn().execute(
                """INSERT INTO iracing_cache
                   (cust_id, last_fetched, member_json, career_json,
                    recent_json, bests_json, image_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cust_id) DO UPDATE SET
                     last_fetched = excluded.last_fetched,
                     member_json  = COALESCE(excluded.member_json,  iracing_cache.member_json),
                     career_json  = COALESCE(excluded.career_json,  iracing_cache.career_json),
                     recent_json  = COALESCE(excluded.recent_json,  iracing_cache.recent_json),
                     bests_json   = COALESCE(excluded.bests_json,   iracing_cache.bests_json),
                     image_url    = COALESCE(excluded.image_url,    iracing_cache.image_url)""",
                (
                    int(cust_id),
                    now,
                    json.dumps(member) if member is not None else None,
                    json.dumps(career) if career is not None else None,
                    json.dumps(recent) if recent is not None else None,
                    json.dumps(bests) if bests is not None else None,
                    image_url,
                ),
            )
            _conn().commit()
        except Exception as e:
            print(f"[DB] iracing_cache upsert error for cust_id={cust_id}: {e}")


def get_owner_history(limit: int = 10) -> list[dict]:
    """Return the owner's recent race results."""
    rows = _conn().execute(
        """SELECT r.race_id, r.track_name, r.race_date, r.winner_name,
                  r.dotd_name, rd.finish_position, rd.grid_position,
                  rd.best_lap, rd.laps_led, rd.dotd_score, rd.incidents
           FROM race_drivers rd
           JOIN races r ON r.race_id = rd.race_id
           WHERE rd.is_owner = 1
           ORDER BY r.race_id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def current_race_id() -> Optional[int]:
    """Return the active race_id (or None if no race running)."""
    return _current_race_id


# ─── Generic CRUD helpers (for Database Explorer) ────────────────

_ALLOWED_TABLES = {
    "races", "race_drivers", "commentary_log", "incidents",
    "driver_career", "prompt_templates", "media_assets",
    "iracing_cache",
}


def _validate_table(table: str) -> str:
    """Validate table name against whitelist to prevent injection."""
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Unknown table: {table}")
    return table


def get_table_names() -> list[dict]:
    """Return all table names with row counts."""
    conn = _conn()
    tables = []
    for name in sorted(_ALLOWED_TABLES):
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
            tables.append({"name": name, "rows": count})
        except Exception:
            tables.append({"name": name, "rows": 0})
    return tables


def get_table_schema(table: str) -> list[dict]:
    """Return column info for a table."""
    _validate_table(table)
    rows = _conn().execute(f"PRAGMA table_info([{table}])").fetchall()
    return [{"cid": r[0], "name": r[1], "type": r[2], "notnull": r[3],
             "default": r[4], "pk": r[5]} for r in rows]


def get_rows(table: str, limit: int = 50, offset: int = 0,
             sort: str = None, order: str = "ASC") -> dict:
    """Return paginated rows from a table."""
    _validate_table(table)
    conn = _conn()
    total = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]

    # Get PK column name for reference
    schema = get_table_schema(table)
    pk_col = next((c["name"] for c in schema if c["pk"]), schema[0]["name"] if schema else "rowid")

    order_clause = f"[{pk_col}] DESC"
    if sort:
        # Validate sort column exists
        valid_cols = {c["name"] for c in schema}
        if sort in valid_cols:
            order_clause = f"[{sort}] {('DESC' if order.upper() == 'DESC' else 'ASC')}"

    rows = conn.execute(
        f"SELECT * FROM [{table}] ORDER BY {order_clause} LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()

    return {
        "table": table,
        "columns": [c["name"] for c in schema],
        "pk": pk_col,
        "rows": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def update_row(table: str, pk_col: str, pk_val, data: dict) -> bool:
    """Update a single row by primary key."""
    _validate_table(table)
    schema = get_table_schema(table)
    valid_cols = {c["name"] for c in schema}
    # Filter to valid columns only
    updates = {k: v for k, v in data.items() if k in valid_cols and k != pk_col}
    if not updates:
        return False

    set_clause = ", ".join(f"[{k}] = ?" for k in updates)
    values = list(updates.values()) + [pk_val]

    with _write_lock:
        _conn().execute(
            f"UPDATE [{table}] SET {set_clause} WHERE [{pk_col}] = ?", values
        )
        _conn().commit()
    return True


def insert_row(table: str, data: dict) -> int:
    """Insert a new row and return the rowid."""
    _validate_table(table)
    schema = get_table_schema(table)
    valid_cols = {c["name"] for c in schema}
    filtered = {k: v for k, v in data.items() if k in valid_cols}
    if not filtered:
        raise ValueError("No valid columns provided")

    cols = ", ".join(f"[{k}]" for k in filtered)
    placeholders = ", ".join("?" for _ in filtered)
    values = list(filtered.values())

    with _write_lock:
        cur = _conn().execute(
            f"INSERT INTO [{table}] ({cols}) VALUES ({placeholders})", values
        )
        _conn().commit()
    return cur.lastrowid


def delete_row(table: str, pk_col: str, pk_val) -> bool:
    """Delete a single row by primary key."""
    _validate_table(table)
    with _write_lock:
        cur = _conn().execute(
            f"DELETE FROM [{table}] WHERE [{pk_col}] = ?", (pk_val,)
        )
        _conn().commit()
    return cur.rowcount > 0


def execute_readonly(sql: str) -> dict:
    """Execute a read-only SQL query. Only SELECT allowed."""
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    # Block dangerous keywords
    for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH"):
        if kw in stripped:
            raise ValueError(f"Query contains forbidden keyword: {kw}")

    conn = _conn()
    cur = conn.execute(sql)
    columns = [desc[0] for desc in cur.description] if cur.description else []
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    return {"columns": columns, "rows": rows, "count": len(rows)}


# ─── Prompt template helpers ─────────────────────────────────────

def get_prompt_templates(trigger_type: str = None) -> list[dict]:
    """Return prompt templates, optionally filtered by trigger type."""
    conn = _conn()
    if trigger_type:
        rows = conn.execute(
            "SELECT * FROM prompt_templates WHERE trigger_type=? ORDER BY template_name",
            (trigger_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM prompt_templates ORDER BY trigger_type, template_name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_prompt_template(template_id: int) -> dict | None:
    """Return a single prompt template."""
    row = _conn().execute(
        "SELECT * FROM prompt_templates WHERE id=?", (template_id,)
    ).fetchone()
    return dict(row) if row else None


def save_prompt_template(template_id: int = None, **fields) -> int:
    """Insert or update a prompt template. Returns the id."""
    with _write_lock:
        conn = _conn()
        if template_id:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE prompt_templates SET {sets}, updated_at=datetime('now') WHERE id=?",
                list(fields.values()) + [template_id],
            )
            conn.commit()
            return template_id
        else:
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            cur = conn.execute(
                f"INSERT INTO prompt_templates ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
            return cur.lastrowid


def delete_prompt_template(template_id: int) -> bool:
    """Delete a prompt template."""
    with _write_lock:
        cur = _conn().execute("DELETE FROM prompt_templates WHERE id=?", (template_id,))
        _conn().commit()
    return cur.rowcount > 0


# ─── Media asset helpers ────────────────────────────────────────

# Valid media categories
MEDIA_CATEGORIES = [
    "persona_image", "hud_background", "pitwall_background",
    "page_background", "logo", "track_image",
    "driver_image", "studio_backdrop", "uncategorized",
]


def get_media_asset(file_path: str) -> dict | None:
    """Return tags/metadata for a single media file."""
    row = _conn().execute(
        "SELECT * FROM media_assets WHERE file_path=?", (file_path,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def get_all_media_assets() -> dict:
    """Return all media asset records keyed by file_path."""
    rows = _conn().execute("SELECT * FROM media_assets").fetchall()
    result = {}
    for row in rows:
        d = dict(row)
        if d.get("tags"):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                pass
        result[d["file_path"]] = d
    return result


def save_media_asset(file_path: str, **fields) -> int:
    """Insert or update media asset metadata. Returns the id."""
    with _write_lock:
        conn = _conn()
        existing = conn.execute(
            "SELECT id FROM media_assets WHERE file_path=?", (file_path,)
        ).fetchone()

        # Serialize tags list/dict to JSON string
        if "tags" in fields and not isinstance(fields["tags"], str):
            fields["tags"] = json.dumps(fields["tags"])

        if existing:
            asset_id = existing[0]
            sets = ", ".join(f"[{k}]=?" for k in fields)
            conn.execute(
                f"UPDATE media_assets SET {sets}, updated_at=datetime('now') WHERE id=?",
                list(fields.values()) + [asset_id],
            )
            conn.commit()
            return asset_id
        else:
            fields["file_path"] = file_path
            cols = ", ".join(f"[{k}]" for k in fields)
            placeholders = ", ".join("?" for _ in fields)
            cur = conn.execute(
                f"INSERT INTO media_assets ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
            return cur.lastrowid


def delete_media_asset(file_path: str) -> bool:
    """Delete media asset metadata when a file is removed."""
    with _write_lock:
        cur = _conn().execute(
            "DELETE FROM media_assets WHERE file_path=?", (file_path,)
        )
        _conn().commit()
    return cur.rowcount > 0


def get_media_by_category(category: str) -> list[dict]:
    """Return all media assets in a given category."""
    rows = _conn().execute(
        "SELECT * FROM media_assets WHERE category=? ORDER BY file_path",
        (category,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_media_by_driver(name: str) -> list[dict]:
    """Return media assets whose tags JSON array contains ``name`` (case-
    insensitive). Matches a quoted string inside the JSON text so a tag of
    'Lando' doesn't pull in 'Landon'."""
    if not name:
        return []
    # Match the driver's name appearing as a JSON-quoted string. LOWER() on
    # both sides keeps it case-insensitive. The fallback bare substring
    # handles older rows that stored tags as a comma-separated string.
    quoted = f'%"{name.lower()}"%'
    bare = f"%{name.lower()}%"
    rows = _conn().execute(
        "SELECT * FROM media_assets "
        "WHERE LOWER(tags) LIKE ? OR LOWER(tags) LIKE ? "
        "ORDER BY file_path",
        (quoted, bare),
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        if d.get("tags"):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                pass
        # Confirm the token match — LIKE on bare substring can fuzz-hit
        # other tags. If tags parsed into a list, require an exact
        # (case-insensitive) token; if it stayed a string, require the
        # name to appear as a comma-separated token.
        tags = d.get("tags")
        want = name.lower()
        if isinstance(tags, list):
            if not any(isinstance(t, str) and t.lower() == want for t in tags):
                continue
        elif isinstance(tags, str):
            tokens = [t.strip().lower() for t in tags.split(",")]
            if want not in tokens:
                continue
        else:
            continue
        out.append(d)
    return out


def get_media_by_commentator(name: str) -> list[dict]:
    """Return all media assets tagged to a commentator."""
    rows = _conn().execute(
        "SELECT * FROM media_assets WHERE commentator=? ORDER BY category, file_path",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]
