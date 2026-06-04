# triggers_realtime.py
import random, time
import sector_says_utilities, commentary_team, openai_commentary
from commentary_queue import enqueue_commentary
from trigger_utils import try_trigger
from prompt_builder import get_db_prompt

def handle_realtime_commentary(trigger_name, phrase, tone, clip_type, car_name):
    """Send commentary to the queue immediately for spins, recovery, etc."""
    host = commentary_team.trigger_settings.get(clip_type, {}).get("host", "Murray")

    # Try DB template first
    db_prompt = get_db_prompt(clip_type, {
        "driver": car_name, "driver1": car_name, "driver2": "",
    })
    if db_prompt:
        prompt = db_prompt
    else:
        prompt = (
            f"Live commentary: {phrase}. Think like {commentary_team.commentators.get(host, {}).get('think','')}, "
            f"voice {commentary_team.commentators.get(host, {}).get('voice','')}, "
            f"style {commentary_team.commentators.get(host, {}).get('style','')}, tone {tone}."
        )

    ai_text = openai_commentary.generate_commentary(prompt)
    print(f"[{trigger_name}] Host: {host}\n{ai_text}")
    enqueue_commentary({
        "prompt": ai_text,
        "commentator": host,
        "type": clip_type,
        "clip_id": f"{clip_type}_{int(time.time())}_{car_name}"
    })
