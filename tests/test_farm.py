"""Tests for the farm analytics (farmkit): timing, economy, HP/MP, stats, storage, reports,
rankings and export. No game, screen or network needed.

Run from the project folder:  py -3 tests/test_farm.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["MAPLE_EXP_DATA"] = tempfile.mkdtemp()           # never touch your real data
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cv2  # noqa: E402

from farmkit import analytics, export, stats  # noqa: E402
from farmkit.economy import Ledger, Price  # noqa: E402
from farmkit.events import Event, txn  # noqa: E402
from farmkit.gauges import GaugeConfirm, GaugeSeries, Gauge, label_gauges, read_gauges  # noqa: E402
from farmkit.metrics import ESTIMATED, INFERRED, OBSERVED, UNAVAILABLE  # noqa: E402
from farmkit.recorder import Recorder  # noqa: E402
from farmkit.store import Store  # noqa: E402
from farmkit.timing import Timeline  # noqa: E402

failures = 0
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name, ok):
    global failures
    print(("PASS " if ok else "FAIL ") + name)
    failures += not ok


def near(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol * max(1, abs(b))


def gains_every(start, end, every, amount, level=39):
    t, out = start, []
    while t < end:
        out.append(Event(t, "exp", {"amount": amount, "exp": 1000, "pct": 1.0, "level": level}))
        t += every
    return out


# ============================================================ timing: real vs active

# Steady farming: 100 EXP every 3 s for 10 minutes.
ev = [Event(0, "start")] + gains_every(1, 600, 3, 100) + [Event(600, "stop")]
tl = Timeline(ev, gap=15)
check(f"steady farming: real time is 10 min ({tl.real_seconds})", near(tl.real_seconds, 600))
check(f"steady farming: active ≈ real ({tl.active_seconds:.0f} s)", tl.active_seconds > 595)
check(f"steady farming: 20 gains/min = 120,000 EXP/hr real ({tl.real_rate().value:,.0f})",
      near(tl.real_rate().value, 200 * 100 * 3600 / 600, 0.01))

# Congested map: 5 min of farming, then 5 min with no monsters (waiting for respawn).
ev = [Event(0, "start")] + gains_every(1, 300, 3, 100) + [Event(600, "stop")]
tl = Timeline(ev, gap=15)
check(f"waiting for respawns counts in real time ({tl.real_seconds:.0f} s)", near(tl.real_seconds, 600))
check(f"...but not in active time ({tl.active_seconds:.0f} s)", 295 <= tl.active_seconds <= 300)
check(f"so real EXP/hr is about half of active ({tl.real_rate().value:,.0f} vs {tl.active_rate().value:,.0f})",
      0.45 <= tl.real_rate().value / tl.active_rate().value <= 0.55)
check(f"idle time is reported ({tl.idle_seconds:.0f} s)", 295 <= tl.idle_seconds <= 305)

tl2 = Timeline(ev, gap=600)
check("the inactivity rule can be changed afterwards (gap 600 s: active = real)", near(tl2.active_seconds, 600))
tl3 = Timeline(ev, gap=15, auto_pause=False)
check("auto-pause off: active = real", near(tl3.active_seconds, tl3.real_seconds))

# Manual pause and AFK are left out of real time; EXP in them isn't counted.
ev = ([Event(0, "start")] + gains_every(1, 120, 3, 100) + [Event(120, "pause"), Event(300, "resume")]
      + gains_every(301, 420, 3, 100) + [Event(420, "afk_on"), Event(600, "afk_off")] + [Event(700, "stop")])
tl = Timeline(ev, gap=15)
check(f"Pause and AFK time aren't real time (420 - 180 + ... = {tl.real_seconds:.0f} s)",
      near(tl.real_seconds, 700 - 180 - 180))
check(f"AFK time is reported on its own ({tl.afk_seconds:.0f} s)", near(tl.afk_seconds, 180))
check(f"paused time is reported on its own ({tl.paused_seconds:.0f} s)", near(tl.paused_seconds, 180))
check("eff() skips paused time", near(tl.eff(300), 120) and near(tl.eff(150), 120) and near(tl.eff(310), 130))
ev_x = ev + [Event(200, "exp", {"amount": 5000})]
check("EXP read while paused isn't counted", Timeline(ev_x).exp_gained == Timeline(ev).exp_gained)

# Rolling windows: fast for the last minute only.
ev = [Event(0, "start")] + gains_every(1, 840, 6, 100) + gains_every(840, 900, 1, 100) + [Event(900, "stop")]
tl = Timeline(ev, gap=15)
w = tl.windows()
check(f"1-min rate sees the burst ({w[60].value:,.0f}/hr)", near(w[60].value, 360000, 0.02))
check(f"5-min rate is in between ({w[300].value:,.0f}/hr)", 60000 < w[300].value < 360000)
check(f"15-min rate covers everything ({w[900].value:,.0f})", near(w[900].value, tl.real_rate().value, 1e-9))
early = Timeline([Event(0, "start")] + gains_every(1, 120, 3, 100), now=120)
check("15-min rate early on is labelled partial", early.window_rate(900).confidence == "medium"
      and "so far" in early.window_rate(900).note)
check("no rate before 30 s", Timeline([Event(0, "start"), Event(10, "exp", {"amount": 5})], now=20).window_rate(60).value is None)

# Deaths are recorded but not taken off.
ev = ([Event(0, "start")] + gains_every(1, 300, 3, 100) + [Event(150, "death", {"loss": 2000})] + [Event(300, "stop")])
tl = Timeline(ev)
check("death loss recorded, not subtracted", tl.exp_lost == 2000 and tl.exp_gained == 100 * 100)

# Misread corrections (negative gains) cancel out.
ev = [Event(0, "start"), Event(10, "exp", {"amount": 9000}), Event(12, "exp", {"amount": -9000}),
      Event(14, "exp", {"amount": 50}), Event(60, "stop")]
check("a misread taken back cancels out", Timeline(ev).exp_gained == 50)

# Per map and solo/party.
ev = ([Event(0, "start"), Event(0, "map", {"name": "Jr. Necki A"}), Event(0, "party", {"size": 1})]
      + gains_every(1, 300, 3, 100) + [Event(300, "map", {"name": "Jr. Wraith B"}), Event(300, "party", {"size": 4})]
      + gains_every(301, 600, 3, 50) + [Event(600, "stop")])
tl = Timeline(ev)
bm = tl.by_map()
check(f"EXP per map: {bm['Jr. Necki A']['exp']:.0f} and {bm['Jr. Wraith B']['exp']:.0f}",
      bm["Jr. Necki A"]["exp"] == 10000 and bm["Jr. Wraith B"]["exp"] == 5000)
check("time per map", near(bm["Jr. Necki A"]["real_seconds"], 300) and near(bm["Jr. Wraith B"]["real_seconds"], 300))
sp = tl.solo_vs_party()
check("solo vs party EXP", sp["solo"]["exp"] == 10000 and sp["party"]["exp"] == 5000)
check("active time split by map adds up", near(sum(d["active_seconds"] for d in bm.values()), tl.active_seconds))

# Time to level
r = tl.real_rate()
ttl = tl.time_to_level(r, 2000)
check("time to level uses the last EXP reading", ttl.ok and near(ttl.value, (2000 - 1000) / r.value * 3600))

# ============================================================ economy: no double counting

P = [Price("Blue Potion", 220, "consumable", 0, 200), Price("Iron Ore", 3000, "loot"),
     Price("Mokbi Throwing-Stars", 300000, "loot"), Price("Subi Throwing-Stars", None, "ammo")]


def wallet(*vals):
    return [Event(i * 60.0, "wallet", {"value": v}) for i, v in enumerate(vals)]


# The example from the brief: starts with 100,000, ends with 120,000.
led = Ledger(wallet(100000, 110000, 120000), P)
check("wallet change is end minus start (20,000)", led.wallet_change().value == 20000)
check("realized net = 20,000 with no notes", led.realized_net().value == 20000)

# Mid-session: bought 100 Blue Potions (22,000): the wallet already shows it.
ev = wallet(100000, 125000, 103000, 120000) + [txn(130, "purchase", item="Blue Potion", qty=100, unit_price=220)]
led = Ledger(ev, P)
check("potion purchase isn't subtracted twice: realized net still 20,000", led.realized_net().value == 20000)
check(f"pickups inferred = 20,000 + 22,000 = 42,000 ({led.pickups().value:,.0f})",
      led.pickups().value == 42000 and led.pickups().kind == INFERRED)
check("the purchase was explained, so no unexplained jump", not led.unexplained_jumps())

# Used 30 of them (counted by hand): cost 6,600; 70 still in the bag is stock, not a loss.
ev2 = ev + [txn(400, "use", item="Blue Potion", qty=30)]
led = Ledger(ev2, P)
check(f"consumables used = 30 × 220 = 6,600 ({led.consumables_used().value})", led.consumables_used().value == 6600)
check(f"stock change = 70 × 220 = 15,400 ({led.stock_change().value})", led.stock_change().value == 15400)
check(f"operating profit = 42,000 - 6,600 = 35,400 ({led.operating_profit().value})",
      led.operating_profit().value == 35400)
check(f"estimated total = 20,000 + 15,400 = 35,400 ({led.estimated_total().value})", led.estimated_total().value == 35400)
check("both profits agree when everything is priced at cost", led.operating_profit().value == led.estimated_total().value)

# Same farming, but the potions were bought before the session: profit must be the same.
ev3 = wallet(100000, 125000, 125000, 142000) + [txn(400, "use", item="Blue Potion", qty=30)]
led3 = Ledger(ev3, P)
check("operating profit doesn't depend on when potions were bought", led3.operating_profit().value == 35400)

# Storage: depositing 50,000 isn't a loss.
ev4 = wallet(100000, 120000, 70000) + [txn(125, "deposit", amount=50000)]
led = Ledger(ev4, P)
check("a storage deposit isn't counted as a loss (realized 20,000)", led.realized_net().value == 20000)
ev5 = wallet(100000, 300000) + [txn(30, "withdraw", amount=200000)]
check("a withdrawal isn't income", Ledger(ev5, P).realized_net().value == 0)
ev6 = wallet(100000, 300000) + [txn(30, "transfer_in", amount=200000)]
check("mesos from another character aren't income", Ledger(ev6, P).realized_net().value == 0)

# An unexplained big change is flagged and lowers confidence.
led = Ledger(wallet(100000, 104000, 20000, 25000), P)
check("an unexplained big drop is flagged", len(led.unexplained_jumps()) == 1 and led.realized_net().confidence == "low")

# Sales and loot.
ev7 = (wallet(500000, 520000, 529000) + [Event(0, "snapshot", {"phase": "start", "items": {"Iron Ore": 2}}),
                                          Event(200, "snapshot", {"phase": "end", "items": {"Iron Ore": 7}}),
                                          txn(100, "sale", item="Iron Ore", qty=3, unit_price=3000)])
led = Ledger(ev7, P)
f = led.item_flows()["Iron Ore"]
check(f"snapshots: looted = 7 - 2 + 3 sold = 8 ({f['looted']})", f["looted"] == 8 and f["source"] == OBSERVED)
check(f"unsold loot value = 5 × 3,000 = 15,000 ({led.loot_value().value})", led.loot_value().value == 15000)
check("loot value is an estimate", led.loot_value().kind == ESTIMATED)
check(f"pickups exclude the sale (29,000 - 9,000 = 20,000: {led.pickups().value})", led.pickups().value == 20000)
check(f"operating profit includes the loot sold (20,000 + 9,000 = {led.operating_profit().value})",
      led.operating_profit().value == 29000)
check(f"estimated total = realized 29,000 + unsold 15,000 ({led.estimated_total().value})",
      led.estimated_total().value == 44000)

# Snapshot-measured potion use.
ev8 = wallet(0, 1000) + [Event(0, "snapshot", {"phase": "start", "items": {"Blue Potion": 200}}),
                         Event(100, "snapshot", {"phase": "end", "items": {"Blue Potion": 150}})]
led = Ledger(ev8, P)
check("potions used from snapshots are observed", led.consumables_used().detail["items"]["Blue Potion"]["source"] == OBSERVED
      and led.consumables_used().value == 50 * 220)
check("estimates don't override observed counts", led.consumables_used({"Blue Potion": 999}).value == 50 * 220)
check("estimated potion use is labelled estimated", Ledger(wallet(0, 1), P).consumables_used({"Blue Potion": 5}).kind == ESTIMATED)
unpriced = Ledger(wallet(0, 1) + [txn(5, "use", item="Subi Throwing-Stars", qty=100)], P).consumables_used()
check("unpriced items are left out and said so", not unpriced.ok and "Subi" in unpriced.note)

# Recharges and expenses are operating costs; equipment is an investment.
ev9 = wallet(100000, 90000) + [txn(10, "recharge", amount=8000), txn(20, "equipment", amount=50000)]
ev9[1] = Event(60, "wallet", {"value": 42000})
led = Ledger(ev9, P)
check(f"recharge counts against operating profit ({led.operating_profit().value})",
      led.operating_profit().value == 42000 - 100000 + 58000 - 8000)
check("equipment isn't a farming cost in the estimated total", led.estimated_total().value == -58000 + 50000)
check("one wallet reading: no change claimed", Ledger(wallet(5), P).wallet_change().kind == UNAVAILABLE)
try:
    txn(0, "purchase", amount=-5)
    check("negative amounts are refused", False)
except ValueError:
    check("negative amounts are refused", True)

# ============================================================ HP/MP

tpl = None
try:
    import maple_exp as m
    tpl = m.DigitTemplates.load()
except Exception as e:
    print("     (maple_exp not importable here:", e, ")")
FR = ROOT / "tests" / "frames"
if tpl:
    want = {"status_gh7.png": (993, 1134), "status_gh11.png": (1033, 1134), "status_gh28.png": (482, 1178),
            "status_gh28_rogue.png": (624, 862)}
    for name, (cur, mx) in want.items():
        g = read_gauges(cv2.imread(str(FR / name)), tpl)
        check(f"reads HP {cur}/{mx} from {name}: {[(x.cur, x.max) for x in g]}", [(x.cur, x.max) for x in g] == [(cur, mx)])
    for name in ("exp_full-1920x1080.png", "exp_full-1280x720.png", "exp_win-1280x720.png"):
        check(f"the EXP readout isn't taken for HP/MP ({name})", read_gauges(cv2.imread(str(FR / name)), tpl) == [])
    import numpy as np
    img = cv2.imread(str(FR / "status_gh28.png"))
    rng = np.random.default_rng(3)
    wrong = 0
    for _ in range(40):                                  # noise: either the right number or nothing
        noisy = np.clip(img.astype(int) + rng.integers(-35, 35, img.shape), 0, 255).astype(np.uint8)
        got = read_gauges(noisy, tpl)
        wrong += bool(got) and (got[0].cur, got[0].max) != (482, 1178)
    check(f"noisy status bars never read a wrong HP ({wrong} wrong of 40)", wrong == 0)

check("one readout can't be labelled HP or MP", label_gauges([Gauge(1, 2, 0, 0, 7)]) == {})
lab = label_gauges([Gauge(100, 200, 10, 0, 7), Gauge(50, 80, 50, 0, 7)])
check("two readouts: HP then MP", lab["hp"].cur == 100 and lab["mp"].cur == 50)
gc = GaugeConfirm()
check("a gauge needs its max read twice", gc.feed("mp", Gauge(5, 80, 0, 0, 7)) is None
      and gc.feed("mp", Gauge(4, 80, 0, 0, 7)) is not None and gc.feed("mp", Gauge(4, 81, 0, 0, 7)) is None)


def gauge_ev(t, mp, mp_max=400, hp=500, hp_max=1000):
    return Event(t, "gauge", {"hp": hp, "hp_max": hp_max, "mp": mp, "mp_max": mp_max})


# 10 Lucky Sevens (16 MP each), two potions (200 MP), a little regen, and a level-up refill.
mps = [300]
for _ in range(10):
    mps.append(mps[-1] - 16)            # 300 -> 140
mps += [mps[-1] + 200]                  # potion: 340
mps += [mps[-1] + 3]                    # regen: 343
mps += [mps[-1] - 32]                   # two casts in one reading: 311
mps += [400]                            # potion near full: only fills the 89 MP of room
evg = [gauge_ev(i * 1.5, v) for i, v in enumerate(mps)]
evg.append(gauge_ev(len(mps) * 1.5 + 10, 450, mp_max=450))     # level-up: max rose, refilled
gs = GaugeSeries(evg, levels=[len(mps) * 1.5 + 10])
check(f"MP spent = 12 casts × 16 = 192 ({gs.spent('mp')})", gs.spent("mp") == 192)
casts, other = gs.casts(16)
check(f"casts from MP drops: 12 ({casts}, other {other})", casts == 12 and other == 0)
check(f"potions from MP jumps: 2 ({gs.potions('mp', 200)})", gs.potions("mp", 200) == 2)
check("natural regen isn't a potion", gs.regen("mp", 100) == 3)
summ = gs.summary(mp_potion=("Blue Potion", 200), skill=("Lucky Seven", 16))
check("potion counts are labelled estimated", summ["mp_potions"].kind == ESTIMATED)
hs = GaugeSeries([Event(0, "gauge", {"hp": 100, "hp_max": 1000}), Event(1.5, "gauge", {"hp": 400, "hp_max": 1000})])
check("in a party, HP jumps are low-confidence (could be Heal)",
      hs.summary(hp_potion=("White Potion", 300), party=4)["hp_potions"].confidence == "low")

# ============================================================ stats

lo, hi = stats.clopper_pearson(1, 2000)
check(f"1 drop in 2,000: 95% interval {1/hi:,.0f} to {1/lo:,.0f} kills per drop", 300 < 1 / hi < 450 and 50000 < 1 / lo < 120000)
lo, hi = stats.clopper_pearson(0, 100)
check(f"0 in 100: upper bound about 3.6% ({hi:.4f})", lo == 0 and 0.035 < hi < 0.038)
lo, hi = stats.wilson(50, 100)
check("Wilson interval for 50/100 is about 40%-60%", 0.39 < lo < 0.41 and 0.59 < hi < 0.61)
lo, hi = stats.poisson_rate_interval(10, 3600)
check(f"10 events in an hour: about 4.8 to 18.4 per hour ({lo:.1f}-{hi:.1f})", 4.7 < lo < 4.9 and 18.2 < hi < 18.5)
check("drop summary warns about small samples", "Too few" in stats.drop_rate_summary(1, 2000)["text"])

# ============================================================ storage and upstream data

tmp = Path(tempfile.mkdtemp())
store = Store(tmp / "farm.db")
check("prices are seeded (Blue Potion 220, ores unpriced)",
      any(p.item == "Blue Potion" and p.price == 220 for p in store.prices())
      and any(p.item == "Iron Ore" and p.price is None for p in store.prices()))
check("Lucky Seven MP cost is seeded", store.skills().get("Lucky Seven") == 16)
store.set_price(Price("Iron Ore", 2500, "loot"))
check("prices can be edited", next(p for p in store.prices() if p.item == "Iron Ore").price == 2500)
csvp = tmp / "prices.csv"
csvp.write_text("item,price,kind\nGarnet Ore,4000,loot\nMana Elixir,,consumable\n", encoding="utf-8")
check("prices import from CSV", store.import_prices_csv(csvp) == 2
      and next(p for p in store.prices() if p.item == "Garnet Ore").price == 4000)
Store(tmp / "farm.db").close()
check("reopening keeps edits (no reseeding over them)", next(p for p in Store(tmp / "farm.db").prices() if p.item == "Iron Ore").price == 2500)

for version in sorted(os.listdir(ROOT / "tests" / "upgrade")):
    s2 = Store(tmp / f"legacy-{version}.db")
    log_path = ROOT / "tests" / "upgrade" / version / "exp_log.json"
    before = log_path.read_bytes()
    n = s2.import_legacy(log_path)
    n2 = s2.import_legacy(log_path)
    entries = json.loads(before).get("entries", [])
    legacy = s2.sessions(source="legacy")
    check(f"upstream {version} log imports ({n} lines, {len(legacy)} sessions), re-import adds nothing",
          n == len(entries) == len(legacy) and n2 == n)
    check(f"upstream {version} log file is left untouched", log_path.read_bytes() == before)
    if legacy:
        rep = analytics.session_report(legacy[0], [], s2.prices())
        check(f"legacy lines are marked active-time only ({version})",
              rep.info["basis"] == "active" and not rep["real_rate"].ok)

# ============================================================ recorder → store → report

st = Store(tmp / "rec.db")
rec = Recorder(st, "test")
now = 1000.0
sid = rec.begin(now, settings={"gap": 15}, map_name="Jr. Necki A", party=1, job="Assassin", level=39)
rec.wallet(now, 100000)
rec.gauges(now, 500, 1000, 400, 400)
for i in range(1, 201):                                   # 10 minutes, a gain every 3 s
    t = now + i * 3
    rec.exp(t, 120, exp=10000 + i * 120, pct=3.0, level=39)
    if i % 10 == 0:
        rec.gauges(t, 500, 1000, 400 - 16 * (i // 10 % 5), 400)
rec.wallet(now + 300, 112000)
rec.txn(now + 310, "purchase", item="Blue Potion", qty=50, unit_price=220)
rec.wallet(now + 320, 101000)
rec.wallet(now + 600, 125000)
rec.txn(now + 600, "use", item="Blue Potion", qty=20)
rec.tag(now + 600, "attack_mode", "Lucky Seven")
rec.end(now + 600)
events = st.events(sid)
check(f"the recorder stored the session's events ({len(events)})", len(events) > 200)
check("events are relative to the session start", events[0].kind == "start" and events[0].t == 0)
rep = analytics.session_report(st.session(sid), events, st.prices(), st.skills())
check(f"report: real EXP/hr {rep['real_rate'].value:,.0f}", near(rep["real_rate"].value, 200 * 120 / 600 * 3600, 0.01))
check(f"report: realized net 25,000 ({rep['realized_net'].value})", rep["realized_net"].value == 25000)
check(f"report: pickups 25,000 + 11,000 = 36,000 ({rep['pickups'].value})", rep["pickups"].value == 36000)
check(f"report: potion cost 4,400 ({rep['consumables_used'].value})", rep["consumables_used"].value == 4400)
check("report: tags are kept", rep.info["tags"].get("attack_mode") == "Lucky Seven")
check("report: MP spent is observed", rep["mp_spent"].kind == OBSERVED and rep["mp_spent"].value > 0)
check("report: kills aren't made up with vision off", not rep["personal_kills"].ok)
check("map stored on the session", st.session(sid)["map"] == "Jr. Necki A" and st.session(sid)["level_end"] == 39)


def fake_session(store, rate_per_3s, profit_end, minutes=10, party=1, mapn="M"):
    r = Recorder(store, "t")
    sid = r.begin(0.0, map_name=mapn, party=party)
    r.wallet(0.0, 100000)
    for i in range(1, minutes * 20 + 1):
        r.exp(i * 3.0, rate_per_3s)
    r.wallet(minutes * 60.0, 100000 + profit_end)
    r.txn(minutes * 60.0, "use", item="Blue Potion", qty=1)
    r.end(minutes * 60.0)
    return sid


st2 = Store(tmp / "rank.db")
a = fake_session(st2, 150, -20000 * 10 / 60, mapn="Fast but costly")      # 180k/hr, loses mesos
b = fake_session(st2, 125, 10000 * 10 / 60, mapn="Slower, profitable")    # 150k/hr, makes mesos
c = fake_session(st2, 50, 0, minutes=2, mapn="Too short")
reports = [r for _, r in export.reports_for(st2)]
ranked, left = analytics.rank(reports, "exp_hr")
check("ranked by EXP/hr: fast map first", [r.info["map"] for r, _, _ in ranked][:2] == ["Fast but costly", "Slower, profitable"])
check("sessions under 5 minutes are left out, with a reason", any("shorter" in why for _, why in left))
ranked, left = analytics.rank(reports, "exp_hr_profitable", basis="operating")
check("best EXP/hr without losing mesos picks the profitable map",
      [r.info["map"] for r, _, _ in ranked] == ["Slower, profitable"])
check("the losing map is listed with how much it loses", any("loses" in why for _, why in left))
ranked, _ = analytics.rank(reports, "realized_mesos_hr")
check("ranked by realized mesos/hr", ranked[0][0].info["map"] == "Slower, profitable")
table = analytics.compare(reports[:2])
check("comparison table has a row per metric and a column per session",
      len(table) == len(analytics.COMPARE_ROWS) and all(len(cells) == 2 for _, cells in table))
groups = analytics.group_by(reports)
check("map efficiency groups sessions by map", {g["key"] for g in groups} == {"Fast but costly", "Slower, profitable", "Too short"})

out = tmp / "s.csv"
n = export.to_csv(st2, out)
rows = out.read_text(encoding="utf-8").splitlines()
check(f"CSV export: a header and {n} sessions", n == 3 and len(rows) == 4 and "real_rate_kind" in rows[0])
outj = tmp / "s.json"
export.to_json(st2, outj)
data = json.loads(outj.read_text(encoding="utf-8"))
check("JSON export has metrics with their kind, and raw events",
      data["sessions"][0]["metrics"]["real_rate"]["kind"] == "calculated" and data["sessions"][0]["events"])

print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
