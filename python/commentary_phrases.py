# -*- coding: utf-8 -*-

import random
import os
import json
import re

import race_data_store
from live_leaderboard import get_leaderboard_snapshot
from race_data_store import clean_driver_name
import sector_says_utilities
from sector_says_utilities import voice_format_lap_time, voice_format_gap, voice_format_speed, get_race_times
import commentary_team

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE_DIR, "sector_said_exports")

# -------------------------------
# Helpers
# -------------------------------
def _safe_list(x):
    """Return x if it's a list, otherwise an empty list."""
    return x if isinstance(x, list) else []


# ─── Driver skill labelling from iRating / iRacing License ─────────
# iRating bands are the usual community conventions. Safety Rating (SR)
# runs 0.00–4.99 within each license class A/B/C/D/R (see
# https://support.iracing.com/support/solutions/articles/31000156960).
# We surface short descriptors so the AI commentary has an honest read
# on each driver's experience and cleanliness without having to know
# raw iRating numerology.
def _irating_tier(ir):
    try:
        ir = int(ir)
    except (TypeError, ValueError):
        return None
    if ir < 1350:  return "rookie"
    if ir < 2000:  return "novice"
    if ir < 3000:  return "solid"
    if ir < 4000:  return "strong"
    if ir < 5500:  return "top"
    return "elite"

def _safety_tier(lic_class, lic_safety):
    try:
        sr = float(lic_safety)
    except (TypeError, ValueError):
        return None
    cls = (lic_class or "").strip().upper() or "?"
    if sr < 2.0:  tag = "risky"
    elif sr < 3.5: tag = "steady"
    else:          tag = "clean"
    return f"{cls} {sr:.2f} ({tag})"

def _classify_race_length(total_laps):
    """Classify the race by length so the prediction model can weight
    grid position, iR, and SR appropriately.

    Lessons from Mugello races 14 and 16 (both 19-lap sprints with zero
    cautions): grid position dominated, low-SR front-runners held
    station cleanly, comebacks were small. Short races behave very
    differently from endurance-length events."""
    try:
        n = int(total_laps or 0)
    except Exception:
        return "unknown"
    if n <= 0:
        return "unknown"
    if n < 25:
        return "sprint"        # grid position dominates, cautions rare
    if n < 45:
        return "medium"        # mix of grid and pace
    return "long"              # pace, SR, and attrition matter most


def _race_length_weights(race_class):
    """Return (grid_weight, iR_weight, sr_weight) for the given class.
    Each is a rough narrative weight hint, not a math coefficient —
    we feed them into the prompt so the AI knows what to emphasise."""
    if race_class == "sprint":
        return {
            "grid_weight": 0.55,
            "iR_weight": 0.25,
            "sr_weight": 0.05,    # low SR risk is NOT a sprint heuristic
            "comeback_ceiling": 6,  # +6 places max in a sprint is aggressive
            "caution_likely": "rare",
        }
    if race_class == "long":
        return {
            "grid_weight": 0.20,
            "iR_weight": 0.35,
            "sr_weight": 0.25,   # long race gives low-SR time to crack
            "comeback_ceiling": 15,
            "caution_likely": "expected",
        }
    # medium or unknown
    return {
        "grid_weight": 0.35,
        "iR_weight": 0.35,
        "sr_weight": 0.15,
        "comeback_ceiling": 10,
        "caution_likely": "possible",
    }


def _get_track_history_pattern(track_name, limit=3):
    """Pull the most recent N races at the same track from race_db and
    summarise the pattern: how many cautions on average, how the winner
    compares to the polesitter, how many places the winner gained.

    Returns a short human-readable string or '' if no history. Lets the
    prediction prompt cite concrete precedent instead of inventing one."""
    if not track_name:
        return ""
    try:
        import race_db
    except Exception:
        return ""
    try:
        rows = race_db._conn().execute(
            """SELECT r.race_id, r.race_date, r.total_laps, r.winner_name,
                      (SELECT grid_position FROM race_drivers
                       WHERE race_id=r.race_id AND driver_name=r.winner_name
                         AND finish_position = 1
                       LIMIT 1) as winner_grid
               FROM races r
               WHERE r.track_name LIKE ?
               ORDER BY r.race_id DESC
               LIMIT ?""",
            (f"%{track_name}%", int(limit))
        ).fetchall()
    except Exception as e:
        print(f"[predict] track history lookup failed: {e}")
        return ""
    if not rows:
        return ""
    pattern_bits = []
    from_front_count = 0
    for r in rows:
        wname = r["winner_name"] or "?"
        wgrid = r["winner_grid"]
        # Skip races where the recorded winner didn't actually finish P1
        # (corrupted data from disconnects or broken live capture).
        if wgrid is None and wname != "?":
            continue
        if isinstance(wgrid, int) and wgrid <= 3:
            from_front_count += 1
        pattern_bits.append(
            f"race {r['race_id']}: {wname} won"
            + (f" from P{wgrid}" if wgrid else "")
        )
    summary = "; ".join(pattern_bits[:3])
    trend = ""
    if from_front_count >= 2 and len(rows) >= 2:
        trend = f" ({from_front_count}/{len(rows)} recent winners came from the front row)"
    return f"Past races here — {summary}{trend}."


def _grid_advantage_tag(car, race_class):
    """Return a one-word advantage tag based on the driver's position
    relative to the field in the current race class."""
    try:
        pos = int(car.get("Pos") or car.get("LivePos") or 999)
    except Exception:
        return ""
    if race_class == "sprint":
        if pos <= 3:
            return "grid favourite"
        if pos <= 6:
            return "podium threat"
        if pos >= 15:
            return "recovery candidate"
    elif race_class == "long":
        if pos <= 5:
            return "early pace-setter"
        if pos >= 15:
            return "attrition hunter"
    return ""


def _predict_finish_range(car, race_class, total_cars):
    """Project a realistic finish range for a driver given their current
    position, iR rank, and the race class. Returns (low, high) where
    low is the best-case finish and high is the worst. Capped to the
    field size."""
    try:
        pos = int(car.get("Pos") or car.get("LivePos") or 999)
    except Exception:
        return None
    ir_rank = car.get("IRRank")
    # Baseline: current position ± a small window.
    # Sprint: narrow window (grid locks). Long: wide window.
    if race_class == "sprint":
        window = 3
    elif race_class == "long":
        window = 6
    else:
        window = 4
    low = max(1, pos - window)
    high = min(total_cars or 99, pos + window)
    # If iR rank is significantly different from current pos, widen
    # toward the iR rank side — high-rated drivers starting deep get
    # upside, low-rated drivers leading have downside risk.
    if isinstance(ir_rank, int):
        delta = pos - ir_rank
        if delta >= 4:  # currently worse than their rating
            low = max(1, min(low, ir_rank + 1))
        elif delta <= -4:  # currently better than their rating
            high = min(total_cars or 99, max(high, ir_rank - 1))
    return (low, high)


def driver_skill_blurb(car):
    """Short human-readable skill tag for a leaderboard car dict.
    Returns '' if we don't have enough info. Safe to drop into prompts."""
    if not isinstance(car, dict):
        return ""
    ir = car.get("IRating")
    tier = _irating_tier(ir)
    sf = _safety_tier(car.get("LicClass"), car.get("LicSafety"))
    bits = []
    if ir and tier:
        bits.append(f"{sector_says_utilities.voice_format_irating(ir)} [{tier}]")
    elif ir:
        bits.append(sector_says_utilities.voice_format_irating(ir))
    if sf:
        bits.append(sf)
    return ", ".join(bits)

# --- Helper: get current lead host dynamically (aligned with live_leaderboard) ---
def _get_current_lead_host(default_host: str = "Murray"):
    try:
        t = get_race_times(None)  # uses cached last-known values
        elapsed = t.get("elapsed_seconds", 0.0)
        total = t.get("total_seconds", 1.0)
        lead_host, _, _ = commentary_team.get_host_and_energy(elapsed, total)
        return lead_host or default_host
    except Exception:
        return default_host

# --- Helper: wrap up by throwing back to current (or provided) lead host dynamically ---
def add_host_wrapup(lines, host=None):
    if lines is None:
        lines = []
    if not isinstance(lines, list):
        lines = [lines]
    use_host = host or _get_current_lead_host()
    lines.append(f"And back to you, {use_host}.")
    return lines

