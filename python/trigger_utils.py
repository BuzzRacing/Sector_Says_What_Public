# trigger_utils.py
import time

_trigger_cooldowns = {}

def can_fire(trigger, cooldown=70):
    """Return True if the trigger can fire (cooldown passed)."""
    return time.time() - _trigger_cooldowns.get(trigger, 0) >= cooldown

def mark_fired(trigger):
    """Mark a trigger as fired right now."""
    _trigger_cooldowns[trigger] = time.time()

def _is_trigger_disabled(trigger):
    """Check if a trigger type is disabled via Pit Wall race control config."""
    try:
        import commentary_team
        ts = commentary_team.get_trigger_settings()
        settings = ts.get(trigger, {})
        return settings.get("_disabled", False)
    except Exception:
        return False

def try_trigger(trigger, func, *a, **kw):
    """Fire a trigger if cooldown allows and not disabled, mark it fired."""
    if _is_trigger_disabled(trigger):
        return False
    if can_fire(trigger):
        func(*a, **kw)
        mark_fired(trigger)
        return True
    return False
