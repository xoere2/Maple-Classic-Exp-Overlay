# Changelog

## 0.9.6-farm.1 (Farm Analytics fork)

Fork of 0.9.6-beta (plus upstream's unreleased changes up to 1869c60). See FORK.md.

**New**
- **Real EXP/hr** is the big number: idle time (waiting for respawns, crowded maps) counts; Pause and AFK don't. Active EXP/hr (the original rule) is shown next to it. The inactivity rule can be changed or turned off.
- Last 1, 5 and 15 minutes; EXP per map and solo vs party; deaths recorded but never subtracted.
- **AFK** button and Shift+5 hotkey.
- **Sessions** saved with all their raw observations in `farm.db` (SQLite); a new session per map (optional). Original log lines are imported read-only.
- **Mesos accounting without double counting**: realized net, pickups, purchases/sales/storage/transfer notes, consumables used, stock change, loot value, operating profit, estimated total. Big unexplained wallet changes are flagged.
- **HP and MP** read from the status bar: MP spent, casts (e.g. Lucky Seven at 16 MP), potions drunk (estimated), damage taken.
- **Prices, skills and monster info** you can edit (CSV import); inventory snapshots; session tags (attack mode, weapon, monster).
- **Analytics window**: session details with every metric's kind and confidence, raw observations, comparison, rankings (incl. best EXP/hr without losing mesos), map efficiency, charts, drop rates with 95% intervals, CSV/JSON export, weapon what-if.
- **Recorded video analysis** (OBS recordings), on your PC.
- **EXPERIMENTAL monster vision** (off by default): kills with confidence labels (confirmed / probable / party credit / unattributed), observed respawns in a selected region, waves, attacks per kill.
- Command line: `py -3 -m farmkit`.

**Changed**
- Updates and bug reports point to this fork. The build keeps OpenCV's video reader (for recordings).

**Unchanged**
- Everything the original does, its log format and seals, and its opt-in sharing (no new fields shared).

## Not released yet

**New**
- **One log line per party**: playing a map in a party, people join and leave, and each party size used to get its own line. Now a map's party sizes on a day share one line (e.g. "4–5ppl ▸"), with EXP/hr and mesos/hr over all of that time. Click it to see a line for each party size, to compare them. Solo play keeps its own line. Your log itself isn't changed, only how it's shown.

**Safer**
- Downloads are now built by GitHub from this code, never on a PC, from exact package files checked against their SHA-256. Each one gets an attestation anyone can check (`gh attestation verify`), and its SHA-256 is on the release page. See "Where the download comes from" in the README.
- Built with Pillow 12.3, which fixes the problems published since 11.3. (The overlay never opens image files from elsewhere, so it wasn't affected, but now nothing is flagged.)
- It never runs a `tesseract.exe` from the folder it happens to be started from, only its own or an installed one.

**The best-maps website**
- Security headers: browsers only run the site's own scripts, and other sites can't show it inside theirs.
- Map names that are web addresses are turned down, and early data only shows map names at least 2 players have shared, so one person can't put a made-up name on the site.
- Much lighter on its database (small summary tables, and answers shared between Cloudflare's locations), so it keeps working as more people use it.
- Every player is a dot on a chart: each map's row shows what every player gets there, the typical EXP/hr, what the middle half of players get, and the runs left out as unusual. Point at (or tap) anything for the numbers.
- Maps with fewer than 3 players are shown greyed out as early data, instead of "No maps here yet".
- A bar at the top shows the latest runs players shared, one at a time; click one to see the best maps for that level, class and party. One run per player, only maps at least 2 players have shared, and nothing unusual, so made-up runs stay off the front page.

## 0.9.6-beta

**New**
- **Compact mode** (the Compact button at the top, or Shift+4): a much smaller window with just EXP/hr, when you'll level, the last 5 minutes, mesos/hr and Start/Pause. The button then says Full, to bring everything back.
- **Hotkeys**, while MapleStory or the overlay is in front: Shift+1 Start / Pause, Shift+2 Reset, Shift+3 Don't log, Shift+4 Compact. You can change them, or turn them off, in Settings.
- **Size** in Settings: makes the text and the window smaller or bigger, from 80% to 200%, and changes right away. It stops at 80%, so it can't get too small to read or use.

**Fixed**
- On a second screen with different Windows scaling (e.g. a 1440p screen at 125% next to a 5K one at 200%), the window was far too big. It's now sized for the screen it's on, and resizes when moved.
- The Settings window could stay at its old size after the size changed, cutting its contents off. It now always fits, and opens over the overlay (on the same screen).

## 0.9.5-beta

**Fixed**
- Dying (losing a few % of your EXP) made EXP gained and EXP/hr go down, even below zero. Now the loss isn't counted: it says how much you lost and carries on.
- A run the best-maps site turned down was sent again every 30 minutes, and enough of them could stop newer runs from being shared.
- A log file edited by hand (or damaged) could stop the overlay from starting. Lines it can't use are now left out (a copy of the file is kept), and it starts as usual.

**The best-maps website** (already live)
- Checks every run harder: no more EXP than the levels it covers hold, and start times in a sensible range.
- Only 6 players can share from one internet connection a day, so it's much harder to make up players.
- Other websites can't make their visitors' browsers send runs.
- The code kept for spam limits can no longer be turned back into an internet address.

## 0.9.4-beta

**New**
- **Best maps** link at the top of the overlay: opens the best-maps website already showing the maps for your level, party size and class.
- **Runs for the best-maps site are 15 minutes** instead of an hour, so more of your farming counts (a break or a new map no longer throws away up to an hour).
- **Smaller download:** 82 MB instead of 98 MB. Parts of the libraries it never used are left out.
- **The best-maps website** fills in sooner: it now looks 5 levels either way, and counts parties of any size together (solo or party).
- Downloads can now be built on GitHub straight from the code, the first step toward code signing (so Windows stops warning about new downloads).

## 0.9.3-beta

**New**
- **New name: Maple Classic Exp Overlay** (it was Maple EXP/hr). The program is now `Maple Classic Exp Overlay.exe`. Your log and settings carry over.
- **Class**: read from the status bar (never your character's name), shown in the log, and different classes get separate lines.
- **1-hour runs**: the overlay counts toward a full hour of non-stop farming on one map with the same party size, and saves each finished hour (these are what the best-maps site uses). A level-up doesn't stop a run; a break, a new map or a new party size starts it over.
- **Best maps for your class**: if you agree (it asks once), finished 1-hour runs are shared anonymously with the new stats site, maple-classic-exp-overlay.pages.dev, which shows the best maps for each class and path (named after its 4th job, like Night Lord or Bishop), level and party size. You can change this in Settings.
- **Sealed log**: every line the overlay writes is sealed, so lines edited or added by hand are greyed out, and an edited 1-hour run is never shared. Deleting a line, or renaming a map in the overlay, keeps everything else trusted.

**Fixed**
- The update window showed names with underscores wrong (e.g. explog.txt).

## 0.9.2-beta

**New**
- **Time** in the log: how long each line is based on, growing by 5 minutes with every update. Also in copied lines and `exp_log.txt`.
- **Report a bug** button (in Settings and in View capture): opens a bug report on GitHub, already filled in with what the overlay was reading, plus a picture to drag in. Nothing is sent until you click Submit.
- **Reset** now also looks for the minimap, inventory and party list again, so it picks them up after you move them.

**Fixed**
- The map name wasn't read when the overlay's window was right next to the minimap (but not covering it). The same is fixed for the party list.

## 0.9.1-beta

**New**
- **Pauses by itself** when you get no EXP for 15 seconds (you can change this in Settings) or leave the map, and carries on at your next EXP gain. Paused time doesn't count toward EXP/hr. On a new map, the numbers start from zero.
- **Don't log** button: keeps the current run out of the log while still showing everything. It turns itself off when the run ends.
- **Update check:** tells you when a new version is out and what's new, so you can decide whether to update. You can turn it off, or check by hand, in Settings.
- Your level shows within seconds of opening, and the time to your next level sits right under EXP/hr, like "Lv 31 in 36 min".
- The log's columns can be resized, and the window can be made bigger while the log is open. Both are remembered.

**Fixed**
- In a 1920x1080 window, the EXP number could be read with its first digits cut off.
- Mesos weren't read when the overlay's window was close to (but not covering) the inventory, or when a see-through inventory was in front of a busy background.

**More reliable**
- Much stronger protection against misread numbers, tested with thousands of simulated misreads and screenshots over many backgrounds: a misread can't pass for a level-up, a lost level, or extra mesos.
- Clearer status messages. "Select area" and "Follow game" are now one button.
- Safer saving: a damaged log file is backed up before anything is saved over it, and damaged settings fall back to the defaults.

## 0.9.0-beta

First public beta.

- Shows your EXP per hour, % per hour, EXP gained, and time to your next level.
- Finds the MapleStory window by itself, at any window size, windowed or fullscreen.
- Reads your mesos from the inventory, even while it's see-through during fights, and shows mesos per hour and per 5 minutes.
- Notices whether you're solo or in a party, and how many people are in it.
- Reads the map name from the minimap.
- Keeps a log of your EXP/hr and mesos/hr for each day, map, level and party size, updated every 5 minutes of non-stop farming.
- Settings for the break length, window transparency and always-on-top.
- Saves your settings and log in `%APPDATA%\MapleEXP`, and writes problems to `errors.log` there.
