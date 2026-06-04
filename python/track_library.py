"""
track_library.py — Race track centerline library for the Race Replay.

Tracks are stored as one JSON file per track in:
    python/sector_says_oversee/tracks/<slug>.json

Each track file:
    {
      "slug": "watkins-glen",
      "display_name": "Watkins Glen",
      "ir_names": ["Watkins Glen International - Boot", ...],   # iRacing track_name strings
      "viewBox": "0 0 1260 720",
      "centerline_path": "M... Z",
      "sf_offset": 0.0,
      "notes": ""
    }
"""

from __future__ import annotations
import json
import os
import re
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
TRACKS_DIR = os.path.abspath(os.path.join(_HERE, "sector_says_oversee", "tracks"))


def _ensure_dir() -> None:
    os.makedirs(TRACKS_DIR, exist_ok=True)


def slugify(text: str) -> str:
    """Make a filesystem-safe slug from a display name."""
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "track"


def _path_for(slug: str) -> str:
    safe = slugify(slug)
    return os.path.join(TRACKS_DIR, safe + ".json")


def load_all() -> list[dict]:
    """Return every track JSON in the tracks/ folder, sorted by display_name."""
    _ensure_dir()
    out: list[dict] = []
    for fn in os.listdir(TRACKS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(TRACKS_DIR, fn), "r", encoding="utf-8") as f:
                t = json.load(f)
            t.setdefault("slug", fn[:-5])
            out.append(t)
        except Exception as e:
            print(f"[track_library] failed to load {fn}: {e}")
    out.sort(key=lambda t: (t.get("display_name") or t.get("slug") or "").lower())
    return out


def get(slug: str) -> Optional[dict]:
    """Load a single track by slug."""
    p = _path_for(slug)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def find_for(track_name: str) -> Optional[dict]:
    """
    Find the library entry that matches an iRacing track_name.
    Match priority:
      1. exact match against any ir_names (case + whitespace insensitive)
      2. exact match against display_name
      3. exact match against slug
      4. prefix match (library name is a prefix of incoming, e.g. 'Road Atlanta' ⊂ 'Road Atlanta - Full Course')
    """
    if not track_name:
        return None
    target = _norm(track_name)
    all_tracks = load_all()

    for t in all_tracks:
        for alias in t.get("ir_names") or []:
            if _norm(alias) == target:
                return t

    for t in all_tracks:
        if _norm(t.get("display_name", "")) == target:
            return t

    for t in all_tracks:
        if _norm(t.get("slug", "")) == target:
            return t

    for t in all_tracks:
        for alias in (t.get("ir_names") or []) + [t.get("display_name", "")]:
            n = _norm(alias)
            if n and (target.startswith(n) or n.startswith(target)):
                return t

    return None


def save(data: dict) -> dict:
    """Persist a track. Auto-derives slug from display_name if missing."""
    _ensure_dir()
    if not data.get("slug"):
        data["slug"] = slugify(data.get("display_name", "track"))
    data["slug"] = slugify(data["slug"])

    # Minimal validation
    if not data.get("display_name"):
        raise ValueError("display_name is required")
    if not data.get("centerline_path"):
        raise ValueError("centerline_path is required")
    data.setdefault("viewBox", "0 0 1260 720")
    data.setdefault("ir_names", [])
    data.setdefault("sf_offset", 0.0)
    data.setdefault("notes", "")
    # Per-track hero image used by the replay (and anywhere else that
    # wants a track-specific visual). Empty falls back to
    # iracing_large_image, then the shared _default_bg.jpg.
    data.setdefault("background", "")

    with open(_path_for(data["slug"]), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def delete(slug: str) -> bool:
    p = _path_for(slug)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


# ─── iRacing /data API enrichment ────────────────────────────────

def enrich_with_iracing(slug: str) -> Optional[dict]:
    """Pull marketing-grade metadata from iRacing's /data API into a
    track entry. Adds detail copy, hero images, and the canonical
    iracing_track_id. Existing fields (centerline, viewBox, sf_offset,
    aliases) are left untouched — this is strictly additive.

    Match strategy: walk the iRacing track catalog and pick the first
    track whose track_name is in this entry's ir_names list. Falls back
    to display_name match. Returns the merged dict on success or None
    if anything fails (no creds, no match, etc).
    """
    track = get(slug)
    if not track:
        return None

    try:
        import iracing_api_adapter as ir
    except Exception as e:
        print(f"[track_library] adapter import failed: {e!r}")
        return None

    if not ir.is_available():
        print("[track_library] iRacing API not available — skipping enrichment")
        return None

    # Catalog lookup. /data/track/get returns one entry per track-config
    # (e.g. Watkins Glen Boot vs. Long Course are separate rows).
    catalog = ir.get_tracks() or []
    if not catalog:
        return None

    # Build the alias set we'll match against — case + whitespace
    # insensitive, includes display_name as a last-ditch fallback.
    aliases = {_norm(a) for a in (track.get("ir_names") or []) if a}
    aliases.add(_norm(track.get("display_name", "")))
    aliases.discard("")

    matched_id: Optional[int] = None
    for row in catalog:
        if not isinstance(row, dict):
            continue
        name = _norm(row.get("track_name", ""))
        if name and name in aliases:
            tid = row.get("track_id")
            if isinstance(tid, int):
                matched_id = tid
                break

    if matched_id is None:
        print(f"[track_library] no iRacing match for slug={slug}")
        return None

    # Asset blob is keyed by str(track_id).
    assets_map = ir.get_track_assets() or {}
    assets = assets_map.get(str(matched_id)) or assets_map.get(matched_id) or {}
    if not isinstance(assets, dict):
        assets = {}

    # Merge — existing fields win for things we own (display_name,
    # ir_names, centerline_path, viewBox), iRacing wins for the new
    # marketing fields. iracing_track_id is the only new identifier.
    track["iracing_track_id"] = matched_id
    track["iracing_detail_copy"] = assets.get("detail_copy") or ""
    track["iracing_techspecs_copy"] = assets.get("detail_techspecs_copy") or ""
    track["iracing_large_image"] = ir.iracing_image_url(assets.get("large_image"))
    track["iracing_small_image"] = ir.iracing_image_url(assets.get("small_image"))
    track["iracing_logo"] = ir.iracing_image_url(assets.get("logo"))
    track["iracing_track_map"] = ir.iracing_image_url(assets.get("track_map"))
    track["iracing_gallery_prefix"] = assets.get("gallery_prefix") or ""
    track["iracing_fetched_at"] = int(__import__("time").time())

    # Persist via save() so the existing required-field checks run.
    # save() also re-applies defaults, so we don't lose anything.
    return save(track)
