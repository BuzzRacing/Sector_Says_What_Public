# Sector Says What

AI-driven live motorsport broadcast for iRacing.

Sector Says What turns an iRacing race into a live broadcast package: telemetry
ingestion, grounded AI commentary, Inworld TTS playback through FMOD, a live HUD,
Pit Wall admin tools, race archives, animated replays, Driver of the Day scoring,
and post-race reports.

The project is built around one rule: commentary only says what the race data
supports. No invented tyre strategy, fake damage, imagined overtakes, or
cool-down-lap "new leader" calls.

This public export excludes local race archives, generated telemetry reports,
private agent/session notes, and machine-specific configuration.

## Features

### Live AI Commentary

- Real-time triggers for overtakes, lead changes, fastest laps, sectors, pit
  entries, incidents, spins, flags, battle packs, race summaries, pre-race picks,
  white-flag sendoffs, and checkered-flag recaps.
- OpenAI-compatible commentary generation using either OpenAI or a local LLM,
  configured under `power_unit.provider`.
- System-level anti-fabrication rules on every LLM call.
- DB prompt templates and in-code prompt builders that add trigger-specific
  safety rules.
- Clean-language broadcast instruction applied by `prompt_builder.py`.
- Commentary events logged to SQLite and exported to `trigger_stream.json`.

### Broadcast Audio

- Inworld TTS through `text_to_speech_integration.py`.
- Render-only Inworld expression markup from `tts_expression.py`.
- Render-only pronunciation substitutions from `pronunciation/global.md`.
- FMOD playback through `commentary_queue.py`.
- Runtime audio device selection through `system_settings.json` and
  `/api/audio/devices`.

### Live HUD

The `/` route serves `python/sector_says_oversee/broadcast_hud_mockup.html`.

The HUD includes:

- Timing tower with live positions, gaps, delta from grid, pit/OUT indicators,
  and guaranteed Focus Driver visibility.
- Masthead/session state from `broadcast_state.json`.
- Fastest lap, best sectors, biggest mover, laps-led, battle, flag, pit, and DOTD
  cards.
- Active commentary line plus rolling history.
- Pre-race standby overlay and sound-check control.
- Finish takeover with podium and Driver of the Day cards.

### Pit Wall Admin

The `/pitwall` route serves the control room:

- Race Room and system config.
- Race Control trigger and host settings.
- Stewards' Office DOTD/penalty config.
- Broadcast start/stop/status controls.
- Prompt template editor.
- Race archives and driver career views.
- Replay import from iRacing eventresult JSON.
- Track library and in-browser centerline editor.
- Car library and optional iRacing asset sync.
- Media gallery, tagging, driver-photo matching, and background selection.
- DB explorer and data export browser.
- Owner Driver and commentator profile management.

### Race Replay

Every completed or imported race can be viewed at `/replay/<race_id>`.

Replay features:

- Track-map animation from per-track centerline JSON files.
- Authoritative lap timing from `race_lap_samples` for engine-tracked races.
- Synthetic pace fallback for imported-only races.
- Normalization for layouts with partial early crossings, such as Nordschleife
  Industriefahrten.
- Live re-ranking from each driver's total-distance curve.
- Focus Driver highlight, nameplate, and tracked callout positioning.
- Highlight timeline from commentary, incidents, and synthesized race moments.
- Variable playback speed with automatic slowdowns around highlights.
- Full classification overlay, AI race report, bottom ticker, and finish screen.

### Driver of the Day

DOTD scoring rewards real driving merit rather than only finishing position:

- Position gains and comeback milestones.
- Fastest lap, laps led, race finish, and race win.
- Clean laps and clean streaks with safety-rating-aware multipliers.
- Expectation delta from iRating rank.
- Incident, off-track, collision, and position-loss penalties.

The HUD/replay convention is percent of positive DOTD vote, not raw points.
Imported races render DOTD as unknown because the real score requires per-tick
telemetry.

## Architecture

```text
iRacing telemetry via pyirsdk
        |
        v
python/sector_says_engine.py
        |
        |-- freezes telemetry once per tick
        |-- builds live leaderboard snapshot
        |-- writes JSON exports
        |-- runs race triggers
        |-- captures lap samples
        |-- logs race history
        |
        +--> commentary generation
        |       openai_commentary.py
        |       commentary_phrases.py
        |       prompt_builder.py
        |
        +--> audio playback
        |       text_to_speech_integration.py
        |       tts_expression.py
        |       tts_pronunciation.py
        |       commentary_queue.py
        |
        +--> scoring and race data
        |       sector_says_dotd.py
        |       race_data_store.py
        |       race_db.py
        |       lap_sample_capture.py
        |
        +--> detection
        |       live_leaderboard.py
        |       collision_tracker.py
        |       offtrack_tracker.py
        |       triggers_realtime.py
        |       triggers_summary.py
        |
        +--> Flask dashboard subprocess
                sector_says_oversee/app.py
                sector_says_oversee/pitwall.py
```

