<h1><img src="docs/icon.png" width="40" align="top"> Maple Classic Exp Overlay</h1>

> **Farm Analytics fork.** This is [xoere2/Maple-Classic-Exp-Overlay](https://github.com/xoere2/Maple-Classic-Exp-Overlay), a fork of [beanguri's original](https://github.com/beanguri/Maple-Classic-Exp-Overlay). It adds real vs active EXP/hr, mesos accounting without double counting, HP/MP, notes, an Analytics window, recorded-video analysis and experimental monster vision. **Start with [FORK.md](FORK.md).** The rest of this README is the original's and still applies.

**A free EXP tracker for MapleStory Classic World.** A small window sits next to the game and shows how fast you're leveling and making mesos, and the [best maps for your class](https://maple-classic-exp-overlay.pages.dev).

**[Download it here](../../releases/latest)** · [How to install](#how-to-install)

> **This is a beta.** It works, but you might run into rough edges. I'm actively improving it, so check back for updates. If something doesn't work, please [tell me](../../issues)!

![The overlay next to MapleStory, while farming](docs/screenshot-game.jpg)

## What it shows

![The overlay up close, with its log open](docs/screenshot-main.png)

- **EXP per hour**, for your whole session and for the last 5 minutes
- **How long until your next level**, right under your EXP/hr (for example "Lv 31 in 36 min")
- **Mesos per hour**, read from your inventory
- **A log of every map you farm**: your EXP/hr and mesos/hr there, at which level and class, solo or in a party

You don't type anything in. It reads all of this from your game screen by itself.

## How to install

You need Windows 10 or 11. It takes about 2 minutes, and you don't need to install anything else.

1. Go to the **[download page](../../releases/latest)**.
2. Under **Assets**, click the **Maple-Classic-Exp-Overlay-v….zip** file (for example `Maple-Classic-Exp-Overlay-v0.9.3-beta.zip`) to download it. Don't click "Source code": that's for programmers.
3. Find the zip in your Downloads folder. **Right-click it**, choose **Extract All...**, then click **Extract**. (Just double-clicking the zip isn't enough. It has to be extracted.)
4. Open the new folder, then the **Maple Classic Exp Overlay** folder inside it.
5. Double-click **Maple Classic Exp Overlay.exe**. It may show as just **Maple Classic Exp Overlay**, with a gold chart icon.

**Windows says "Windows protected your PC"?** Click **More info**, then **Run anyway**. Windows shows this for any new program that isn't from a big company.

**Keep the whole folder together.** The program needs the files next to it, so don't drag it out of its folder. To put it on your desktop, right-click **Maple Classic Exp Overlay.exe** and choose **Send to**, then **Desktop (create shortcut)**. On Windows 11, **Send to** is under **Show more options**.

## How to use it

1. **Start MapleStory.** Windowed, borderless or fullscreen all work, at any size.
2. **Open Maple Classic Exp Overlay.** It finds the game window by itself.
3. **Move the overlay so it doesn't cover these parts of the game:**
   - the minimap (top left)
   - the EXP bar (bottom)
   - the mesos at the bottom of your inventory
   - the party list

   The overlay reads them from your screen, so it can't see through itself. If it's in the way, it tells you at the bottom of its window. It remembers where you put it.
4. **Press Start** and play normally. You only need to press it once:
   - It **pauses by itself** when you get no EXP for 15 seconds or leave the map, and carries on at your next EXP gain. Paused time doesn't count, so EXP/hr only covers your farming.
   - On a different map, the numbers start from zero for that map.
5. **Want mesos tracked?** Keep your inventory open, on any tab.

Within a few seconds it shows your level and how much EXP you have. The per-hour numbers fill in once you've gained a little EXP.

| Button | What it does |
|---|---|
| **Start / Pause / Resume** | Starts tracking. **Pause** stops it until you press **Resume**. |
| **Reset** | Starts a new session from zero, and looks for the minimap, inventory and party list again (handy after moving them around) |
| **Don't log** | Keeps the current run out of the log, for example while you're testing a map or waiting for your party. Everything is still shown on screen. It turns itself off when you leave the map or stop getting EXP, so your next run is logged as usual. |
| **Select area** | Lets you drag a box around the EXP numbers, if it ever can't find them. The button then turns into **Follow game**, which goes back to finding them by itself. |
| **View capture** | Shows exactly what it read from your screen |
| **Best maps** | Opens the [best-maps website](#best-maps-for-your-class), already showing maps for your level, party size and class |
| **⚙** | Settings |
| **>** | Opens the log |
| **Compact** | A much smaller window with just EXP/hr, when you'll level, the last 5 minutes, mesos/hr and Start/Pause. The button then says **Full**, to bring everything back. Shift+4 or double-clicking the big number does the same. |

## Hotkeys

They work while MapleStory (or the overlay) is the window in front, so you don't have to click away from the game:

| Keys | What they do |
|---|---|
| **Shift+1** | Start / Pause |
| **Shift+2** | Reset |
| **Shift+3** | Don't log |
| **Shift+4** | Compact / Full |

The status line says what happened, for example "Shift+1: paused." While the hotkeys are on, those keys go to the overlay only, so Shift+1 won't type **!** in the game's chat. If that gets in your way, change them (or turn them off) in ⚙: click one, then press the keys you want. Backspace turns one off.

## The log

Click **>** at the top right of the overlay to open the log. It's the table on the right in the close-up picture above.

- **After 5 minutes of non-stop farming**, it adds a line for that map, your level and your party size.
- **Every 5 more minutes** it updates that line, so the numbers get more accurate the longer you farm.
- **Time** shows how long a line is based on. It grows by 5 minutes with each update.
- **Class** is read from the status bar (never your character's name). Different classes get separate lines.
- **15-minute runs:** under the log it also counts toward 15 minutes of non-stop farming on one map with the same party size ("15-min run: 7 of 15 min"). A level-up doesn't stop it; a break, a new map or a new party size starts it over. Finished runs are saved separately and, if you said yes, shared with the [best-maps site](#best-maps-for-your-class).
- **Greyed-out lines** were changed outside the overlay. Every line the overlay writes is sealed, so a line edited or added by hand is easy to spot, and it won't count toward the best-maps site. Deleting a line, or renaming a map in the overlay, is fine.
- **Taking a break restarts the 5-minute timer.** A break means no EXP for 15 seconds (you can change this in ⚙). Changing maps or party size restarts it too.
- **Don't want a run logged?** Press **Don't log**. It switches back on its own when that run ends.
- **Solo** means just you. **4ppl** means a party of 4.
- **?** under Mesos/hr means your inventory wasn't open.
- **Right-click a line** to:
  - **Copy line**, to paste it into Discord or anywhere else
  - **Rename map**, if it read the name wrong (it remembers the fix for next time)
  - **Delete entry**
- **Can't see a whole column?** Drag the lines between the column titles to make columns narrower or wider. While the log is open, you can also make the window bigger by dragging its edge. It remembers both.

## Settings (⚙)

- **Pause tracking after no EXP for...** How long you can go without EXP before it pauses by itself. This also restarts the log's 5-minute timer. The default is 15 seconds.
- **Window transparency.** Makes the overlay see-through.
- **Size.** Makes the text and the whole window smaller or bigger, from 80% to 200%. It can't go any smaller, so it always stays readable. It's sized for each screen, so a window moved to a screen with different Windows scaling (like a 5K and a 1440p screen) looks the same size on both.
- **Hotkeys.** Turn them on or off, and change them. See [Hotkeys](#hotkeys).
- **Always on top of other windows.** Keeps the overlay above the game. It's on by default.
- **Check for updates when it starts.** On by default. See [Updates](#updates).
- **Share my 15-minute runs.** See [Best maps for your class](#best-maps-for-your-class).
- **Open data folder.** Opens the folder where your log and settings are saved.
- **Check for updates.** Checks right now.
- **Report a bug.** See below.

## Best maps for your class

**[maple-classic-exp-overlay.pages.dev](https://maple-classic-exp-overlay.pages.dev)** shows which maps give the most EXP/hr and mesos/hr for your class and level, solo or in a party, from real hours farmed by players. Click **Best maps** at the top of the overlay to open it, already filled in for you.

- **Enter your level and whether you're solo or in a party, then pick your class and path.** Paths are named after their 4th job, like Night Lord or Bishop, and include the jobs before it (Shadower is Bandits, Chief Bandits and Shadowers). Under Lv 30 you're still 1st job, so Path is greyed out and you see what every player of your class gets.
- **Around your level** means within 5 levels either way, and parties of any size count together, so maps fill in sooner.

- **It's built from 15-minute runs:** 15 minutes of non-stop farming on one map with the same party size. The log shows your progress ("15-min run: 7 of 15 min").
- **Sharing is your choice.** The first time you start the overlay, it asks whether to share your runs. You can change it any time in ⚙.
- **What's shared:** the map, your level, class and party size, the EXP and mesos you got, and a random ID for your copy of the overlay. Never your character's name.
- **Keeping it honest:** the site uses the typical (median) run, counts each player once, leaves out runs far from what other players get there, and only ranks a map once 3 different players have farmed it (before that, it's shown greyed out as early data). Every player's typical run is a dot on the chart, so anything odd is easy to spot.

## How accurate is it?

It double-checks everything it reads before counting it:

- **EXP:** the number and the % have to agree with each other, and with the EXP your level needs.
- **Mesos:** a new amount has to show up in several readings in a row.
- **Sudden jumps:** an EXP jump or a level-up has to be confirmed before it counts. A mesos amount that looks like an extra or missing digit is ignored.
- **Dying:** the EXP you lose isn't taken off your EXP/hr. It says how much you lost, then carries on from there.
- **When in doubt, it waits.** If it can't read something clearly, it skips it instead of guessing.

Every version is tested against thousands of misread numbers, and against real screenshots of the game pasted over many different backgrounds at different window sizes.

## Something not working?

**Click View capture first.** It shows exactly what the overlay read, with a box around every digit:

![View capture](docs/screenshot-capture.png)

| Problem | Try this |
|---|---|
| An error pops up when you open Maple Classic Exp Overlay.exe | Extract the zip first (step 3 above). It can't run from inside the zip or away from its folder. |
| "MapleStory window not found" | Start the game first. If it's minimized, bring it back up. |
| "Can't find the EXP bar" | Make sure nothing covers the EXP bar at the bottom of the game, including the overlay. If the game window is very small, make it bigger. |
| View capture shows the wrong part of the screen | If the button says **Follow game**, click it. |
| Mesos says "open inventory" | Open your inventory, and make sure the mesos at its bottom aren't hidden. In small game windows the chat bar can cover them, so drag the inventory up a bit. If it still doesn't show, stand still for a moment so the inventory stops being see-through. |
| The log says "Map not read yet" | Make sure the minimap is open at the top left of the game, and the overlay isn't covering it. |
| The map name is wrong | Right-click the log line and choose **Rename map**. |
| "This window is covering..." | Drag the overlay to another spot. |
| Numbers stopped updating, or anything else | Please report it (see below). |

**To report a bug,** click **Report a bug** in ⚙ (or **Something's wrong? Report a bug** in View capture):
1. Your browser opens a bug report on GitHub, already filled in with your version and what the overlay was reading. Nothing personal is included, and nothing is sent until you click Submit.
2. A folder opens with **bug-report.png**, a picture of what the overlay read. Drag it into the report.
3. Write a sentence about what went wrong, and click **Submit**. (You need a free GitHub account.)

## Is it safe? Is it allowed?

- **It only looks at your screen, like a screen recorder.** It never touches the game's files or memory, and never presses keys or clicks for you.
- **It only goes online for two things:** asking GitHub whether there's a newer version when it starts, and, only if you said yes, sharing your finished 15-minute runs (see above). Nothing about who you are is sent. Both can be turned off in ⚙. See [Privacy](#privacy).
- **Your antivirus might warn about it.** Antivirus programs sometimes flag small new programs by mistake. All of the code is public right here, so anyone can check what it does.
- **It isn't made by or affiliated with Nexon.** Like any third-party tool, you use it at your own risk.

## Privacy

- **Your log and settings stay on your PC**, in `%APPDATA%\MapleEXP`.
- **The update check** (on by default, can be turned off in ⚙) asks GitHub for the newest version and what's new in it. Nothing about you is sent.
- **15-minute runs are only shared if you said yes.** Each one is the map, when you farmed it, your level, class and party size, the EXP and mesos you got, the overlay's version, and a random ID made by your copy of the overlay. Never your character's name, your game account or your Windows user name.
- **The website keeps** those runs. To limit spam it also keeps a scrambled code made from your internet address with a secret key, which changes every day and can't be turned back into the address. Your address itself isn't kept.
- **Bug reports** only go to GitHub if you click Submit there yourself.

## Where the download comes from

Downloads on the [Releases page](../../releases) are built by GitHub from the code in this repository ([Build workflow](.github/workflows/build.yml)), not on anyone's own PC. Each build:

- installs exact package files, each checked against its SHA-256 ([requirements-build.txt](requirements-build.txt)),
- runs all the tests,
- records a signed **attestation**: which workflow built which file, from which commit,
- puts the zip's **SHA-256** on the release page (`SHA256SUMS.txt`).

**Check your download** with the free [GitHub CLI](https://cli.github.com):

```
gh attestation verify "the zip you downloaded.zip" --repo beanguri/Maple-Classic-Exp-Overlay
```

It tells you whether that exact file was built here, and from which commit. Or compare the file's SHA-256 with `SHA256SUMS.txt` on its release page (in PowerShell: `Get-FileHash` and the file name).

v0.9.6 was built by GitHub from its tag before the package checks and attestations were added, so it has no attestation: its SHA-256 is on its release page, and the same file is on [its build's page](https://github.com/beanguri/Maple-Classic-Exp-Overlay/actions/runs/38631744258) until January 2027 (with a GitHub account). Versions before v0.9.6 were built on my PC.

### Code signing policy

Free code signing from [SignPath Foundation](https://signpath.org) is planned, so Windows stops warning about new downloads. Only builds made by the Build workflow from this repository are signed.

- Committers and reviewers: [beanguri](https://github.com/beanguri)
- Approvers: [beanguri](https://github.com/beanguri)

## Updates

This is a beta, and I'm still adding features and fixing things.

**New name:** since v0.9.3 it's called Maple Classic Exp Overlay. It used to be Maple EXP/hr. Your log and settings carry over.

**When a new version is out,** the overlay tells you when you start it, and shows what's new. Click **Get the update** to open the download page, or **Not now** / **Skip this version** if you'd rather wait.

**To update:** download the newest zip from the [download page](../../releases/latest) and use it instead of the old folder. Your log and settings stay, because they're saved separately (⚙ → **Open data folder** shows where).

**To remove it:** delete its folder. To also delete your log and settings, delete the folder that **Open data folder** opens.

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

---

<details>
<summary><b>For developers</b></summary>

**Run from source.** You need Python 3.10 or newer, and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) for map names.

```
py -3 -m pip install -r requirements.txt
run.bat
```

**Build the download** (puts `Maple-Classic-Exp-Overlay-v<version>.zip` in `release/`, and a copy to use right away in the project folder: `Maple Classic Exp Overlay.exe` with its `_internal` folder):

```
py -3 -m pip install -r requirements.txt pyinstaller
py -3 build.py
```

**Run the tests:**

```
py -3 tests/test_logic.py
py -3 tests/test_screen.py
```

To build with exactly the packages the releases use: `py -3 -m pip install --require-hashes -r requirements-build.txt`.

GitHub runs the logic tests on every push ([Tests workflow](.github/workflows/tests.yml)). The [Build workflow](.github/workflows/build.yml) builds the download on GitHub (Actions tab, **Build**, **Run workflow**), runs all the tests first, and signs the .exe once code signing is set up. Pushing a version tag also attests the zip and drafts the release with it; releases are only ever made that way.

**The stats site** is in `server/`: a Cloudflare Pages site with a D1 database (free tier). The page is `server/public/`, the API is `server/src/index.js`. `npm --prefix server install`, then `npm --prefix server run dev` runs it locally, `py -3 server/test_server.py http://localhost:8787` tests it, and `npm --prefix server run deploy` puts it online.

**How it reads the screen:**
- It finds the `MapleStory.exe` window, then the EXP numbers (like `12,345 [12.34%]`) in the status bar.
- Each digit is resized to a standard size and compared against saved examples of the game's font in `assets/`.
- A reading only counts if the EXP and % agree with how much EXP that level needs, so misreads are ignored.
- Mesos are read the same way. The mesos box is found by its outline, which also works while the inventory is see-through.
- The party size is the number of HP bars in the party list.
- Map names are read with Tesseract.
- Settings, the log and `errors.log` are saved in `%APPDATA%\MapleEXP` (from its old name, Maple EXP/hr, so updates keep them).

</details>

## Credits

- The Classic World EXP table is from [meowdb](https://meowdb.com/msclassic/guides/exp-table-level-1-to-100). It's used to work out your level.
- Map names are read with [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (Apache 2.0 license), which is included in the download.

## License

[MIT](LICENSE): free to use, change and share.