def get_focus_driver_record():
    try:
        path = os.path.join(EXPORT_DIR, "focus_driver_of_the_day.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# Rotation helpers
_last_owner_car_lines_used = {}
_last_fun_fact_prompt_used = None
_last_prediction_prompt_used = None
_last_historical_prompt_used = None
_last_trivia_prompt_used = None
_last_focus_driver_used = None


# ---------- snapshot formatter (robust, corrected) ----------
def _format_snapshot_detailed(snapshot, top_n=10):
    try:
        if not snapshot or not isinstance(snapshot, list):
            return {"cars": []}
        def safe_pos(c):
            try:
                return int(
                    c.get("LivePos")
                    or c.get("Position")
                    or c.get("Pos")
                    or c.get("ClassPosition")
                    or 9999
                )
            except:
                return 9999
        # Sort by position safely
        sorted_snapshot = sorted(snapshot, key=safe_pos)
        top_cars = sorted_snapshot[:top_n]
        # ------------------------------------------
        # FDOTD record lookup
        # ------------------------------------------
        fd = get_focus_driver_record()
        focus_car = None
        fd_idx = fd.get("CarIdx") if fd else None
        fd_num = str(fd.get("CarNumber", "")).lstrip("0") if fd else None
        fd_name = (
            clean_driver_name(fd.get("Name", "")).lower().strip()
            if fd else None
        )
        # Normalize number matching function
        def normalize_num(n):
            try:
                return str(int(n))  # "001" → "1"
            except:
                return str(n).strip()
        # PRIORITY 1 — CarIdx match
        if fd_idx is not None:
            for c in snapshot:
                if str(c.get("CarIdx", "")).strip() == str(fd_idx).strip():
                    focus_car = c
                    break
        # PRIORITY 2 — CarNumber match
        if focus_car is None and fd_num:
            for c in snapshot:
                cnum = normalize_num(c.get("CarNumber", ""))
                if cnum == normalize_num(fd_num):
                    focus_car = c
                    break
        # PRIORITY 3 — Name match
        if focus_car is None and fd_name:
            for c in snapshot:
                cname = clean_driver_name(c.get("Name", "")).lower().strip()
                if cname == fd_name:
                    focus_car = c
                    break
        # Add FDOTD into the snapshot if missing
        cars_to_include = list(top_cars)
        if focus_car and focus_car not in cars_to_include:
            cars_to_include.append(focus_car)

        # ── Field-wide iRating ranking for expected-vs-actual analysis ──
        # Rank every car in the FULL snapshot (not just the top slice)
        # by iRating, so downstream prompts can say things like
        # "iR rank 11 finishing P8 = +3 above expectation". We ignore
        # cars without an iRating rather than penalising them.
        ir_ranked = sorted(
            [c for c in snapshot if isinstance(c.get("IRating"), (int, float)) and c.get("IRating") > 0],
            key=lambda c: -float(c.get("IRating") or 0)
        )
        ir_rank_by_idx = {}
        for rank, c in enumerate(ir_ranked, start=1):
            cid = c.get("CarIdx")
            if cid is not None:
                ir_rank_by_idx[cid] = rank
        # Helper: Lap formatting
        def fmt_lap(val):
            try:
                if val in (None, "", "None", 0, "0", 0.0):
                    return "--"
                return voice_format_lap_time(float(val))
            except:
                return "--"
        # Helper: Gap formatting
        def safe_gap(c):
            # Best available gap key
            for key in ("GapAhead", "GapToLeader", "Gap", "GapToFront"):
                val = c.get(key)
                if val not in (None, "", "None"):
                    try:
                        return voice_format_gap(val)
                    except:
                        return "--"
            return "--"
        # Helper: clean sectors
        def clean_sector(v):
            # Handle 0, "0", 0.0, None, "--"
            if v in (None, "", "None", "--"):
                return "--"
            try:
                return str(v)
            except:
                return "--"
        # Build cleaned list
        clean_list = []
        seen_ids = set()  # Avoid duplicates by CarIdx
        for c in cars_to_include:
            cid = c.get("CarIdx")
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            name_raw = c.get("Name", c.get("Driver", "Unknown"))
            name = clean_driver_name(name_raw) if isinstance(name_raw, str) else "Unknown"
            # Normalize CarNumber
            car_num_raw = c.get("CarNumber", "")
            try:
                car_num = str(int(car_num_raw))
            except:
                car_num = str(car_num_raw).strip()
            # Normalize laps
            raw_lap = c.get("Lap", c.get("Laps", "?"))
            try:
                lap_val = int(float(raw_lap))
            except:
                lap_val = raw_lap or "?"
            # Best / Last — try multiple keys
            best_raw = (
                c.get("Best")
                or c.get("BestLap")
                or c.get("BestLapTime")
                or None
            )
            last_raw = (
                c.get("Last")
                or c.get("LastLap")
                or c.get("LastLapTime")
                or None
            )
            # Final car info structure
            cur_pos = safe_pos(c)
            ir_rank = ir_rank_by_idx.get(cid)
            # Expected-vs-actual: how many places is this driver ahead
            # of (positive) or behind (negative) where their iRating
            # rank would put them. This is the analytical hook that
            # turns a raw leaderboard into a "who's over/under
            # performing" narrative.
            if ir_rank is not None and isinstance(cur_pos, int):
                perf_delta = ir_rank - cur_pos
            else:
                perf_delta = None

            # Thin-ice status from LiveIncidents (ties into the tiered
            # penalty curve: 1-4 free, 5-9 -2, 10-14 -4, 15+ -6).
            inc = int(c.get("LiveIncidents") or 0)
            if inc >= 15:
                inc_status = "danger"     # 15+ incidents — one more = big hit
            elif inc >= 10:
                inc_status = "thin ice"   # 10-14 — each one costs -4
            elif inc >= 5:
                inc_status = "scruffy"    # 5-9 — each one costs -2
            else:
                inc_status = "clean"      # 0-4 — free zone

            car_info = {
                "CarIdx": cid,
                "CarNumber": car_num,
                "Name": name,
                "Pos": cur_pos,
                "Lap": lap_val,
                "LapsLed": c.get("LapsLed", c.get("LapLed", c.get("Led", 0))),
                "Best": fmt_lap(best_raw),
                "Last": fmt_lap(last_raw),
                "S1": clean_sector(c.get("S1")),
                "S2": clean_sector(c.get("S2")),
                "S3": clean_sector(c.get("S3")),
                "Gap": safe_gap(c),
                "DOTD": c.get("DOTD", c.get("dotd", 0)),
                # iRacing credentials — populated by live_leaderboard from
                # DriverInfo YAML. May be None before session info loads.
                "IRating": c.get("IRating"),
                "LicClass": c.get("LicClass"),
                "LicSafety": c.get("LicSafety"),
                "LicString": c.get("LicString"),
                "SkillTier": _irating_tier(c.get("IRating")),
                "SkillBlurb": driver_skill_blurb(c),
                # Analytical layer — iR rank in field + performance delta
                "IRRank": ir_rank,
                "PerformanceDelta": perf_delta,
                "IncidentStatus": inc_status,
                # Pit state (from CarIdxOnPitRoad edge tracker)
                "OnPitRoad": bool(c.get("OnPitRoad")),
                "PitStops": int(c.get("PitStops") or 0),
                "LastPitLap": c.get("LastPitLap"),
                "LastPitDuration": c.get("LastPitDuration"),
                # Live incidents from SessionInfo ResultsPositions
                "LiveIncidents": inc,
                "is_focus": (focus_car is not None and c is focus_car),
            }
            clean_list.append(car_info)
        return {"cars": clean_list}
    except Exception as e:
        print(f"[ERROR _format_snapshot_detailed] {e}")
        return {"cars": []}

# -------------------------------
# Fun Fact (Fully Stabilized)
# -------------------------------
def fun_fact_phrases(leaderboard=None,race_data_store_module=None,track_info=None,host="Commentator"):
    try:
        global _last_fun_fact_prompt_used
        # Safe load of weekend metadata
        weekend_info = (
            track_info
            or getattr(race_data_store_module or race_data_store, "current_weekend_info", {})
            or {}
        )
        def safe(val, default="Unknown"):
            if val in (None, "", "None"):
                return default
            return val
        # ---------------------------------------
        # Track / Location Information
        # ---------------------------------------
        track = safe(
            weekend_info.get("TrackDisplayName")
            or weekend_info.get("track_display_name")
            or weekend_info.get("track_name")
            or "Unknown Track"
        )
        city = safe(
            weekend_info.get("TrackCity")
            or weekend_info.get("track_city")
            or "Unknown City"
        )
        country = safe(
            weekend_info.get("TrackCountry")
            or weekend_info.get("track_country")
            or "Unknown Country"
        )
        # ---------------------------------------
        # Track length normalization
        # ---------------------------------------
        raw_len = (
            weekend_info.get("TrackLength")
            or weekend_info.get("track_length")
            or None
        )
        def fmt_length(x):
            try:
                v = float(x)
                if v > 50:
                    return f"{v/1000:.1f} km"   # meters → km
                return f"{v:.1f} km"
            except:
                return "length unknown"
        track_length = fmt_length(raw_len) if raw_len else "length unknown"
        # ---------------------------------------
        # Prompt rotation. Each prompt narrows to ONE verifiable fact
        # category so the LLM has less room to invent fictitious local
        # festivals, races, or food traditions. Race 4 had a fabricated
        # "Glen 5K where runners race through the iconic track" — that
        # path is now closed by the explicit FACT-SAFETY rule below.
        # ---------------------------------------
        FACT_SAFETY = (
            " FACT-SAFETY: Stick to widely-known, verifiable facts about "
            "the venue or city. Do NOT invent annual races, charity runs, "
            "festivals, food traditions, or local events. If you cannot "
            "name a real one, talk about the track itself (length, year "
            "opened, famous corner, climate, surrounding geography) "
            "instead. Better to say less than to make something up."
        )
        prompts = [
            (
                f"In one or two broadcast-style sentences, introduce "
                f"{track} in {city}, {country}. Focus on the track's own "
                f"character — length ({track_length}), year established, "
                f"or a famous corner that experienced racing fans would "
                f"recognise."
                + FACT_SAFETY
            ),
            (
                f"Write a short broadcaster line introducing {city}, "
                f"{country}, home to {track}. Mention what the city is "
                f"genuinely known for in geography, climate, or industry "
                f"(only if you can name something real)."
                + FACT_SAFETY
            ),
            (
                f"Compose a 1-2 sentence broadcast intro about {track}. "
                f"Anchor on the circuit itself: lap length {track_length}, "
                f"its position in the motorsport calendar, or the kind "
                f"of racing style it rewards (high-speed, technical, "
                f"flowing, etc)."
                + FACT_SAFETY
            ),
            (
                f"Brief broadcast line about racing at {track} in {city}, "
                f"{country}. Lead with one factual hook from the track's "
                f"history (an iconic moment, a layout change, the sanctioning "
                f"body) — but only if you can cite something verifiable."
                + FACT_SAFETY
            ),
        ]
        # Ensure rotation still works even if last index is out of range
        last_idx = _last_fun_fact_prompt_used if isinstance(_last_fun_fact_prompt_used, int) else -1
        valid_choices = [i for i in range(len(prompts)) if i != last_idx] or [0]
        idx = random.choice(valid_choices)
        _last_fun_fact_prompt_used = idx
        final_line = prompts[idx]

        # Optional field-skill flavour — if the grid has enough iRating data
        # the AI can weave in a one-line colour note about the overall field
        # quality (elite-heavy, mixed, rookie-heavy…). Purely additive so it
        # never breaks the core track-trivia prompt.
        try:
            lb = get_leaderboard_snapshot() or []
            irs = [c.get("IRating") for c in lb if isinstance(c.get("IRating"), (int, float))]
            if len(irs) >= 5:
                avg_ir = int(sum(irs) / len(irs))
                tier = _irating_tier(avg_ir) or "mixed"
                final_line += (
                    f" (Optional colour: today's field averages iRating {avg_ir} "
                    f"({tier} overall) — weave this in only if it lands naturally.)"
                )
        except Exception:
            pass

        return add_host_wrapup(final_line, host)
    except Exception as e:
        print(f"[ERROR fun_fact] {e}")
        # Absolute last fallback — still produces a usable line
        return add_host_wrapup(
            "Local fun fact segment is warming up — more details as telemetry stabilizes.",
            host
        )

# -------------------------------
# Race Prediction (Fully Stabilized)
# -------------------------------
def race_prediction_phrases(
    leaderboard=None,
    race_data_module=None,
    track_info=None,
    host="Commentator"
):
    try:
        lb = leaderboard or get_leaderboard_snapshot() or []
        if not isinstance(lb, list) or len(lb) == 0:
            return add_host_wrapup(
                "Race prediction unavailable: awaiting updated timing data.",
                host
            )

        # --------------------------------------------
        # Robust Position Helper
        # --------------------------------------------
        def safe_pos(c):
            try:
                return int(c.get("LivePos") or c.get("Position") or 9999)
            except:
                return 9999

        # --------------------------------------------
        # Snapshot Build
        # --------------------------------------------
        snapshot = _format_snapshot_detailed(lb)
        cars_block = _safe_list(snapshot.get("cars"))

        if not cars_block:
            leaders = sorted(lb, key=safe_pos)[:3]
            cars_block = []
            for c in leaders:
                cars_block.append({
                    "Name": clean_driver_name(c.get("Name", c.get("Driver", "Driver"))),
                    "Pos": safe_pos(c),
                    "ΔPos": c.get("ΔPos") or c.get("DeltaPos") or 0,
                })

        # --------------------------------------------
        # Car Output Normalization
        # --------------------------------------------
        def clean_delta(dp):
            try:
                return int(dp)
            except:
                return 0

        def clean_car(c):
            nm = clean_driver_name(c.get("Name", c.get("Driver", "Driver")))
            pos = c.get("Pos", c.get("LivePos", "?"))
            try:
                pos = int(pos)
            except:
                pos = "?"
            dp = clean_delta(c.get("ΔPos") or c.get("DeltaPos"))
            skill = c.get("SkillBlurb") or driver_skill_blurb(c)
            bits = [f"P{pos}", f"ΔPos {dp:+d}"]
            # Expected-vs-actual narrative hook
            ir_rank = c.get("IRRank")
            perf = c.get("PerformanceDelta")
            if ir_rank is not None:
                bits.append(f"iR#{ir_rank}")
                if perf is not None:
                    if perf > 0:
                        bits.append(f"+{perf} vs rating")
                    elif perf < 0:
                        bits.append(f"{perf} vs rating")
            if skill:
                bits.append(skill)
            # Pit state
            stops = int(c.get("PitStops") or 0)
            if c.get("OnPitRoad"):
                bits.append("IN PITS")
            elif stops > 0:
                bits.append(f"{stops} stop{'s' if stops != 1 else ''}")
            # Incidents + thin-ice status
            inc = int(c.get("LiveIncidents") or 0)
            inc_status = c.get("IncidentStatus") or ""
            if inc > 0:
                if inc_status and inc_status != "clean":
                    bits.append(f"{inc}x [{inc_status}]")
                else:
                    bits.append(f"{inc}x")
            return f"{nm} ({', '.join(bits)})"

        cars_text = ", ".join(clean_car(c) for c in cars_block)

        # --------------------------------------------
        # Field skill summary — gives the AI a quick read on the
        # overall quality of the grid and where the Focus Driver sits
        # relative to it. All fields tolerate missing iR/license data.
        # --------------------------------------------
        irs = [c.get("IRating") for c in cars_block if isinstance(c.get("IRating"), (int, float))]
        field_skill_text = ""
        if irs:
            avg_ir = int(sum(irs) / len(irs))
            hi_ir = int(max(irs))
            lo_ir = int(min(irs))
            field_skill_text = (
                f"Field iRating: avg {sector_says_utilities.voice_format_irating(avg_ir)}, "
                f"range {sector_says_utilities.voice_format_irating(lo_ir)} to "
                f"{sector_says_utilities.voice_format_irating(hi_ir)} "
                f"({_irating_tier(avg_ir) or 'mixed'} overall)."
            )

        # --------------------------------------------
        # DOTD Top 3
        # --------------------------------------------
        dotd_data = {}
        dotd_path = os.path.join(EXPORT_DIR, "dotd_top3.json")
        if os.path.exists(dotd_path):
            try:
                with open(dotd_path, "r", encoding="utf-8") as f:
                    dotd_data = json.load(f)
            except:
                dotd_data = {}

        raw_top3 = dotd_data.get("top3", []) or []

        # --------------------------------------------
        # Load FDOTD JSON (Correct Source for Percent)
        # --------------------------------------------
        fd_json = {}
        fd_path = os.path.join(EXPORT_DIR, "focus_driver_of_the_day.json")
        if os.path.exists(fd_path):
            try:
                with open(fd_path, "r", encoding="utf-8") as f:
                    fd_json = json.load(f)
            except:
                fd_json = {}

        fd_target_name = clean_driver_name(fd_json.get("Name", ""))

        # --------------------------------------------
        # Resolve FD Name → Telemetry Car
        # --------------------------------------------
        focus_driver = None
        if fd_target_name:
            for c in lb:
                if clean_driver_name(c.get("Name", c.get("Driver", ""))) == fd_target_name:
                    focus_driver = c
                    break

        # Fallback to CarIdx in FDOTD JSON
        if not focus_driver:
            try:
                fd_idx = str(fd_json.get("CarIdx", "")).strip()
                for c in lb:
                    if str(c.get("CarIdx", "")).strip() == fd_idx:
                        focus_driver = c
                        break
            except:
                pass

        # Final fallback → leader
        if not focus_driver:
            focus_driver = sorted(lb, key=safe_pos)[0]

        # --------------------------------------------
        # Extract FD fields
        # --------------------------------------------
        fd_name = clean_driver_name(
            focus_driver.get("Name") or focus_driver.get("Driver") or "Driver"
        )

        try:
            fd_pos = int(focus_driver.get("LivePos", focus_driver.get("Position", 999)))
        except:
            fd_pos = "?"

        # Correct DOTD Percent source
        try:
            fd_percent = int(round(float(fd_json.get("DOTDPercent", 0))))
        except:
            fd_percent = 0

        # --------------------------------------------
        # Filter out FD from Top 3
        # --------------------------------------------
        fd_lower = fd_name.lower()
        def pct(d):
            try:
                return int(round(float(d.get("percent", 0))))
            except:
                return 0

        filtered_top3 = [
            d for d in raw_top3
            if clean_driver_name(d.get("name", d.get("Driver", ""))).lower() != fd_lower
        ]
        filtered_top3 = sorted(filtered_top3, key=pct, reverse=True)[:3]

        top3_entries = ", ".join(
            f"{d.get('name', 'Unknown')} ({pct(d)}%)" for d in filtered_top3
        ) or "no data"

        fd_skill = driver_skill_blurb(focus_driver)
        fd_skill_text = f" Skill read: {fd_skill}." if fd_skill else ""

        formatted_dotd = (
            f"Top contenders: {top3_entries}. "
            f"Focus Driver: {fd_name} ({fd_percent}%, P{fd_pos}).{fd_skill_text}"
        )

        # --------------------------------------------
        # Race-length context — pulled from broadcast_state / weekend
        # info so the prediction can weight grid vs pace appropriately.
        # --------------------------------------------
        total_laps = 0
        current_lap = 0
        track_name_for_pattern = ""
        try:
            bs_path = os.path.join(EXPORT_DIR, "broadcast_state.json")
            if os.path.exists(bs_path):
                with open(bs_path, "r", encoding="utf-8") as f:
                    bs = json.load(f)
                total_laps = int(bs.get("total_laps") or 0)
                current_lap = int(bs.get("lap") or 0)
            wi_path = os.path.join(EXPORT_DIR, "weekend_info.json")
            if os.path.exists(wi_path):
                with open(wi_path, "r", encoding="utf-8") as f:
                    wi = json.load(f)
                track_name_for_pattern = (
                    wi.get("TrackDisplayName")
                    or wi.get("track_display_name")
                    or wi.get("track_name")
                    or ""
                )
        except Exception:
            pass
        # Clamp sentinel values (iRacing 32767 = unlimited)
        if total_laps > 999:
            total_laps = 0
        race_class = _classify_race_length(total_laps)
        weights = _race_length_weights(race_class)

        # Race-phase awareness (early / mid / late)
        race_progress = 0.0
        if total_laps > 0 and current_lap > 0:
            race_progress = min(1.0, current_lap / float(total_laps))
        if race_progress < 0.25:
            phase = "early"
        elif race_progress < 0.75:
            phase = "mid"
        else:
            phase = "late"

        # Track history pattern — cites prior races at this venue.
        track_history = _get_track_history_pattern(track_name_for_pattern, limit=3)

        # Focus driver realistic finish range
        fd_range = _predict_finish_range(focus_driver, race_class, len(lb))
        fd_range_text = ""
        if fd_range and isinstance(fd_pos, int):
            lo, hi = fd_range
            fd_range_text = f"Realistic finish range: P{lo}-P{hi}."

        # Grid-advantage tags for the top 5 and the focus driver
        def _advantage_line():
            bits = []
            for c in cars_block[:5]:
                nm = clean_driver_name(c.get("Name", "?"))
                tag = _grid_advantage_tag(c, race_class)
                if tag:
                    bits.append(f"{nm}: {tag}")
            fd_tag = _grid_advantage_tag(focus_driver, race_class)
            if fd_tag and isinstance(fd_pos, int):
                bits.append(f"{fd_name}: {fd_tag}")
            return "; ".join(bits) if bits else ""
        advantage_text = _advantage_line()

        # --------------------------------------------
        # Final Prompt — race-length aware
        # --------------------------------------------
        race_class_label = {
            "sprint": f"{total_laps}-lap sprint",
            "medium": f"{total_laps}-lap race",
            "long":   f"{total_laps}-lap long-distance race",
            "unknown": "race",
        }.get(race_class, "race")

        phase_guidance = {
            "early": (
                "This is the EARLY phase — focus on who is off their grid "
                "position (gained or lost), any first-lap chaos reflected "
                "in ΔPos, and whether the front row is holding."
            ),
            "mid": (
                "This is the MID phase — focus on sustained pace, position "
                "trends over the last several laps, and who is on a "
                "recovery drive from deep on the grid."
            ),
            "late": (
                "This is the LATE phase — focus on who is locked in vs "
                "who is still closing, incident counts near the danger "
                "zone, and the focus driver's projected final position."
            ),
        }[phase]

        class_guidance = {
            "sprint": (
                "SPRINT-RACE RULE: grid position dominates in sub-25-lap "
                "races with few cautions. A clean front-row starter almost "
                "always converts. Do NOT predict that a low-SR driver at "
                "the front will crash — that heuristic is for long races. "
                "Big comebacks from deep are capped around 6 places. Track "
                "position is king."
            ),
            "medium": (
                "MEDIUM-RACE RULE: grid and pace both matter. Comeback "
                "drives of up to 10 places are realistic if there is any "
                "attrition. Watch for incident-tier flips."
            ),
            "long": (
                "LONG-RACE RULE: pace and SR cleanliness matter most, grid "
                "position matters least. Low-SR drivers at the front are "
                "a real attrition risk over this distance. Comeback drives "
                "of 15+ places are possible."
            ),
            "unknown": (
                "Race length unknown — use position trends, ΔPos, and "
                "incident status as the primary signals."
            ),
        }[race_class]

        analysis_prompt = (
            f"RACE CONTEXT: {race_class_label}, currently at {int(race_progress*100)}%"
            f" of the distance ({phase} phase). "
            + (f"{track_history} " if track_history else "")
            + f"Live race data: {cars_text}. "
            f"{field_skill_text} "
            f"Driver of the Day standings: {formatted_dotd} "
            + (f"GRID ADVANTAGE TAGS: {advantage_text}. " if advantage_text else "")
            + (f"FOCUS DRIVER PROJECTION: {fd_range_text} " if fd_range_text else "")
            + f"Each driver tag shows iR rank in field (iR#N), performance "
            f"delta vs rating (+X / -X vs rating), iRating tier (rookie/"
            f"novice/solid/strong/top/elite), safety class + SR, current "
            f"pit status, and live incident count with tier status "
            f"(clean/scruffy/thin ice/danger). "
            f"{class_guidance} "
            f"{phase_guidance} "
            f"Deliver a short broadcast prediction using ONLY these data "
            f"points. Ground every claim in a number or tag from above. "
            f"Do NOT mention tyres, tyre strategy, undercut/overcut, fuel, "
            f"pit windows, sector reliability, or pit strategy. A pitting "
            f"driver is most likely losing time — never frame a pit stop "
            f"as strategic."
        )

        return add_host_wrapup(analysis_prompt, host)

    except Exception as e:
        print(f"[ERROR race_prediction] {e}")
        return add_host_wrapup(
            "Prediction module encountered an unexpected fault — awaiting updated telemetry.",
            host
        )

# -------------------------------
# Historical / Local Color
# -------------------------------
def historical_phrases(leaderboard=None,race_data_module=None,track_info=None,host="Commentator"):
    try:
        global _last_historical_prompt_used
        # --- Weekend metadata ---
        weekend_info = (
            track_info
            or getattr(race_data_store, "current_weekend_info", {})
            or {}
        )
        # Safe cleaner
        def safe(v, default="Unknown"):
            if v in (None, "", "None"):
                return default
            return v
        city = safe(
            weekend_info.get("TrackCity")
            or weekend_info.get("track_city")
            or "Unknown City"
        )
        country = safe(
            weekend_info.get("TrackCountry")
            or weekend_info.get("track_country")
            or "Unknown Country"
        )
        track = safe(
            weekend_info.get("TrackDisplayName")
            or weekend_info.get("track_display_name")
            or "Unknown Track"
        )
        # Grammar helper — "the {city} history" -> "{city}'s history"
        def possessive(city_name):
            city_name = safe(city_name)
            if city_name.endswith("s"):
                return f"{city_name}'"
            return f"{city_name}'s"
        city_possessive = possessive(city)
        # --- Prompts. Each variant locks onto ONE verifiable category
        # so the LLM has less room to invent local colour. Rotated to
        # avoid the same opening line twice in a race. ---
        FACT_SAFETY = (
            " FACT-SAFETY: stick to widely-known, verifiable facts. Do "
            "NOT invent festivals, annual races, charity events, food "
            "traditions, or 'did you know' trivia you cannot verify. If "
            "no real fact is at hand, lean on the track's own racing "
            "history (years it has hosted top series, layout era, "
            "famous winners). Better to say less than to fabricate."
        )
        # Rotate openings so consecutive calls don't both begin with
        # "{city}, {country} is renowned for…" — that pattern dominated
        # race 4's two historical lines.
        opening_rotations = [
            "Lead with the track itself (year opened, layout era).",
            "Lead with the city's geography or climate, not its history.",
            "Lead with motorsport at this venue (series it hosts).",
            "Lead with a single concrete winner / record from this circuit.",
        ]
        rot = opening_rotations[(_last_historical_prompt_used or 0) % len(opening_rotations)]
        prompts = [
            (
                f"In two broadcast sentences, give an 'About Here' beat "
                f"for a race at {track} in {city}, {country}. {rot}"
                + FACT_SAFETY
            ),
            (
                f"Write a short broadcast line introducing the venue at "
                f"{track}. Anchor on something verifiable about the "
                f"circuit's place in motorsport. {rot}"
                + FACT_SAFETY
            ),
            (
                f"Provide a 1-2 sentence broadcast snippet about the "
                f"setting for today's race at {track} in {city}, {country}. "
                f"{rot}"
                + FACT_SAFETY
            ),
            (
                f"Generate a brief racing-broadcast intro to {track} "
                f"({city}, {country}). {rot}"
                + FACT_SAFETY
            ),
        ]
        # --- Avoid repeating the same prompt ---
        last_idx = (
            _last_historical_prompt_used
            if isinstance(_last_historical_prompt_used, int)
            else -1
        )
        choices = [i for i in range(len(prompts)) if i != last_idx]
        idx = random.choice(choices)
        _last_historical_prompt_used = idx
        final_line = prompts[idx]
        return add_host_wrapup(final_line, host)
    except Exception as e:
        print(f"[ERROR historical] {e}")
        return add_host_wrapup(
            "Historical insight unavailable — telemetry stabilizing.",
            host
        )

# -------------------------------
# Trivia Time (Stabilized)
# -------------------------------
def trivia_time_phrases(leaderboard=None,
                        race_data_module=None,
                        track_info=None,
                        host="Commentator"):
    try:
        global _last_trivia_prompt_used

        # ==========================================================
        # LOAD WEEKEND INFO SAFELY
        # ==========================================================
        weekend_info = (
            track_info
            or getattr(race_data_module or race_data_store, "current_weekend_info", {})
            or {}
        )

        def safe(val, default="Unknown"):
            return default if val in (None, "", "None") else val

        # Track identity
        track = safe(
            weekend_info.get("TrackDisplayName")
            or weekend_info.get("track_display_name")
            or "Unknown Track"
        )
        city = safe(
            weekend_info.get("TrackCity")
            or weekend_info.get("track_city")
            or "Unknown City"
        )
        country = safe(
            weekend_info.get("TrackCountry")
            or weekend_info.get("track_country")
            or "Unknown Country"
        )

        # ==========================================================
        # TRACK LENGTH — CLEANED + NORMALIZED
        # ==========================================================
        raw_length = (
            weekend_info.get("TrackLength")
            or weekend_info.get("track_length")
            or None
        )

        def fmt_length(v):
            try:
                f = float(v)
                if f > 50:      # assume meters
                    return f"{f/1000:.1f} km"
                return f"{f:.1f} km"
            except:
                return "unknown length"

        track_length = fmt_length(raw_length) if raw_length else "unknown length"

        # ==========================================================
        # WEATHER NORMALIZATION (AIR / TRACK / HUMIDITY / SKIES)
        # ==========================================================
        def extract_number(text):
            if not text:
                return None
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(text))
            return float(m.group(1)) if m else None

        # air temp
        air_temp_val = extract_number(weekend_info.get("WeatherTemp"))
        air_temp = f"{air_temp_val:.0f}" if air_temp_val is not None else "unreported"

        # track temp
        track_temp_val = extract_number(
            weekend_info.get("TrackTemp")
            or weekend_info.get("TrackTempCrew")
        )
        track_temp = f"{track_temp_val:.0f}" if track_temp_val is not None else "unreported"

        # humidity
        humidity_val = extract_number(weekend_info.get("RelativeHumidity"))
        humidity = f"{humidity_val:.0f}" if humidity_val is not None else "unreported"

        # skies
        skies_raw = weekend_info.get("Skies") or ""
        skies_clean = skies_raw.lower().strip()
        if skies_clean in ("", "none", "n/a", "unknown"):
            skies_clean = "unreported"

        # ==========================================================
        # TRIVIA PROMPTS — Q&A FORMAT
        # Every prompt makes the host POSE A QUESTION to the audience,
        # pause with "...", then give the answer. Broadcast game-show
        # style, not a lecture. Questions must be real-fact-based —
        # never invent history.
        # ==========================================================
        qa_frame = (
            "Trivia time — broadcast style. Pose ONE real trivia "
            "question about the topic below, then immediately answer "
            "it yourself. Format exactly: 'Here's one for you — "
            "<question>?... The answer: <answer>.' or equivalent "
            "broadcast phrasing with a '...' pause before the reveal. "
            "Never invent facts; if you aren't sure of a detail, stay "
            "general (e.g. 'decades ago' rather than a specific year). "
            "Single sentence for the question, single sentence for the "
            "answer. No preamble, no hosts introducing themselves."
        )
        topics = [
            (f"Topic: a layout quirk, famous corner, or elevation "
             f"feature of {track} in {city}, {country}."),
            (f"Topic: a historical milestone in the history of {track} "
             f"({track_length} per lap) — its debut, a famous moment, "
             f"or a record set there."),
            (f"Topic: the design origins of {track} — its original "
             f"purpose before racing, or a quirk of how it was built."),
            (f"Topic: a signature corner or sector at {track} and why "
             f"drivers remember it."),
            (f"Topic: a unique feature of {track} that no other circuit "
             f"on the calendar shares."),
            (f"Topic: a famous driver or team moment connected to "
             f"{track} in {city}, {country}."),
        ]
        prompts = [f"{qa_frame} {topic}" for topic in topics]

        # ==========================================================
        # ROTATE PROMPTS (NEVER REPEAT BACK-TO-BACK)
        # ==========================================================
        last_idx = (
            _last_trivia_prompt_used
            if isinstance(_last_trivia_prompt_used, int)
            else -1
        )

        available = [i for i in range(len(prompts)) if i != last_idx]
        idx = random.choice(available)
        _last_trivia_prompt_used = idx

        # ==========================================================
        # FINAL OUTPUT — ALWAYS VALID
        # ==========================================================
        return add_host_wrapup(prompts[idx], host)

    except Exception as e:
        print(f"[ERROR trivia_time] {e}")
        return add_host_wrapup(
            "Trivia segment paused while we grab the right notes.",
            host
        )


