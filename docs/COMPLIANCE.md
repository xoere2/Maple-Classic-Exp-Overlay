# Terms of Service considerations and privacy

**This is not legal advice, and screen-reading tools are not approved by Nexon.** Using any
third-party program with MapleStory is at your own risk.

## What the fork does and doesn't do

| Never | Verified how |
|---|---|
| Reads or writes the game's memory | No `ReadProcessMemory`/`WriteProcessMemory`. The only process call is `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`, used to read the executable's **name** (upstream code, unchanged) |
| Injects code, loads DLLs into the game, hooks game functions | None: no `SetWindowsHookEx`, `CreateRemoteThread` or similar |
| Modifies game files | Never opens them |
| Captures, decrypts or reverse-engineers network traffic | No packet capture. The only network use is upstream's update check and opt-in run sharing |
| Sends keys or mouse input to the game | None. Hotkeys are **received** via `RegisterHotKey` (as Discord/OBS do) |
| Plays, fights or loots for you | No automation of any kind |
| Hides from anti-cheat | Nothing is concealed: a normal window, a normal process name |
| Needs administrator rights | No elevation, no manifest |

What it does: takes screenshots of the game window through standard Windows screen capture
(GDI, the same mechanism OBS display capture uses), reads them on your PC, and shows numbers
in its own window. Recorded-video analysis reads a video file you choose.

## Rules reviewed, and what stays unresolved

- **Nexon Terms of Use** (<https://www.nexon.com/terms-of-use>). During this work the page's
  text could not be retrieved (it loads client-side), so its exact clauses on "third-party
  software" were **not** reviewed here. MMO terms commonly prohibit unauthorized
  third-party programs that interact with or modify the game. A passive screen reader is
  generally regarded as lower risk than memory readers or bots, but **no document states
  that screen-reading overlays are permitted**. Read the current terms yourself.
- **Nexon's anti-cheat** for Classic World is not documented publicly. A screen-capture
  tool is not designed to evade it, and nothing here tries to. Detection heuristics are
  unknown. Some anti-cheat systems flag any program that screenshots the game.
- **Community price sites**: one (Maple.Community) forbids automation, so the fork
  doesn't fetch prices from anywhere.
- **Monster vision** only uses sprites **you** crop from your own screen. No game assets
  are extracted or distributed.

Unresolved risk, in one line: Nexon has not approved this (or the original) overlay, and
the fork adds no technique beyond screen reading, but whether *any* third-party overlay is
allowed is Nexon's call.

## Privacy

- All data stays in `%APPDATA%\MapleEXP` (config, upstream log, `farm.db`).
- No screenshots, video frames, character names, inventory contents, notes or sessions
  are uploaded.
- The original **opt-in** sharing of 15-minute runs is unchanged and sends exactly the
  original fields. The fork adds nothing to it. New fields would need a separate decision.
- The update check now asks this fork's GitHub releases (not upstream's).
- Exports (CSV/JSON) are files you save yourself.
