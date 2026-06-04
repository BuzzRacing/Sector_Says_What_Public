# Contributing

Thanks for taking a look at Sector Says What.

## Ground Rules

- Keep commentary grounded in telemetry and stored race data.
- Do not add prompts that encourage invented strategy, tyre calls, damage claims,
  emotions, blame, team radio, or future speculation.
- Do not commit secrets, local race databases, personal telemetry exports, or
  generated runtime JSON.
- Keep UI changes pointed at the active files listed in `README.md`.
- Prefer small, focused changes over broad rewrites.

## Local Setup

1. Install Python 3.10+.
2. Run `pip install -r requirements.txt`.
3. Copy `python/race_config.example.json` to `python/race_config.json`.
4. Copy `python/race_secrets.example.json` to `python/race_secrets.json` only if
   you plan to use iRacing `/data` enrichment.
5. Set `OPENAI_API_KEY` and `INWORLD_API_KEY` if you want generated commentary
   and TTS.

## Pull Requests

Before opening a PR:

- Run `python -B -m py_compile` on touched Python files.
- Validate changed JSON with `python -m json.tool`.
- For UI changes, run the dashboard and inspect the affected route in a browser.
- Include what was tested in the PR description.