# -------------------------------
# Timing Tower Snapshot Formatter
# -------------------------------
def _format_timing_tower(snapshot):
    lines = []
    if not snapshot or not isinstance(snapshot, list):
        return lines
    for entry in snapshot:
        if not isinstance(entry, dict):
            continue
        try:
            pos = int(entry.get("Pos") or entry.get("LivePos") or 0)
        except Exception:
            pos = 0
        driver = entry.get("Driver") or entry.get("Name") or ""
        last = driver.strip().split()[-1] if isinstance(driver, str) and driver.strip() else ""
        short = last[:3].upper() if len(last) >= 1 else "---"
        dpos = entry.get("ΔPos")
        if dpos is None:
            dpos = entry.get("DeltaPos")
        try:
            dpos_val = int(dpos)
            dpos_str = f"{dpos_val:+d}"
        except Exception:
            dpos_str = "   "
        gap = entry.get("GapToLeader", entry.get("GapAhead"))
        gap_str = ""
        if pos == 2:
            try:
                gap_str = f"{float(gap):.3f}"
            except Exception:
                gap_str = ""
        lines.append(f"{pos:>2} {short} {dpos_str:>4} {gap_str:>7}")
    return lines

# -------------------------------
# Speed Fun Fact (Rewritten)
# -------------------------------
def speed_fun_fact_phrases(leaderboard=None, race_data_store_module=None,track_info=None, host="Commentator"):
    try:
        # TRACK LENGTH
        weekend_info = (
            track_info
            or getattr(race_data_store_module or race_data_store, "current_weekend_info", {})
            or {}
        )
        raw_len = (
            weekend_info.get("TrackLength")
            or weekend_info.get("track_length")
            or ""
        )
        s = str(raw_len).lower().strip()
        length_km = None
        km_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*km", s)
        if km_match:
            length_km = float(km_match.group(1))
        if length_km is None:
            mi_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mi", s)
            if mi_match:
                length_km = float(mi_match.group(1)) * 1.60934
        if length_km is None:
            num_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", s)
            if num_match:
                val = float(num_match.group(1))
                length_km = (val / 1000.0) if val > 50 else val
        if length_km is None:
            length_km = 0.0
        # LOAD FDOTD JSON
        try:
            with open("sector_said_exports/focus_driver_of_the_day.json",
                      "r", encoding="utf-8") as f:
                fd = json.load(f)
        except:
            fd = {}
        fd_last = fd.get("Last")
        fd_best = fd.get("Best")
        fd_name = fd.get("Name", "the Focus Driver")
        fd_percent = fd.get("DOTDPercent", None)
        # LEADER LAP TIME (existing logic)
        lb = leaderboard or get_leaderboard_snapshot() or []
        if not isinstance(lb, list):
            lb = []
        leader = None
        if lb:
            try:
                leader = sorted(lb, key=lambda c: c.get("LivePos", 9999))[0]
            except Exception:
                leader = None
        last_lap_time = None
        # Leader-based last lap time
        if leader:
            raw_last = (
                leader.get("Last")
                or leader.get("LastLap")
                or leader.get("BestLapTime")
                or None
            )
            try:
                if raw_last not in (None, "", "None", -1, "-1", 0, "0"):
                    last_lap_time = float(raw_last)
            except:
                pass
        # NEW — FDOTD fallback if leader lap is invalid
        def good(v):
            return v not in (None, "", "None", -1, "-1", 0, "0")
        if last_lap_time is None and good(fd_last):
            last_lap_time = float(fd_last)
        elif last_lap_time is None and good(fd_best):
            last_lap_time = float(fd_best)
        # Global fallback to field average
        if last_lap_time is None:
            times = []
            for c in lb:
                raw = c.get("Last") or c.get("LastLap")
                try:
                    if good(raw):
                        times.append(float(raw))
                except:
                    pass
            last_lap_time = (sum(times) / len(times)) if times else 70.0
        last_lap_time = max(40.0, min(last_lap_time, 200.0))
        # SPEED CALCULATION (existing)
        if length_km > 0:
            avg_speed = length_km / (last_lap_time / 3600.0)
        else:
            avg_speed = 0.0
        avg_speed = max(0.0, min(avg_speed, 400.0))
        lap_str = voice_format_lap_time(last_lap_time)
        # BUILD COMMENTARY PROMPT (NEW – richer, FDOTD-aware)
        dotd_snippet = ""
        if fd_percent is not None:
            dotd_snippet = (
                f" Our Focus Driver {fd_name} currently holds {fd_percent:.0f}% "
                f"of the Driver of the Day vote, adding extra intrigue to today's pace profile."
            )
        analysis_prompt = (
            f"This circuit covers {length_km:.2f} km per lap. The latest stable data gives us "
            f"a representative lap time of {lap_str}, translating to an average speed of "
            f"about {voice_format_speed(avg_speed)}.{dotd_snippet} "
            f"Turn this into a smooth, broadcast-ready insight about the sheer speed and character "
            f"of this venue."
        )
        return add_host_wrapup(analysis_prompt, host)
    except Exception as e:
        print(f"[ERROR speed_fun_fact] {e}")
        return add_host_wrapup(
            "Speed fun fact is cooling off in the pits while we refresh data.",
            host
        )

