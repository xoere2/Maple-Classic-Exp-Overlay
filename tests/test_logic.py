"""Tests for the tracking and logging logic (no game or screen needed).

Run from the project folder:  py -3 tests/test_logic.py
"""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maple_exp as m  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def new_log():
    folder = Path(tempfile.mkdtemp())
    return m.AutoLog(15, folder / "log.json", folder / "log.txt"), folder / "log.json"


def farm(log, t0, seconds, party=1, map_name="Rocky Road II", level=27, every=3, gap_at=None, gap=0, job=None):
    """100 EXP every `every` seconds (optionally with a pause); returns (end time, logs written)."""
    t, nxt, logged = t0, t0, []
    while t < t0 + seconds:
        paused = gap_at is not None and gap_at <= t - t0 < gap_at + gap
        if t >= nxt:
            if not paused:
                log.gain(t, 100)
            nxt = t + every
        entry = log.tick(t, map_name, level, party, None, job)
        if entry:
            logged.append(round(t - t0))
        t += 0.5
    return t, logged


# ---- the log
log, path = new_log()
_, logged = farm(log, 0, 660)
check(f"logs after 5 min of non-stop EXP, then updates at 10 min ({logged})", logged == [300, 600])
rate = log.entries[0]["exp"] / log.entries[0]["seconds"] * 3600
check(f"EXP/hr is right (120,000, got {rate:,.0f})", abs(rate - 120000) < 2000)

log, _ = new_log()
_, logged = farm(log, 0, 540, gap_at=180, gap=20)
check(f"a 20 s break restarts the 5-minute clock ({logged})", logged and 495 <= logged[0] <= 505)

log, _ = new_log()
_, logged = farm(log, 0, 330, gap_at=100, gap=10)
check(f"a 10 s break doesn't ({logged})", logged and 300 <= logged[0] <= 301)

log, _ = new_log()
t, a = farm(log, 0, 200, party=1)
t, b = farm(log, t, 400, party=4)
check("changing party size restarts the clock", not a and b and 300 <= b[0] <= 305)

log, _ = new_log()
t, _ = farm(log, 0, 310, level=27)
farm(log, t + 60, 310, level=28)
check("a new level gets its own line", len(log.entries) == 2)

a, path = new_log()
b = m.AutoLog(15, path, path.with_suffix(".txt"))
farm(a, 0, 310, party=5)
farm(b, 1000, 310, party=4)
check("two copies of the app can't erase each other's lines",
      sorted(e["party"] for e in json.loads(path.read_text())["entries"]) == [4, 5])

e = {"date": "2026-10-11", "map": "The Land of Wild Boar II", "level": 29, "party": 4,
     "exp": 51862, "seconds": 3600, "mesos": 32713, "mesos_seconds": 3600}
check("full line format", m.format_entry(e) ==
      "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - 4ppl party - 51,862exp/hr - 32,713mesos/hr - 1 h 00 min")
check("table row format", m.log_row(e)[1:] == ("The Land of Wild Boar II", 29, "", "4ppl", "1h 00m", "51,862", "32,713"))
check("the class is in the line", m.format_entry({**e, "class": "Bandit"}).startswith(
    "Oct 11th, 2026 - The Land of Wild Boar II - Lv 29 - Bandit - 4ppl party"))

# ---- party sizes of one map share a row in the log
five = {**e, "party": 5, "exp": 50000, "seconds": 1800, "mesos": 10000, "mesos_seconds": 1800}
solo = {**e, "party": 1}
other_level = {**e, "party": 3, "level": 30}
other_day = {**e, "party": 3, "date": "2026-10-12"}
old_line = {k: v for k, v in e.items() if k not in ("party", "mesos", "mesos_seconds")}  # from before party tracking
groups = m.party_groups([five, solo, e, other_level, other_day, old_line])
shape = [[x["party"] if "party" in x else "-" for x in g] for _, g in groups]
check(f"a map's party sizes share a row, smallest first; solo, other levels and days don't ({shape})",
      shape == [[4, 5], [1], [3], [3], ["-"]])
