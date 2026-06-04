# Sector Says What

AI-driven live motorsport broadcast for iRacing.

Sector Says What turns an iRacing race into a live broadcast package: telemetry
ingestion, grounded AI commentary, Inworld TTS playback through FMOD, a live HUD,
Pit Wall admin tools, race archives, animated replays, Driver of the Day scoring,
and post-race reports.

The project is built around one non-negotiable rule: commentary only says what the
data supports. No invented tyre strategy, no imagined damage, no fake overtakes,
no "new leader" calls after the chequered flag.

Last README refresh: 2026-06-04.

## Current Status

- Primary platform: Windows development box running iRacing.
- Main entry point: `python/sector_says_engine.py`.
- Dashboard server: Flask app in `python/sector_says_oversee/app.py`.
- Live HUD: `http://localhost:8080/`.
- Pit Wall admin: `http://localhost:8080/pitwall`.
- Replay browser: `http://localhost:8080/replays`.
- Product/preview page: `http://localhost:8080/product`.
- Race history DB: `python/race_history.db`.
- Live export feed: `python/sector_said_exports/`.

This public export intentionally excludes local race archives, generated telemetry
reports, private agent/session notes, and other machine-specific files. Those are
runtime artifacts, not required source files.

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
- DB prompt templates and in-code prompt builders add trigger-specific safety
  rules so the LLM receives grounded context at multiple layers.
- Clean-language broadcast instruction is always applied by `prompt_builder.py`.
- Commentary events are logged to SQLite and exported to `trigger_stream.json`
  even when audio is dropped because the queue is busy.

### Broadcast Audio

- Inworld TTS through `text_to_speech_integration.py`.
- Render-only Inworld expression markup from `tts_expression.py`.
- Render-only pronunciation substitutions from `pronunciation/global.md` via
  `tts_pronunciation.py`; logs, HUD text, and replay text stay unchanged.
- FMOD playback through `commentary_queue.py` with a single `_is_playing` gate:
  audio is reserved at enqueue time to prevent talk-over.
- Audio device selection is stored in
  `python/sector_said_exports/system_settings.json` and mirrored from Pit Wall
  config saves.
- `/api/audio/devices` performs a live FMOD enumeration instead of trusting stale
  cached device JSON.

### Live Broadcast HUD

The `/` route serves `python/sector_says_oversee/broadcast_hud_mockup.html`.
Despite the filename, this is the active live HUD.

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

The `/pitwall` route serves the control center in
`python/sector_says_oversee/pitwall_static/`.

Current admin/API surface includes:

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

### iRacing `/data` Enrichment

Optional iRacing `/data` credentials in `python/race_secrets.json` enable:

- Driver/member lookup and cached career context.
- Recent form and Focus Driver dossier enrichment.
- iRacing portrait URLs in driver profiles.
- Car library asset sync.

Auth is intentionally CAPTCHA-safe: one bad-auth sentinel is set, retries stop,
and a process restart is required to clear the lockout.

### Product Page

`/product` serves a public preview/overview page from:

- `python/sector_says_oversee/product.html`
- `python/sector_says_oversee/product.css`
- `python/sector_says_oversee/product.js`
- screenshots in `docs/product-page/screenshots/`

It uses `/api/product/summary` to show live archive stats when available.

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

### Main Tick Flow

1. `ir.freeze_var_buffer_latest()` runs once at the top of the tick.
2. `live_leaderboard(ir)` builds one enriched car snapshot from that frozen
   telemetry buffer.
3. Export JSON files are written under `python/sector_said_exports/`.
4. `check_triggers(ir, leaderboard)` evaluates flags, events, summaries, and
   DOTD scoring.
5. Collision/off-track trackers update.
6. `lap_sample_capture.capture()` records start/finish crossings for replay.
7. The loop sleeps for the configured tick interval.

### Race Start Flow

The engine waits until the session is both:

- an iRacing Race session, and
- `SessionState == 4` racing state.

At race start it:

1. Upgrades the starting grid from `QualifyResultsInfo` when available.
2. Falls back to a settled live car snapshot only if quali grid data is missing.
3. Refreshes each car's `GridPosition` before DB insertion.
4. Calls `race_db.start_race()`.
5. Resets lap-sample capture.
6. Starts the race intro commentary.

