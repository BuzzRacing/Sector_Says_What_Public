"""
Render-time pronunciation overrides for TTS.

The commentary transcript stays unchanged; this module only respells text
immediately before it is sent to the voice provider.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Iterable


_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DEFAULT_LIBRARY = os.path.join(_PROJECT_ROOT, "pronunciation", "global.md")
_ENV_LIBRARY = "SSW_PRONUNCIATION_FILE"

_WORD_CHARS = r"A-Za-z0-9_"
_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?`?([^`|]+?)`?\s*\|\s*`?([^`|]+?)`?\s*\|\s*([^|]+)"
)


@dataclass(frozen=True)
class PronunciationEntry:
    printed: str
    spoken: str
    scopes: tuple[str, ...]
    pattern: re.Pattern[str]


_lock = threading.RLock()
_cache_path: str | None = None
_cache_mtime: float | None = None
_cache_entries: tuple[PronunciationEntry, ...] = ()


def _library_path() -> str:
    configured = os.environ.get(_ENV_LIBRARY, "").strip()
    return configured or _DEFAULT_LIBRARY


def _clean_cell(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`") and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def _compile_pattern(printed: str) -> re.Pattern[str]:
    escaped = re.escape(printed)
    return re.compile(
        rf"(?<![{_WORD_CHARS}]){escaped}(?![{_WORD_CHARS}])",
        re.IGNORECASE,
    )


def _parse_scope(scope: str) -> tuple[str, ...]:
    values = []
    for part in scope.split(","):
        part = _clean_cell(part).lower()
        if part:
            values.append(part)
    return tuple(values) or ("all",)


def _parse_line(line: str) -> PronunciationEntry | None:
    match = _LINE_RE.match(line)
    if not match:
        return None
    printed = _clean_cell(match.group(1))
    spoken = _clean_cell(match.group(2))
    scopes = _parse_scope(match.group(3))
    if not printed or not spoken:
        return None
    return PronunciationEntry(
        printed=printed,
        spoken=spoken,
        scopes=scopes,
        pattern=_compile_pattern(printed),
    )


def _load_entries(path: str) -> tuple[PronunciationEntry, ...]:
    entries: list[PronunciationEntry] = []
    if not os.path.exists(path):
        return ()

    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            entry = _parse_line(line)
            if entry:
                entries.append(entry)

    entries.sort(key=lambda item: len(item.printed), reverse=True)
    return tuple(entries)


def load_entries(force: bool = False) -> tuple[PronunciationEntry, ...]:
    """Return cached pronunciation entries, reloading when the file changes."""
    global _cache_path, _cache_mtime, _cache_entries

    path = _library_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    with _lock:
        if force or path != _cache_path or mtime != _cache_mtime:
            try:
                _cache_entries = _load_entries(path)
                print(f"[TTS PRONUNCIATION] Loaded {len(_cache_entries)} override(s) from {path}")
            except Exception as e:
                print(f"[TTS PRONUNCIATION] Failed to load {path}: {e}")
                _cache_entries = ()
            _cache_path = path
            _cache_mtime = mtime
        return _cache_entries


def _scope_matches(scopes: Iterable[str], voice_id: str | None) -> bool:
    if not voice_id:
        voice_tokens: set[str] = set()
    else:
        voice = voice_id.strip().lower()
        voice_tokens = {voice, f"inworld:{voice}"}
        if ":" in voice:
            voice_tokens.add(voice.rsplit(":", 1)[-1])

    for scope in scopes:
        scope = scope.lower()
        if scope in ("all", "*"):
            return True
        if scope in voice_tokens:
            return True
        if voice_id and ":" in scope and scope.rsplit(":", 1)[-1] in voice_tokens:
            return True
    return False


def apply_pronunciation(text: str, voice_id: str | None = None) -> str:
    """Apply pronunciation respellings for the selected Inworld voice."""
    if not isinstance(text, str) or not text:
        return text or ""

    rendered = text
    for entry in load_entries():
        if _scope_matches(entry.scopes, voice_id):
            rendered = entry.pattern.sub(
                lambda match, spoken=entry.spoken: _match_case(match.group(0), spoken),
                rendered,
            )
    return rendered


def _match_case(original: str, spoken: str) -> str:
    if original[:1].isupper() and spoken:
        return spoken[:1].upper() + spoken[1:]
    return spoken