row = m.group_row(groups[0][1])
check(f"the shared row is over all their time ({row[5:]})",
      row[4:] == ("4–5ppl ▸", "1h 30m", f"{(51862 + 50000) / 5400 * 3600:,.0f}", f"{(32713 + 10000) / 5400 * 3600:,.0f}"))
check("open, its arrow points down", m.group_row(groups[0][1], True)[4] == "4–5ppl ▾")
check("a party size under it shows just its own numbers",
      m.log_row(five, part=True) == ("", "", "", "", "5ppl", "30m", "100,000", "20,000"))
check("the lines themselves aren't changed", five["exp"] == 50000 and e["seconds"] == 3600)

# ---- class, seals and 15-minute runs
log, _ = new_log()
t, _ = farm(log, 0, 310, job="Bandit")
farm(log, t + 60, 310, job="Hunter")
check("a different class gets its own line", sorted(e["class"] for e in log.entries) == ["Bandit", "Hunter"])

log, path = new_log()
farm(log, 0, 310)
check("a line the tracker wrote is sealed", log.sealed(log.entries[0]) and not log.edited(log.entries[0]))
data = json.loads(path.read_text())
data["entries"][0]["exp"] *= 2                                   # someone edits the file by hand
path.write_text(json.dumps(data))
log = m.AutoLog(15, path, path.with_suffix(".txt"))
check("a line edited by hand is marked as edited", log.edited(log.entries[0]))
farm(log, 1000, 310)
check("more EXP added to an edited line doesn't make it trusted again",
      len(log.entries) == 1 and log.edited(log.entries[0]))
data["entries"].append(dict(data["entries"][0], level=28, seal="0" * 24))   # a made-up line
path.write_text(json.dumps(data))
check("a line added by hand isn't trusted either", m.AutoLog(15, path, path.with_suffix(".txt")).edited(
    [e for e in json.loads(path.read_text())["entries"] if e["level"] == 28][0]))
log, _ = new_log()
farm(log, 0, 310)
log.rename(log.entries[0], "Renamed Map")
check("renaming a map in the app keeps the line sealed (the name as read stays on record)",
      log.sealed(log.entries[0]) and log.entries[0]["map_read"] == "Rocky Road II")

log, path = new_log()
farm(log, 0, 950, job="Bandit")
runs = log.runs
check(f"15 minutes of non-stop farming is saved as a sealed run ({len(runs)})",
      len(runs) == 1 and abs(runs[0]["seconds"] - 900) < 2 and log.sealed(runs[0], m._SEALED_RUN)
      and abs(runs[0]["exp"] / runs[0]["seconds"] * 3600 - 120000) < 2000)
check("the log line counts its runs", log.runs_done("Rocky Road II", 1, "Bandit") == 1)
data = json.loads(path.read_text())
data["runs"][0]["exp"] *= 3                                      # a run edited by hand
path.write_text(json.dumps(data))
check("an edited run isn't counted", m.AutoLog(15, path, path.with_suffix(".txt")).runs_done(
    "Rocky Road II", 1, "Bandit") == 0)
log, _ = new_log()
farm(log, 0, 3650)
check(f"an hour of farming makes 4 runs, one after another ({len(log.runs)})",
      len(log.runs) == 4 and all(abs(r["seconds"] - 900) < 2 for r in log.runs))
log, _ = new_log()
t, _ = farm(log, 0, 500, level=30)
farm(log, t, 500, level=31)
check("a level-up doesn't stop a run (the run records both levels)",
      len(log.runs) == 1 and (log.runs[0]["level_start"], log.runs[0]["level_end"]) == (30, 31))
log, _ = new_log()
t, _ = farm(log, 0, 600, party=1)
farm(log, t, 600, party=4)
check("a party-size change restarts the run", len(log.runs) == 0)
log, _ = new_log()
farm(log, 0, 1000, gap_at=500, gap=20)
check("a break restarts the run", len(log.runs) == 0)
log, _ = new_log()
t, _ = farm(log, 0, 600)
farm(log, t, 600, map_name="The Swamp of Despair I")
check("a new map restarts the run", len(log.runs) == 0)
# ---- hotkeys
check("hotkeys: Shift+1 to 4 by default (fork: + Shift+5 AFK)",
      m.DEFAULT_HOTKEYS == {"start": "Shift+1", "reset": "Shift+2", "skip": "Shift+3", "compact": "Shift+4",
                            "afk": "Shift+5"})
