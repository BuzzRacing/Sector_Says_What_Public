from openai import OpenAI
import race_config

# Default client (uses OPENAI_API_KEY env var)
_openai_client = OpenAI()
_local_client = None


def _get_client():
    """Return the correct OpenAI-compatible client based on config provider."""
    global _local_client
    pu = race_config.cfg("power_unit")
    provider = pu.get("provider", "openai")

    if provider == "local_llm":
        local_cfg = pu.get("local_llm", {})
        base_url = local_cfg.get("base_url", "http://localhost:1234/v1")
        api_key = local_cfg.get("api_key", "not-needed")
        # Re-create client if base_url changed
        if _local_client is None or str(_local_client.base_url).rstrip("/") != base_url.rstrip("/"):
            _local_client = OpenAI(base_url=base_url, api_key=api_key)
        return _local_client, local_cfg

    return _openai_client, pu.get("openai", {})


def _ai_cfg():
    _, cfg = _get_client()
    return cfg


# ─── Global ground rules ──────────────────────────────────────────
# Every prompt the engine sends goes through this. The goal is to
# stop the model from fabricating plausible-sounding racing colour
# that we have no telemetry for. Pit stops in this sim generally mean
# trouble, not strategy; there is no live tyre data, no team radio,
# no setup changes, no strategy calls.  Anything the model cannot
# see in the prompt text does not exist.
SYSTEM_GROUND_RULES = """You are a live race commentator for a sim-racing broadcast.

HARD RULES — these override everything else in the user prompt:
1. Report ONLY what is stated in the user prompt's data block. Do not invent facts.
2. DO NOT mention: tyre strategy, tyre compounds, fresh rubber, pit strategy,
   undercut, overcut, fuel saving, fuel loads, team radio, driver emotions,
   setup changes, damage assessments, mechanical issues, or weather evolution
   — unless the user prompt explicitly provides that data.
3. Pit stops in this series are usually forced (damage, spin recovery, end of
   race for that car). Never call a pit stop "strategic", "planned", or claim
   "fresh tyres". A pitting car is likely losing time they will not recover.
4. Do not speculate about the future beyond what the data supports. No "they
   might undercut", no "fresh tyres will help", no "this opens the window".
5. Do not describe on-track action you were not told about. No "wheel-to-wheel",
   "side-by-side", "brave move around the outside" unless the prompt says so.
   A close gap is just a close gap.
6. Do not assign blame in collisions unless the prompt identifies a cause.
7. When data is missing, say LESS. Never fill gaps with invention.
8. Stay within the requested character limit. Be punchy and factual.
9. PACE CLAIMS require evidence. Never say a driver is "catching",
   "closing", "reeling in", "pulling away", "extending the lead", "getting
   faster", "fading", or "losing pace" unless the prompt gives an explicit
   gap delta (e.g. "gap dropped 0.8s over 2 laps") or a lap-time comparison.
   A single snapshot gap by itself is just a gap — not a trend.
10. A car that is in the pits is NOT racing anyone. If the prompt marks a
    driver as on pit road, pitting, in the pit lane, or tagged [PIT],
    never describe them as catching / closing on / battling / overtaking
    anyone. Describe the pit event only, never on-track progress. This
    applies even to the Focus Driver / owner — pit time is lost time.

NUMBERS, TIMES & GAPS — broadcast convention (a deterministic post-hoc
sanitiser will translate raw numerals you emit, but you produce better
audio when you write in broadcast form to begin with):

- Lap times: "a one thirty-eight point six four zero" (1:38.640), "a
  one oh three point four five six" (1:03.456 — leading-zero seconds
  become "oh"), "ninety-six point four six three" for sub-minute laps.
  Never "one minute thirty-eight point six four zero seconds" and never
  the colon notation in spoken text.
- Sub-tenth gaps: "eight thousandths" (0.008), "one thousandth" (0.001),
  "dead level" (under a millisecond).
- Sub-second gaps: "point one two three" (0.123), "half a second"
  (0.500). Each digit spelled, no padding beyond what the data shows.
- Multi-second gaps: "one point two three four" (1.234), "six and a
  half seconds" (6.500), "twelve point five" (12.5). Round-number
  gaps: "five seconds", "ten seconds".
- Long gaps: "over a minute", "a minute and a half", "two minutes back".
- Speeds: "two hundred kilometers an hour" (200 km/h), "one twenty
  miles an hour" (120 mph). Round to the integer; no decimals on speed.
- Distances: "five point four kilometers", "two and a half miles".
- Percent: "thirty-four point nine percent". Never the % symbol in spoken
  text.
- iRating: "four point three K iRating" (4324). Always one decimal in
  thousands.
- Positions: "P fourteen" or "fourteenth", never the literal "P14"
  pronounced as "pee-fourteen". "P one" / "the lead" for the leader.
"""


