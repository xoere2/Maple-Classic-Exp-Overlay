# Known limitations

## Tested, and how

| Area | Tested with |
|---|---|
| Upstream behaviour | Upstream's own tests (logic, screen) all pass. Two hotkey assertions were extended for the new AFK key |
| Real/active time, rolling rates, AFK, pause, deaths, splits | `test_farm.py`, synthetic event streams |
| Mesos accounting, double-counting cases, storage, transfers, sales, snapshots | `test_farm.py`, worked examples |
| HP/MP reading | The **real** status-bar frames shipped with upstream (4 readings at 3 UI sizes), noisy variants, and synthetic bars built from the game-font digit examples |
| Upstream log import | Every past upstream log version in `tests/upgrade/` (files left byte-for-byte untouched) |
| Monster vision | **Synthetic scenes only** (made-up sprites over textured backgrounds) |
| Recorded video | A synthetic MP4 (lossy, mp4v) with a game-font status bar, the real inventory and party crops, and synthetic monsters |
| The window | `test_ui.py` drives the real Tk window on a fake clock |
| The exe | Built locally with PyInstaller; launched, ran, created `farm.db`, no errors |

**Not tested against the live game or real gameplay footage.** No MapleStory client
was driven, and no real recording was available. HP/MP reading uses real frames, so it is
the most trustworthy new reader. Monster vision has only been checked on synthetic
scenes. Its accuracy on real maps is **unknown** until it's measured on recordings.

## Experimental (off by default, clearly labelled)

- **Monster vision** (kills, respawns, attacks per kill, drop-rate denominators).
  - Needs sprites you crop yourself. Without them the foreground detector counts anything
    that moves (players, pets, effects) and can't name species.
  - Sprite matching is per-pixel. Monster animations, hit flashes and other players'
    skill effects lower the scores. Add several sprites per monster.
  - The damage-number colours (orange/yellow) were not calibrated on Classic World footage.
    Hit counts are a lower bound: overlapping numbers merge.
  - Flying monsters (Stirge) crossing each other may swap tracks.
  - A monster hidden behind the player or an effect for over ~1.3 s (4 missed looks at
    3/s) counts as vanished and respawned.
  - Only the selected region is ever measured. Respawn rates are **never** map-wide.
- **Potion and cast counts from HP/MP** are estimates. Readings are ~1.5 s apart, so
  several potions in one reading are counted from the size of the rise. HP rises in a
  party may be a Cleric's Heal (labelled low confidence). Natural regeneration bigger than
  25 that fills to max would count as a potion.
- **Weapon what-if** uses community pre-Big-Bang formulas, not verified for Classic World.
  Monster HP/defence come from you.

## Not possible (and why)

- **Personal kills in a party from EXP.** Party EXP is shared, so EXP can't tell whose kill
  it was. The fork never reports it as yours ("party credit").
- **Map-wide respawn rates.** Only what's on screen can be observed.
- **Item recognition from inventory icons.** No item icon set ships with the game files
  the fork may use, and reading the game's files is out of bounds. Inventory counts are
  typed in (snapshots) instead.
- **Automatic market prices.** No legitimate public price API for Classic World was
  found. Scraping community sites was ruled out: at least one forbids automation. Prices
  are yours to set (CSV import works offline).
- **Kills per hour without vision.** Solo only, and only with the monster's EXP entered:
  "estimated kills from EXP".

## Other known gaps

- Mesos need the inventory open (as upstream). Without it there's no wallet data, and
  every mesos metric says so.
- HP/MP reading needs the overlay in "follow game" mode (not a selected area).
- Live vision captures the region from the game window's position. If the region is
  covered by the overlay itself, frames are skipped.
- A 4-hour session's live report takes ~110 ms (refreshed every ~5 s at that length). Very
  long sessions are better split.
- Legacy (upstream log) sessions only have active-time totals, so they're left out of
  real-time rankings.
- The fork shares upstream's opt-in stats site for 15-minute runs. Its version string
  (`0.9.6-farm.1`) identifies fork runs there.
- Not code-signed: Windows SmartScreen will warn, as it does for upstream before SignPath.
