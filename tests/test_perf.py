"""Performance: the farm additions must not slow the overlay (or the game) down.

Measures: report time for long sessions (the overlay works one out every 2 s), HP/MP and
EXP reading time per look, monster vision per frame, memory, and the cost of a screen
capture of a small region (nothing captured is kept or saved).

Run from the project folder:  py -3 tests/test_perf.py
"""
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import maple_exp as m  # noqa: E402
from farmkit import analytics  # noqa: E402
from farmkit.gauges import read_gauges  # noqa: E402
from farmkit.recorder import Recorder  # noqa: E402
from farmkit.store import Store  # noqa: E402
from synth_bar import status_bar  # noqa: E402

failures = 0
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def timed(fn, n=5):
    best = 1e9
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best * 1000


# ---- a 4-hour session: an EXP gain, an HP/MP change and a wallet change every 1.5 s
store = Store(Path(tempfile.mkdtemp()) / "farm.db")
rec = Recorder(store, "perf")
rec.begin(0.0, map_name="Jr. Necki Platform", party=1)
t, mp, mesos = 0.0, 400, 100000
tracemalloc.start()
while t < 4 * 3600:
    t += 1.5
    rec.exp(t, 40, exp=1000, pct=1.0, level=39)
    mp = mp - 16 if mp > 32 else 400
    rec.gauges(t, 900, 1000, mp, 400)
    mesos += 7
    rec.wallet(t, mesos)
    if int(t) % 600 == 0:
        rec.vision(t, {"event": "vanish", "species": "Jr. Necki", "hits": 2})
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
n = len(rec.events)
prices, skills, monsters = store.prices(), store.skills(), store.monsters()
ms = timed(lambda: rec.report(t, prices, skills, monsters, {}, every=0), 3)
print(f"     ({n:,} events in memory, peak {peak / 1e6:.1f} MB while recording)")
check(f"a 4-hour session's live report takes {ms:.0f} ms (worked out every 2 s; under 250 ms)", ms < 250)
check(f"memory for a 4-hour session stays small ({peak / 1e6:.0f} MB < 150 MB)", peak < 150e6)
rec.end(t)
ms_load = timed(lambda: store.events(rec.store.sessions()[0]["id"]), 2)
check(f"loading a 4-hour session from farm.db takes {ms_load:.0f} ms (< 2,000)", ms_load < 2000)

# ---- reading costs per look (every 1.5 s)
tpl = m.DigitTemplates.load()
bar = status_bar(68601, 71.68, hp=(482, 1178), mp=(240, 400), gh=14, width=1600)
ms_g = timed(lambda: read_gauges(bar[:, :1000], tpl), 10)
ms_e = timed(lambda: m.read_layout(tpl, m.find_exp(bar)), 10)
check(f"HP/MP reading adds {ms_g:.1f} ms per look (EXP reading itself: {ms_e:.1f} ms; budget 1,500 ms)", ms_g < 60)

# ---- a screen capture of a small region (what the overlay grabs each look)
import mss  # noqa: E402
with mss.mss() as sct:
    def grab():
        m.grab(sct, 0, 0, 400, 120)            # thrown away straight after
    ms_c = timed(grab, 10)
check(f"capturing a 400x120 region takes {ms_c:.1f} ms", ms_c < 100)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
