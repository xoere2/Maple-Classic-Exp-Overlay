# Architecture review: Maple Classic Exp Overlay, Farm Analytics fork

This document covers Phase 1 of the fork: what the upstream application
(`beanguri/Maple-Classic-Exp-Overlay`, commit `1869c60`, v0.9.6-beta) does, verified
against its source. It then describes how the fork extends it.

## 1. Upstream components (verified in source)

All client code is in one file, `maple_exp.py` (3,893 lines). The sections are:

| Section | Key symbols | What it does |
|---|---|---|
| Files / config | `load_config`, `save_config`, `DATA_DIR` | Settings in `%APPDATA%\MapleEXP\config.json`. Every key is type-checked on load, and writes are atomic (tmp + `os.replace`). |
| Updates | `check_for_update`, `post_runs` | One GET to `api.github.com/.../releases/latest`. One opt-in POST of 15-minute runs to the stats site. |
| Hotkeys | `Hotkeys` | `RegisterHotKey` on its own message thread. Hotkeys are registered only while MapleStory or the overlay is the foreground window. Nothing is ever sent to the game. |
| Game window | `find_game_window`, `client_rect`, `grab` | Finds the window by class `MapleStoryClass` or by exe name `maplestory.exe` (`OpenProcess` with `PROCESS_QUERY_LIMITED_INFORMATION`, only to read the image name). Screenshots come from `mss` (GDI BitBlt). |
| EXP reading | `find_exp`, `DigitTemplates`, `read_layout` | Finds the green `[`/`]` brackets on the status line and the white digit blobs. Each digit is rescaled to 24 px and correlated against `assets/digits.npz`. Tesseract is only a fallback. |
| Level | `EXP_TABLE`, `levels_for`, `level_for_req` | The level is inferred by matching EXP ÷ % against the Classic EXP table. |
| Class / map | `read_job`, `find_map_box`, `read_map_name` | Tesseract on the status-bar job label and the minimap's info box, with voting over several scales. |
| Mesos | `find_mesos_field`, `MesosBoxFinder`, `read_mesos_positions`, `MesosDigits` | Finds the inventory mesos box (solid or see-through) and reads digits at fixed right-aligned positions. Digit examples are learned from the player's own screen, with Tesseract confirming. |
| Party | `find_party_bars` | Counts the evenly spaced, identical HP-bar outlines in the party list. |
| Orchestration | `ScreenReader` | Follows the game window, caches each UI element's location, blanks out its own window in screenshots, and re-searches after misses. |
| EXP logic | `ExpTracker` | Robust gain accounting. A reading is accepted only if `exp/pct` matches the level requirement. Big jumps and level-ups need confirmation. Death losses are recorded but not subtracted. Misread recovery included. |
| Mesos logic | `MesoTracker` | Confirms readings over 2–3 frames. Jumps above 50% (shop visits, storage) are not counted as gained. |
| Session | `Session` | Start/Pause. **Auto-pauses after `gap_seconds` (default 15) without EXP, and the clock is rewound to the last gain.** Leaving the map also pauses. EXP on a new map starts from zero. |
| Log | `AutoLog` | Per-day/map/level/class/party lines, built from 5-minute blocks of non-stop EXP. 15-minute "runs" for the stats site. HMAC "seal" against hand edits. JSON at `exp_log.json`. |
| UI | `App`, `RegionSelector` | tkinter: full and compact modes, log table, settings, view capture, bug-report helper. DPI-aware per monitor. |
| Server | `server/` | Cloudflare Pages + D1. `/api/runs` ingests opt-in runs and stores a daily-rotating HMAC of the IP (rate limiting), never the IP itself. Serves best-maps aggregates. |

### Data flow (upstream)

```
mss screenshot ──► ScreenReader.read() (worker thread, every 1.5 s)
                    ├─ find_exp/read_layout ─► Reading.candidates
                    ├─ _read_info (minimap, Tesseract)  ─► Reading.map
                    └─ _read_extras (inventory + party, every 4 s) ─► Reading.mesos/party
                         │  queue
                         ▼
App._pump (Tk thread) ─► App._handle ─► Session.observe ─► ExpTracker / MesoTracker
                                     └► AutoLog.gain/tick ─► exp_log.json (+ opt-in POST)
App._refresh (every 0.5 s) ─► labels
```

### Tests (upstream)

- `tests/test_logic.py`: AutoLog timing, seals, upgrades from every past version's data,
  ExpTracker misread/death/level-up handling, MesoTracker rollovers, Session auto-pause.