# -------------------------------
# Focus Driver of the Day (Stabilized)
# -------------------------------
def focus_driver_of_the_day_phrases(leaderboard=None,race_data_store_module=None,track_info=None,host="Commentator"):
    try:
        global _last_focus_driver_used
        # ============================================================
        # LOAD LEADERBOARD + WEEKEND INFO
        # ============================================================
        lb = leaderboard or get_leaderboard_snapshot() or []
        if not isinstance(lb, list):
            lb = []

        weekend_info = (
            track_info
            or getattr(race_data_store_module or race_data_store, "current_weekend_info", {})
            or {}
        )

        def safe(val, default="Unknown"):
            return default if val in (None, "", "None") else val

        track_name = safe(
            weekend_info.get("TrackDisplayName")
            or weekend_info.get("track_display_name")
            or "this circuit"
        )
        city = safe(
            weekend_info.get("TrackCity")
            or weekend_info.get("track_city")
            or ""
        )
        country = safe(
            weekend_info.get("TrackCountry")
            or weekend_info.get("track_country")
            or ""
        )

        # ============================================================
        # LOAD FDOTD JSON (AUTHORITATIVE SOURCE)
        # ============================================================
        fd = get_focus_driver_record()
        if not fd:
            print("[DEBUG FDOTD] No focus_driver_of_the_day.json found.")
            return add_host_wrapup(
                "The Focus Driver of the Day hasn't been selected yet.",
                host
            )

        # Clean raw JSON fields
        fd_name_raw = fd.get("Name", "")
        fd_name_clean = clean_driver_name(fd_name_raw).strip()
        fd_name_clean_l = fd_name_clean.lower()

        fd_idx_raw = str(fd.get("CarIdx", "")).strip()
        fd_num_raw = str(fd.get("CarNumber", "")).strip()

        # ============================================================
        # MATCH AGAINST LEADERBOARD (ROBUST BUT NON-AGGRESSIVE)
        # ============================================================
        focus_driver = None

        for c in lb:
            if not isinstance(c, dict):
                continue

            c_idx = str(c.get("CarIdx", "")).strip()
            c_num = str(c.get("CarNumber", "")).strip()
            c_name_clean = clean_driver_name(c.get("Name") or c.get("Driver") or "").lower().strip()

            if (
                (fd_idx_raw and c_idx == fd_idx_raw)
                or (fd_num_raw and c_num == fd_num_raw)
                or (fd_name_clean_l and c_name_clean == fd_name_clean_l)
            ):
                focus_driver = c
                break

        # IMPORTANT: the FDOTD JSON is a stale snapshot taken when the
        # Focus Driver was picked. Its LivePos/ΔPos/GapToLeader/Last/Lap
        # values are frozen from that moment — DO NOT use them as live
        # telemetry. If the live leaderboard has no match, report unknown
        # rather than invent "down 20 from grid" from stale snapshot data.
        live_matched = focus_driver is not None
        if not focus_driver:
            print("[DEBUG FDOTD] No leaderboard match; only driver identity is known.")
            focus_driver = {}

        # NAME — safe to pull from JSON (identity only)
        name = clean_driver_name(
            focus_driver.get("Name")
            or focus_driver.get("Driver")
            or fd_name_raw
            or "the Focus Driver"
        )

        def _live_num(key, *aliases):
            """Return a numeric live value only if the car was matched in
            the leaderboard. Never fall back to the stale FDOTD JSON."""
            if not live_matched:
                return None
            for k in (key,) + aliases:
                v = focus_driver.get(k)
                if v is None or v == "" or v == "None":
                    continue
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
            return None

        # POSITION — live only
        pos_num = _live_num("LivePos", "Position", "Pos")
        pos = int(pos_num) if pos_num and pos_num > 0 else "?"

        # DELTA POS — live only; 0 is a valid answer (held position)
        delta = 0
        if live_matched:
            for k in ("ΔPos", "DeltaPos"):
                if k in focus_driver and focus_driver[k] is not None:
                    try:
                        delta = int(focus_driver[k])
                        break
                    except (TypeError, ValueError):
                        pass

        # LAP NUMBER — live only
        lap_num = _live_num("Lap")
        lap = int(lap_num) if lap_num is not None else 0

        # LAST LAP TIME — live only, positive only
        last_raw = _live_num("Last", "LastLap")
        last_lap_str = None
        if last_raw and last_raw > 0:
            try:
                last_lap_str = voice_format_lap_time(last_raw)
            except Exception:
                last_lap_str = None

        # GAP — live only, positive only
        gap_val = _live_num("GapAhead", "GapToLeader")
        gap_phrase = None
        if gap_val is not None and gap_val > 0:
            try:
                gap_phrase = voice_format_gap(gap_val)
            except Exception:
                gap_phrase = None

        # PIT STATUS — live only
        on_pit = bool(focus_driver.get("OnPitRoad")) if live_matched else False

        # ============================================================
        # LOAD DOTD TOP-3 AND FD PERCENT
        # ============================================================
        dotd_path = os.path.join(EXPORT_DIR, "dotd_top3.json")
        dotd_data = {}
        if os.path.exists(dotd_path):
            try:
                with open(dotd_path, "r", encoding="utf-8") as f:
                    dotd_data = json.load(f)
            except Exception as e:
                print(f"[DEBUG FDOTD] DOTD file error: {e}")

        raw_top3 = dotd_data.get("top3", []) or []

        def pct(d):
            try:
                return int(round(float(d.get("percent", 0))))
            except:
                return 0

        # FDOTD percent from the selection JSON is only a snapshot from
        # when the Focus Driver was picked. Prefer the live DOTD scoring
        # table below whenever it is available.
        try:
            focus_percent = int(round(float(fd.get("DOTDPercent", 0))))
        except:
            focus_percent = 0

        # Live DOTD rank — read straight from the in-process scoring
        # table so we know where the Focus Driver sits even when they
        # aren't in the broadcast top 3. focus_percent above already
        # comes from the FDOTD JSON; this fills in the missing "X of N"
        # context the user has asked for in every Focus Driver mention.
        focus_dotd_rank = None
        focus_dotd_total = None
        focus_dotd_leader = None
        live_focus_percent = None
        try:
            from sector_says_dotd import dotd as _dotd_mgr
            fd_car_idx_int = None
            try:
                fd_car_idx_int = int(focus_driver.get("CarIdx")) if focus_driver.get("CarIdx") not in (None, "") else None
            except Exception:
                fd_car_idx_int = None
            scores = getattr(_dotd_mgr, "scores", None) or {}
            if fd_car_idx_int is not None and scores:
                rows = []
                total = 0.0
                for did, rs in scores.items():
                    try:
                        pts = max(float(rs.get_score()), 0.0)
                    except Exception:
                        pts = 0.0
                    total += pts
                    rows.append((did, pts))
                if total > 0 and rows:
                    rows.sort(key=lambda x: x[1], reverse=True)
                    for i, (did, _p) in enumerate(rows, start=1):
                        if did == fd_car_idx_int:
                            focus_dotd_rank = i
                            live_focus_percent = int(round((_p / total) * 100.0))
                            break
                    focus_dotd_total = len(rows)
                    leader_did, _lp = rows[0]
                    focus_dotd_leader = (getattr(_dotd_mgr, "driver_names", {}) or {}).get(leader_did, "")
        except Exception as _e:
            print(f"[FDOTD] DOTD rank lookup failed: {_e}")
        if live_focus_percent is not None:
            focus_percent = live_focus_percent

        # FILTER FD OUT OF TOP 3
        name_l = name.lower()
        top3_clean = [
            d for d in raw_top3
            if clean_driver_name(d.get("name", d.get("Driver", ""))).lower() != name_l
        ]
        top3_clean = sorted(top3_clean, key=pct, reverse=True)[:3]

        formatted_top3 = (
            ", ".join(f"{d.get('name', 'Unknown')} ({pct(d)}%)" for d in top3_clean)
            or "no Driver of the Day data"
        )

        # ============================================================
        # BUILD SNIPPETS — ALWAYS VALID OUTPUT
        # ============================================================
        if delta > 0:
            delta_snippet = f"gaining {delta} places from the start"
        elif delta < 0:
            delta_snippet = f"dropping {abs(delta)} spots from the start"
        else:
            delta_snippet = "holding station relative to the grid"

        has_completed_lap = (lap > 0) or bool(last_lap_str)
        if lap > 0 and last_lap_str:
            lap_snippet = f"They're working lap {lap}, with a most recent lap of {last_lap_str}."
        elif lap > 0:
            lap_snippet = f"They're currently on lap {lap}."
        elif last_lap_str:
            lap_snippet = f"Their latest time recorded is {last_lap_str}."
        elif on_pit:
            lap_snippet = "They have no completed laps yet and are currently on pit road."
        else:
            lap_snippet = "No timed lap recorded yet — they are out on track working up to a time."

        gap_snippet = f" They sit with a gap of {gap_phrase} to nearby cars." if gap_phrase else ""

        # Skill blurb — gives the AI an honest read on how experienced and
        # clean the focus driver is, so "P3 with gains" can be coloured as
        # "elite, as expected" or "novice punching above their weight".
        skill_blurb = driver_skill_blurb(focus_driver)
        skill_snippet = f" Skill profile: {skill_blurb}." if skill_blurb else ""

        # Build the FD standing phrase with rank when available so the AI
        # has the full picture (rank, percent, race position) — the user
        # asked for explicit DOTD position in every Focus Driver mention.
        if focus_dotd_rank and focus_dotd_total:
            if focus_dotd_rank == 1:
                fd_dotd_standing = (
                    f"leading the Driver of the Day vote at {focus_percent} percent "
                    f"(P1 of {focus_dotd_total})"
                )
            else:
                trail_bit = (
                    f", trailing {focus_dotd_leader}"
                    if focus_dotd_leader and focus_dotd_leader.lower() != name.lower()
                    else ""
                )
                fd_dotd_standing = (
                    f"P{focus_dotd_rank} of {focus_dotd_total} in Driver of the Day "
                    f"voting at {focus_percent} percent{trail_bit}"
                )
        else:
            fd_dotd_standing = f"{focus_percent} percent of the Driver of the Day vote"

        formatted_dotd = (
            f"Top contenders: {formatted_top3}. "
            f"Focus Driver: {name} running P{pos} on track, {fd_dotd_standing}.{skill_snippet} "
            f"REQUIRED: state the DOTD standing aloud — both the rank and the vote share."
        )

        # ============================================================
        # FINAL PROMPT
        # ============================================================
        where_snippet = (
            f"here at {track_name}" if track_name != "this circuit" else "out on track"
        )

        location_snippet = (
            f"{name} running P{pos} {where_snippet}, "
            f"with {focus_percent}% of the Driver of the Day voting."
        )

        body_core = (
            f"{formatted_dotd} They are {delta_snippet}. "
            f"{lap_snippet}{gap_snippet}"
        )

        skill_hint = (
            " Use the skill profile (iRating tier + license class/SR) to "
            "colour the update — an elite/clean driver climbing is expected "
            "pace, a novice/risky one climbing is a story; the reverse for "
            "drops."
        ) if skill_blurb else ""

        # HARD anti-fabrication rules — the AI keeps inventing things
        # like "charging from the back to P1" for drivers who are stuck
        # in the pits. Be explicit.
        if on_pit:
            pit_status_rule = f" {name} IS currently on pit road — you may mention that."
        else:
            pit_status_rule = (
                f" {name} is OUT ON TRACK — DO NOT say they are in the "
                "pits, in pit lane, or on pit road."
            )
        ground_rules = (
            " STRICT RULES: Use ONLY the facts above. Do NOT invent "
            "positions, overtakes, gains, momentum, or progress that "
            "isn't in the data. Do NOT say the driver is charging, "
            "climbing, or contending for the lead unless the data "
            "explicitly says so. Do NOT call the Driver of the Day score "
            "strong, top-tier, or impressive unless the stated vote share "
            "is above zero percent."
            + pit_status_rule
        )
        if not has_completed_lap and on_pit:
            pit_lane_rules = (
                f" CRITICAL: {name} has NOT completed any lap and is "
                "currently on pit road. DO NOT say they are driving, "
                "racing, charging, gaining, leading, or making progress. "
                "Report only that they are in the pits without a timed "
                "lap. Keep it short and factual."
            )
            lines = [
                (
                    f"Focus Driver: {name}, currently P{pos}, "
                    f"on pit road with zero completed laps. "
                    f"{formatted_dotd}{pit_lane_rules}"
                )
            ]
        elif not has_completed_lap:
            no_lap_rules = (
                f" CRITICAL: {name} has no timed lap yet but is OUT ON "
                "TRACK (not in the pits). DO NOT say they are in the "
                "pits, in pit lane, or on pit road. DO NOT invent "
                "overtakes or pace claims. Keep it short and factual."
            )
            lines = [
                (
                    f"Focus Driver: {name}, currently P{pos}, out on "
                    f"track still working up to a timed lap. "
                    f"{formatted_dotd}{no_lap_rules}"
                )
            ]
        else:
            lines = [
                (
                    f"{location_snippet} {body_core} "
                    f"Provide a broadcast-ready update on their current situation "
                    f"for viewers watching in {city}, {country}.{skill_hint}"
                    f"{ground_rules}"
                ),
                (
                    f"{name} holds P{pos} with {focus_percent}% of the vote. "
                    f"{formatted_dotd} {lap_snippet} {gap_snippet} "
                    f"Deliver a punchy commentary hit summarizing their progress.{skill_hint}"
                    f"{ground_rules}"
                ),
            ]

        final_line = random.choice(lines)
        _last_focus_driver_used = name

        return add_host_wrapup(final_line, host)

    except Exception as e:
        print(f"[ERROR focus_driver_of_the_day] {e}")
        return add_host_wrapup(
            "Focus Driver update coming once telemetry stabilizes.",
            host
        )