check("a hotkey needs Ctrl, Alt or Shift (or an F key), so it can't block plain typing",
      m.parse_hotkey("Shift+1") == (4, 0x31) and m.parse_hotkey("F5") == (0, 0x74)
      and m.parse_hotkey("A") is None and m.parse_hotkey("Win+1") is None and m.parse_hotkey("Shift+!") is None)
check("keys pressed in Settings get the right names", m.hotkey_text(False, False, True, 0x31) == "Shift+1"
      and m.hotkey_text(True, True, False, 0x5A) == "Ctrl+Alt+Z" and m.hotkey_text(False, False, False, 0x41) is None)
m.CONFIG_PATH.write_text(json.dumps({"hotkeys": {"start": "Ctrl+Q", "reset": "", "skip": "nonsense", "compact": 5},
                                     "ui_scale": 3, "compact": "yes"}))
cfg = m.load_config()
check(f"hotkeys from the settings file: kept if valid or off, else the default ({cfg['hotkeys']})",
      cfg["hotkeys"] == {"start": "Ctrl+Q", "reset": "", "skip": "Shift+3", "compact": "Shift+4", "afk": "Shift+5"})
check("a size or compact setting edited by hand can't break the window", cfg["ui_scale"] == 100 and cfg["compact"] is False)
pressed = []
hk = m.Hotkeys(pressed.append)
hk.in_front = lambda: True                                   # as if the game were in front
hk.set({"start": "Ctrl+Alt+Shift+F12"})
import time as _time
_time.sleep(0.8)                                             # it registers within a quarter second
ctypes_ok = m._user32.PostThreadMessageW(hk.thread_id, 0x0312, 1, 0)   # as if the keys were pressed
_time.sleep(0.3)
check(f"a hotkey press reaches the overlay ({pressed}, problems: {hk.problems})", ctypes_ok and pressed == ["start"])
hk.in_front = lambda: False                                  # another program in front: off
_time.sleep(0.6)
m._user32.PostThreadMessageW(hk.thread_id, 0x0312, 1, 0)
_time.sleep(0.3)
check("hotkeys are off while another program is in front", pressed == ["start"])
check("release notes are shown as plain text, keeping names like exp_log.txt",
      m._plain_text("**New**\n- **Time** in `exp_log.txt`, see [README](https://x)") == "New\n• Time in exp_log.txt, see README")
check("how long each line was measured over", [m.fmt_logged(s) for s in (300.4, 1500, 3900)] == ["5 min", "25 min", "1 h 05 min"]
      and [m.fmt_logged(s, short=True) for s in (300.4, 1500, 3900)] == ["5m", "25m", "1h 05m"])

log, _ = new_log()
log.entries = [{"date": "2026-10-08", "map": "Kerning City", "level": 27, "exp": 1, "seconds": 1},
               {"date": "2026-10-08", "map": "Rocky Road II", "level": 27, "exp": 1, "seconds": 1}]
check("a misread map name is matched to the known one", log.canonical("Ketning City") == "Kerning City")
check("Rocky Road I and II stay different maps", log.canonical("Rocky Road I") == "Rocky Road I")
check("'Il' at the end is read as 'II'", m._clean_map_name("Rocky Road Il") == "Rocky Road II")
check("the level comes from the EXP table", m.level_for_req(63666) == 27 and m.level_for_req(60000) is None)

