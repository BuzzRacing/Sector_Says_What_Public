"""
car_library.py — Car library backed by per-car JSON files.

Mirrors track_library.py but for cars. Each entry holds a slug, the
display name, an alias list of iRacing car_name strings, and the rich
asset fields pulled from /data/car/assets when the user runs a sync.

Files live at:
    python/sector_says_oversee/cars/<slug>.json
"""

from __future__ import annotations
import json
import os
import re
import time
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
CARS_DIR = os.path.abspath(os.path.join(_HERE, "sector_says_oversee", "cars"))


def _ensure_dir() -> None:
    os.makedirs(CARS_DIR, exist_ok=True)


def slugify(text: str) -> str:
    """Filesystem-safe slug from a display name."""
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "car"


def _path_for(slug: str) -> str:
    return os.path.join(CARS_DIR, slugify(slug) + ".json")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def load_all() -> list[dict]:
    """Return every car JSON in the cars/ folder, sorted by display_name."""
    _ensure_dir()
    out: list[dict] = []
    for fn in os.listdir(CARS_DIR):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            with open(os.path.join(CARS_DIR, fn), "r", encoding="utf-8") as f:
                c = json.load(f)
            c.setdefault("slug", fn[:-5])
            out.append(c)
        except Exception as e:
            print(f"[car_library] failed to load {fn}: {e}")
    out.sort(key=lambda c: (c.get("display_name") or c.get("slug") or "").lower())
    return out


def get(slug: str) -> Optional[dict]:
    p = _path_for(slug)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def find_for(car_name: str) -> Optional[dict]:
    """Match an iRacing car_name string against the library.

    Same priority chain as track_library.find_for(): exact alias match,
    then display_name, then slug, then prefix.
    """
    if not car_name:
        return None
    target = _norm(car_name)
    cars = load_all()

    for c in cars:
        for alias in c.get("ir_names") or []:
            if _norm(alias) == target:
                return c
    for c in cars:
        if _norm(c.get("display_name", "")) == target:
            return c
    for c in cars:
        if _norm(c.get("slug", "")) == target:
            return c
    for c in cars:
        for alias in (c.get("ir_names") or []) + [c.get("display_name", "")]:
            n = _norm(alias)
            if n and (target.startswith(n) or n.startswith(target)):
                return c
    return None


def save(data: dict) -> dict:
    """Persist a car. Auto-derives slug from display_name if missing."""
    _ensure_dir()
    if not data.get("slug"):
        data["slug"] = slugify(data.get("display_name", "car"))
    data["slug"] = slugify(data["slug"])

    if not data.get("display_name"):
        raise ValueError("display_name is required")
    data.setdefault("ir_names", [])
    data.setdefault("notes", "")

    with open(_path_for(data["slug"]), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def delete(slug: str) -> bool:
    p = _path_for(slug)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


# ─── iRacing /data API sync ──────────────────────────────────────

def sync_from_iracing() -> dict:
    """Pull the full iRacing car catalog + asset blob and persist one
    JSON per car. Existing files are merged in place — manually-set
    fields (display_name override, ir_names, notes) are preserved.

    Returns a {"created": N, "updated": M, "errors": [..]} summary.
    """
    try:
        import iracing_api_adapter as ir
    except Exception as e:
        return {"error": f"adapter import failed: {e!r}"}
    if not ir.is_available():
        return {"error": "iRacing API not available — enable in Power Unit"}

    catalog = ir.get_cars() or []
    assets_map = ir.get_car_assets() or {}
    if not catalog:
        return {"error": "iRacing returned no cars (auth or rate limit?)"}

    _ensure_dir()
    created = 0
    updated = 0
    errors: list[str] = []

    for row in catalog:
        if not isinstance(row, dict):
            continue
        car_id = row.get("car_id")
        car_name = row.get("car_name") or ""
        if not isinstance(car_id, int) or not car_name:
            continue

        assets = assets_map.get(str(car_id)) or assets_map.get(car_id) or {}
        if not isinstance(assets, dict):
            assets = {}

        slug = slugify(car_name)
        existing = get(slug) or {}

        merged = dict(existing)  # preserve user fields
        merged["slug"] = slug
        # Display name: keep user override if present, else use the
        # iRacing canonical name.
        merged.setdefault("display_name", car_name)
        # ir_names: ensure the canonical name is in the alias list
        # without losing user-added entries.
        aliases = list(merged.get("ir_names") or [])
        if car_name not in aliases:
            aliases.append(car_name)
        merged["ir_names"] = aliases
        merged.setdefault("notes", "")

        # iRacing-owned fields — overwrite each sync.
        merged["iracing_car_id"] = car_id
        merged["iracing_car_name"] = car_name
        merged["iracing_car_make"] = row.get("car_make") or ""
        merged["iracing_car_model"] = row.get("car_model") or ""
        merged["iracing_detail_copy"] = assets.get("detail_copy") or ""
        merged["iracing_techspecs_copy"] = assets.get("detail_techspecs_copy") or ""
        merged["iracing_large_image"] = ir.iracing_image_url(assets.get("large_image"))
        merged["iracing_small_image"] = ir.iracing_image_url(assets.get("small_image"))
        merged["iracing_group_image"] = ir.iracing_image_url(assets.get("group_image"))
        merged["iracing_logo"] = ir.iracing_image_url(assets.get("logo"))
        merged["iracing_sponsor_logo"] = ir.iracing_image_url(assets.get("sponsor_logo"))
        merged["iracing_fetched_at"] = int(time.time())

        try:
            save(merged)
            if existing:
                updated += 1
            else:
                created += 1
        except Exception as e:
            errors.append(f"{car_name}: {e!r}")

    return {"created": created, "updated": updated, "errors": errors}