# =================================================================
# Pre-Race Show (qualifying → green flag window)
# =================================================================
#
# Fires ONCE per race, after the grid is set and before the green
# flag. Two back-to-back segments:
#   1. pre_race_pick  — Nigel's data-driven podium bet (the "gambling
#                        opportunity" framing — imagine you're taking
#                        bets on this race, where's your money?)
#   2. pre_race_focus — Clara's focus driver preview with explicit
#                        realistic finish range + DOTD path
#
# Both reuse the same helpers the live prediction uses
# (_classify_race_length, _grid_advantage_tag, _predict_finish_range,
# _get_track_history_pattern, driver_skill_blurb) so the analytical
# framework stays consistent across live and pre-race calls.

def _format_grid_entry(e):
    """Short tag for a grid-entry dict built by race_triggers."""
    name = e.get("name") or "?"
    grid = e.get("grid") or "?"
    ir = e.get("irating")
    lic = e.get("lic_class") or ""
    sr = e.get("lic_safety")
    bits = [f"P{grid}"]
    if isinstance(ir, (int, float)) and ir > 0:
        bits.append(sector_says_utilities.voice_format_irating(ir))
    if lic and isinstance(sr, (int, float)):
        bits.append(f"{lic} {sr:.2f}")
    tier = _irating_tier(ir) if ir else None
    if tier:
        bits.append(tier)
    return f"{name} ({', '.join(bits)})"