# ---- updating: logs and settings from every released version open with nothing lost
import shutil
real_config = m.CONFIG_PATH
for old in sorted((Path(__file__).parent / "upgrade").iterdir()):
    folder = Path(tempfile.mkdtemp())
    shutil.copy2(old / "exp_log.json", folder / "log.json")
    before = json.loads((old / "exp_log.json").read_text(encoding="utf-8"))
    log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
    same = [all(a.get(k) == b.get(k) for k in ("date", "map", "level", "party", "exp", "seconds", "mesos", "class"))
            for a, b in zip(before["entries"], log.entries)]
    m.CONFIG_PATH = old / "config.json"
    cfg = m.load_config()
    m.CONFIG_PATH = real_config
    check(f"updating from {old.name}: every log line, run, rename and setting is kept",
          len(log.entries) == len(before["entries"]) and all(same) and not any(log.edited(e) for e in log.entries)
          and len(log.runs) == len(before.get("runs", [])) and all(log.sealed(r, m._SEALED_RUN) for r in log.runs)
          and log.aliases == before["aliases"] and not list(folder.glob("exp_log.damaged-*"))
          and (cfg["pos"], cfg["transparency"], cfg["gap_seconds"], cfg["topmost"]) == ([5300, 800], 30, 20, False))

# ---- damaged files
folder = Path(tempfile.mkdtemp())
(folder / "log.json").write_text('{"entries": [{"date": "2026-10-1', encoding="utf-8")   # cut off
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
farm(log, 0, 310)
check("a damaged log is copied before anything is saved over it",
      len(list(folder.glob("exp_log.damaged-*.json"))) == 1 and len(log.entries) == 1)
folder = Path(tempfile.mkdtemp())
good = {"date": "2026-10-11", "map": "Kerning City", "level": 27, "party": 1, "exp": 100, "seconds": 300}
(folder / "log.json").write_text(json.dumps({"version": 2, "entries": [good, {"map": 5}]}), encoding="utf-8")
log = m.AutoLog(15, folder / "log.json", folder / "log.txt")
check("a damaged line is left out (with a copy kept), the rest is kept",
      log.entries == [good] and len(list(folder.glob("exp_log.damaged-*.json"))) == 1)
m.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
m.CONFIG_PATH.write_text(json.dumps({"gap_seconds": "15", "transparency": 400, "pos": "x",
                                     "log_size": [5], "mode": "region"}), encoding="utf-8")
cfg = m.load_config()
check("damaged settings fall back to the defaults",
      cfg["gap_seconds"] == 15 and cfg["transparency"] == 90 and "pos" not in cfg
      and "log_size" not in cfg and cfg["mode"] == "window")
m.CONFIG_PATH.write_text("[1, 2", encoding="utf-8")
check("an unreadable settings file gives the defaults", m.load_config()["topmost"] is True)

# ---- mesos
t = m.MesoTracker()
for v in (9820, 9820):
    t.observe(v, 0, 0, True)
check("the first count needs 3 readings that agree exactly", t.value is None)
for v in (9820, 9850, 9850):
    t.observe(v, 0, 0, True)
check("mesos picked up are counted", t.gained == 30)
t.observe(9999, 0, 0, True)
check("a one-off reading isn't counted yet", t.value == 9850)
for v in (820, 820, 820, 9880, 9880, 9880):
    t.observe(v, 0, 0, True)
check("a lost digit and its correction don't count as mesos gained", t.gained == 30 and t.value == 9880)
for v in (25000, 25000, 25000):
    t.observe(v, 0, 0, True)
check("selling loot (a big jump) doesn't count as farming", t.gained == 30)
for v in (250000,) * 7:
    t.observe(v, 0, 0, True)
check("an extra digit (10x) needs 8 readings in a row, not 3", t.value == 25000)
t = m.MesoTracker()
for v in (20000, 20000, 20000, 15001, 15001, 20000, 20000, 20040, 20040):
    t.observe(v, 0, 0, True)
check(f"a misread twice in a row and its correction cancel out (gained {t.gained})", t.gained == 40)

