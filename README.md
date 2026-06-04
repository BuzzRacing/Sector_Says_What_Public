# Sector Says What

AI-driven live motorsport broadcast for iRacing.

Sector Says What turns an iRacing race into a live broadcast package: telemetry
ingestion, grounded AI commentary, Inworld TTS playback through FMOD, a live HUD,
Pit Wall admin tools, race archives, animated replays, Driver of the Day scoring,
and post-race reports.

## Features

### Live AI Commentary

- Real-time race triggers for overtakes, lead changes, fastest laps, sectors, pit
  entries, incidents, spins, flags, battle packs, race summaries, focus-driver
  updates, pre-race picks, white-flag sendoffs, and checkered-flag recaps.
- OpenAI-compatible commentary generation using either OpenAI or a local LLM,
  configured under `power_unit.provider`.
- A system ground-rules prompt on every LLM call bans unsupported claims about
  tyres, fuel, strategy, setup, driver emotions, collision blame, damage, future
  speculation, and wheel-to-wheel detail.

### Broadcast Audio

- Inworld TTS through `text_to_speech_integration.py`.
- FMOD playback through `commentary_queue.py` with a single `_is_playing` gate:
  audio is reserved at enqueue time to prevent talk-over.
- Audio device selection

### Live Broadcast HUD

The `/` route serves `python/sector_says_oversee/broadcast_hud_mockup.html`.

The HUD includes:

- Timing tower with live position, gaps, delta from grid, pit/OUT indicators, and
  guaranteed Focus Driver visibility.
- Masthead/session state from `broadcast_state.json`.
- Fastest lap, best sectors, biggest mover, laps-led, battle, flag, pit, and DOTD
  cards.
- Active commentary line plus rolling history.
- Pre-race standby overlay and sound-check control.
- Finish takeover with shared podium/DOTD card styling used by both live HUD and
  replay.

### Pit Wall Admin

- Race Room and system config.
- Race Control trigger and host settings.
- Stewards' Office DOTD/penalty config.
- Broadcast start/stop/status controls.
- Prompt template editor.
- Race archives and driver career views.
- Replay import from iRacing eventresult JSON.
- Track library and in-browser centerline editor.
- Car library and iRacing asset sync.
- Media gallery, tagging, driver-photo matching, and page/HUD background picks.
- DB explorer and data export browser.
- Owner Driver and commentator profile management.

### Race Replay

Every completed or imported race can be viewed at `/replay/<race_id>`.
Replay data is served by `/api/replay/<race_id>` and animated by
`python/sector_says_oversee/replay.js`.

Replay features:

- Track-map animation from per-track centerline JSON files.
- Authoritative lap timing from `race_lap_samples` for engine-tracked races.
- Synthetic pace fallback for imported-only races.
- Normalization for layouts with partial early crossings, such as Nordschleife
  Industriefahrten.
- Live re-ranking from each driver's total-distance curve.
- Focus Driver highlight, persistent nameplate, and tracked callout positioning.
- Sparse broadcast highlight timeline from commentary, incidents, and synthesized
  race moments.
- Variable playback speed with automatic slowdowns around highlights.
- Full classification overlay, AI race report, bottom ticker, and finish screen.

### Driver of the Day

DOTD scoring rewards real driving merit rather than just race finish.

The scorer combines:

- Position gains and comeback milestones.
- Fastest lap, laps led, race finish, and race win.
- Clean laps and clean streaks with safety-rating-aware multipliers.
- Expectation delta from iRating rank.
- Incident, off-track, collision, and position-loss penalties.

The replay/HUD convention is percent of positive DOTD vote, not raw points.
Imported races cannot compute real DOTD scores because the rubric needs per-tick
telemetry, so imports write `NULL` DOTD values and the UI renders dashes.

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

## Configuration

## Importing Historical Races

Import an iRacing eventresult JSON:

```bat
python python\import_iracing_eventresult.py path\to\eventresult-<subsession>.json
```

Imported races are replayable. They use official result data plus synthetic replay
timing when live `race_lap_samples` are unavailable. Imported-only races do not
have real DOTD scores, because that score requires per-tick telemetry.

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


## 

Sector Says What: Live commentary, replay storytelling, and a full Pit Wall control room for sim races that deserve more than a quiet timing screen.
