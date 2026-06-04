"""Import an iRacing official event result JSON into race_history.db.

Usage:
    python import_iracing_eventresult.py <path-to-eventresult.json>

Maps the iRacing /data/results/get JSON shape (the same shape iRacing exports
as eventresult-<subsession_id>.json) to the local races / race_drivers schema
so the race shows up under /replays.

If a race with the same iRacing subsession_id is already in the DB (typically
because the engine recorded it live), the importer MERGES enrichment fields
into that row instead of inserting a duplicate. Engine-only fields
(dotd_score, laps_led, best_lap, race_report, weather, commentary_log,
race_lap_samples, race_pit_stops) are never overwritten. Pass --force-new
to insert a fresh row regardless.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "race_history.db")
RACE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "race_config.json")

# iRacing stores lap times as 10000ths of a second
LAP_UNIT = 10_000.0


def _load_owner_identifiers():
    """Return (set of lowercased display_names, set of cust_id ints) for owner drivers.

    An event result has no Owner flag, so we match on race_config.json's
    owner_drivers map: by display_name and/or iracing_customer_id.
    """
    names, ids = set(), set()
    try:
        with open(RACE_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return names, ids
    owners = cfg.get("owner_drivers") or {}
    if isinstance(owners, dict):
        for key, val in owners.items():
            if key:
                names.add(str(key).strip().lower())
            if isinstance(val, dict):
                dn = val.get("display_name")
                if dn:
                    names.add(str(dn).strip().lower())
                cid = val.get("iracing_customer_id")
                try:
                    if cid not in (None, ""):
                        ids.add(int(cid))
                except (TypeError, ValueError):
                    pass
    single = cfg.get("owner_driver") or {}
    if isinstance(single, dict):
        dn = single.get("display_name")
        if dn:
            names.add(str(dn).strip().lower())
        cid = single.get("iracing_customer_id")
        try:
            if cid not in (None, ""):
                ids.add(int(cid))
        except (TypeError, ValueError):
            pass
    return names, ids


def _is_owner_driver(drv, owner_names, owner_ids):
    cid = drv.get("cust_id")
    try:
        if cid is not None and int(cid) in owner_ids:
            return True
    except (TypeError, ValueError):
        pass
    name = (drv.get("display_name") or "").strip().lower()
    return bool(name) and name in owner_names


def _to_seconds(t):
    if t is None or t < 0:
        return None
    return t / LAP_UNIT


def _best_lap_to_seconds(t):
    """Convert best_lap_time which may be in 10000ths (official) or ms (AI export)."""
    if t is None or t < 0:
        return None
    # AI exports store best_lap_time in ms (e.g. 99870 for ~100s)
    # Official exports use 10000ths (e.g. 998700 for ~100s)
    # Heuristic: if value > 500_000, it's 10000ths; otherwise ms
    if t > 500_000:
        return t / LAP_UNIT
    return t / 1000.0


def _find_race_session(session_results):
    """Pick the actual RACE simsession from the list."""
    for s in session_results:
        name = (s.get("simsession_name") or "").upper()
        if name == "RACE":
            return s
    # Fallback: simsession_type_name == "Race"
    for s in session_results:
        if (s.get("simsession_type_name") or "").lower() == "race":
            return s
    return session_results[-1] if session_results else None


def _find_existing_race_by_subsession(cur, subsession_id):
    """Search races for a row whose iRacing SubSessionID matches.

    Engine-tracked races store SubSessionID inside the live SessionInfo
    dump in the `weather` column. Previously-imported races store
    subsession_id inside `config_snapshot`. Either path matches.
    """
    if not subsession_id:
        return None
    try:
        target = int(subsession_id)
    except (TypeError, ValueError):
        return None
    cur.execute("SELECT race_id, weather, config_snapshot FROM races")
    for race_id, weather, cfg_snap in cur.fetchall():
        if weather:
            try:
                w = json.loads(weather)
                if isinstance(w, dict) and int(w.get("SubSessionID") or 0) == target:
                    return race_id
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if cfg_snap:
            try:
                c = json.loads(cfg_snap)
                if isinstance(c, dict) and int(c.get("subsession_id") or 0) == target:
                    return race_id
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    return None


def _pick_iracing_int(drv, old_key, new_key):
    """Prefer the pre-race value (old_*) when it's a non-negative int, else
    fall back to the post-race value, else None. iRacing fills these only
    for official sessions; AI exports leave them at -1."""
    for k in (old_key, new_key):
        v = drv.get(k)
        if isinstance(v, int) and v >= 0:
            return v
    return None


def _merge_race_row(cur, race_id, *, track_name, total_laps, duration_s):
    """Update missing/wrong fields on the existing races row. Engine-captured
    values for dotd_*, weather, race_report, config_snapshot are never
    touched.
    """
    cur.execute(
        "SELECT track_name, total_laps, duration_s FROM races WHERE race_id=?",
        (race_id,),
    )
    row = cur.fetchone()
    if not row:
        return
    cur_track, cur_laps, cur_dur = row
    new_track = cur_track
    if track_name and (not cur_track or len(track_name) > len(cur_track)):
        new_track = track_name
    new_laps = cur_laps
    if total_laps and (not cur_laps or cur_laps == 0):
        new_laps = total_laps
    new_dur = cur_dur if cur_dur is not None else duration_s
    cur.execute(
        "UPDATE races SET track_name=?, total_laps=?, duration_s=? WHERE race_id=?",
        (new_track, new_laps, new_dur, race_id),
    )


def _merge_driver_rows(cur, race_id, finishers, owner_names, owner_ids):
    """Merge iRacing per-driver enrichment into the existing race_drivers
    rows by cust_id. Returns (updated_count, unmatched_names).

    Engine values for dotd_score, laps_led, best_lap, finish_position,
    cust_id, driver_name, car_idx are NEVER overwritten.

    grid_position IS overwritten with iRacing's starting_position+1 (when
    iRacing has a valid value), because the engine's CarIdxPosition-based
    grid can stamp the player on pole in AI sessions when iRacing returns
    0 for their slot at GREEN — see build_starting_grid in
    sector_says_utilities. iRacing's eventresult is the authoritative
    qualifying-grid source.

    avg_lap is always replaced (engine's calc is unreliable);
    irating/license_level/license_sub_level are filled only when engine left
    them NULL; incidents replaces engine zero (engine doesn't aggregate from
    the incidents table). is_owner is upgraded 0 -> 1 when the driver
    matches owner_drivers config (engine's car_idx==0 heuristic missed
    AI sessions where the player isn't slotted at car_idx 0).
    """
    cur.execute(
        "SELECT id, cust_id, irating, license_level, license_sub_level, incidents, is_owner, grid_position "
        "FROM race_drivers WHERE race_id=?",
        (race_id,),
    )
    by_cust = {}
    for row in cur.fetchall():
        if row[1] is not None:
            by_cust[int(row[1])] = row
    updated, unmatched = 0, []
    for drv in finishers:
        try:
            cust_id = int(drv["cust_id"]) if drv.get("cust_id") is not None else None
        except (TypeError, ValueError):
            cust_id = None
        if cust_id is None or cust_id not in by_cust:
            unmatched.append(drv.get("display_name") or "?")
            continue
        row_id, _cid, cur_ir, cur_lic, cur_sub, cur_inc, cur_owner, cur_grid = by_cust[cust_id]
        new_ir = _pick_iracing_int(drv, "oldi_rating", "newi_rating")
        new_lic = _pick_iracing_int(drv, "old_license_level", "new_license_level")
        new_sub = _pick_iracing_int(drv, "old_sub_level", "new_sub_level")
        new_inc = drv.get("incidents")
        new_avg = _to_seconds(drv.get("average_lap"))
        # iRacing starting_position is 0-indexed; bump to 1-based for the
        # display column. Negative values mean iRacing didn't track a
        # grid slot (DNS / driver-change) — keep the engine value then.
        sp = drv.get("starting_position")
        new_grid = (sp + 1) if isinstance(sp, int) and sp >= 0 else None
        ir = cur_ir if cur_ir is not None else new_ir
        lic = cur_lic if cur_lic is not None else new_lic
        sub = cur_sub if cur_sub is not None else new_sub
        inc = cur_inc if cur_inc not in (None, 0) else (new_inc if new_inc is not None else cur_inc)
        grid = new_grid if new_grid is not None else cur_grid
        owner_flag = cur_owner
        if not cur_owner and _is_owner_driver(drv, owner_names, owner_ids):
            owner_flag = 1
        cur.execute(
            "UPDATE race_drivers SET irating=?, license_level=?, license_sub_level=?, "
            "incidents=?, avg_lap=?, is_owner=?, grid_position=? WHERE id=?",
            (ir, lic, sub, inc, new_avg, owner_flag, grid, row_id),
        )
        updated += 1
    return updated, unmatched


def _fix_dns_finish_positions(cur, race_id):
    """Push DNS rows past the last running finisher.

    The engine had a fallback bug where DNS drivers (no LivePos because
    iRacing omits them from ResultsPositions) defaulted to finish_position=1,
    colliding with the actual winner. The engine fix is in race_db.py;
    this is the matching repair step the importer runs after merging so
    older races (and any future races still recorded by an unfixed engine)
    get cleaned up.
    """
    cur.execute(
        "SELECT id, finish_position, laps_complete, reason_out "
        "FROM race_drivers WHERE race_id=?",
        (race_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return 0
    DNS_REASONS = {"DNS", "DQ", "Disqualified", "Disconnected", "Towed"}
    def _is_dns(laps, reason):
        if laps is not None and laps < 0:
            return True
        if (reason or "").strip() in DNS_REASONS and (laps or 0) <= 0:
            return True
        return False
    runners = [(rid, fp) for rid, fp, lc, ro in rows if not _is_dns(lc, ro)]
    dnses = [(rid, fp) for rid, fp, lc, ro in rows if _is_dns(lc, ro)]
    if not dnses:
        return 0
    max_running = max((fp or 0) for _, fp in runners) if runners else 0
    # Only renumber DNS rows that collide with the running field
    bad_dns = [(rid, fp) for rid, fp in dnses if (fp or 0) <= max_running]
    if not bad_dns:
        return 0
    # Start past any DNS that already sits above max_running so we don't
    # collide with another correctly-placed DNS row
    bad_ids = {rid for rid, _ in bad_dns}
    above_runners = [(fp or 0) for rid, fp in dnses
                     if rid not in bad_ids and (fp or 0) > max_running]
    next_pos = max([max_running] + above_runners) + 1
    # Stable order: by current finish_position, then row id
    bad_dns.sort(key=lambda x: (x[1] or 999, x[0]))
    fixed = 0
    for rid, _fp in bad_dns:
        cur.execute(
            "UPDATE race_drivers SET finish_position=? WHERE id=?",
            (next_pos, rid),
        )
        next_pos += 1
        fixed += 1
    return fixed


def _sync_owner_summary(cur, race_id):
    """Keep the races owner summary in step with the flagged owner row."""
    cur.execute(
        """SELECT finish_position, car_number
           FROM race_drivers
           WHERE race_id=? AND is_owner=1
           ORDER BY id
           LIMIT 1""",
        (race_id,),
    )
    row = cur.fetchone()
    if not row:
        return
    cur.execute(
        "UPDATE races SET owner_finish=?, owner_car_num=? WHERE race_id=?",
        (row[0], row[1], race_id),
    )


def import_eventresult_payload(payload, db_path: str = DB_PATH, *, force_new: bool = False) -> dict:
    """Import an already-parsed eventresult payload (dict). Returns a structured
    result so callers (CLI, Flask) can render whatever UI they want.

    Result keys: race_id, merged (bool), subsession_id, track_name, track_config,
    drivers_updated, drivers_count, unmatched (list[str]), message (str).
    """
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(data, dict):
        raise ValueError("Unrecognised event result shape")

    subsession_id = data.get("subsession_id")
    track = data.get("track") or {}
    track_name = track.get("track_name") or "Unknown Track"
    track_config = track.get("config_name")

    sessions = data.get("session_results") or []
    race_session = _find_race_session(sessions)
    if not race_session:
        raise SystemExit("No race session found in event result JSON")

    results = race_session.get("results") or []
    # AI race exports use race_summary.laps_complete; official uses event_laps_complete
    total_laps = data.get("event_laps_complete") or 0
    if not total_laps:
        rs = data.get("race_summary") or {}
        total_laps = rs.get("laps_complete") or race_session.get("lapsComplete") or 0
    avg_lap_s = _to_seconds(data.get("event_average_lap"))
    if not avg_lap_s:
        # AI exports store averageLapTime in seconds on the session object
        raw_avg = race_session.get("averageLapTime")
        if raw_avg and raw_avg > 0:
            avg_lap_s = raw_avg
    duration_s = round(avg_lap_s * total_laps) if (avg_lap_s and total_laps) else None

    # iRacing finish_position is 0-indexed (0 = winner)
    # AI exports use finish_position_in_class instead of finish_position
    def _fpos(r):
        try:
            fp = r.get("finish_position")
            if fp is None:
                fp = r.get("finish_position_in_class")
            return int(fp) if fp is not None else 999
        except Exception:
            return 999

    finishers = sorted(results, key=_fpos)
    winner = finishers[0] if finishers else None

    # DOTD heuristic: most laps led, then best finish
    dotd = max(
        results,
        key=lambda r: (r.get("laps_lead") or 0, -_fpos(r)),
        default=None,
    ) if results else None

    weather_blob = json.dumps(data.get("weather") or {}, ensure_ascii=False)

    race_date = data.get("end_time") or datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    existing_race_id = None if force_new else _find_existing_race_by_subsession(cur, subsession_id)

    if existing_race_id is not None:
        _merge_race_row(
            cur, existing_race_id,
            track_name=track_name, total_laps=total_laps, duration_s=duration_s,
        )
        owner_names_m, owner_ids_m = _load_owner_identifiers()
        updated, unmatched = _merge_driver_rows(
            cur, existing_race_id, finishers, owner_names_m, owner_ids_m,
        )
        dns_fixed = _fix_dns_finish_positions(cur, existing_race_id)
        _sync_owner_summary(cur, existing_race_id)
        conn.commit()
        conn.close()
        msg = (
            f"Merged subsession {subsession_id} into existing race_id={existing_race_id}: "
            f"{track_name}" + (f" - {track_config}" if track_config else "")
            + f", {updated} driver rows enriched"
        )
        if dns_fixed:
            msg += f", {dns_fixed} DNS row(s) renumbered to last"
        if unmatched:
            msg += f" ({len(unmatched)} unmatched: " + ", ".join(unmatched[:5])
            if len(unmatched) > 5:
                msg += f" +{len(unmatched)-5} more"
            msg += ")"
        return {
            "race_id": existing_race_id,
            "merged": True,
            "subsession_id": subsession_id,
            "track_name": track_name,
            "track_config": track_config,
            "drivers_updated": updated,
            "drivers_count": len(finishers),
            "unmatched": unmatched,
            "message": msg,
        }

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
            data.get("num_drivers") or len(results) or 0,
            total_laps,
            race_date,
            (winner.get("display_name") if winner else None),
            (winner.get("livery", {}) or {}).get("car_number") if winner else None,
            # Surface the most-laps-led driver as the race-level "DOTD"
            # placeholder so the race list has *something* to show, but
            # leave the numeric score NULL — see per-driver comment.
            (dotd.get("display_name") if dotd else None),
            None,
            None,
            None,
            weather_blob,
            duration_s,
            json.dumps({
                "imported_from": "iracing_eventresult",
                "subsession_id": subsession_id,
                "series_name": data.get("series_name"),
                "season_name": data.get("season_name"),
                "sof": data.get("event_strength_of_field"),
            }),
        ),
    )
    new_race_id = cur.lastrowid

    owner_names, owner_ids = _load_owner_identifiers()

    for idx, drv in enumerate(finishers):
        livery = drv.get("livery") or {}
        is_owner_flag = 1 if _is_owner_driver(drv, owner_names, owner_ids) else 0
        cur.execute(
            """INSERT INTO race_drivers
               (race_id, car_idx, car_number, driver_name, grid_position,
                finish_position, best_lap, avg_lap, laps_led, dotd_score,
                is_owner, incidents, irating, license_level, license_sub_level,
                cust_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_race_id,
                idx,
                livery.get("car_number") or drv.get("car_number"),
                drv.get("display_name") or "",
                # iRacing uses 0-based positions; bump to 1-based for display
                (drv.get("starting_position") + 1) if isinstance(drv.get("starting_position"), int) and drv.get("starting_position") >= 0 else None,
                (_fpos(drv) + 1),
                _best_lap_to_seconds(drv.get("best_lap_time")),
                _to_seconds(drv.get("average_lap")),
                drv.get("laps_lead") or 0,
                # dotd_score: NULL for imported races. The DOTD rubric
                # requires per-tick telemetry (clean-lap counts, comeback
                # curves, incident severity) that an eventresult dump
                # cannot reconstruct. Leaving it NULL makes the replay
                # table render "—" honestly instead of showing laps_led
                # dressed up as a DOTD score.
                None,
                is_owner_flag,
                drv.get("incidents") or 0,
                drv.get("oldi_rating") if isinstance(drv.get("oldi_rating"), int) and drv.get("oldi_rating") >= 0 else (drv.get("newi_rating") if isinstance(drv.get("newi_rating"), int) and drv.get("newi_rating") >= 0 else None),
                drv.get("old_license_level") if isinstance(drv.get("old_license_level"), int) and drv.get("old_license_level") >= 0 else (drv.get("new_license_level") if isinstance(drv.get("new_license_level"), int) and drv.get("new_license_level") >= 0 else None),
                drv.get("old_sub_level") if isinstance(drv.get("old_sub_level"), int) and drv.get("old_sub_level") >= 0 else (drv.get("new_sub_level") if isinstance(drv.get("new_sub_level"), int) and drv.get("new_sub_level") >= 0 else None),
                int(drv["cust_id"]) if isinstance(drv.get("cust_id"), int) else None,
            ),
        )

    _sync_owner_summary(cur, new_race_id)

    conn.commit()
    conn.close()

    msg = (
        f"Imported subsession {subsession_id} -> race_id={new_race_id}: "
        f"{track_name}" + (f" — {track_config}" if track_config else "")
        + f", {len(results)} drivers, {total_laps} laps"
        + (f", winner {winner.get('display_name')}" if winner else "")
    )
    return {
        "race_id": new_race_id,
        "merged": False,
        "subsession_id": subsession_id,
        "track_name": track_name,
        "track_config": track_config,
        "drivers_updated": len(finishers),
        "drivers_count": len(finishers),
        "unmatched": [],
        "message": msg,
    }


def import_eventresult(json_path: str, db_path: str = DB_PATH, *, force_new: bool = False) -> dict:
    """File-based entry point. Reads JSON from disk and delegates to
    ``import_eventresult_payload``. Returns the same structured result dict.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return import_eventresult_payload(payload, db_path, force_new=force_new)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("json_path")
    p.add_argument("--db", default=DB_PATH)
    p.add_argument(
        "--force-new",
        action="store_true",
        help="Insert a fresh race even if a row with the same iRacing subsession_id already exists.",
    )
    args = p.parse_args()
    if not os.path.exists(args.json_path):
        sys.exit(f"File not found: {args.json_path}")
    result = import_eventresult(args.json_path, args.db, force_new=args.force_new)
    print(result["message"])


if __name__ == "__main__":
    main()