# Anti-fabrication layer 3 (post-hoc string sanitizer; see README
# "Critical Invariants"). The LLM occasionally emits a meta-reply when it gets
# a malformed prompt — e.g. "Please provide the data block for the
# commentary." — which then sails into TTS as if it were race colour.
# Catch known failure prefixes here so they never reach the broadcast.
_LLM_META_REPLY_PHRASES = (
    "please provide",
    "could you provide",
    "i need more information",
    "i don't have enough information",
    "i'm sorry, i can't",
    "i'm sorry, but i can't",
    "as an ai",
    "as a language model",
    "[error]",
)


def is_llm_meta_reply(text: str) -> bool:
    """True if `text` looks like an LLM asking for input or refusing to
    answer rather than producing race commentary. Treated as a hard
    failure: never log to DB, never enqueue for TTS.
    """
    if not text:
        return True
    head = text.strip().lower()[:80]
    return any(head.startswith(p) for p in _LLM_META_REPLY_PHRASES)


def generate_commentary(prompt: str, commentator: str = None) -> str:
    """Send prompt to the configured LLM and return AI-generated race commentary."""
    try:
        if commentator:
            print(f"[AI REQUEST] {commentator}: {prompt[:80]}...")

        client, ai = _get_client()
        resp = client.chat.completions.create(
            model=ai.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": SYSTEM_GROUND_RULES},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=ai.get("max_tokens", 250),
            temperature=ai.get("temperature", 0.5),
        )
        text = resp.choices[0].message.content.strip()
        if is_llm_meta_reply(text):
            print(f"[AI BLOCK] meta-reply detected, dropping: {text[:80]!r}")
            return ""
        return text

    except Exception as e:
        print(f"[ERROR] LLM request failed: {e}")
        return f"[ERROR] Could not generate commentary for {commentator or 'AI'}."


REPORT_SYSTEM_PROMPT = """You are an F1 journalist writing a post-race report for a sim-racing broadcast called "Sector Says What".

Write 180-280 words in a professional motorsport journalism style.
Start with a compelling headline on its own line.

Required coverage, in approximately this order:
1. Who won and how (margin if given). Quote the winner's fastest lap when supplied.
2. The key battle (top-3 fight, late-race drama, or whichever the data supports).
3. The OWNER / FOCUS DRIVER's race — grid position, finish, swing, pace, any
   moments from the KEY MOMENTS list involving them. They are the audience;
   do not omit them. If the data block contains an "OWNER / FOCUS DRIVER" line
   they MUST appear in the report with at least a sentence.
4. BIGGEST MOVER (and BIGGEST LOSER, when listed and the drop is significant —
   a P3 → P22 collapse is news, do not bury it).
5. Driver of the Day, with the score when given.

Only reference facts provided in the data below — do not invent tyre strategy,
pit strategy, weather, team radio, or any details not in the data. Short
paragraphs. Vivid but factual. No filler.
"""


def generate_report_text(prompt: str) -> str:
    """Generate a longform race report via the configured LLM (higher token limit)."""
    try:
        client, ai = _get_client()
        resp = client.chat.completions.create(
            model=ai.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=500,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] Race report generation failed: {e}")
        return f"[ERROR] Could not generate race report: {e}"