This keeps DB grid position, in-memory grid, and `starting_grid.json` aligned.

## Web Routes

| Route | Purpose |
| --- | --- |
| `/` | Live broadcast HUD |
| `/product` | Product/preview page |
| `/pitwall` | Pit Wall admin dashboard |
| `/replays` | Replay picker |
| `/replay/<race_id>` | Replay viewer |
| `/sector_said_exports/<file>` | No-cache JSON/JSONL export feed |
| `/api/replay/<race_id>` | Replay payload |
| `/api/replay/<race_id>/report` | Cached/generated AI race report |
| `/api/replay/<race_id>/timeline` | Replay timeline events |
| `/api/config/*` | Race/Pit Wall config |
| `/api/prompts/*` | Prompt template management |
| `/api/tracks/*` | Track library |
| `/api/cars/*` | Car library |
| `/api/media/*` | Media gallery and tagging |
| `/api/history/*` | Race and driver archives |
| `/api/db/*` | DB explorer |
| `/api/broadcast/*` | Engine process controls and sound check |
| `/api/audio/devices` | Live audio device enumeration |
| `/api/product/summary` | Product page archive summary |

By default the Flask server binds to `127.0.0.1:8080`.

To expose read-only HUD/replay/product surfaces on the LAN:

```bat
set SSW_HOST=0.0.0.0
set SSW_PORT=8080
run_dashboard.bat
```

Sensitive Pit Wall and write API routes are only available from loopback unless
`SSW_ADMIN_TOKEN` is set and supplied as `X-SSW-Admin-Token` or `admin_token`.
Do not expose this Flask server directly to the public internet.

## Live Data Exports

The engine writes JSON/JSONL files into `python/sector_said_exports/`.
The HUD polls these files and Flask serves them with no-cache headers.

| File | Purpose |
| --- | --- |
| `leaderboard.json` | Live enriched car list |
| `broadcast_state.json` | Session status, lap, flags, car count |
| `session_info.json` | Session type/progress/remaining data |
| `weekend_info.json` | Track and weather metadata |
| `dotd_top3.json` | Top 3 DOTD with percentages |
| `focus_driver_of_the_day.json` | Focus Driver identity and DOTD context |
| `driver_profiles.json` | Driver colors, photos, owner flags, iR/license data |
| `commentator_images.json` | Presenter portraits |
| `audio_devices.json` | Last exported audio-device list |
| `system_settings.json` | Focus driver and system/audio preferences |
| `starting_grid.json` | Authoritative starting grid |
| `commentary_requests.json` | Dashboard -> engine request bridge |
| `trigger_stream.json` | Rolling commentary/event log |
| `unity_feed.jsonl` | Append-only external overlay feed |

Dashboard-to-engine actions use file IPC because Flask and the engine are
separate processes. The writer stores a monotonically increasing request id; the
engine polls and tracks the last consumed id. On startup, the cursor is seeded so
stale requests do not fire.

## Database

SQLite database: `python/race_history.db`.

Core tables:

| Table | Purpose |
| --- | --- |
| `races` | One row per race, including winner, DOTD, weather, report |
| `race_drivers` | Per-driver race result, grid/finish, iR/license, incidents |
| `commentary_log` | Every generated commentary line |
| `incidents` | Collision/off-track events |
| `race_pit_stops` | Pit entry/exit records |
| `race_lap_samples` | Per-car lap crossing samples for replay timing |
| `driver_career` | Aggregated career stats |
| `prompt_templates` | Editable LLM prompt templates |
| `media_assets` | Media gallery metadata and tags |
| `iracing_cache` | Cached `/data` API responses |

## Project Structure

