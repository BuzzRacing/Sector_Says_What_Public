┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                                                                              ┃
┃                           ╔═ SECTOR  SAYS  WHAT ═╗                           ┃
┃                                                                              ┃
┃                        Live AI Commentary System                             ┃
┃          Pitwall Dashboard • Driver of the Day Analytics • Race Insights     ┃
┃                                                                              ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃                  A Buzz Racing Motorsport Intelligence Project               ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

# Sector Says What — Overview & Acknowledgements
**Author:** BuzzRacing / ABomb  
**Project:** Sector Says What — AI-Driven Live Motorsport Commentary System

## What’s in this repo

### `/sector_says_oversee/`
Auto-refresh **Pitwall Dashboard** (Flask + HTML/JS).  
Includes:
- Timing Tower with live deltas, gaps & flags  
- Full slide‑out leaderboard  
- Right‑side settings panel (Focus Driver selector)
- Dynamic commentator portraits (Studio / Paddock / Pitlane)  
- Background rotation (trackside, paddock, studio, fanzone)  
- DOTD breakdown + percentages  

### `/sector_said_exports/`
JSON exports consumed by Dashboard + Unity overlays:
- `leaderboard.json`
- `timingtower.json`
- `dotd_top3.json`
- `weekend_info.json`
- `session_info.json`
- `current_commentator.json`
- `commentator_images.json`

### Core Engine
Modules controlling:
- Telemetry ingestion  
- Commentary triggers  
- Fractional progress (0.15 / 0.45 / 0.70)  
- Focus Driver of the Day logic  
- DOTD scoring & ranking  
- Collision & off‑track tracking  
- FMOD + Inworld TTS hooks

## Notes
- Active commentator → `sector_said_exports/current_commentator.json`  
- All triggers stream to → `sector_said_exports/trigger_stream.json`  
- DOTD Top 3 & percentages → `sector_said_exports/dotd_top3.json`  
- Backgrounds & commentator portraits follow the emerald‑green + gold theme  
- Vision‑OS‑style HUD panes for overlays  

## Acknowledgements
- **Lead & Concept:** ABomb  
- **Branding & Aesthetic:** Emerald‑green & gold F1‑inspired identity  
- **Audio Pipeline:** FMOD + Inworld TTS integration  
- **Engineering:** Live rotation logic, DOTD system, HUD data pipeline  
- **Testers:** Everyone helping validate timing, rotation, triggers & UI flows  


 █▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚ SECTOR  SAYS  WHAT ▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚▚█
 
            The Official AI Race Commentary Platform               
 Pitwall Dashboard • Driver of the Day Analytics • Sector-by-Sector Insight       
