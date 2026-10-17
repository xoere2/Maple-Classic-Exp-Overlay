"""Smoke test of the overlay window with the farm additions: builds the real window, feeds it
readings (as the screen reader would) on a fake clock, and opens every Analytics tab.
Needs a desktop (Tk), not the game. Screenshots go to the folder given as the first
argument, if any.

Run from the project folder:  py -3 tests/test_ui.py [screenshot folder]
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tkinter as tk  # noqa: E402

import maple_exp as m  # noqa: E402

failures = 0
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


class Clock:
    t = 10000.0


real_monotonic = time.monotonic
time.monotonic = lambda: Clock.t               # the overlay and farmkit both read this

m.DATA_DIR.mkdir(parents=True, exist_ok=True)
m.CONFIG_PATH.write_text(json.dumps({"share_runs": False, "check_updates": False, "hotkeys_on": False,
                                     "pos": [40, 40]}))
root = tk.Tk()
app = m.App(root, m.DigitTemplates.load())
app.stop_evt.set()                              # no screen reading: readings come from this test
root.update()
farm = app.farm
check("the farm rows and buttons are built", "Net mesos / hr" in farm.rows and farm.afk_btn.winfo_exists())
check("the big number says it's real EXP/hr", app.big_title.cget("text") == "EXP / hr (real)")

LEVEL, REQ = 39, m.EXP_TABLE[38]
state = {"exp": 50000, "mesos": 100000, "mp": 400}


def reading(mesos=True, mp=True):
    e = state["exp"]
    pct = int(e / REQ * 10000) / 100
    g = {"hp": 900, "hp_max": 1000, "mp": state["mp"], "mp_max": 400} if mp else None
    return m.Reading("MapleStory window 1280x720", [(e, pct)], True, f"{e:,} [{pct:.2f}%]",
                     map="Jr. Necki Platform" if mesos else None, mesos=state["mesos"] if mesos else None,
                     party=1, gauges=g)


def tick(seconds, gain=0, every=3.0, meso_gain=0, mp_cost=0):
    end = Clock.t + seconds
    next_gain = Clock.t + every
    while Clock.t < end:
        Clock.t += 1.5
        if gain and Clock.t >= next_gain:
            state["exp"] += gain
            state["mesos"] += meso_gain
            state["mp"] -= mp_cost
            if state["mp"] < mp_cost:
                state["mp"] += 200                   # a Blue Potion
                state["potions"] = state.get("potions", 0) + 1
            state["casts"] = state.get("casts", 0) + (1 if mp_cost else 0)
            next_gain += every
        app._handle(reading())
        app._update_display()
    root.update()


for _ in range(4):                               # the level is worked out from clean readings
    Clock.t += 1.5
    app._handle(reading())
app.toggle()                                     # Start
check("Start begins a farm session", farm.rec.active)
tick(300, gain=120, meso_gain=40, mp_cost=16)    # 5 minutes of farming
rep = farm.report
check(f"real EXP/hr shows while farming ({app.big.cget('text')})", app.big.cget("text") not in ("—", ""))
check(f"real EXP/hr ≈ 144,000 ({rep['real_rate'].value:,.0f})", abs(rep["real_rate"].value - 144000) < 8000)
tick(60)                                         # a minute with no monsters
rep = farm.rec.report(Clock.t, *farm.lookups(force=True), farm.settings(), every=0)
check(f"idle time counts in real time, not active ({rep['real_seconds'].value:.0f} s real, "
      f"{rep['active_seconds'].value:.0f} s active)", rep["real_seconds"].value - rep["active_seconds"].value > 40)
check(f"upstream's own EXP/hr still pauses itself ({app.session.waiting})", app.session.waiting == "idle")
farm.toggle_afk()
check("AFK marks the session", farm.rec.afk and "on" in farm.afk_btn.cget("text"))
tick(120)
farm.toggle_afk()
rep = farm.rec.report(Clock.t, *farm.lookups(force=True), farm.settings(), every=0)
check(f"AFK time is left out of real time ({rep['afk_seconds'].value:.0f} s AFK)", rep["afk_seconds"].value >= 118)
tick(120, gain=120, meso_gain=40, mp_cost=16)    # back to it: the first gain resumes upstream's clock
farm.rec.txn(Clock.t, "purchase", item="Blue Potion", qty=10, unit_price=220)
state["mesos"] -= 2200
farm.rec.txn(Clock.t, "use", item="Blue Potion", qty=4)
tick(30, gain=120, meso_gain=40, mp_cost=16)
app._update_display()
rep = farm.report
check(f"wallet tracked: realized net {rep['realized_net'].value:,.0f}", rep["realized_net"].ok)
check(f"pickups add the potion purchase back ({rep['pickups'].value:,.0f} = realized + 2,200)",
      abs(rep["pickups"].value - rep["realized_net"].value - 2200) < 1)
check(f"MP spent is observed ({rep['mp_spent'].value})", rep["mp_spent"].ok and rep["mp_spent"].value > 0)
check(f"casts worked out from MP ({rep['skill_casts'].value} of {state['casts']})",
      rep["skill_casts"].ok and state["casts"] - 2 <= rep["skill_casts"].value <= state["casts"])
check(f"potions worked out from MP jumps ({rep['mp_potions'].value} of {state['potions']})",
      rep["mp_potions"].ok and rep["mp_potions"].value == state["potions"])
check("farm rows filled in", farm.rows["Net mesos / hr"][0].get() not in ("—", "") and
      "400" in farm.rows["HP · MP"][0].get())
farm.toggle_more()
root.update()
check("More shows the extra rows", farm.rows["Kills / hr"][1].winfo_ismapped())
app.set_compact(True)
app._update_display()
root.update()
check("compact mode hides the farm rows and shows net mesos in its line",
      not farm.frame.winfo_ismapped() and "Net" in app.compact_line.cget("text"))
if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)
    from PIL import ImageGrab

    def shot(widget, name):
        """Just that window (by its own handle), lifted to the front first."""
        import ctypes
        widget.lift()
        widget.attributes("-topmost", True)
        widget.update()
        time.sleep(0.3)
        hwnd = m._GetAncestor(widget.winfo_id(), 2)
        rect = m.wintypes.RECT()
        m._GetWindowRect(hwnd, ctypes.byref(rect))
        ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(SHOTS / name)
    shot(root, "overlay-compact.png")
app.set_compact(False)
root.update()
if SHOTS:
    shot(root, "overlay-expanded.png")

farm.open_analytics()
win = farm.analytics
root.update()
check("the Analytics window opens", win.alive())
sid = farm.rec.sid
win.sess_tree.selection_set(str(sid))
win._session_selected()
text = win.detail.get("1.0", "end")
check("session details list every metric with its kind", "real_rate" in text and "[calculated" in text
      and "pickups" in text and "[inferred" in text)
win._compare_selected()
root.update()
check("compare shows a column for the session", win.cmp_tree is not None and len(win.cmp_tree.get_children()) > 10)
win._fill_rank()
win._draw_charts()
win.wi["new"].set("46")
win.wi["hp"].set("400")
win._whatif()
check("the what-if works out both weapons", "UPGRADE" in win.wi_text.get("1.0", "end"))
for i in range(len(win.nb.tabs())):
    win.nb.select(i)
    root.update()
    if SHOTS:
        shot(win.win, f"analytics-{i}.png")
check("every Analytics tab opens", True)

app.reset()
root.update()
check("Reset ends the farm session", not farm.rec.active and farm.store.session(sid)["ended"])
events = farm.store.events(sid)
check(f"the session's events are saved ({len(events)})", len(events) > 100 and events[-1].kind == "stop")
kinds = {e.kind for e in events}
check(f"it has EXP, wallet, HP/MP, map, party, AFK and notes ({sorted(kinds)})",
      {"exp", "wallet", "gauge", "map", "party", "afk_on", "afk_off", "txn"} <= kinds)
check("the upstream log still works (5-minute line written)", len(app.autolog.entries) >= 1)

app.close()
time.monotonic = real_monotonic
print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
