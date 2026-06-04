"""
Inworld audio markup helpers for live commentary.

This is deliberately conservative: one emotion tag at the beginning of the
request, plus a couple of short SSML breaks for pacing. Logs and HUD text never
see these markups; they are added only for the TTS render string.
"""

from __future__ import annotations

import re


_SUPPORTED_EMOTIONS = {
    "happy",
    "sad",
    "angry",
    "surprised",
    "fearful",
    "disgusted",
    "laughing",
    "whispering",
}
_MARKUP_PREFIX_RE = re.compile(r"^\s*\[(happy|sad|angry|surprised|fearful|disgusted|laughing|whispering)\]\s*", re.I)
_BREAK_RE = re.compile(r"<\s*break\b", re.I)
_SENTENCE_BREAK_RE = re.compile(r"([.!?])\s+")


_DEFAULT_TRIGGER_EMOTIONS = {
    "race_intro": "happy",
    "white_flag": "surprised",
    "checkered_flag": "happy",
    "overtake": "surprised",
    "leader_change": "surprised",
    "fastest_lap": "happy",
    "fastest_S1": "happy",
    "fastest_S2": "happy",
    "fastest_S3": "happy",
    "collision_event": "surprised",
    "off_track": "surprised",
    "spinning-off_track": "surprised",
    "towed_car": "sad",
    "friendly_goodbye": "sad",
    "fun_fact": "happy",
    "trivia_time": "happy",
    "focus_driver_of_the_day": "happy",
}


def _tts_cfg() -> dict:
    try:
        import race_config
        return (race_config.cfg("power_unit") or {}).get("inworld", {}) or {}
    except Exception:
        return {}


def _emotion_for_trigger(trigger_name: str | None, cfg: dict) -> str:
    emotions = dict(_DEFAULT_TRIGGER_EMOTIONS)
    emotions.update(cfg.get("trigger_emotions") or {})
    emotion = str(emotions.get(trigger_name or "") or "").strip().lower()
    if emotion in ("", "none", "neutral", "off", "false"):
        return ""
    if emotion not in _SUPPORTED_EMOTIONS:
        print(f"[TTS EXPRESSION] Unsupported emotion '{emotion}' for trigger '{trigger_name}'")
        return ""
    return emotion


def _add_sentence_breaks(text: str, cfg: dict) -> str:
    if _BREAK_RE.search(text):
        return text

    break_ms = int(cfg.get("sentence_break_ms", 180) or 0)
    max_breaks = int(cfg.get("max_sentence_breaks", 2) or 0)
    min_chars = int(cfg.get("min_chars_for_breaks", 90) or 0)

    if break_ms <= 0 or max_breaks <= 0 or len(text) < min_chars:
        return text

    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        if count >= max_breaks:
            return match.group(0)
        count += 1
        return f'{match.group(1)} <break time="{break_ms}ms" /> '

    return _SENTENCE_BREAK_RE.sub(repl, text)


def apply_expression_markup(text: str, trigger_name: str | None = None) -> str:
    """Add Inworld audio markups for the TTS render pass only."""
    if not isinstance(text, str) or not text.strip():
        return text or ""

    cfg = _tts_cfg()
    if not cfg.get("enable_audio_markups", True):
        return text

    rendered = _add_sentence_breaks(text.strip(), cfg)

    if cfg.get("enable_emotion_markups", True) and not _MARKUP_PREFIX_RE.match(rendered):
        emotion = _emotion_for_trigger(trigger_name, cfg)
        if emotion:
            rendered = f"[{emotion}] {rendered}"

    return rendered
