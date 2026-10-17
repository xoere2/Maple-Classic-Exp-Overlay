# Using the farm analytics

## Install

Same as the original overlay (see the README): download the zip from this fork's
**Releases** page, right-click → **Extract All**, and run **Maple Classic Exp Overlay.exe**.
The fork builds are made by GitHub Actions from this source. Check one with
`gh attestation verify <zip> --repo xoere2/Maple-Classic-Exp-Overlay`.

Your existing log and settings (`%APPDATA%\MapleEXP`) carry over. The fork adds
`farm.db` next to them. Close the original overlay first: only one can run at a time.

From source: `py -3 -m pip install -r requirements.txt`, then `run.bat`.

## The overlay

- **Big number = real EXP/hr.** Waiting for respawns, crowded maps and looting count.
  Pause and AFK don't. The row **Active EXP/hr** is the original overlay's idea (idle time
  left out). Swap them in ⚙ → **Farm settings** → Big number.
- **Net mesos / hr**: operating profit by default (pickups + loot sold − consumables used).
  Change it in Farm settings → "Net mesos means".
- **More ▾** shows map · party, session time · idle %, last 1 / 15 min, pickups, potion cost,
  loot value, kills, respawns and HP · MP. **Compact** shows EXP/hr, time to level, last
  5 min, net mesos/hr and kills/hr (when monster vision is on).
- **AFK** (or **Shift+5**): marks you as away; that time isn't real farming time.
- **+ Note**: anything the wallet can't explain on its own: bought potions, recharged stars,
  sold ore, put mesos in storage, mesos to/from a mule, items used or picked up (counted by
  hand). The wallet already shows purchases, so notes never get subtracted twice.
- **Inventory snapshot** (in + Note): type in counts of the items you care about at the
  **start** and **end**. Loot gained and potions used then come out exactly.
- **Tags**: attack mode (Basic attack / Lucky Seven / Mixed), weapon ("26 att claw"),
  monster farmed, a note. They stay on for the next sessions until you change them.
- **Analytics**: the big window (below).

A session runs from **Start** to **Reset** (or closing the overlay). By default a new map
starts a new session, so maps compare one to one (Farm settings).

## Farm settings (⚙ → Farm settings)

Big number (real/active) · auto-pause of the active clock on/off (the pause time is in ⚙) ·
new session per map · read HP/MP · your MP potion, HP potion and main skill (for potion
and cast estimates) · what "net mesos" means · **EXPERIMENTAL monster vision**: on/off,
looks per second (1–10, default 3) and **Select region** (drag a box over the platform
you farm, in the game window).

## The Analytics window

| Tab | What's there |
|---|---|
| Sessions | Every session (● = going on). Select one for every metric with its kind, confidence and how it was worked out. Ctrl+click several to compare. **Notes & raw events** shows every observation (and deletes your own notes). **Tag...**, **Delete**, **Export CSV/JSON** |
| Compare | Side-by-side table: map, level, party, tags, real time, idle share, real/active EXP/hr, kills, respawns, MP, potions, realized/pickups/operating/loot/estimated mesos per hour |
| Rankings | The five rankings, with a minimum length and the profit basis. Shows what was left out and why. Map efficiency: all sessions per map and solo/party added up |
| Charts | EXP over real minutes (flat stretches = no EXP) and wallet change, for the selected sessions |
| Prices & notes | Your prices (mesos each, kind, HP/MP restored), CSV import (`item,price,kind,restore_hp,restore_mp`), skills and their MP costs. Only the Blue Potion (200 MP, 220 mesos) and Lucky Seven (16 MP) are filled in, from the numbers you gave. Everything else starts unpriced |
| Monsters | Monster info (EXP, HP, defence: yours to fill in), **sprites** for vision (Add sprite from screen: drag a tight box around a monster), and **drop rates** with 95% intervals |
| Recorded video | Analyse a recording (below) |
| Weapon what-if | Basic attack vs Lucky Seven, now vs after an ATT change. Theory: pre-Big-Bang formulas, not verified for Classic World |

## Recorded video (OBS)

Record the whole game window (the bottom status bar must be visible; keep the inventory
open if you want mesos). In Analytics → **Recorded video**, pick the file. Tick the monster
box to count kills/respawns, optionally limited to a region `x,y,w,h` in video pixels.
Analysis runs on your PC, faster than real time on the tests (60 s in ~42 s with vision
on), and adds a **video** session. From a terminal:
`py -3 -m farmkit analyze-video farming.mp4 --vision --roi 0,0,1280,400 --tag attack_mode="Lucky Seven"`.

## Answering the five questions

1. **Basic attacks vs Lucky Seven on Jr. Neckis.** Tag a session "Basic attack", farm 20–30
   min. Tag the next "Lucky Seven", same map, same party size. Note potion purchases (or
   take start/end snapshots). Compare: real EXP/hr, operating profit/hr, potion cost/hr,
   MP per kill, EXP per MP. Rankings → "Best EXP/hour without losing mesos" picks the winner.
2. **Jr. Wraiths vs Jr. Neckis with Mokbi.** Price Mokbi Throwing-Stars. Note or snapshot
   any Mokbi drops. Compare estimated total profit/hr (includes unsold loot at your price)
   with operating profit/hr (doesn't). Monsters → Drop rate gives the observed rate and its
   (wide) interval. Expect the interval to be wide: one drop in a few thousand kills
   proves little.
3. **Damage or monsters?** Look at idle share (Session · idle), real vs active EXP/hr,
   and with monster vision on a platform: visible monsters on average, empty share, wave
   interval, deaths per wave and competition. Real ≪ active with lots of empty frames
   means monster supply is the limit. Little idle with many visible monsters means damage is.
4. **The level 40 claw.** Tag sessions with the weapon ("26 att claw" / the new one) and
   compare like with like (map, party size, level). The what-if gives a theoretical
   expectation first.
5. **Best EXP/hr without losing mesos.** Rankings → "Best EXP/hour without losing mesos",
   basis *operating* (or *realized* for cash only, *estimated* to count unsold loot).

## Privacy

Everything stays on your PC. Nothing new is uploaded: not screenshots, video frames,
names, inventories, notes or sessions. The original opt-in run sharing is unchanged, and
none of the fork's data is added to it. Exports are files you choose to save.
