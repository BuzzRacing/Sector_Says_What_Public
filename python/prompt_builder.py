"""
Builds structured race commentary prompts for AI generation.
Supports database-backed prompt templates with variable substitution.
"""

import random
from typing import List, Dict, Optional
from commentary_team import commentators, get_host_and_energy
from commentary_team import trigger_settings as TRIGGERS


# ─── Database prompt template resolver ───────────────────────────

def get_db_prompt(trigger_type: str, context: dict = None) -> Optional[str]:
    """Fetch a random active prompt template from the DB for this trigger type.

    Performs safe {variable} substitution using the context dict.
    Returns the resolved prompt string, or None if no templates exist.
    """
    try:
        import race_db
        templates = race_db.get_prompt_templates(trigger_type)
    except Exception:
        return None

    # Filter to active templates only
    active = [t for t in templates if t.get("is_active")]
    if not active:
        return None

    # Pick a random template
    template = random.choice(active)
    prompt_text = template["prompt_text"]
    char_limit = template.get("char_limit", 150)

    # Build substitution context with char_limit included
    ctx = {"char_limit": char_limit}
    if context:
        ctx.update(context)

    # Safe substitution — leave unmatched {placeholders} as-is
    try:
        # Use format_map with a defaultdict-like fallback
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"
        prompt_text = prompt_text.format_map(_SafeDict(ctx))
    except Exception:
        pass  # If formatting fails, use the raw template

    return prompt_text


def get_db_prompt_with_meta(trigger_type: str, context: dict = None) -> Optional[Dict]:
    """Like get_db_prompt but returns the full template metadata too."""
    try:
        import race_db
        templates = race_db.get_prompt_templates(trigger_type)
    except Exception:
        return None

    active = [t for t in templates if t.get("is_active")]
    if not active:
        return None

    template = random.choice(active)
    prompt_text = template["prompt_text"]
    char_limit = template.get("char_limit", 150)

    ctx = {"char_limit": char_limit}
    if context:
        ctx.update(context)

    try:
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"
        prompt_text = prompt_text.format_map(_SafeDict(ctx))
    except Exception:
        pass

    return {
        "prompt_text": prompt_text,
        "char_limit": char_limit,
        "template_name": template.get("template_name", ""),
        "template_id": template.get("id"),
    }

def build_commentary_prompt(
    trigger_type: str,
    leaderboard: Optional[List[Dict]] = None,
    summary: str = "",
    char_limit: int = 150,
    prompt_keywords: str = "general race insight",
    elapsed_time: float = 0.0,
    total_time: float = 3600.0,
    commentator_name: Optional[str] = None,
    context: Optional[Dict] = None
) -> Dict:
    """
    Build a structured AI commentary prompt.
    """
    context = context or {}

    # --- Determine commentator and energy ---
    if commentator_name:
        comm_info = commentators.get(commentator_name, {"voice": "neutral", "location": "studio", "think": "balanced analysis", "style": "neutral", "energy": "calm"})
        energy = comm_info.get("energy", "calm")
    else:
        commentator_name, energy, _loc = get_host_and_energy(elapsed_time, total_time)
        comm_info = commentators.get(commentator_name, {"voice": "neutral", "location": "studio", "think": "balanced analysis", "style": "neutral", "energy": energy})

    # --- Trigger-based style and char limit ---
    settings = TRIGGERS.get(trigger_type, TRIGGERS.get("host_segment", {}))
    style = settings.get("style", comm_info["style"])
    char_limit = int(settings.get("characters", char_limit))

    # --- Race context ---
    leader = context.get("leader", "the race leader")
    lap = context.get("lap", "an unknown lap")
    gap = context.get("gap")

    tone_note = "This is a family-friendly broadcast — clean language only, no profanity or adult content. "

    # --- Build prompt ---
    prompt_text = (
        f"You are {commentator_name}, a {comm_info['voice']} motorsport commentator broadcasting live from the {comm_info['location']}. "
        f"It is a {trigger_type} moment in the race - currently {leader} is leading on {lap}. "
        f"{tone_note}"
        f"Speak with {energy} energy and a {style} style. "
        f"Think like {comm_info['think']} - keep it quick, vivid, and spontaneous. "
        f"Your commentary should sound natural and exciting, under {char_limit} characters. "
        f"Use these keywords for inspiration: {prompt_keywords}."
    )

    if gap is not None and gap > 0:
        prompt_text += f" The current gap to the car ahead is approximately {gap:.2f} percent of lap distance."

    return {
        "prompt": prompt_text,
        "commentator": commentator_name,
        "energy": energy,
        "trigger_type": trigger_type,
        "style": style,
        "char_limit": char_limit,
        "context": context
    }


# -------------------
# Example test
# -------------------
if __name__ == "__main__":
    test_prompt = build_commentary_prompt(
        trigger_type="race_start",
        elapsed_time=30,
        total_time=3600,
        prompt_keywords="launch, grid, acceleration",
        leaderboard=[{"CarIdx": 0, "Pos": 1, "LapDistPct": 0.1}, {"CarIdx": 1, "Pos": 2, "LapDistPct": 0.05}],
        context={"leader": "Verstappen", "lap": "Lap 1", "CarIdx": 1}
    )
    print("\n[DEBUG] Generated Prompt Object:")
    for k, v in test_prompt.items():
        print(f"{k}: {v}")