```text
Sector_Says_What/
|-- README.md                         This file
|-- requirements.txt                  Python dependencies
|-- install.bat                       Windows setup helper
|-- run.bat                           Engine + dashboard launcher
|-- run_dashboard.bat                 Dashboard-only launcher
|-- run_hidden.vbs                    Hidden desktop-launcher helper
|-- telemetry_11_23_15.pdf            iRacing telemetry reference
|-- pronunciation/
|   `-- global.md                     TTS pronunciation substitutions
|-- docs/
|   |-- product-page/screenshots/     Product-page screenshots
|   `-- dev-mockups/                  Retired/design mockups
|-- audio/
|   `-- fmod/                         FMOD project/assets
|-- graphics/                         Broadcast graphics
`-- python/
    |-- sector_says_engine.py         Main race engine
    |-- sector_says_utilities.py      SDK helpers, flags, voice number formatters
    |-- live_leaderboard.py           Per-tick car snapshot/enrichment
    |-- race_triggers.py              Trigger orchestrator and race gates
    |-- triggers_realtime.py          Real-time event triggers
    |-- triggers_summary.py           Periodic/fractional summary triggers
    |-- commentary_phrases.py         Prompt text and race prediction framework
    |-- prompt_builder.py             DB prompt rendering and clean-language rule
    |-- openai_commentary.py          OpenAI/local-LLM client and ground rules
    |-- commentary_queue.py           FMOD playback and commentary logging
    |-- text_to_speech_integration.py Inworld TTS integration
    |-- tts_expression.py             Render-only audio markup
    |-- tts_pronunciation.py          Render-only pronunciation pass
    |-- sector_says_dotd.py           Driver of the Day scoring
    |-- race_data_store.py            Grid, sectors, session state
    |-- lap_sample_capture.py         Replay timing sample capture
    |-- race_db.py                    SQLite schema and data access
    |-- race_report.py                AI race report generator
    |-- collision_tracker.py          Contact detection
    |-- offtrack_tracker.py           Off-track/spin detection
    |-- iracing_api_adapter.py        Optional iRacing `/data` API wrapper
    |-- iracing_api_warmer.py         Grid enrichment warmer
    |-- car_library.py                Car metadata library
    |-- track_library.py              Track centerline library
    |-- driver_profiles.py            Driver profile/color/photo exports
    |-- import_iracing_eventresult.py Historical iRacing eventresult importer
    |-- import_grumpyrobot_race.py    GrumpyRobot-compatible importer
    |-- telemetry_diagnostic.py       Telemetry verification tool
    |-- mock_ir.py                    Mock SDK support
    |-- race_config.example.json      Example non-secret config
    |-- race_secrets.example.json     Example secret config
    |-- race_config.json              Local config, gitignored in normal use
    |-- race_secrets.json             Local secrets, gitignored in normal use
    |-- sector_said_exports/          Live JSON/JSONL export feed
    |-- sector_says_oversee/          Flask app and browser UI
    |   |-- app.py                    Flask app, public routes, access gate
    |   |-- pitwall.py                Pit Wall blueprint and API routes
    |   |-- broadcast_hud_mockup.html Active live HUD
    |   |-- hud_v2.css                Shared HUD/replay stylesheet
    |   |-- replay.html               Replay viewer shell
    |   |-- replay.js                 Replay animation/runtime
    |   |-- replay_list.html          Replay picker shell
    |   |-- replay_list.js            Replay picker runtime
    |   |-- product.html              Product/preview page
    |   |-- product.css
    |   |-- product.js
    |   |-- tracks/                   Per-track centerline JSON
    |   |-- cars/                     Per-car metadata JSON
    |   |-- backgrounds/              HUD/replay/product backgrounds
    |   |-- studio/                   Studio side-panel assets
    |   |-- commentators/             Presenter portraits
    |   |-- drivers/                  Driver profile images
    |   `-- pitwall_static/           Admin UI JS/CSS/HTML
    `-- racing_replay_standalone/     Standalone replay prototype
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

## Configuration

Most settings can be edited in Pit Wall. Important files:

| File | Purpose |
| --- | --- |
| `python/race_config.json` | Main non-secret config |
| `python/race_config.example.json` | Bootstrap/default example |
| `python/race_secrets.json` | Optional iRacing `/data` credentials |
| `python/sector_said_exports/system_settings.json` | Runtime focus/audio preferences |
| `python/sector_said_exports/focus_driver_of_the_day.json` | Current Focus Driver |
| `pronunciation/global.md` | TTS pronunciation substitutions |

Important config sections:

- `power_unit.provider`: `openai` or `local_llm`.
- `power_unit.openai`: OpenAI model, token limit, and temperature.
- `power_unit.local_llm`: OpenAI-compatible local endpoint settings.
- `power_unit.inworld`: TTS URL, voice map, and markup behavior.
- `owner_drivers`: Focus/owner driver identity and profile metadata.
- `system`: audio output and TTS enablement.
- `dotd`: Driver of the Day scoring weights and disabled triggers.

Config section updates deep-merge by default. UI pages that represent a complete
section state must save with `?replace=true` so deleted keys, unchecked flags, and
sliders moved to zero actually persist.

## Importing Historical Races

Import an iRacing eventresult JSON:

```bat
python python\import_iracing_eventresult.py path\to\eventresult-<subsession>.json
```

Import from a GrumpyRobot-compatible service:

```bat
python python\import_grumpyrobot_race.py <race_id> --host http://localhost:5000
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

