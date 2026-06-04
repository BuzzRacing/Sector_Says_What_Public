# Pronunciation Overrides - Sector Says What

Render-time text-to-speech substitutions. Commentary text in the HUD, replay,
database, and logs stays unchanged. This file only changes the text sent to
Inworld for audio generation.

## Format

Each entry: `printed_form | spoken_form | scope | notes`

`scope` is `all` for every commentator, or a comma-separated list of Inworld
voice IDs such as `Ronald,Deborah`.

## Broadcast phrases

- `we are live` | `we are lyve` | all | Inworld sometimes says the broadcast adjective as "life"
- `live at` | `lyve at` | all | Broadcast location phrase
- `live from` | `lyve from` | all | Broadcast location phrase
- `live in` | `lyve in` | all | Broadcast location phrase
- `live on` | `lyve on` | all | Broadcast location phrase
- `broadcast live` | `broadcast lyve` | all | Broadcast status phrase

## Broadcast words

- `iRacing` | `eye-racing` | all | Spell the product name naturally
- `iRating` | `eye-rating` | all | iRacing rating term
- `DOTD` | `Driver of the Day` | all | Avoid reading it as a word
- `SF23` | `S-F twenty three` | all | Dallara chassis shorthand
- `CarIdx` | `car index` | all | Internal iRacing field name

## Car and track names

- `Dallara` | `duh-LAR-uh` | all | Chassis manufacturer
- `Hungaroring` | `HUN-ga-ro-ring` | all | Track name
- `Hockenheimring` | `HOCK-en-hime-ring` | all | Track name
- `Mugello` | `moo-JELL-oh` | all | Track name
- `Interlagos` | `in-ter-LAH-gos` | all | Track name
- `Autodromo Jose Carlos Pace` | `ow-toh-DROH-moh zho-ZAY CAR-los PAH-say` | all | Interlagos official name
- `Algarve` | `al-GARV` | all | Track name