## Setup

### Prerequisites

- Windows.
- Python 3.10+; Python 3.11 is preferred.
- iRacing installed and running for live telemetry.
- FFmpeg available for audio processing.
- FMOD runtime DLLs/assets under `audio/fmod/`.
- `OPENAI_API_KEY` for OpenAI commentary, unless using only a local LLM.
- `INWORLD_API_KEY` for TTS.
- Optional iRacing `/data` credentials in `python/race_secrets.json`.

### Install Dependencies

```bat
install.bat
```

or manually:

```bat
pip install -r requirements.txt
```

### Create Local Config

```bat
copy python\race_config.example.json python\race_config.json
copy python\race_secrets.example.json python\race_secrets.json
```

Keep real secrets out of git. `race_secrets.json` is for iRacing `/data`
credentials only; API keys should come from environment variables.

## Running

Full engine plus dashboard:

```bat
run.bat
```

Dashboard only:

```bat
run_dashboard.bat
```

Direct engine launch:

```bat
cd python
python sector_says_engine.py
```

Direct dashboard launch:

```bat
cd python\sector_says_oversee
python app.py
```

Normal live-race flow:

1. Start iRacing and enter the session.
2. Start Sector Says What.
3. Open `http://localhost:8080/`.
4. The engine waits silently through practice/qualifying.
5. Commentary starts only when the race goes green.
6. After the race, open `http://localhost:8080/replays`.

## Web Routes

| Route | Purpose |
| --- | --- |
| `/` | Live broadcast HUD |
| `/product` | Product/preview page |
| `/pitwall` | Pit Wall admin dashboard |
| `/replays` | Replay picker |
| `/replay/<race_id>` | Replay viewer |
| `/sector_said_exports/<file>` | No-cache JSON/JSONL export feed |

By default Flask binds to `127.0.0.1:8080`. To expose read-only surfaces on a
LAN, set `SSW_HOST=0.0.0.0`. Sensitive Pit Wall and write API routes require
loopback access or `SSW_ADMIN_TOKEN`.

Do not expose this Flask server directly to the public internet.

## Configuration

Most settings can be edited in Pit Wall. Important files:

| File | Purpose |
| --- | --- |
| `python/race_config.json` | Main non-secret config, created locally |
| `python/race_config.example.json` | Bootstrap/default example |
| `python/race_secrets.json` | Optional iRacing `/data` credentials, created locally |
| `python/sector_said_exports/system_settings.json` | Runtime focus/audio preferences |
| `python/sector_said_exports/focus_driver_of_the_day.json` | Current Focus Driver |
| `pronunciation/global.md` | TTS pronunciation substitutions |

## Importing Historical Races

Import an iRacing eventresult JSON:

```bat
python python\import_iracing_eventresult.py path\to\eventresult-<subsession>.json
```

Import from a GrumpyRobot-compatible service:

```bat
python python\import_grumpyrobot_race.py <race_id> --host http://localhost:5000
```

Imported races are replayable. They use official result data plus synthetic
replay timing when live `race_lap_samples` are unavailable.

## Diagnostics

Verify telemetry variables from a live iRacing session:

```bat
cd python
python telemetry_diagnostic.py
```

Useful runtime artifacts:

- `engine.log`: desktop/hidden-launcher output.
- `python/telemetry_diagnostic_report.json`: telemetry diagnostic output.
- `python/sector_said_exports/*.json`: current HUD data.
- `python/race_history.db`: archive and replay source.

## Critical Invariants

- Freeze telemetry once per engine tick, then pass the frozen snapshot down.
- Commentary is gated until the race session is active and green.
- Rolling triggers stay silent after the white flag until checkered.
- Race-over state blocks all post-checkered rolling calls.
- Pit detection uses `CarIdxOnPitRoad` edge detection.
- Overtake detection uses total distance (`Lap + LapDistPct`), not raw `RacePos`.
- `lap_sample_capture.capture()` runs once per main race tick.
- Never weaken the anti-fabrication stack: system rules, DB templates, and
  post-hoc sanitizer.

## Security

Never commit:

- `.env` files
- `python/race_secrets.json`
- real API keys, tokens, passwords, cookies, or session files
- local race databases
- generated runtime export JSON

See `SECURITY.md`.

## License

Code and documentation are released under the MIT license. See `LICENSE`.

This project is independent and unofficial. iRacing, OpenAI, Inworld, FMOD, and
other product/service names remain the property of their owners. See `NOTICE.md`.

## Credits

- Telemetry: iRacing SDK via `pyirsdk`.
- Commentary AI: OpenAI-compatible LLMs.
- TTS: Inworld.
- Audio pipeline: FMOD.

Sector Says What: live commentary, replay storytelling, and a full Pit Wall
control room for sim races that deserve more than a quiet timing screen.
