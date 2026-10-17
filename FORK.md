# Maple Classic Exp Overlay: Farm Analytics fork

A fork of [beanguri/Maple-Classic-Exp-Overlay](https://github.com/beanguri/Maple-Classic-Exp-Overlay)
(MIT). It keeps everything the original does and adds farming analytics. The goal is to
answer questions an EXP/hr number can't:

1. Is farming Jr. Neckis with basic attacks more profitable than with Lucky Seven?
2. Are Jr. Wraiths better than Jr. Neckis once Mokbi drops are counted?
3. Is a map slow because I lack damage, or because there aren't enough monsters?
4. Does a level 40 claw meaningfully improve farming?
5. Which map gives the best EXP/hr without losing mesos?

It only looks at the screen, like the original. It never reads or changes the game's
memory or files, never sends input to the game, and never touches its network traffic.
See [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

## What's different from upstream

| | Upstream 0.9.6-beta | This fork |
|---|---|---|
| EXP/hr | Active time only: it pauses after 15 s with no EXP | **Real EXP/hr** (the default big number: idle time counts, Pause/AFK doesn't) plus active EXP/hr. The inactivity rule can be changed, turned off, or re-applied to old sessions |
| Rolling rates | Last 5 min | Last 1, 5 and 15 min |
| AFK | — | AFK button / Shift+5: marked time is left out of real time |
| Session history | Daily log lines (totals) | Every session saved with its raw observations in `farm.db` (SQLite). Old log lines are imported read-only |
| Mesos | Mesos/hr from small pickups | A wallet ledger: realized net, pickups, purchases/sales/storage/transfers notes, consumables used, stock change, loot value, operating profit, estimated total. **No double counting** |
| HP/MP | — | Read from the status bar: MP spent, casts per skill, potions drunk (estimated), damage taken |
| Prices | — | Editable price list (CSV import), skills (MP per cast), monster info |
| Loot | — | Inventory snapshots and loot notes; drop rates with exact 95% intervals |
| Kills / respawns | — | **Experimental, off by default**: monster vision on a region you choose, with confidence labels (confirmed / probable / party credit / unattributed) |
| Recorded video | — | Analyse an OBS recording locally (EXP, HP/MP, mesos, party, map, optional monsters) |
| Comparison | Best-maps website (shared runs) | Analytics window: compare sessions side by side, rankings (incl. best EXP/hr without losing mesos), map efficiency, charts, CSV/JSON export |
| Weapon upgrades | — | Before/after tags, plus a clearly labelled theoretical what-if |
| Provenance | — | Every number is labelled observed / manual / calculated / inferred / estimated |
| Sharing | Opt-in 15-minute runs | Unchanged and still opt-in. **No new field is ever shared** |

Data compatibility: same data folder (`%APPDATA%\MapleEXP`), same `config.json` and
`exp_log.json` formats (seals stay valid), and upstream and the fork can't run at the
same time (same single-instance lock). Going back to upstream just ignores `farm.db`.

## Documentation

- [docs/FARM_ANALYTICS.md](docs/FARM_ANALYTICS.md): installing, using every feature, and answering the five questions
- [docs/METRICS.md](docs/METRICS.md): every metric, how it's worked out, and whether it's observed, estimated or manual
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md): what doesn't work, what's experimental, what wasn't possible
- [docs/COMPLIANCE.md](docs/COMPLIANCE.md): Terms of Service considerations and privacy
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the upstream audit and the fork's design
- [CHANGELOG.md](CHANGELOG.md)

## Build and test

```
py -3 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements-build.txt
.venv\Scripts\python tests\test_logic.py      # upstream
.venv\Scripts\python tests\test_screen.py     # upstream (class OCR needs Tesseract)
.venv\Scripts\python tests\test_farm.py       # fork: timing, economy, HP/MP, stats, storage, rankings
.venv\Scripts\python tests\test_vision.py     # fork: monster vision on synthetic scenes
.venv\Scripts\python tests\test_video.py      # fork: end-to-end recorded-video analysis
.venv\Scripts\python tests\test_perf.py       # fork: speed and memory
.venv\Scripts\python tests\test_ui.py         # fork: the window, driven on a fake clock (opens windows)
.venv\Scripts\python build.py                 # needs Tesseract in C:\Program Files\Tesseract-OCR
```

GitHub Actions runs all of these on every push ([Tests](.github/workflows/tests.yml)). The
[Build](.github/workflows/build.yml) workflow builds the download from hash-pinned
packages and records an attestation:
`gh attestation verify <zip> --repo xoere2/Maple-Classic-Exp-Overlay`.

## Credit

The original overlay, its screen reading, EXP logic, log and stats site are by
[beanguri](https://github.com/beanguri) (MIT). This fork adds `farmkit/` and small,
marked hooks in `maple_exp.py`.