def pre_race_pick_phrases(grid=None, weekend_info=None, race_class=None,
                          total_laps=0, track_history="", host="Nigel"):
    """Nigel's pre-race podium pick — 'where's your money?' framing.

    grid is a list of dicts shaped by race_triggers._build_grid_preview:
      [{name, grid, car_idx, irating, lic_class, lic_safety, ir_rank}, ...]
    sorted by grid position ascending. Fallback safely to a silence
    line if grid data is missing or thin.
    """
    try:
        if not grid or not isinstance(grid, list) or len(grid) < 3:
            return add_host_wrapup(
                "No predictions for this race.",
                host,
            )

        weekend_info = weekend_info or {}
        track_name = (
            weekend_info.get("TrackDisplayName")
            or weekend_info.get("track_display_name")
            or weekend_info.get("track_name")
            or "the circuit"
        )

        # Top 5 on the grid
        grid_sorted = sorted(grid, key=lambda e: int(e.get("grid") or 999))
        top5 = grid_sorted[:5]
        top5_text = "; ".join(_format_grid_entry(e) for e in top5)

        # iRating ranking for the full field (1 = highest)
        rated = [e for e in grid_sorted if isinstance(e.get("irating"), (int, float)) and e["irating"] > 0]
        ir_sorted = sorted(rated, key=lambda e: -e["irating"])
        ir_top3_names = ", ".join(e["name"] for e in ir_sorted[:3])

        # Grid-advantage tags for the top 5
        rc = race_class or _classify_race_length(total_laps)
        tag_bits = []
        for e in top5:
            car = {"Pos": int(e.get("grid") or 999)}
            tag = _grid_advantage_tag(car, rc)
            if tag:
                tag_bits.append(f"{e['name']}: {tag}")
        tag_text = "; ".join(tag_bits) if tag_bits else ""

        # Race class label
        class_label = {
            "sprint": f"{total_laps}-lap sprint" if total_laps else "sprint race",
            "medium": f"{total_laps}-lap race" if total_laps else "medium-distance race",
            "long":   f"{total_laps}-lap long-distance race" if total_laps else "long race",
            "unknown": "race",
        }.get(rc, "race")

        # Class rule
        class_rule = {
            "sprint": (
                "SPRINT RULE: grid position dominates. A clean front-row "
                "starter almost always converts. Do NOT predict a low-SR "
                "front-runner will crash — that's a long-race heuristic. "
                "Comeback drives from deep are capped around 6 places."
            ),
            "medium": (
                "MEDIUM RULE: grid and pace both matter. Comeback drives "
                "of up to 10 places are realistic if there's attrition."
            ),
            "long": (
                "LONG RULE: pace and SR cleanliness matter most, grid "
                "matters least. Low-SR drivers at the front ARE a real "
                "attrition risk over this distance."
            ),
        }.get(rc, "")

        history_line = f" {track_history}" if track_history else ""

        prompt = (
            f"Nigel's pre-race podium bet at {track_name} — this is a "
            f"{class_label}, grid locked in, lights out moments away. "
            f"IMAGINE YOU'RE TAKING BETS on today's race: where is your "
            f"money going, and why? "
            f"GRID (front five, sorted by start position): {top5_text}. "
            f"Field's top three by iRating: {ir_top3_names}. "
            + (f"GRID TAGS: {tag_text}. " if tag_text else "")
            + f"{history_line} "
            f"{class_rule} "
            f"Call your top three bets for the podium with a one-line "
            f"reason grounded in the data above. Sharp, confident, "
            f"specific — a punter's tip, not a disclaimer. Name ONE "
            f"dark horse to watch. Do NOT mention tyres, strategy, "
            f"undercut, fuel, or anything you cannot see in the data. "
            f"A pit stop is not a strategic choice. Under 220 characters."
        )
        return add_host_wrapup(prompt, host)
    except Exception as e:
        print(f"[ERROR pre_race_pick] {e}")
        return add_host_wrapup("No predictions for this race.", host)


