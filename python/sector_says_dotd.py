# sector_says_dotd.py
from __future__ import annotations
import logging
import threading
import csv
import io
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable
from sector_says_utilities import has_flag

# ----------------------------- Debug Switch -----------------------------
DEBUG = False  # Set True to enable DOTD debug messages

# ----------------------------- Logger setup -----------------------------
logger = logging.getLogger("sector_says_dotd")
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][DOTD] %(message)s"))
    logger.addHandler(ch)
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)

# ----------------------------- Data models -----------------------------
class ScoreEvent:
    def __init__(self, trigger: str, points: float, ts: Optional[datetime] = None, lap: Optional[int] = None, desc: Optional[str] = None, meta: Optional[Dict[str,Any]] = None):
        self.trigger = trigger
        self.points = float(points)
        self.ts = ts or datetime.now(timezone.utc)
        self.lap = lap
        self.desc = desc or ""
        self.meta = meta or {}

    def as_dict(self):
        return {
            "trigger": self.trigger,
            "points": self.points,
            "ts": self.ts.isoformat(),
            "lap": self.lap,
            "desc": self.desc,
            "meta": self.meta,
        }

class RaceScore:
    def __init__(self, driver_id: Any):
        self.driver_id = driver_id
        self.score = 0.0
        self.events: List[ScoreEvent] = []
        self.counters: Dict[str, int] = {}
        self.last_event_time: Dict[str, datetime] = {}
        self.lock = threading.RLock()

    def add_event(self, event: ScoreEvent):
        with self.lock:
            self.events.append(event)
            self.score += event.points
            if DEBUG:
                logger.debug(f"Driver {self.driver_id} -> event {event.trigger} {event.points:+.1f} -> new score {self.score:.1f}")

    def get_score(self) -> float:
        with self.lock:
            return float(self.score)

    def get_events(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [e.as_dict() for e in self.events]

# ----------------------------- DOTD Manager -----------------------------
class DOTDManager:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._lock = threading.RLock()
        self.scores: Dict[Any, RaceScore] = {}
        self.subscribers: List[Callable[[Any, RaceScore], None]] = []
        self.config = config or self._default_config()
        self._recent_events: Dict[tuple, datetime] = {}
        if DEBUG:
            logger.debug("DOTDManager initialized with config: %s", self.config)

    def _default_config(self) -> Dict[str, Any]:
        """Load DOTD config from race_config.json, falling back to built-in defaults."""
        try:
            import race_config
            dotd_cfg = race_config.cfg("dotd")
            if dotd_cfg and "triggers" in dotd_cfg:
                return dotd_cfg
        except Exception:
            pass
        # Built-in fallback (matches race_config.json defaults)
        return {
            "triggers": {
                "become_leader":    {"points": 20,  "min_interval_s": 2, "cap_per_race": 1},
                "lost_lead":        {"points": -5,  "min_interval_s": 0.5},
                "lead_lap":         {"points": 3,   "min_interval_s": 0, "per_lap": True},
                "win_race":         {"points": 40,  "min_interval_s": 0},
                "gain_position":    {"points": 12,  "min_interval_s": 0, "diminishing_window_s": 20},
                "fastest_lap":      {"points": 30,  "min_interval_s": 1, "cap_per_race": 1},
                "being_passed":     {"points": -3,  "min_interval_s": 0.5},
                "lap_clean":        {"points": 2,   "min_interval_s": 0, "per_lap": True},
                "clean_streak_5":   {"points": 10,  "min_interval_s": 0},
                "off_track":        {"points": -5,  "min_interval_s": 1},
                "incident_point":   {"points": -4,  "min_interval_s": 0},
                "places_gained_5":  {"points": 20,  "cap_per_race": 1},
                "places_gained_10": {"points": 35,  "cap_per_race": 1},
                "caused_collision": {"points": -25, "min_interval_s": 1},
                "defended_position":{"points": 6,   "min_interval_s": 10},
                "exceeded_expectation": {"points": 8,   "cap_per_race": 1},
                "underperformed":       {"points": -4,  "cap_per_race": 1},
                "race_finish":          {"points": 10,  "cap_per_race": 1},
            },
            "cap_per_lap": 300,
            "diminishing": {"enabled": True, "decay_steps": [1.0, 0.8, 0.6, 0.4]},
        }

    def subscribe(self, callback: Callable[[Any, RaceScore], None]):
        with self._lock:
            self.subscribers.append(callback)
        if DEBUG:
            logger.debug("Subscriber added: %s", callback)

    def _notify(self, driver_id: Any):
        rs = self.scores.get(driver_id)
        if not rs:
            return
        for cb in list(self.subscribers):
            try:
                cb(driver_id, rs)
            except Exception as e:
                logger.exception("Subscriber callback error: %s", e)

    def create_race_scores(self, drivers: List[Any]):
        """Ensure a RaceScore exists for every driver in the list. ADDITIVE —
        existing scores are preserved so this is safe to call every tick as
        new drivers populate iRacing's DriverInfo.Drivers (AI sessions or
        late-joiners commonly don't appear in the very first call).

        Use reset() when starting a fresh race; this method must NOT clear.
        """
        with self._lock:
            added = 0
            for d in drivers:
                if isinstance(d, dict):
                    driver_id = d.get("id")
                else:
                    driver_id = d
                if driver_id is None:
                    continue
                if driver_id not in self.scores:
                    self.scores[driver_id] = RaceScore(driver_id)
                    added += 1
                    if DEBUG:
                        logger.debug("Created RaceScore for driver %s", driver_id)
        if added and DEBUG:
            logger.info("Added %d new RaceScore(s); now tracking %d drivers",
                        added, len(self.scores))

    def reset(self):
        with self._lock:
            self.scores.clear()
            self._recent_events.clear()
        if DEBUG:
            logger.info("DOTDManager reset complete.")

    def on_trigger(self, driver_id: Any, trigger_id: str, meta: Optional[Dict[str, Any]] = None):
    # Ensure meta exists before we read from it
        if meta is None:
            meta = {}
        """Call this when a trigger occurs for a driver."""
        if driver_id not in self.scores:
            if DEBUG:
                logger.warning(
                    "on_trigger called for unknown driver %s (trigger %s). Ignoring.",
                    driver_id,
                    trigger_id
                )
            return
        cfg = self.config["triggers"].get(trigger_id)
        if not cfg:
            if DEBUG:
                logger.warning("No config for trigger '%s' - ignoring", trigger_id)
            return
        now = datetime.now(timezone.utc)
        key = (driver_id, trigger_id)
        min_interval = cfg.get("min_interval_s", 0)
        last = self._recent_events.get(key)
        if last and (now - last).total_seconds() < min_interval:
            if DEBUG:
                logger.debug("Debounced trigger %s for driver %s", trigger_id, driver_id)
            return
        # Enforce cap_per_race — e.g. become_leader / fastest_lap / place
        # milestones should fire at most N times for a given driver.
        cap = cfg.get("cap_per_race")
        if cap is not None:
            rs = self.scores[driver_id]
            fired_count = sum(1 for e in rs.events if e.trigger == trigger_id)
            if fired_count >= int(cap):
                if DEBUG:
                    logger.debug(
                        "cap_per_race hit for %s driver %s (%d/%d)",
                        trigger_id, driver_id, fired_count, cap,
                    )
                return
        points = cfg.get("points", 0)
        points = self._apply_diminishing(driver_id, trigger_id, points, meta)
        points = self._apply_modifiers(driver_id, trigger_id, points, meta)
        event = ScoreEvent(trigger=trigger_id, points=points, ts=now, lap=meta.get("lap"), desc=meta.get("desc"), meta=meta)
        self._recent_events[key] = now
        self.scores[driver_id].add_event(event)
        self._increment_counter(driver_id, trigger_id)
        self._notify(driver_id)
        # Export updated DOTD top 3 to HUD
        _export_top3(self)

    # ------------------ Helpers ------------------
    def _apply_diminishing(self, driver_id: Any, trigger_id: str, base_points: float, meta: Dict[str,Any]) -> float:
        if not self.config.get("diminishing", {}).get("enabled", False):
            return base_points
        steps = self.config["diminishing"].get("decay_steps", [1.0])
        cfg = self.config["triggers"].get(trigger_id, {})
        window_s = cfg.get("diminishing_window_s", 0)
        if window_s <= 0:
            return base_points
        now = datetime.now(timezone.utc)
        events = [e for e in self.scores[driver_id].events if e.trigger == trigger_id]
        count_recent = 0
        for e in reversed(events):
            if (now - e.ts).total_seconds() <= window_s:
                count_recent += 1
            else:
                break
        idx = min(count_recent, len(steps)-1)
        multiplier = steps[idx]
        points = base_points * multiplier
        if DEBUG:
            logger.debug("Diminishing applied for %s driver %s: multiplier=%.2f -> %.1f", trigger_id, driver_id, multiplier, points)
        return points

    def _apply_modifiers(self, driver_id: Any, trigger_id: str, points: float, meta: Dict[str,Any]) -> float:
        # Additional modifiers (e.g., streaks, severity)
        return points

    def _increment_counter(self, driver_id: Any, trigger_id: str):
        rs = self.scores[driver_id]
        if trigger_id == "overtake":
            rs.counters["overtake_streak"] = rs.counters.get("overtake_streak", 0) + 1
        else:
            if trigger_id in ("being_passed", "off_track", "caused_collision"):
                rs.counters["overtake_streak"] = 0

    # ------------------ Queries ------------------
    def get_score(self, driver_id: Any) -> float:
        rs = self.scores.get(driver_id)
        if not rs:
            return 0.0
        return rs.get_score()

    def get_score_breakdown(self, driver_id: Any) -> Dict[str, Any]:
        rs = self.scores.get(driver_id)
        if not rs:
            return {"score": 0.0, "events": []}
        return {"score": rs.get_score(), "events": rs.get_events()}

    def top_n_by_score(self, n: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            ranked = sorted(self.scores.values(), key=lambda r: r.score, reverse=True)
            return [{"driver_id": r.driver_id, "score": r.score, "events": len(r.events)} for r in ranked[:n]]

    def get_dotd(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self.scores:
                return None
            best = max(self.scores.values(), key=lambda r: r.score)
            if DEBUG:
                logger.info("DOTD candidate: %s score=%.1f", best.driver_id, best.score)
            return {"driver_id": best.driver_id, "score": best.score, "events": best.get_events()}

    def export_csv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["driver_id", "score", "events_count"])
        with self._lock:
            for r in sorted(self.scores.values(), key=lambda r: r.score, reverse=True):
                writer.writerow([r.driver_id, f"{r.score:.1f}", len(r.events)])
        return output.getvalue()

# ------------------ Singleton Instance ------------------
dotd = DOTDManager()
dotd.driver_names = {}
dotd.driver_positions = {}
dotd.driver_deltas = {}
# Store CarIdx → Name mapping here (live_leaderboard seeds it)
dotd.driver_names = {}
def _export_top3(dotd_mgr):
    """
    Export DOTD Top-3 to JSON.

    Percentages are now computed as:
        driver_points / total_positive_points (all drivers)

    We also stash that percent on each RaceScore as .percent so the HUD
    or leaderboard can re-use it if desired.
    """
    try:
        from sector_says_utilities import _EXPORTS_DIR
        import json, os

        # --- Load current leaderboard to check lap counts ---
        # Drivers with zero completed laps (stuck in pits from the start)
        # are excluded from DOTD — they can't be Driver of the Day without
        # actually completing a lap.
        lap_by_car_idx = {}
        try:
            lb_path = os.path.join(_EXPORTS_DIR, "leaderboard.json")
            if os.path.exists(lb_path):
                with open(lb_path, "r", encoding="utf-8") as f:
                    lb = json.load(f)
                for c in lb.get("cars", []) or []:
                    ci = c.get("CarIdx")
                    lap = c.get("Lap", 0) or 0
                    if ci is not None:
                        try:
                            lap_by_car_idx[int(ci)] = int(lap)
                        except Exception:
                            pass
        except Exception:
            pass

        # --- Build raw list from all DOTD scores ---
        results = []
        total_points_all = 0.0

        for driver_id, score_obj in dotd_mgr.scores.items():
            pts = max(score_obj.get_score(), 0.0)
            # Zero out drivers with no completed laps
            try:
                driver_idx = int(driver_id)
                if lap_by_car_idx.get(driver_idx, -1) == 0:
                    pts = 0.0
            except Exception:
                pass
            total_points_all += pts
            results.append({
                "driver_id": driver_id,
                "name": dotd_mgr.driver_names.get(driver_id, "Unknown"),
                "points": pts,
                "pos": dotd_mgr.driver_positions.get(driver_id, "?"),
                "delta": dotd_mgr.driver_deltas.get(driver_id, ""),
                "percent": 0.0,   # filled in below
            })

        # If no scores yet, export a placeholder
        if not results or total_points_all <= 0:
            results = [{
                "driver_id": None,
                "name": "No Data",
                "points": 0.0,
                "pos": "–",
                "delta": "",
                "percent": 0.0,
            }]
            total_points_all = 1.0  # avoid divide-by-zero

        # --- Compute true vote-share percentages over the whole field ---
        for r in results:
            pct = (max(r["points"], 0.0) / total_points_all) * 100.0
            pct = round(pct, 1)
            r["percent"] = pct

            # Persist on the RaceScore so other modules can re-use it
            rs = dotd_mgr.scores.get(r["driver_id"])
            if rs is not None:
                setattr(rs, "percent", pct)

        # --- Sort & take Top-3 by points ---
        results.sort(key=lambda r: r["points"], reverse=True)
        top3 = results[:3]

        # --- Build natural-language summary ---
        summary = ", ".join(
            f"{r['name']} ({r['percent']:.1f}%, P{r['pos']})"
            for r in top3
        )
        summary_text = "Driver of the Day leaderboard — " + summary

        # --- Write file ---
        path = os.path.join(_EXPORTS_DIR, "dotd_top3.json")
        os.makedirs(_EXPORTS_DIR, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "top3": top3,
                "summary_text": summary_text,
                "total_points_all": total_points_all,
            }, f, indent=2)

        if DEBUG:
            pass #logger.info(f"[DOTD] Exported DOTD summary to {path}")

    except Exception as e:
        print(f"[DOTD] export top3 failed: {e}")


def get_dotd_summary(dotd_path=None) -> Dict[str, Any]:
    """
    Reads dotd_top3.json and returns normalized DOTD data.

    Percentages are based on total positive DOTD points across ALL drivers,
    not just the Top-3.
    """
    import os, json
    from sector_says_utilities import _EXPORTS_DIR

    dotd_file = dotd_path or os.path.join(_EXPORTS_DIR, "dotd_top3.json")
    if not os.path.exists(dotd_file):
        return {"error": "dotd_top3.json not found"}

    with open(dotd_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    cars = data.get("top3", [])
    if not cars:
        return {"error": "No DOTD data found"}

    total_points_all = data.get("total_points_all") or 0.0
    if total_points_all <= 0:
        # Fallback: use whatever is already in the file
        return {"top3": cars}

    # Recompute percents from total_points_all to be safe
    for c in cars:
        pts = max(c.get("points", 0.0), 0.0)
        pct = (pts / total_points_all) * 100.0
        c["percent"] = round(pct, 1)

    return {"top3": cars}


def explain_dotd_voting(dotd_path=None) -> str:
    """
    Produces a DOTD leaderboard string.
    Focus Driver is not injected here — separate system handles FDOTD text.
    """
    info = get_dotd_summary(dotd_path)
    if "error" in info:
        return "Driver of the Day data unavailable."

    top3 = info.get("top3", [])
    if not top3:
        return "No Driver of the Day data yet."

    lines = []
    for c in top3:
        name = c.get("name", "Unknown")
        pct = c.get("percent", 0)
        pos = c.get("pos", "?")
        lines.append(f"{name} ({pct:.1f}%, P{pos})")

    summary = ", ".join(lines)
    return f"Driver of the Day leaderboard — {summary}."

# ============================================================
# PUBLIC API — submit_custom_points()
# For triggers whose point value is computed at call time (e.g.
# tiered incident penalty, skill-rating deltas, SR-aware clean
# multipliers). Bypasses the race_config.json cfg.points lookup.
# ============================================================
def submit_custom_points(trigger_id: str,
                         driver_id: Any,
                         points: float,
                         desc: Optional[str] = None,
                         lap: Optional[int] = None,
                         meta: Optional[Dict[str, Any]] = None):
    if meta is None:
        meta = {}
    if desc is not None:
        meta["desc"] = desc
    if lap is not None:
        meta["lap"] = lap
    try:
        if driver_id not in dotd.scores:
            return
        event = ScoreEvent(
            trigger=trigger_id,
            points=float(points),
            ts=datetime.now(timezone.utc),
            lap=meta.get("lap"),
            desc=meta.get("desc"),
            meta=meta,
        )
        dotd.scores[driver_id].add_event(event)
        dotd._notify(driver_id)
        _export_top3(dotd)
    except Exception as e:
        logger.exception(f"[DOTD] submit_custom_points failed: {e}")


# ============================================================
# PUBLIC API — submit_trigger()
# This is the function race_triggers and other modules should call.
# ============================================================
def submit_trigger(trigger_id: str,
                   driver_id: Any,
                   desc: Optional[str] = None,
                   lap: Optional[int] = None,
                   meta: Optional[Dict[str, Any]] = None):
    """
    Public wrapper so external modules (race_triggers, pit logic, etc.)
    can easily submit DOTD scoring events without importing dotd directly.
    """
    if meta is None:
        meta = {}

    # Inject desc + lap into meta if provided
    if desc is not None:
        meta["desc"] = desc
    if lap is not None:
        meta["lap"] = lap

    try:
        dotd.on_trigger(driver_id, trigger_id, meta)
    except Exception as e:
        logger.exception(f"[DOTD] submit_trigger failed ({trigger_id}, driver={driver_id}): {e}")