- `tests/test_screen.py`: real cropped frames pasted over 10 synthetic backgrounds. Checks
  mesos reading, the see-through box finder and party count at 3 UI sizes, plus class OCR
  (skipped when Tesseract isn't installed).
- Baseline on this machine (Python 3.10.6, `requirements-build.txt` hashes): **all pass**.

### Build and release

`build.py` runs PyInstaller (one-dir, windowed), bundles Tesseract and `eng.traineddata`,
**removes `opencv_videoio_ffmpeg*.dll`** to save space, and zips the result. The GitHub
Actions `build.yml` installs hash-pinned packages, installs Tesseract via Chocolatey, runs
both test files, builds, attests the zip, and drafts a release. Actions are pinned to commit SHAs.

## 2. Security review

| Concern | Finding |
|---|---|
| Unexpected network traffic | Only three: (1) GET `api.github.com/repos/<repo>/releases/latest` at start (setting, default on); (2) POST `maple-classic-exp-overlay.pages.dev/api/runs` **only if** the player answered yes to the first-run prompt; (3) `webbrowser.open` for links the user clicks. No telemetry, no other hosts. |
| PII | Shared runs contain map, level, class, party, EXP, mesos, version, start time and a random 16-hex install id. No character name: `read_job` explicitly skips the name line. Bug reports replace the home path and username with `<you>` and are only sent if the user submits the GitHub form. The server stores an HMAC of the IP with a daily key. |
| Credentials | None collected or stored. |
| Game memory | None. The only process access is `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `QueryFullProcessImageNameW`, used to read the executable name. |
| Code injection / hooks | None. No `SetWindowsHookEx`, `WriteProcessMemory`, `CreateRemoteThread` or DLL loading into other processes. |
| Input simulation | None. `RegisterHotKey` only *receives* hotkeys. `SetForegroundWindow` is only called on the overlay's own window (single-instance activation). |
| Privilege elevation | None. There is no manifest requesting admin. DPI awareness is set via `SetProcessDpiAwareness(2)`. |
| Background processes | One daemon capture thread, one hotkey thread, and short-lived threads for the update check and sharing. `subprocess.Popen(["explorer", "/select,..."])` only runs from the bug-report button. |
| Supply chain | Release builds use `--require-hashes` and SHA-pinned actions. `find_tesseract` ignores the current working directory to avoid binary planting. `requirements.txt` (for running from source) is unpinned (`>=`), which is acceptable for development. |
| Anti-cheat interaction | Screen capture via GDI is the same mechanism OBS's display capture uses. The overlay does not hide itself from anything. |

**Verdict:** the source matches its README claims. We did not run any upstream binary. The
fork is built only from this audited source.

## 3. Technical limitations found

1. **Idle time is excluded by design.** `Session.tick` pauses after 15 s without EXP and
   `ExpTracker.pause(at=last_gain)` rewinds the clock. EXP/hr is therefore "active" EXP/hr
   only. On a contested map with slow respawns this overstates real efficiency, which is
   the fork's main motivation.
2. Gains that arrive while auto-paused are not counted (the gain that resumes tracking is
   discarded on purpose). That is right for "active" rate and wrong for a wall-clock rate.
3. Mesos/hr counts only small confirmed increments. Shop purchases and storage moves are
   silently treated as "jumps" and dropped, so there is no notion of expenses or of net
   wallet change.
4. No persistent session history beyond aggregated log lines: you can't compare two
   specific sessions or re-compute with different rules.
5. HP/MP are not read, although they use the same font and brackets as EXP.
6. Monolithic file: the logic classes are testable, but UI and capture are intertwined.
7. Offline (video) analysis isn't possible: OpenCV's FFmpeg plugin is stripped at build time.

## 4. Fork architecture

The fork keeps `maple_exp.py` as the overlay so it can still merge from upstream, with
small, clearly marked hooks. New functionality lives in a separate package, `farmkit/`,
whose logic modules have no Tk or screen dependency:

```
farmkit/
  events.py      Event model: the raw, timestamped observations every metric is computed from
  timing.py      Real vs active time, manual pause, AFK, rolling 1/5/15-min windows,
                 per-map and solo/party splits (recomputable with any inactivity rule)
  gauges.py      HP/MP reader (same font and templates as EXP) + MP/HP usage and potion inference
  economy.py     Ledger: wallet observations, annotated transactions, inventory snapshots,
                 consumption, price DB valuation, double-count-free profit identities
  combat.py      Attack/kill efficiency, MP per kill, EXP/mesos per MP, claw-upgrade what-if
  stats.py       Wilson / Clopper-Pearson intervals, Poisson rate intervals
  vision/        EXPERIMENTAL (feature-flagged): camera-motion compensation, monster detection
                 (motion and sprite templates), tracking, kill/spawn classification with
                 confidence labels, damage-number evidence, ROI support
  video.py       Offline analysis of OBS/gameplay recordings (EXP, mesos, party, HP/MP, vision)
  store.py       SQLite (farm.db): sessions, events, transactions, prices, snapshots,
                 annotations; import of the upstream exp_log.json (read-only, idempotent)
  analytics.py   Session summaries with per-metric provenance (observed/estimated/manual),
                 comparisons, rankings (incl. "best EXP/hr with non-negative profit")
  export.py      CSV / JSON
  recorder.py    Bridge used by the overlay: turns Readings into events
  ui/            Analytics window (sessions, compare, rankings, economy, monsters, charts)
  __main__.py    CLI: analyze-video, export, summary
```

**Design principle: store raw observations, derive metrics.** The recorder writes
timestamped events (EXP gains with level and req, wallet values, HP/MP readings, map and
party changes, manual pause/resume/AFK, annotations, vision events). Every metric is a
pure function of those events plus settings, so:

- "real" and "active" EXP/hr come from the same data, and the inactivity threshold can be
  changed after the fact;
- every number can carry its provenance (`observed`, `estimated`, `manual`, `inferred`),
  plus a confidence and the raw observations behind it;
- the same pipeline runs on live capture and on recorded video.

**Upstream data compatibility.** `exp_log.json` and `config.json` keep their formats and
the fork never writes new keys into log lines, so seals stay valid and upstream can still
read the files. Fork settings are added as new config keys with defaults. The SQLite
database is a new file next to them. Old log lines are imported into it as `legacy`
sessions (aggregates only) for the rankings view.

**Community sharing** is unchanged and stays opt-in. No fork-specific field (expenses,
kills, inventory, HP/MP, video) is ever added to the shared payload; `post_runs` sends the
same fixed whitelist as upstream.