def pre_race_focus_phrases(grid=None, weekend_info=None, focus_driver=None,
                           race_class=None, total_laps=0, host="Clara"):
    """Clara's focus driver preview — realistic finish range + DOTD path."""
    try:
        if not focus_driver or not isinstance(focus_driver, dict):
            return add_host_wrapup("No predictions for this race.", host)
        if not grid or not isinstance(grid, list) or len(grid) < 3:
            return add_host_wrapup("No predictions for this race.", host)

        weekend_info = weekend_info or {}
        track_name = (
            weekend_info.get("TrackDisplayName")
            or weekend_info.get("track_display_name")
            or weekend_info.get("track_name")
            or "the circuit"
        )

        name = focus_driver.get("name") or "our focus driver"
        grid_pos = int(focus_driver.get("grid") or 99)
        ir = focus_driver.get("irating") or 0
        lic = focus_driver.get("lic_class") or ""
        sr = focus_driver.get("lic_safety")
        ir_rank = focus_driver.get("ir_rank")

        # Skill tier
        tier = _irating_tier(ir) or "mid-pack"
        sr_text = ""
        if lic and isinstance(sr, (int, float)):
            sr_text = f" {lic} {sr:.2f}"

        # Realistic finish range
        rc = race_class or _classify_race_length(total_laps)
        fake_car = {"Pos": grid_pos, "IRRank": ir_rank}
        rng = _predict_finish_range(fake_car, rc, len(grid))
        if rng:
            lo, hi = rng
            range_text = f"P{lo} to P{hi}"
        else:
            range_text = f"around P{grid_pos}"

        # How many low-SR drivers ahead (opportunity to inherit places)
        lowsr_ahead = sum(
            1 for e in grid
            if isinstance(e.get("grid"), int)
            and e["grid"] < grid_pos
            and isinstance(e.get("lic_safety"), (int, float))
            and e["lic_safety"] < 2.0
        )
        # How many novice/lower-tier drivers behind (easy pickups)
        novice_behind = sum(
            1 for e in grid
            if isinstance(e.get("grid"), int)
            and e["grid"] > grid_pos
            and isinstance(e.get("irating"), (int, float))
            and e["irating"] < (ir or 99999) - 400
        )

        # DOTD path hints
        gap_text = ""
        if grid_pos >= 15 and rc == "sprint":
            gap_text = (
                "Starting deep — the comeback-drive path is open. Gaining "
                "five places unlocks the +20 DOTD milestone, ten unlocks "
                "the +35 bonus. "
            )
        elif grid_pos >= 10:
            gap_text = (
                "Mid-pack start — clean laps and the places-gained-5 "
                "milestone are the path to a real DOTD score. "
            )

        # Key metric hint
        incident_hint = (
            "Key metric: keep incident count under five — that's where "
            "the new tiered penalty starts biting hard."
        )

        # Skill blurb for narrative
        skill_blurb = ""
        if ir and lic and isinstance(sr, (int, float)):
            skill_blurb = driver_skill_blurb({
                "IRating": ir, "LicClass": lic, "LicSafety": sr
            })

        prompt = (
            f"Clara's focus driver preview at {track_name}. "
            f"Focus Driver: {name}, starting P{grid_pos}, "
            f"{sector_says_utilities.voice_format_irating(ir)}{sr_text} ({tier}). "
            + (f"Skill read: {skill_blurb}. " if skill_blurb else "")
            + f"REALISTIC FINISH RANGE for this race class: {range_text}. "
            f"Drivers with SR under 2.0 starting ahead: {lowsr_ahead} "
            f"(potential inherited places). Slower rated drivers starting "
            f"behind: {novice_behind} (easy pickups). "
            f"{gap_text}{incident_hint} "
            f"Deliver a warm, narrative-style focus driver preview grounded "
            f"in these numbers. Name the realistic finish range explicitly. "
            f"Name the key DOTD hook explicitly. Do NOT invent tyre talk, "
            f"strategy, emotion not shown in data, or anything you cannot "
            f"see above. Under 220 characters."
        )
        return add_host_wrapup(prompt, host)
    except Exception as e:
        print(f"[ERROR pre_race_focus] {e}")
        return add_host_wrapup("No predictions for this race.", host)


# -------------------------------
# Export mapping
# -------------------------------
phrases = {
    "fun_fact": fun_fact_phrases,
    "race_prediction": race_prediction_phrases,
    "historical": historical_phrases,
    "trivia_time": trivia_time_phrases,
    "speed_fun_fact": speed_fun_fact_phrases,
    "focus_driver_of_the_day": focus_driver_of_the_day_phrases,
    "pre_race_pick": pre_race_pick_phrases,
    "pre_race_focus": pre_race_focus_phrases,
}