for before, after, gained in ((9, 10, 1), (99, 100, 1), (9999, 10000, 1),
                              (9000, 10000, 1000), (10000, 9000, -1000),
                              (9980, 10020, 40), (999999, 1000000, 1)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 2:
        t.observe(v, 0, 0, True)
    check(f"a digit rollover still needs 3 exact readings ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 60, 60, True)
    check(f"a small digit rollover counts the gain ({before} to {after}: {t.gained})",
          t.value == after and t.gained == gained and abs(t.per_hour(60) - gained * 60) < 1e-6
          and t.per_5min(60) == gained * 5)
    for v in (before,) * 3:
        t.observe(v, 60, 60, True)
    check(f"a digit rollover and its correction cancel out ({after} to {before})",
          t.value == before and t.gained == 0)

for before, after, nearby, gained in ((9980, 10020, 10010, 40), (10020, 9980, 9990, -40)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (nearby, after, nearby, after, after):
        t.observe(v, 0, 0, True)
    check(f"fluctuating readings across a digit boundary aren't enough ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 0, 0, True)
    check(f"a digit rollover counts once the readings agree exactly ({before} to {after})",
          t.value == after and t.gained == gained)

for before, after in ((0, 1), (0, 10), (9, 19), (99, 199), (9999, 19999),
                      (19999, 9999), (8, 10), (89, 100), (8999, 10000)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 3:
        t.observe(v, 0, 0, True)
    check(f"zero, extra or lost digits, and wider jumps still rebase ({before} to {after})",
          t.value == after and t.gained == 0)
    for v in (before,) * 3:
        t.observe(v, 0, 0, True)
    check(f"correcting a rebased count doesn't add mesos ({after} to {before})",
          t.value == before and t.gained == 0)

for before, after in ((9, 90), (99, 990), (9999, 99990), (10, 1), (100, 10), (10000, 1000)):
    t = m.MesoTracker()
    for v in (before,) * 3 + (after,) * 7:
        t.observe(v, 0, 0, True)
    check(f"a tenfold change still waits for 8 exact readings ({before} to {after})",
          t.value == before and t.gained == 0)
    t.observe(after, 0, 0, True)
    check(f"a confirmed tenfold change still rebases ({before} to {after})",
          t.value == after and t.gained == 0)

t = m.MesoTracker()
for v in (9980,) * 3:
    t.observe(v, 0, 0, True)
t.pause()
for v in (10020,) * 3:
    t.observe(v, 30, 0, False)
check("a digit rollover while paused isn't counted", t.value == 10020 and t.gained == 0)
t.observe(10020, 60, 0, True)
for v in (10030,) * 2:
    t.observe(v, 90, 30, True)
check("mesos picked up after resuming still count", t.value == 10030 and t.gained == 10)

# ---- EXP outliers
def exp_tracker(e=47850, p=50.0):
    tr = m.ExpTracker(); tr.start()
    for _ in range(3):
        tr.observe([(e, p)], True)                       # Lv 30, 50%
    return tr

tr = exp_tracker()
for e in (86130, 86130, 47850, 47850, 47950, 47950):     # too high twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"the real number coming back after a misread isn't taken for a level-up "
      f"(gained {tr.gained:.0f}, levels {tr.levels})", tr.levels == 0 and tr.gained == 100)
tr = exp_tracker(95500, 99.79)
for e in (150, 150, 270):                                # a real level-up into Lv 31 (108,480)
    tr.observe([(e, int(e / 108480 * 10000) / 100)], True)
check(f"a real level-up counts, and the new level's EXP is known right away "
      f"(gained {tr.gained:.0f}, Lv {m.level_for_req(tr.req)})",
      tr.levels == 1 and tr.gained == 200 + 150 + 120 and tr.req == 108480)
tr = exp_tracker()
tr.observe([(0, 0.0)], True)                             # a misread 0
check("a misread 0 doesn't crash or count", tr.gained == 0)
tr = exp_tracker(95680, 99.97)
for e, p in ((32, 0.06), (32, 0.06), (32, 0.02), (32, 0.02), (152, 0.14)):   # 0.02% misread as 0.06%, twice
    tr.observe([(e, p)], True)
check(f"a level-up whose first readings are misread still counts right (gained {tr.gained:.0f}, levels {tr.levels})",
      tr.levels == 1 and tr.gained == 20 + 32 + 120)
tr = exp_tracker(76560, 80.0)
for e in (19140, 19140, 76660, 76660):                   # 80% misread as 20% twice, then the real number
    tr.observe([(e, int(e / 95700 * 10000) / 100)], True)
check(f"EXP never drops by more than a death costs: a big drop is ignored (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.levels == 0)
tr = exp_tracker()
for _ in range(10):
    tr.observe([(47850, 70.0)], False)                   # a misread that suggests another level
check("misreads can't change the level's EXP once it's known from the table", tr.req == 95700)

# ---- dying: EXP drops a few % of the level (Lv 30 needs 95,700)
def read(tr, e):
    return tr.observe([(e, int(e / 95700 * 10000) / 100)], True)

tr = exp_tracker()
for e in (48850, 48850):                                 # +1,000
    read(tr, e)
for e in (39280, 39280):                                 # dies: -10% (9,570)
    note = read(tr, e)[1]
check(f"dying isn't taken off the EXP gained (gained {tr.gained:.0f}, deaths {tr.deaths}, lost {tr.last_loss:.1f}%)",
      note == "died" and tr.gained == 1000 and tr.deaths == 1 and abs(tr.last_loss - 10) < 0.1)
read(tr, 39780)
check(f"after dying, EXP gained counts as usual (gained {tr.gained:.0f})", tr.gained == 1500)
tr = exp_tracker(479, 0.5)
for e in (0, 0):                                         # dies at 0.5%: EXP goes to 0
    read(tr, e)
check(f"dying with less EXP than it costs (down to 0) isn't counted either (gained {tr.gained:.0f})",
      tr.gained == 0 and tr.deaths == 1)
tr = exp_tracker()
for e in (43065, 47850):                                 # 5% lower once (a misread), then the real number
    read(tr, e)
check("a single low reading is ignored", tr.gained == 0 and tr.deaths == 0)
tr = exp_tracker()
for e in (43065, 43065, 47850, 47950):                   # the same low misread twice, then the real number
    read(tr, e)
check(f"a misread that looks like dying, then the real number: no EXP made up (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.deaths == 0)
tr = exp_tracker()
for e in (50721, 50721, 47850, 47950):                   # 3% too high twice, then the real number
    read(tr, e)
check(f"a misread a bit too high and the real number after it cancel out (gained {tr.gained:.0f})",
      tr.gained == 100 and tr.deaths == 0)
log, _ = new_log()
log.gain(0, 100)
log.gain(1, -2000)                                       # more taken back than gained
for s in range(2, 1000):
    if s % 3 == 0:
        log.gain(s, 1)
    log.tick(s, "Rocky Road II", 27, 1)
check(f"no log line or run is ever made of EXP lost (lines: {[e['exp'] for e in log.entries]}, runs: {len(log.runs)})",
      [e["exp"] for e in log.entries] == [200] and log.runs == [])   # the 5-minute blocks after the loss


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self, _=None):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


real_urlopen = m.urllib.request.urlopen
m.urllib.request.urlopen = lambda *a, **k: FakeResponse({"results": [
    {"started": "a", "ok": True}, {"started": "b", "ok": False, "why": "overlaps another run"},
    {"started": "c", "ok": False, "why": "too many runs today", "retry": True}]})
done = m.post_runs("0" * 16, [{"started": x} for x in "abc"])
m.urllib.request.urlopen = real_urlopen
check(f"runs the site has, or turned down for good, aren't sent again; 'try tomorrow' ones are ({done})",
      done == ["a", "b"])

# ---- EXP tracking with noisy readings: misreads must never inflate the gains
random.seed(1)
reqs, worst = [54900, 63666, 73080], 0.0
for trial in range(60):
    tr = m.ExpTracker()
    tr.start()
    lvl, e, true_gain, counting = 0, random.randint(20000, 50000), 0, False
    for step in range(500):
        if random.random() < 0.3:
            g = random.choice([50, 100, 150])
            e += g
            if e >= reqs[lvl]:
                e, lvl = e - reqs[lvl], lvl + 1
            true_gain += g if counting else 0
        p = int(e / reqs[lvl] * 10000) / 100
        bad = int(str(e)[:-1] or 0) if random.random() < 0.1 else None       # a dropped digit
        ok, note = tr.observe([(bad, p)] if bad is not None else [(e, p)], bad is None)
        if note == "baseline":
            counting, true_gain = True, 0
    worst = max(worst, (tr.gained - true_gain) / max(true_gain, 1))
check(f"misread EXP never inflates the gain (worst over-count {worst:.1%})", worst <= 0.02)

# ---- reading the real EXP bar, captured at different game resolutions
import cv2  # noqa: E402
tpl = m.DigitTemplates.load()
for f in sorted((Path(__file__).parent / "frames").glob("exp_*.png")):
    lay = m.find_exp(cv2.imread(str(f)))
    got = m.read_layout(tpl, lay) if lay else None
    check(f"reads the EXP bar at {f.stem[4:]} ({got})", got == (68601, 71.68))
check("a reading that fits a level is trusted", m.fits_exp_table(68601, 71.68))
tr = m.ExpTracker()
tr.observe([(68601, 71.68)], True)
tr.observe([(68601, 71.68)], True)
check("knows the level from two clean readings, without waiting for EXP gains",
      tr.req == 95700 and m.level_for_req(tr.req) == 30)
tr = m.ExpTracker()
tr.observe([(1, 71.68)], False)
tr.observe([(1, 71.68)], False)
check("an untrusted reading doesn't set the level", tr.req is None)
check("'1 [71.68%]' (digits cut off) fits no level, so it isn't trusted", not m.fits_exp_table(1, 71.68))

# ---- pausing by itself (a fake clock, so these run instantly)
clock = [10000.0]
m.time.monotonic = lambda: clock[0]
EXP0 = 60000


def session(gap=15):
    s = m.Session(gap)
    s.map_seen("Rocky Road II")
    for _ in range(2):                                  # learns the level (Lv 30)
        s.observe([(EXP0, round(EXP0 / 95700 * 100, 2))], True, clock[0])
    return s


def gains(s, seconds, every=3, map_name=None):
    """Kills worth 100 EXP every few seconds; the clock moves in half-second steps."""
    end, nxt = clock[0] + seconds, clock[0] + every
    while clock[0] < end:
        clock[0] += 0.5
        if clock[0] >= nxt:
            nxt += every
            e = s.exp.exp + 100
            s.observe([(e, int(e / 95700 * 10000) / 100)], True, clock[0])
        s.tick(clock[0])


def idle(s, seconds):
    end = clock[0] + seconds
    while clock[0] < end:
        clock[0] += 0.5
        s.tick(clock[0])


s = session()
s.start(clock[0])
gains(s, 30)
idle(s, 40)
check(f"no EXP for 15 s pauses by itself, without counting the idle time "
      f"({s.waiting}, {s.exp.elapsed():.0f} s)", s.waiting == "idle" and abs(s.exp.elapsed() - 30) < 0.6)
gains(s, 3.5)
check(f"the next EXP gain carries on (the gain that restarts it isn't counted: {s.exp.gained})",
      s.waiting is None and s.exp.running and s.exp.gained == 1000)
gained = s.exp.gained
s.map_seen("The Swamp of Despair I")
check("leaving the map pauses by itself", s.waiting == "map" and not s.exp.running)
s.map_seen("Rocky Road II")
gains(s, 6.5)
check("back on the same map, it carries on adding up", s.exp.running and s.exp.gained == gained + 100)
s.map_seen("The Swamp of Despair I")
gains(s, 6.5)
check(f"EXP on a different map starts from zero, still knowing the level "
      f"(gained {s.exp.gained}, Lv {m.level_for_req(s.exp.req)})",
      s.map == "The Swamp of Despair I" and s.exp.gained == 100 and s.exp.req == 95700)
s.pause()
gains(s, 6.5)
check("after pressing Pause, it stays paused", not s.exp.running and s.on is False)
s = session(gap=60)
s.start(clock[0])
gains(s, 9.5)
idle(s, 40)
check("the pause time can be changed in Settings (60 s: a 40 s break doesn't pause)", s.exp.running)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