## Critical Invariants

These are not style preferences. Breaking them has caused real broadcast failures.

### One Telemetry Freeze Per Tick

`ir.freeze_var_buffer_latest()` belongs at the top of the engine loop tick.
Helpers must read from the already-frozen buffer. Do not add freeze calls inside
leaderboard helpers, trigger helpers, export helpers, or session-var wrappers.

### Race Commentary Gates

Commentary is blocked by:

1. The engine pre-green wait loop.
2. The early race-session check inside `check_triggers()`.
3. The `_race_finished` post-checkered gate.

The pre-race show is the limited exception and is explicitly one-shot.

### White-Flag Lull

After the white flag, rolling triggers are silenced until checkered. The intended
closing sequence is:

1. White-flag commentary.
2. Focus Driver final-lap summary once audio is idle.
3. Silence from rolling triggers.
4. Checkered-flag podium/DOTD recap.
5. Race-finished gate closes the rest of the broadcast.

### Pit Detection

Pit detection uses `CarIdxOnPitRoad` edge detection. Do not infer pit stops from
`LivePos` or track surface.

### Overtake Detection

Overtakes use total distance (`Lap + LapDistPct`) with confirmation for one-place
changes. Do not use raw `RacePos` jitter as the trigger source.

### Replay Timing

`lap_sample_capture.capture()` runs once per main race tick and writes
`race_lap_samples`. Do not call it from helpers and do not write sample rows
directly from triggers.

### AI Anti-Fabrication Stack

Never weaken these layers:

1. `SYSTEM_GROUND_RULES` in `openai_commentary.py`.
2. DB prompt templates.
3. In-code prompt builders/sanitizers.

When the data does not support a claim, the correct commentary is less specific.

## Editing Map

Common "wrong file" traps:

| Change | Edit this | Not this |
| --- | --- | --- |
| Live HUD | `broadcast_hud_mockup.html` | `index.html`, `dashboard.js` |
| Replay CSS | `hud_v2.css` | `dashboard.css` |
| Replay runtime | `replay.js` | legacy mockups |
| Replay layout | `replay.html` and `hud_v2.css` | inline SVG aspect-ratio hacks |
| DOTD on live HUD | `_updateDOTD` in `broadcast_hud_mockup.html` | stale `dashboard.js` |
| Finish cards | shared classes in `hud_v2.css` plus HUD/replay builders | one-off CSS only |
| Pit Wall tabs | `pitwall_static/pitwall-*.js` | live HUD files |
| Track centerline editor | `pitwall_static/track_editor.*` | replay-only files |
| Product page | `product.html`, `product.css`, `product.js` | HUD/replay files |

## Design Notes

- Theme: emerald `#2ecc71` and gold `#D4AF37`.
- Red is reserved for warnings, destructive actions, and negative scores.
- Main fonts: Titillium Web for body, Audiowide for big titles.
- HUD panes use glass-style overlays.
- UI copy uses Formula 1-inspired naming: Pit Wall, Race Room, Race Control,
  Stewards' Office, Marshalling, Power Unit, Race Archives, Garage, Owner Driver.

## Credits

- Lead and concept: ABomb / BuzzRacing.
- Telemetry: iRacing SDK via `pyirsdk`.
- Commentary AI: OpenAI-compatible LLMs.
- TTS: Inworld.
- Audio pipeline: FMOD.
- Engineering assistance: AI coding tools plus a lot of real race-session
  debugging. The glamorous bit where one bad grid read becomes three days of
  archaeology.

## Motto

Sector Says What: every corner has a story, and the story has to be true.
