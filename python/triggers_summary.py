# triggers_summary.py — race summary & fractional triggers
import time, random
import sector_says_utilities, openai_commentary, commentary_team
from commentary_queue import enqueue_commentary

def get_race_progress(ir):
    return sector_says_utilities.get_race_progress(ir)

def check_race_summary(now, leaderboard, owner_car, session_info, state):
    """Generate periodic race summary commentary."""
    events = []
    SUMMARY_INTERVAL = 180
    if now - state["summary_race_time"] < SUMMARY_INTERVAL:
        return events
    state["summary_race_time"] = now

    lines_pool = build_race_summary_lines(leaderboard, owner_car, session_info)
    if not lines_pool:
        return events

    prompt_options = random.sample(lines_pool, k=min(2, len(lines_pool)))
    race_summary_prompt_text = " ".join(prompt_options)
    if race_summary_prompt_text == state["summary_phrase"]:
        random.shuffle(prompt_options)
        race_summary_prompt_text = " ".join(prompt_options)
    state["summary_phrase"] = race_summary_prompt_text

    prompt = (
        f"Generate a live commentary snippet: {race_summary_prompt_text} "
        f"Think like {commentator_info.get('think','')}, voice {commentator_info.get('voice','')}, "
        f"style {commentator_info.get('style','')}. Keep it concise to "
        f"{commentary_team.trigger_settings['race_summary']['characters']} characters."
    )

    ai_text = openai_commentary.generate_commentary(prompt)
    print(f"[RaceSummary] Host: {host}\n{ai_text}")

    enqueue_commentary({
        "prompt": ai_text,
        "commentator": host,
        "type": "race_summary",
        "clip_id": f"race_summary_{int(time.time())}"
    })

    events.append({"type": "race_summary"})
    return events

def check_fastest_lap(now, leaderboard, state):
    events = []
    fastest_car = min((c for c in leaderboard if c.get("Last")), key=lambda c: c["Last"], default=None)
    if fastest_car:
        ft = fastest_car["Last"]
        if state["fastest_lap"] is None or ft < state["fastest_lap"]:
            from sector_says_utilities import voice_format_lap_time
            phrase = f"Fastest lap by {fastest_car['Name']} at {voice_format_lap_time(ft)}."
            host = commentary_team.trigger_settings['fastest_lap'].get("host")
            _announce("fastest_lap", phrase, host, leaderboard, state)
            state["fastest_lap"] = ft
            events.append({"type": "fastest_lap"})
    return events

def run_fractional_triggers(progress, leaderboard, session_info, state):
    events = []
    fractional_triggers = ["fun_fact", "race_prediction", "historical", "trivia_time", "owner_car_update"]
    for ftrigger in fractional_triggers:
        fractions = commentary_team.trigger_settings[ftrigger].get("fractions", [])
        for frac in fractions:
            key = f"{ftrigger}_{frac}"
            if state["fractional_done"].get(key) or progress < frac:
                continue
            focus_options = build_focus_list(ftrigger, leaderboard)
            if not focus_options:
                continue
            chosen_focus = random.choice(focus_options)
            host = commentary_team.trigger_settings[ftrigger].get("host")
            try_trigger(ftrigger, build_and_enqueue, ftrigger, session_info, leaderboard,
                        host_override=host, focus_topic=chosen_focus)
            state["fractional_done"][key] = True
            events.append({"type": ftrigger, "focus": chosen_focus, "fraction": frac})
    return events

# --- Helper Functions ---
def build_race_summary_lines(cars, owner_car, session_info):
    # Slimmed version of your existing build_race_summary_lines function
    lines_pool = []
    # Example: top 3 drivers
    top3 = cars[:3]
    if top3:
        lines_pool.append("Top 3 drivers: " + ", ".join([f"{c['Name']} (P{c['LivePos']})" for c in top3]))
    return lines_pool

def _announce(trigger, phrase, host, leaderboard, state):
    """Centralized AI announcement helper"""
    prompt = f"{phrase} Think like {commentary_team.commentators[host].get('think','')}, voice {commentary_team.commentators[host].get('voice','')}."
    ai_text = openai_commentary.generate_commentary(prompt)
    print(f"[{trigger}] Host: {host}\n{ai_text}")
    enqueue_commentary({
        "prompt": ai_text,
        "commentator": host,
        "type": trigger,
        "clip_id": f"{trigger}_{int(time.time())}"
    })
