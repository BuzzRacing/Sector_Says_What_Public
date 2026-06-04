"""iracing_api_adapter.py — wrapper around the iRacing /data API.

Wraps the third-party `iracingdataapi.client.irDataClient` so the rest
of the engine sees a small, stable surface and never has to know about
auth, S3 redirects, or the underlying library version.

Design rules
------------
- **Disabled by default.** If `power_unit.iracing_api.enabled` is False
  or no creds are configured, every public method silently returns
  ``None`` / ``{}``. Callers don't need to special-case this.
- **CAPTCHA-safe.** A single bad-auth attempt sets a process-wide
  sentinel and we refuse to retry. iRacing CAPTCHA-locks accounts that
  get hammered with bad credentials, and a hot-reload loop could trip
  it within seconds.
- **No exceptions cross the boundary.** Anything the upstream library
  raises is logged once and swallowed.
- **Lazy login.** The underlying client is built on first real call,
  not at import time. Cheap to import even when disabled.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Optional

import race_config


# ─── Static asset base URL ────────────────────────────────────────
# /data/track/assets and /data/car/assets return relative paths like
# "img/tracks/123/large.png". Per the schema docs they live under this
# CDN host. Public images, no auth needed once you have the relative
# path, so we can hand the full URL straight to the browser.
IRACING_IMAGE_BASE = "https://images-static.iracing.com/"


def iracing_image_url(rel: str | None) -> str | None:
    """Resolve a relative iRacing asset path to a fully-qualified URL.

    Returns the input untouched if it's already absolute (starts with
    http) and ``None`` for falsy input. Useful for UI code that wants a
    URL it can drop into an <img src> without caring whether the asset
    came back relative or absolute.
    """
    if not rel:
        return None
    if rel.startswith("http://") or rel.startswith("https://"):
        return rel
    return IRACING_IMAGE_BASE + rel.lstrip("/")


# ─── Module state ─────────────────────────────────────────────────

_client: Any = None              # irDataClient instance, built on demand
_client_lock = threading.Lock()  # Serialise lazy construction

# Single-shot lockout. Once True, every call short-circuits to None/{}
# until the process restarts. Protects the iRacing account from being
# CAPTCHA-banned by repeated bad-auth attempts.
_auth_failed_this_process: bool = False

# Constants are stable forever — fetch once, keep in memory.
_constants_cache: dict[str, Any] = {}


# ─── Internal helpers ─────────────────────────────────────────────

def _is_enabled() -> bool:
    """True if the user has explicitly turned the integration on."""
    try:
        return bool(race_config.cfg("power_unit").get("iracing_api", {}).get("enabled"))
    except Exception:
        return False


def _get_creds() -> tuple[Optional[str], Optional[str]]:
    """Pull (email, password) from race_secrets.json or env."""
    try:
        ir = race_config.secrets("iracing") or {}
        email = ir.get("email") or None
        password = ir.get("password") or None
        return email, password
    except Exception:
        return None, None


def _get_client() -> Any:
    """Return a logged-in irDataClient, or None if unavailable.

    Builds the client on first call and caches it for the lifetime of
    the process. After any failure the sentinel blocks all retries.
    """
    global _client, _auth_failed_this_process

    if _auth_failed_this_process:
        return None
    if not _is_enabled():
        return None
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client
        if _auth_failed_this_process:
            return None

        email, password = _get_creds()
        if not email or not password:
            print("[IRAPI] disabled — no credentials in race_secrets.json or env")
            _auth_failed_this_process = True
            return None

        try:
            from iracingdataapi.client import irDataClient
            _client = irDataClient(username=email, password=password, silent=True)
            print("[IRAPI] client constructed (login is lazy on first call)")
            return _client
        except Exception as e:
            _auth_failed_this_process = True
            print(f"[IRAPI] failed to construct client: {e!r} — disabling for this process")
            return None


def _safe_call(method_name: str, *args, default=None, **kwargs):
    """Invoke a client method, swallow any exception, log once on auth fail."""
    global _auth_failed_this_process
    client = _get_client()
    if client is None:
        return default
    method = getattr(client, method_name, None)
    if not callable(method):
        print(f"[IRAPI] {method_name}: not available on installed iracingdataapi version")
        return default
    try:
        return method(*args, **kwargs)
    except Exception as e:
        msg = str(e).lower()
        # Auth-class errors trip the lockout sentinel; everything else
        # is treated as transient and just returns the default.
        if any(tok in msg for tok in ("auth", "401", "403", "captcha", "credential")):
            _auth_failed_this_process = True
            print(f"[IRAPI] auth failure on {method_name}: {e!r} — disabling for this process")
        else:
            print(f"[IRAPI] {method_name} failed: {e!r}")
        return default


# ─── Public API: status ───────────────────────────────────────────

def is_available() -> bool:
    """True if the adapter is enabled, configured, and not locked out."""
    return _is_enabled() and not _auth_failed_this_process and bool(_get_creds()[0])


def reset_auth_lockout() -> None:
    """Manually clear the lockout sentinel.

    Use only after the operator has verified the credentials in
    race_secrets.json. Safe to call when not locked out.
    """
    global _auth_failed_this_process, _client
    _auth_failed_this_process = False
    _client = None
    print("[IRAPI] auth lockout cleared")


def configure(*_args, **_kwargs) -> None:
    """Legacy stub kept for backwards compatibility.

    Earlier code called ``configure(api_key=..., base_url=...)``. The
    real client now reads creds from race_secrets / env, so this is a
    no-op.
    """
    return None


# ─── Public API: drivers ──────────────────────────────────────────

def get_member_info(cust_ids: Iterable[int]) -> dict[str, Any]:
    """Batch member lookup. ``cust_ids`` may be one id or a list.

    Returns the raw payload (with a ``members`` array) or {} on failure.
    """
    if not cust_ids:
        return {}
    if isinstance(cust_ids, int):
        ids_arg: int | str = cust_ids
    else:
        ids_arg = ",".join(str(int(c)) for c in cust_ids if c)
        if not ids_arg:
            return {}
    return _safe_call("member", ids_arg, default={}) or {}


def get_member_profile(cust_id: int) -> dict[str, Any]:
    """Full /data/member/profile blob (license history, recent events, image_url)."""
    if not cust_id:
        return {}
    return _safe_call("member_profile", cust_id=int(cust_id), default={}) or {}


def get_member_career(cust_id: int) -> dict[str, Any]:
    """/data/stats/member_career — per-category aggregates (wins, top5, poles…)."""
    if not cust_id:
        return {}
    return _safe_call("stats_member_career", cust_id=int(cust_id), default={}) or {}


def get_member_recent_races(cust_id: int) -> dict[str, Any]:
    """/data/stats/member_recent_races — last 10 races with iR delta and finish."""
    if not cust_id:
        return {}
    return _safe_call("stats_member_recent_races", cust_id=int(cust_id), default={}) or {}


def get_member_bests(cust_id: int, car_id: int | None = None) -> dict[str, Any]:
    """/data/stats/member_bests — best laps at tracks (optionally per car)."""
    if not cust_id:
        return {}
    return _safe_call("stats_member_bests", cust_id=int(cust_id), car_id=car_id, default={}) or {}


def lookup_driver(name: str) -> list[dict[str, Any]]:
    """Resolve a display name → cust_id list. Useful for legacy race rows."""
    if not name:
        return []
    return _safe_call("lookup_drivers", search_term=name, default=[]) or []


# ─── Public API: assets (cars / tracks) ───────────────────────────

def get_car_assets() -> dict[str, Any]:
    """/data/car/assets — keyed by car_id (string). Big payload, fetch sparingly."""
    return _safe_call("get_cars_assets", default={}) or {}


def get_track_assets() -> dict[str, Any]:
    """/data/track/assets — keyed by track_id (string). Big payload, fetch sparingly."""
    return _safe_call("get_tracks_assets", default={}) or {}


def get_cars() -> list[dict[str, Any]]:
    """/data/car/get — full car list with names, ids, classes."""
    return _safe_call("get_cars", default=[]) or []


def get_tracks() -> list[dict[str, Any]]:
    """/data/track/get — full track list with names, ids, configs."""
    return _safe_call("get_tracks", default=[]) or []


# ─── Public API: world records & series ──────────────────────────

def get_world_records(car_id: int, track_id: int) -> dict[str, Any]:
    """Fastest recorded lap for a (car, track) pair."""
    if not car_id or not track_id:
        return {}
    return _safe_call(
        "stats_world_records",
        car_id=int(car_id),
        track_id=int(track_id),
        default={},
    ) or {}


def get_series() -> list[dict[str, Any]]:
    return _safe_call("get_series", default=[]) or []


# ─── Public API: session-scoped lookups ──────────────────────────

def get_strength_of_field(subsession_id: Optional[int]) -> Optional[int]:
    """Return SoF for a subsession, parsed from /data/results/get."""
    if not subsession_id:
        return None
    res = _safe_call("result", subsession_id=int(subsession_id), default=None)
    if not isinstance(res, dict):
        return None
    sof = res.get("event_strength_of_field")
    return int(sof) if isinstance(sof, (int, float)) else None


def get_session_result(subsession_id: Optional[int]) -> dict[str, Any]:
    """Full /data/results/get payload for a subsession."""
    if not subsession_id:
        return {}
    return _safe_call("result", subsession_id=int(subsession_id), default={}) or {}


def get_lap_chart_data(subsession_id: Optional[int], simsession_number: int = 0) -> list[dict[str, Any]]:
    """Return per-lap crossings for every driver in a subsession.

    Each row has cust_id, lap_number, session_time (10000ths s), lap_time
    (10000ths s), lap_position, interval (ms to leader), flags, etc. Source
    of truth for replay timing — matches the iRacing website's lap chart.
    Empty list on any failure.
    """
    if not subsession_id:
        return []
    raw = _safe_call(
        "result_lap_chart_data",
        subsession_id=int(subsession_id),
        simsession_number=int(simsession_number),
        default=[],
    )
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        if isinstance(row, dict):
            out.append(row)
        elif hasattr(row, "model_dump"):
            out.append(row.model_dump())
        else:
            out.append(dict(row))
    return out


# ─── Public API: constants (cached forever) ──────────────────────

def get_constants() -> dict[str, Any]:
    """Categories / divisions / event types — fetched once per process."""
    if _constants_cache:
        return _constants_cache
    cats = _safe_call("constants_categories", default=None)
    divs = _safe_call("constants_divisions", default=None)
    evts = _safe_call("constants_event_types", default=None)
    if cats is not None or divs is not None or evts is not None:
        _constants_cache.update({
            "categories": cats or [],
            "divisions": divs or [],
            "event_types": evts or [],
        })
    return _constants_cache


# ─── Backwards-compatible stub aliases ───────────────────────────
# Older callers used these names. They now route to real methods or
# return empty data when called against a cust_id we don't have.

def get_driver_profile(cust_id: Optional[int]) -> dict[str, Any]:
    """Alias for get_member_profile (kept for legacy callers)."""
    if not cust_id:
        return {}
    return get_member_profile(int(cust_id))


def get_extended_grid(subsession_id: Optional[int]) -> list[dict[str, Any]]:
    """Return the session_results array from /data/results/get, or []."""
    payload = get_session_result(subsession_id)
    if not isinstance(payload, dict):
        return []
    sessions = payload.get("session_results") or []
    return sessions if isinstance(sessions, list) else []
