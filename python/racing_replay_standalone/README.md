# Racing Replay — Standalone

A self-contained, browser-based race replay visualizer extracted from the
GrumpyRobot iRacing race-story page (section S5b). Loads a JSON file describing
a race and animates it on a smoothed track centerline with an F1-style timing
tower, position reordering, intervals, and a 30s–3min replay speed slider.

No build step. No dependencies. One HTML file + one JSON file.

## Files

```
racing_replay_standalone/
├── index.html        ← the entire app (HTML + CSS + JS, ~600 lines)
├── sample_data.json  ← example race data with 12 drivers
└── README.md         ← this file
```

## Install / Setup

1. Unzip anywhere.
2. Serve the folder over a local HTTP server (the auto-load of `sample_data.json`
   uses `fetch`, which most browsers block under `file://`):

   ```bash
   cd racing_replay_standalone
   python -m http.server 8000
   ```

3. Open <http://localhost:8000/> in a browser. The sample race auto-loads.
4. Click **▶ Start Race** to play. Drag the slider to change replay duration.
5. To load your own race, use the **Load Race JSON** file picker at the top.

You can also open `index.html` directly via `file://` if you only use the file
picker (skipping the auto-load).

## JSON Schema

```jsonc
{
  "track_name": "Road Atlanta",
  "track_config": "Full Course",     // optional subtitle
  "total_laps": 12,
  "raceline_path": "M..L..Z",         // optional — overrides the built-in track outline
  "drivers": [
    {
      "name": "First Last",
      "car_number": "42",
      "race_start_pos": 0,            // 0-indexed grid slot
      "race_finish_pos": 0,           // 0-indexed final position
      "race_avg_lap": 825000,         // average lap in 1/10000ths of a second (iRacing units)
      "race_laps": 12,                // laps completed
      "reason_out": "Running",        // "Running" = finished; anything else = DNF
      "is_us": false,                 // true highlights this driver in orange
      "livery": { "color1": "ff9100" } // hex color, no '#'
    }
  ]
}
```

### Notes

- `race_avg_lap` is in iRacing's native units (1 unit = 0.0001s). 825000 ≈ 82.5s.
  If your data is in seconds, multiply by 10000.
- The leader is determined dynamically per frame from cumulative distance.
- Intervals are simulated from `race_avg_lap` deltas, not from per-lap telemetry.
- Exactly one driver should have `is_us: true` (the highlighted car).
- DNFs fade out after they complete their final lap.

## Track Outline (Raceline)

The built-in `<path id="raceline">` in `index.html` is a Road Atlanta-shaped
outline tracing **both edges** of the road surface. The replay code:

1. Samples the outline from both ends simultaneously and averages opposite
   points to recover an approximate centerline.
2. Smooths it with 3 passes of Chaikin corner-cutting.
3. Resamples to 400 uniform points and builds a cumulative distance table.

To use a different track, either:

- Replace the `d=""` of `<path id="raceline">` directly in `index.html`, or
- Add `"raceline_path": "M.. .. Z"` to your JSON. The path should trace both
  edges of the road in one continuous closed loop.

The SVG viewBox is `0 60 1260 660`. Scale your path to fit, or change the
viewBox to match.

## Origin

Extracted from `web/grumpyrobot_web.py` (RACE_DETAIL_BLOCK template, section
S5b ANIMATED RACE PROGRESSION) on 2026-04-07. The original Jinja template was
converted to a vanilla-JS loader that consumes a JSON file directly instead of
a Flask `ev` context object.

## Roadmap ideas

- Per-lap telemetry input (real positions per lap instead of simulated intervals)
- Pit stop markers
- Sector colors / fastest lap highlighting
- Multiple track outlines bundled
- Export to video/gif
