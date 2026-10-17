"""End-to-end test of offline video analysis: a synthetic 'gameplay' video (a scene with
made-up monsters, a status bar built from the game's digit font with EXP and MP that
change, the real inventory and party-list crops) is encoded to MP4, then analysed.

Run from the project folder:  py -3 tests/test_video.py
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
sys.path.insert(0, str(ROOT / "tests"))
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import maple_exp as m  # noqa: E402
from farmkit import analytics, vision as v  # noqa: E402
from farmkit.store import Store  # noqa: E402
from farmkit.video import analyze_video  # noqa: E402
from synth_bar import status_bar  # noqa: E402
from synth_scene import make_sprite, make_world, render  # noqa: E402

failures = 0
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


FR = ROOT / "tests" / "frames"
PARTS = json.loads((FR / "screen_parts.json").read_text())
W, H, FPS, SECONDS = 1280, 720, 10, 60
GH = 11
LEVEL = 39
REQ = m.EXP_TABLE[LEVEL - 1]
inv = cv2.imread(str(FR / "inventory_gh11.png"))
party_row = cv2.imread(str(FR / "party_row_gh11_0.png"))
world = make_world(480, 2000)
necki = make_sprite((40, 200, 90), (30, 26), seed=1)

# The story: a kill every 3 s on one platform (EXP 120 each, a Lucky Seven of 16 MP each),
# a Blue Potion (+200 MP) every 30 s, solo, 18,047 mesos in the inventory the whole time.
kills = [3.0 * k + 2.0 for k in range(19)]
monsters = []
for i, t_die in enumerate(kills):
    monsters.append({"sprite": necki, "x": 300 + 120 * (i % 4), "y": 250, "born": t_die - 2.6, "dies": t_die,
                     "damage": [t_die - 0.4]})


def state(t):
    n = sum(1 for k in kills if k + 0.3 <= t)            # the EXP shows a moment after the death
    exp = 150000 + 120 * n
    casts = sum(1 for k in kills if k - 0.4 <= t)
    potions = int(t // 30)
    mp = 400 - 16 * casts + 200 * potions
    return exp, mp


tmp = Path(tempfile.mkdtemp())
video = tmp / "farming.mp4"
writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
check("can write a test video (OpenCV's video support is present)", writer.isOpened())
for i in range(SECONDS * FPS):
    t = i / FPS
    frame = np.full((H, W, 3), 30, np.uint8)
    frame[0:400] = render(world, 0, W, monsters, t, view_h=400)
    exp, mp = state(t)
    pct = int(exp / REQ * 10000) / 100
    bar = status_bar(exp, pct, hp=(1000, 1000), mp=(max(0, min(mp, 600)), 600), gh=GH, width=W)
    frame[H - bar.shape[0]:H] = bar
    frame[420:420 + inv.shape[0], 900:900 + inv.shape[1]] = inv
    frame[440:440 + party_row.shape[0], 40:40 + party_row.shape[1]] = party_row
    writer.write(frame)
writer.release()
check(f"wrote {SECONDS} s of video ({video.stat().st_size / 1e6:.1f} MB)", video.stat().st_size > 10000)

store = Store(tmp / "farm.db")
analyzer = v.VisionAnalyzer(v.SpriteDetector({"Jr. Necki": [necki]}, threshold=v.VIDEO_THRESHOLD),
                            damage=v.DamageDetector())
t0 = time.perf_counter()
sid, stats = analyze_video(video, store, roi=(0, 0, W, 400), vision=analyzer, vision_fps=10,
                           tags={"attack_mode": "Lucky Seven"}, settings={"skill": "Lucky Seven"})
took = time.perf_counter() - t0
print(f"     ({stats['frames']} frames looked at, {stats['exp_reads']} EXP reads, {took:.1f} s for {SECONDS} s of video)")
check(f"EXP bar read in the video ({stats['exp_ok']} of {stats['exp_reads']} readings trusted)",
      stats["exp_ok"] >= 0.9 * stats["exp_reads"])
session = store.session(sid)
events = store.events(sid)
rep = analytics.session_report(session, events, store.prices(), store.skills())
gained = rep["exp_gained"].value
check(f"EXP gained: 19 kills × 120 = 2,280 (minus the first, the baseline) → {gained}",
      gained in (120 * 18, 120 * 19))
check(f"real time = the video's length ({rep['real_seconds'].value:.0f} s)", abs(rep["real_seconds"].value - SECONDS) < 1)
check(f"real EXP/hr ≈ {gained / SECONDS * 3600:,.0f} ({rep['real_rate'].value:,.0f})",
      abs(rep["real_rate"].value - gained / SECONDS * 3600) < 1)
check(f"level read from the EXP table: {rep.info['level']}", rep.info["level"] == LEVEL)
check(f"HP/MP read ({rep['gauge_readings'].value} readings)", rep["gauge_readings"].ok and rep["gauge_readings"].value > 10)
casts = rep["skill_casts"].value if rep.get("skill_casts") and rep["skill_casts"].ok else None
check(f"Lucky Seven casts from MP: 19 ({casts})", casts is not None and 17 <= casts <= 19)
pots = rep["mp_potions"].value if rep.get("mp_potions") and rep["mp_potions"].ok else None
check(f"Blue Potions from MP jumps: 1 at 30 s ({pots})", pots == 1)
wallet = [e.get("value") for e in events if e.kind == "wallet"]
check(f"mesos read from the inventory: {wallet[:3]}", wallet and set(wallet) == {PARTS['inventory']['inventory_gh11.png']['mesos']})
parties = [e.get("size") for e in events if e.kind == "party"]
check(f"party size: solo ({parties})", parties and parties[-1] == 1)
kc = rep["kills_by_confidence"].value
check(f"kills seen and confirmed (solo, damage, EXP): {kc}", kc.get("confirmed", 0) >= 16)
check("no kills made up", sum(kc.values()) <= 19)
check(f"MP per kill ≈ 16 ({rep['mp_per_kill'].value:.1f})", rep["mp_per_kill"].ok and 14 <= rep["mp_per_kill"].value <= 18)
check(f"attacks per kill: 1 damage number each ({rep['attacks_per_kill'].value})",
      rep["attacks_per_kill"].ok and abs(rep["attacks_per_kill"].value - 1) < 0.2)
check("the session is a 'video' session with its tags", session["source"] == "video"
      and session["tags"].get("attack_mode") == "Lucky Seven")
check(f"analysis is faster than real time ({took:.1f} s < {SECONDS} s)", took < SECONDS)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
