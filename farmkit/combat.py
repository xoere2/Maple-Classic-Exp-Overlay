"""Combat efficiency: attacks per kill, MP per kill, EXP and mesos per MP, and a clearly
labelled what-if for weapon upgrades.

Measured numbers come from sessions (kills from vision, MP from the status bar, casts
from MP drops, your own tags such as attack_mode=Lucky Seven). The what-if uses the
community's pre-Big-Bang damage formulas, which haven't been checked against Classic
World: it's for deciding what to test, and measured sessions always win.
"""
import random

from .metrics import CALCULATED, ESTIMATED, Metric, missing


def hits_distribution(kill_events):
    """From vision kill events with an observed hit count: share of one-shot, two-shot...
    kills, and the average. Only kills where hits were seen count."""
    hits = [int(e.get("hits") or 0) for e in kill_events if (e.get("hits") or 0) > 0]
    if not hits:
        return {"kills_with_hits": 0, "avg": missing("no damage numbers matched to kills"), "share": {}}
    share = {}
    for h in hits:
        key = str(h) if h < 5 else "5+"
        share[key] = share.get(key, 0) + 1
    share = {k: v / len(hits) for k, v in sorted(share.items())}
    return {"kills_with_hits": len(hits),
            "avg": Metric(sum(hits) / len(hits), ESTIMATED, "low" if len(hits) < 30 else "medium",
                          f"damage numbers seen on {len(hits)} kills (EXPERIMENTAL vision)"),
            "share": share}


def per_unit(numerator, denominator, note):
    """numerator / denominator as a Metric (provenance is the weaker of the two)."""
    from .metrics import weakest
    if not numerator.ok or not denominator.ok or not denominator.value:
        return missing(note + ": not enough data")
    return Metric(numerator.value / denominator.value, weakest(numerator.kind, denominator.kind, CALCULATED),
                  "low" if "low" in (numerator.confidence, denominator.confidence) else "medium", note)


# ---- what-if (theory)

CLAW_FORMULA = "pre-BB claw: max = (LUK×3.6 + STR + DEX) × ATT/100, min uses LUK×3.6×0.9×mastery"
L7_FORMULA = "pre-BB Lucky Seven: per star LUK×2.5 to LUK×5.0 × ATT/100 × skill %, 2 stars per cast"


def claw_range(luk, dex, str_, att, mastery=0.1):
    hi = (luk * 3.6 + str_ + dex) * att / 100
    lo = (luk * 3.6 * 0.9 * mastery + str_ + dex) * att / 100
    return max(1.0, lo), max(1.0, hi)


def lucky_seven_range(luk, att, skill_pct=150):
    return max(1.0, luk * 2.5 * att / 100 * skill_pct / 100), max(1.0, luk * 5.0 * att / 100 * skill_pct / 100)


def simulate_hits(dmg_range, monster_hp, monster_def=0, hits_per_attack=1, trials=20000, seed=7, crit=(0.0, 1.0)):
    """Attacks needed per kill, by Monte Carlo: damage uniform in range, minus about half
    the monster's defence, at least 1 per hit. Returns {attacks: share} and the average."""
    rng = random.Random(seed)
    lo, hi = dmg_range
    crit_rate, crit_mult = crit
    counts = {}
    total = 0
    for _ in range(trials):
        hp, attacks = monster_hp, 0
        while hp > 0 and attacks < 100:
            attacks += 1
            for _ in range(hits_per_attack):
                d = rng.uniform(lo, hi)
                if crit_rate and rng.random() < crit_rate:
                    d *= crit_mult
                hp -= max(1.0, d - monster_def * rng.uniform(0.5, 0.6))
        counts[attacks] = counts.get(attacks, 0) + 1
        total += attacks
    return {k: v / trials for k, v in sorted(counts.items())}, total / trials


def upgrade_what_if(luk, dex, str_, att_now, att_new, monster_hp, monster_def=0, mastery=0.1,
                    l7_pct=150, l7_mp=16):
    """Basic attack and Lucky Seven, now vs after a weapon change. Theory only."""
    out = {}
    for name, att in (("now", att_now), ("upgrade", att_new)):
        basic = claw_range(luk, dex, str_, att, mastery)
        l7 = lucky_seven_range(luk, att, l7_pct)
        bdist, bavg = simulate_hits(basic, monster_hp, monster_def)
        ldist, lavg = simulate_hits(l7, monster_hp, monster_def, hits_per_attack=2)
        out[name] = {"att": att, "basic_range": basic, "basic_attacks": bavg, "basic_one_shot": bdist.get(1, 0.0),
                     "basic_dist": bdist, "l7_range": l7, "l7_casts": lavg, "l7_one_shot": ldist.get(1, 0.0),
                     "l7_dist": ldist, "l7_mp_per_kill": lavg * l7_mp}
    n, u = out["now"], out["upgrade"]
    out["basic_attacks_saved"] = 1 - u["basic_attacks"] / n["basic_attacks"]
    out["l7_mp_saved"] = 1 - u["l7_mp_per_kill"] / n["l7_mp_per_kill"]
    out["note"] = (f"THEORY ONLY. {CLAW_FORMULA}; {L7_FORMULA}; defence taken as 50-60% of the value given. "
                   "Not verified for Classic World. Compare tagged sessions before and after the upgrade "
                   "to measure the real change.")
    return out
