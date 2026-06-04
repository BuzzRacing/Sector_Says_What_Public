"""iracing_api_warmer.py — pre-fetch /data API blobs for the current grid.

Runs once per race in a daemon thread, kicked off by sector_says_engine
as soon as the first non-empty roster lands. Pulls member info, career
stats, and recent form for every cust_id we have, drops the JSON into
the iracing_cache table, and writes back the iRacing member portrait
URL into driver_profiles.json so the HUD can prefer it over the local
fallback pool.

Design rules
------------
- **Daemon thread, never blocks the main tick.** Worst case ~10s for
  a 50-car grid (one batched member call + 2 calls per driver at 200ms
  spacing).
- **Idempotent per process.** ``start(cars)`` is a no-op if a warm is
  already in flight or has finished. Call ``reset()`` between races if
  the engine starts a new race in the same process.
- **Silent degradation.** If the adapter is disabled or auth fails, we
  log one line and exit. Callers never see an exception.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Iterable

import iracing_api_adapter
import race_db


# ─── Module state ─────────────────────────────────────────────────

_started: bool = False
_thread: threading.Thread | None = None
_state_lock = threading.Lock()

# Inter-call spacing — keeps us well under iRacing's ~240/min cap and
# leaves headroom for the rest of the engine to make its own calls.
_INTER_CALL_DELAY_S = 0.20

# Path to the driver_profiles.json the engine writes per-race. We
# read/merge in place so manually-set fields (profile_image, etc) are
# preserved.
_PROFILES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "sector_said_exports",
    "driver_profiles.json",
)


# ─── Public API ───────────────────────────────────────────────────

def start(cars: list[dict]) -> None:
    """Kick off the warmer for the given grid (no-op if already running).

    ``cars`` is the leaderboard list from live_leaderboard. Each entry
    is expected to have a ``CustId`` field (added in Phase 1). Drivers
    without one are skipped silently — usually AI cars or pace cars.
    """
    global _started, _thread
    with _state_lock:
        if _started:
            return
        _started = True

    cust_ids = _extract_cust_ids(cars)
    if not cust_ids:
        print("[IRWARM] no cust_ids on roster — nothing to fetch")
        return

    if not iracing_api_adapter.is_available():
        print(f"[IRWARM] disabled — {len(cust_ids)} drivers would have been fetched")
        return

    _thread = threading.Thread(
        target=_run,
        args=(cust_ids,),
        name="iracing-warmer",
        daemon=True,
    )
    _thread.start()
    print(f"[IRWARM] started for {len(cust_ids)} drivers")


def reset() -> None:
    """Clear the started flag so the next race can warm again."""
    global _started, _thread
    with _state_lock:
        _started = False
        _thread = None


def is_running() -> bool:
    """True if a warm is currently in flight."""
    return bool(_thread and _thread.is_alive())


# ─── Internals ────────────────────────────────────────────────────

def _extract_cust_ids(cars: Iterable[dict]) -> list[int]:
    """Pull unique, valid cust_ids out of a leaderboard list."""
    seen: set[int] = set()
    for c in cars or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("CustId")
        try:
            cid_int = int(cid) if cid not in (None, "", 0) else None
        except (TypeError, ValueError):
            cid_int = None
        if cid_int and cid_int not in seen:
            seen.add(cid_int)
    return sorted(seen)


def _run(cust_ids: list[int]) -> None:
    """Worker body. Runs in a daemon thread; never raises out."""
    t0 = time.monotonic()
    try:
        # Step 1: batched member lookup. One API call for the whole
        # grid — gives us the canonical name, country, club, and
        # most importantly the helmet image_url for each cust_id.
        members_by_id: dict[int, dict] = {}
        try:
            payload = iracing_api_adapter.get_member_info(cust_ids) or {}
            members = payload.get("members") if isinstance(payload, dict) else None
            for m in members or []:
                if not isinstance(m, dict):
                    continue
                cid = m.get("cust_id")
                if isinstance(cid, int):
                    members_by_id[cid] = m
            print(f"[IRWARM] member batch returned {len(members_by_id)} of {len(cust_ids)}")
        except Exception as e:
            print(f"[IRWARM] member batch failed: {e!r}")

        # Step 2: per-driver career + recent races, then upsert into
        # the SQLite cache. Spaced out so we don't burn through the
        # rate limit.
        for cid in cust_ids:
            member = members_by_id.get(cid)
            career = iracing_api_adapter.get_member_career(cid) or None
            time.sleep(_INTER_CALL_DELAY_S)
            recent = iracing_api_adapter.get_member_recent_races(cid) or None
            time.sleep(_INTER_CALL_DELAY_S)

            image_url = _extract_image_url(member)
            try:
                race_db.set_cached_driver(
                    cust_id=cid,
                    member=member,
                    career=career,
                    recent=recent,
                    image_url=image_url,
                )
            except Exception as e:
                print(f"[IRWARM] cache write failed for {cid}: {e!r}")

            wins = _peek_wins(career)
            print(f"[IRWARM] {cid}: career_wins={wins} image={'yes' if image_url else 'no'}")

        # Step 3: write iracing_image_url back into driver_profiles.json
        # so the HUD can prefer real iRacing portraits over the pool.
        try:
            _merge_image_urls_into_profiles()
        except Exception as e:
            print(f"[IRWARM] profile merge failed: {e!r}")

        elapsed = time.monotonic() - t0
        print(f"[IRWARM] complete — {len(cust_ids)} drivers in {elapsed:.1f}s")
    except Exception as e:
        # Daemon threads dying silently is the worst — log loudly.
        print(f"[IRWARM] worker crashed: {e!r}")


def _extract_image_url(member: dict | None) -> str | None:
    """Pull the helmet/avatar URL out of a /data/member entry, if present."""
    if not isinstance(member, dict):
        return None
    for key in ("image_url", "imageURL", "image"):
        v = member.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _peek_wins(career: dict | None) -> int | str:
    """Best-effort win count across categories — only used for log lines."""
    if not isinstance(career, dict):
        return "?"
    stats = career.get("stats")
    if not isinstance(stats, list):
        return "?"
    total = 0
    for s in stats:
        if isinstance(s, dict):
            w = s.get("wins")
            if isinstance(w, int):
                total += w
    return total


def _merge_image_urls_into_profiles() -> None:
    """Read driver_profiles.json, attach iracing_image_url per driver,
    write back atomically. Defensive about missing files and partial
    cache hits.
    """
    if not os.path.exists(_PROFILES_PATH):
        return
    with open(_PROFILES_PATH, "r", encoding="utf-8") as f:
        try:
            profiles = json.load(f) or {}
        except Exception as e:
            print(f"[IRWARM] driver_profiles.json unreadable: {e!r}")
            return
    if not isinstance(profiles, dict):
        return

    touched = 0
    for car_idx_key, prof in profiles.items():
        if not isinstance(prof, dict):
            continue
        cid = prof.get("cust_id")
        if not isinstance(cid, int) or not cid:
            continue
        cached = race_db.get_cached_driver(cid)
        if not cached:
            continue
        url = cached.get("image_url")
        if url and prof.get("iracing_image_url") != url:
            prof["iracing_image_url"] = url
            touched += 1

    if not touched:
        return

    tmp = _PROFILES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)
    os.replace(tmp, _PROFILES_PATH)
    print(f"[IRWARM] driver_profiles.json: attached image_url to {touched} driver(s)")
