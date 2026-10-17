"""Tests for the EXPERIMENTAL monster vision, on synthetic scenes (made-up sprites over a
textured world; see synth_scene.py). They check the logic (spawn vs walking in, death vs
walking out, scrolling, attribution, waves) and the false-positive guards, not accuracy on
real game footage, which still has to be measured on recordings.

Run from the project folder:  py -3 tests/test_vision.py
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
import numpy as np  # noqa: E402

from farmkit import vision as v  # noqa: E402
from synth_scene import make_sprite, make_world, render  # noqa: E402

failures = 0
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


WORLD = make_world()
NECKI = make_sprite((40, 200, 90), (30, 26), seed=1)
WRAITH = make_sprite((200, 200, 230), (34, 28), seed=2)
STIRGE = make_sprite((120, 40, 150), (22, 30), seed=3)
SPRITES = {"Jr. Necki": [NECKI], "Jr. Wraith": [WRAITH], "Stirge": [STIRGE]}


def run(monsters, seconds=10.0, fps=10, cam=lambda t: 0, view_w=560, roi=None, damage=True, sprites=SPRITES,
        detector=None):
    an = v.VisionAnalyzer(detector or v.SpriteDetector(sprites), damage=v.DamageDetector() if damage else None)
    for i in range(int(seconds * fps)):
        t = i / fps
        f = render(WORLD, cam(t), view_w, monsters, t)
        if roi:
            x, y, w, h = roi
            f = f[y:y + h, x:x + w]
        an.process(f, t)
    an.finish(seconds)
    return an


def kinds(an):
    return sorted(e["event"] for e in an.events)


def mon(spr, x, y, born=0.0, dies=1e9, vx=0, damage=()):
    return {"sprite": spr, "x": x, "y": y, "born": born, "dies": dies, "vx": vx, "damage": list(damage)}


solo = lambda t: 1
party4 = lambda t: 4

# ---- deaths
an = run([mon(NECKI, 260, 200, dies=5, damage=[4.2, 4.6])])
check(f"a monster already there dies mid-region: present, then vanish ({kinds(an)})", kinds(an) == ["present", "vanish"])
van = [e for e in an.events if e["event"] == "vanish"][0]
check(f"the damage numbers on it are counted as hits ({van['hits']})", van["hits"] == 2)
check(f"its species is recognised ({van['species']})", van["species"] == "Jr. Necki")
lab = v.classify_vanishes(an.events, [(5.6, 40)], solo)
check("solo + damage + EXP right after: confirmed kill",
      [e["confidence"] for e in lab if e["event"] == "vanish"] == ["confirmed"])
lab = v.classify_vanishes(an.events, [(5.6, 40)], party4)
check("same in a party: only 'probable' (anyone in the party may have killed it)",
      [e["confidence"] for e in lab if e["event"] == "vanish"] == ["probable"])

# ---- disappearance without death evidence
an = run([mon(NECKI, 260, 200, dies=5)])
lab = v.classify_vanishes(an.events, [], solo)
check("vanished with no damage and no EXP: unattributed (someone else's kill or a despawn)",
      [e["confidence"] for e in lab if e["event"] == "vanish"] == ["unattributed"])
lab = v.classify_vanishes(an.events, [(5.5, 40)], solo)
check("solo, EXP but no damage seen: probable", [e["confidence"] for e in lab if e["event"] == "vanish"] == ["probable"])
lab = v.classify_vanishes(an.events, [(5.5, 40)], party4)
check("party, EXP but no damage seen: party credit (never counted as yours)",
      [e["confidence"] for e in lab if e["event"] == "vanish"] == ["party_credit"])
lab = v.classify_vanishes(an.events, [(9.5, 40)], solo)
check("EXP long after doesn't count as evidence", [e["confidence"] for e in lab if e["event"] == "vanish"] == ["unattributed"])

# ---- multiple simultaneous kills
an = run([mon(NECKI, 150, 200, dies=5, damage=[4.5]), mon(WRAITH, 350, 180, dies=5, damage=[4.5])])
vans = [e for e in an.events if e["event"] == "vanish"]
check(f"two monsters dying together: two vanishes ({len(vans)})", len(vans) == 2)
check("each keeps its own species", sorted(e["species"] for e in vans) == ["Jr. Necki", "Jr. Wraith"])
lab = v.classify_vanishes(an.events, [(5.5, 80)], solo)
check("one EXP reading confirms both (two kills between readings)",
      [e["confidence"] for e in lab if e["event"] == "vanish"] == ["confirmed", "confirmed"])

# ---- movement: walking in and out isn't spawning or dying
an = run([mon(NECKI, -40, 220, vx=60)], seconds=12)
check(f"walking in from the edge and out the other side: entered + left ({kinds(an)})", kinds(an) == ["entered", "left"])
an = run([mon(STIRGE, 200, 120, vx=12)], seconds=8)
check(f"a monster moving around mid-region: no spawn, no vanish ({kinds(an)})", kinds(an) == ["present"])
an = run([mon(STIRGE, 200, 120, born=2, vx=12)], seconds=8)
check(f"one appearing mid-region later is a respawn ({kinds(an)})", kinds(an) == ["spawn"])

# ---- screen scrolling
cam = lambda t: 0 if t < 3 else min(160, int((t - 3) * 60))
an = run([mon(NECKI, 260, 200), mon(WRAITH, 420, 160)], seconds=9, cam=cam)
check(f"scrolling the view doesn't make monsters spawn or die ({kinds(an)})", kinds(an) == ["present", "present"])
cam = lambda t: 0 if t < 2 else min(400, int((t - 2) * 120))
an = run([mon(NECKI, 120, 200), mon(WRAITH, 800, 160)], seconds=8, cam=cam)
k = kinds(an)
check(f"scrolling brings monsters in at the edge (entered) and out (left): {k}",
      "vanish" not in k and "left" in k and "entered" in k)
jump = lambda t: 0 if t < 4 else 900
an = run([mon(NECKI, 260, 200), mon(WRAITH, 1250, 150)], seconds=8, cam=jump)
check(f"a teleport/map change resets tracking without false deaths or respawns ({kinds(an)}, resets {an.resets})",
      kinds(an) == ["present", "present"] and an.resets == 1)

# ---- spawns and waves
mons = []
for wave in range(4):                                    # a wave every 8 s; each lives 5 s
    for k in range(3):
        mons.append(mon(NECKI, 120 + 140 * k, 200, born=1 + 8 * wave, dies=6 + 8 * wave, damage=[5.5 + 8 * wave]))
an = run(mons, seconds=34)
ev = v.classify_vanishes(an.events, [(6.2 + 8 * w, 120) for w in range(4)], solo)
summ = v.summarize(ev, an.frames, real_seconds=34)
check(f"respawns detected ({summ['spawns_hr'].detail['count']} of 12)", summ["spawns_hr"].detail["count"] == 12)
check(f"spawn waves 8 s apart ({summ['wave_interval'].value:.1f} s)", abs(summ["wave_interval"].value - 8) < 0.5)
check(f"3 deaths between waves ({summ['deaths_per_wave'].value:.1f})", abs(summ["deaths_per_wave"].value - 3) < 0.01)
check("spawn rates are labelled as the observed region only", "region" in summ["spawns_hr"].note)
check("rates carry a 95% interval", summ["spawns_hr"].detail["ci95"][0] < summ["spawns_hr"].value < summ["spawns_hr"].detail["ci95"][1])
check(f"visible monsters averaged ({summ['visible_avg'].value:.2f}, max {summ['visible_max'].value})",
      summ["visible_max"].value == 3 and 1 < summ["visible_avg"].value < 3)
check(f"your share of deaths ({summ['your_share'].value:.0%})", summ["your_share"].value == 1.0)

# ---- a platform region: monsters outside it aren't counted
platform = (0, 140, 300, 140)                            # x, y, w, h in the view
an = run([mon(NECKI, 120, 190, born=1, dies=6), mon(WRAITH, 420, 190, born=1, dies=6)], roi=platform)
check(f"only the platform's monster is seen ({kinds(an)})", kinds(an) == ["spawn", "vanish"]
      and all(e["species"] == "Jr. Necki" for e in an.events))

# ---- false positives
an = run([], seconds=6)
check("no monsters, busy background: nothing detected", an.events == [] and max(n for _, n in an.frames) == 0)
noise = np.random.default_rng(5)
an2 = v.VisionAnalyzer(v.SpriteDetector(SPRITES), damage=v.DamageDetector())
for i in range(40):
    an2.process(noise.integers(0, 255, (400, 560, 3), dtype=np.uint8), i / 10)
check("pure noise: nothing detected", an2.events == [])


def occluded(t):                                          # a "player" walks in front for 0.3 s
    return 3.0 <= t < 3.3


mons = [mon(NECKI, 260, 200)]
an = v.VisionAnalyzer(v.SpriteDetector(SPRITES), damage=None)
for i in range(80):
    t = i / 10
    f = render(WORLD, 0, 560, mons, t)
    if occluded(t):
        f[190:240, 250:300] = (200, 120, 60)
    an.process(f, t)
an.finish(8)
check(f"a short occlusion doesn't count as a death and a respawn ({kinds(an)})", kinds(an) == ["present"])

# ---- foreground detector (no sprites needed)
fg = v.ForegroundDetector(learn_frames=10)
an = v.VisionAnalyzer(fg, damage=None)
for i in range(80):
    t = i / 10
    an.process(render(WORLD, 0, 560, [mon(NECKI, 100, 200, born=3, dies=7, vx=20)], t), t)
an.finish(8)
k = kinds(an)
check(f"foreground detector finds a moving monster without sprites ({k})", k.count("spawn") == 1 and k.count("vanish") == 1)
check("its species is unknown (it can't tell)", all(e["species"] == "unknown" for e in an.events))

# ---- performance
an = v.VisionAnalyzer(v.SpriteDetector(SPRITES), damage=v.DamageDetector())
mons = [mon(NECKI, 100 + 90 * k, 150 + 20 * (k % 3), vx=(-1) ** k * 10) for k in range(5)]
tracemalloc.start()
frames = [render(WORLD, 0, 560, mons, i / 10) for i in range(60)]
t0 = time.perf_counter()
for i, f in enumerate(frames):
    an.process(f, i / 10)
per = (time.perf_counter() - t0) / len(frames) * 1000
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(f"     (560x400 region, 3 species x 2 directions, 5 monsters: {per:.1f} ms/frame, peak {peak / 1e6:.1f} MB)")
check(f"vision keeps up with 5 frames/s on a 560x400 region ({per:.0f} ms/frame < 200)", per < 200)
small = [f[150:280, 0:300] for f in frames]
t0 = time.perf_counter()
an = v.VisionAnalyzer(v.SpriteDetector(SPRITES), damage=v.DamageDetector())
for i, f in enumerate(small):
    an.process(f, i / 10)
per_small = (time.perf_counter() - t0) / len(small) * 1000
check(f"a platform-sized region is much cheaper ({per_small:.1f} ms/frame)", per_small < per)

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
