"""Session reports, comparisons and rankings.

A report is worked out fresh from a session's events every time (nothing is cached), so a
price change, a new transaction note or a different inactivity rule shows up everywhere.
"""
from .combat import hits_distribution, per_unit
from .economy import Ledger
from .exp_table import EXP_TABLE
from .gauges import GaugeSeries
from .metrics import CALCULATED, ESTIMATED, MANUAL, OBSERVED, Metric, fmt, missing, per_hour
from .timing import Timeline
from .vision import classify_vanishes, summarize as vision_summary

DEFAULT_SETTINGS = {"gap": 15, "auto_pause": True, "mp_potion": "Blue Potion", "hp_potion": "",
                    "skill": "Lucky Seven", "profit_basis": "operating"}


def _party_at(tl):
    segs = tl._segments(tl.parties, 1)

    def at(t_raw):
        return tl._value_at(segs, tl.eff(t_raw))
    return at


class Report(dict):
    """{metric name: Metric} plus .info (map, level, party, tags...)."""

    def __init__(self, info, metrics):
        super().__init__(metrics)
        self.info = info

    def get_value(self, key):
        m = self.get(key)
        return m.value if isinstance(m, Metric) and m.ok else None


def session_report(session, events, prices, skills=None, settings=None, monsters=None, now=None):
    """session: a row from Store.sessions(); events: its events."""
    s = {**DEFAULT_SETTINGS, **(session.get("settings") or {}), **(settings or {})}
    skills, monsters = skills or {}, monsters or {}
    if session.get("source") == "legacy":
        return legacy_report(session)
    tl = Timeline(events, gap=s["gap"], auto_pause=s["auto_pause"], now=now)
    real = tl.real_seconds
    m = dict(tl.summary())

    # Level and time to level
    last = tl.last_exp or {}
    level = last.get("level")
    req = None
    if level and 1 <= level <= len(EXP_TABLE):
        req = EXP_TABLE[level - 1]
    elif last.get("exp") is not None and (last.get("pct") or 0) >= 0.5:
        req = last["exp"] / (last["pct"] / 100)
    m["ttl"] = tl.time_to_level(m["rate_5m"] if m["rate_5m"].ok else m["real_rate"], req)

    # HP/MP and potions
    price = {p.item: p for p in prices}
    gs = GaugeSeries(events, [e.t for e in events if e.kind == "level"])
    mp_item, hp_item = price.get(s.get("mp_potion") or ""), price.get(s.get("hp_potion") or "")
    skill_cost = skills.get(s.get("skill") or "")
    party = tl.main_party()
    gm = gs.summary(mp_potion=(mp_item.item, mp_item.restore_mp) if mp_item and mp_item.restore_mp else None,
                    hp_potion=(hp_item.item, hp_item.restore_hp) if hp_item and hp_item.restore_hp else None,
                    skill=(s.get("skill"), skill_cost) if skill_cost else None, party=party)
    m.update(gm)
    estimates = {}
    if "mp_potions" in gm and gm["mp_potions"].ok:
        estimates[mp_item.item] = gm["mp_potions"].value
    if "hp_potions" in gm and gm["hp_potions"].ok:
        estimates[hp_item.item] = gm["hp_potions"].value

    # Mesos
    ledger = Ledger(events, prices)
    econ = ledger.summary(real, estimates)
    m.update(econ)
    m["potion_cost_hr"] = econ["consumables_used_hr"]
    m["unexplained_wallet_jumps"] = Metric(len(ledger.unexplained_jumps()), OBSERVED, "high",
                                           "big mesos changes with no note near them")

    # Monsters (experimental)
    vis = [dict(e.data, t=e.t) for e in events if e.kind == "vision"]
    frames = [(e.t, e.get("visible", 0)) for e in events if e.kind == "vision_frame"]
    gains_raw = [(e.t, float(e.get("amount") or 0)) for e in events if e.kind == "exp"]
    if vis or frames:
        vis = classify_vanishes(vis, gains_raw, _party_at(tl))
        vm = vision_summary(vis, frames, real)
        m.update({k: v for k, v in vm.items() if isinstance(v, Metric)})
        m["kills_by_confidence"] = Metric(vm.get("kills_by_confidence", {}), ESTIMATED, "low")
        m["by_species"] = Metric(vm.get("by_species", {}), ESTIMATED, "low")
        kills = [e for e in vis if e.get("confidence") in ("confirmed", "probable")]
        m["personal_kills"] = Metric(len(kills), ESTIMATED, "low", "confirmed + probable (EXPERIMENTAL)")
        hd = hits_distribution(kills)
        m["attacks_per_kill"] = hd["avg"]
        m["hit_share"] = Metric(hd["share"], ESTIMATED, "low", f"{hd['kills_with_hits']} kills with hits seen") \
            if hd["share"] else missing("no hits seen")
    else:
        m["observed_deaths_hr"] = missing("monster vision was off (Settings > Experimental)")
        m["spawns_hr"] = missing("monster vision was off")
        m["personal_kills"] = missing("monster vision was off")
    kills_metric = m.get("personal_kills", missing(""))
    # EXP-based kill estimate: solo only, with the monster's EXP entered by you
    species = s.get("species") or (session.get("tags") or {}).get("monster")
    mon = monsters.get(species) if species else None
    if mon and mon.get("exp") and party <= 1 and tl.exp_gained > 0:
        n = tl.exp_gained / mon["exp"]
        m["estimated_kills_from_exp"] = Metric(n, ESTIMATED, "medium",
                                               f"solo EXP ÷ {mon['exp']} EXP per {species} (assumes only {species})")
        m["estimated_kills_hr"] = per_hour(m["estimated_kills_from_exp"], real)
        if not kills_metric.ok:
            kills_metric = m["estimated_kills_from_exp"]
    elif party > 1:
        m["estimated_kills_from_exp"] = missing("not worked out in a party: party EXP isn't your kills")
    m["exp_per_kill"] = per_unit(m["exp_gained"], kills_metric, "EXP per kill")
    mp = m.get("mp_spent", missing(""))
    m["mp_per_kill"] = per_unit(mp, kills_metric, "MP per kill")
    m["exp_per_mp"] = per_unit(m["exp_gained"], mp, "EXP per MP")
    m["mesos_per_mp"] = per_unit(econ["pickups"], mp, "mesos picked up per MP")
    cost = econ["consumables_used"]
    running = econ["operating_profit"].detail.get("running_costs", 0) if econ["operating_profit"].ok else 0
    spent = Metric((cost.value or 0) + running, cost.kind, cost.confidence, "consumables used + recharges/expenses") \
        if cost.ok else cost
    m["exp_per_meso_spent"] = per_unit(m["exp_gained"], spent, "EXP per meso spent") if spent.ok and spent.value \
        else missing("no spending recorded")

    tags = session.get("tags") or {}
    info = {"id": session.get("id"), "source": session.get("source"), "title": session.get("title"),
            "started": session.get("started"), "map": tl.main_map() or session.get("map"),
            "maps": tl.by_map(), "party": party, "solo_vs_party": tl.solo_vs_party(),
            "level": level or session.get("level_end"), "level_start": session.get("level_start"),
            "job": session.get("job"), "tags": tags, "settings": s,
            "basis": "real", "kill_events": vis}
    return Report(info, m)


def legacy_report(session):
    """An imported upstream log line: totals over ACTIVE time only."""
    sm = session.get("summary") or {}
    secs, exp = sm.get("seconds") or 0, sm.get("exp") or 0
    note = "upstream log line: active time only (idle excluded), so not comparable to real EXP/hr"
    m = {"exp_gained": Metric(exp, OBSERVED, "high", note),
         "active_seconds": Metric(secs, OBSERVED, "high", note),
         "active_rate": Metric(exp / secs * 3600, CALCULATED, "high", note) if secs else missing("no time"),
         "real_rate": missing("not recorded by the upstream log"),
         "real_seconds": missing("not recorded by the upstream log")}
    ms = sm.get("mesos_seconds") or 0
    m["upstream_mesos_hr"] = Metric(sm["mesos"] / ms * 3600, OBSERVED, "medium",
                                    "upstream mesos/hr (pickups only, while the inventory was open)") \
        if ms else missing("mesos not read")
    info = {"id": session.get("id"), "source": "legacy", "title": session.get("title"), "started": session.get("started"),
            "map": session.get("map"), "maps": {}, "party": session.get("party") or 1, "level": session.get("level_end"),
            "job": session.get("job"), "tags": session.get("tags") or {}, "basis": "active", "kill_events": []}
    return Report(info, m)


# --------------------------------------------------------------------------- compare and rank

COMPARE_ROWS = [
    ("Map", lambda r: r.info.get("map") or "?"),
    ("Level", lambda r: r.info.get("level") or "?"),
    ("Class", lambda r: r.info.get("job") or ""),
    ("Party size", lambda r: "Solo" if (r.info.get("party") or 1) <= 1 else r.info.get("party")),
    ("Tags", lambda r: ", ".join(f"{k}={v}" for k, v in (r.info.get("tags") or {}).items())),
    ("Session duration (real)", "real_seconds"),
    ("Idle share", "idle_share"),
    ("EXP/hour (real)", "real_rate"),
    ("EXP/hour (active)", "active_rate"),
    ("Personal kills/hour", "personal_kills_hr"),
    ("Estimated kills/hour (from EXP)", "estimated_kills_hr"),
    ("Observed deaths/hour (region)", "observed_deaths_hr"),
    ("Observed respawns/hour (region)", "spawns_hr"),
    ("MP spent", "mp_spent"),
    ("HP lost", "hp_lost"),
    ("MP per kill", "mp_per_kill"),
    ("EXP per MP", "exp_per_mp"),
    ("Potion cost/hour", "potion_cost_hr"),
    ("Realized net mesos/hour", "realized_net_hr"),
    ("Mesos picked up/hour", "pickups_hr"),
    ("Operating profit/hour", "operating_profit_hr"),
    ("Loot value/hour (unsold, est.)", "loot_value_hr"),
    ("Estimated total profit/hour", "estimated_total_hr"),
]


def _cell(report, key):
    if callable(key):
        return str(key(report))
    if key == "idle_share":
        r, i = report.get("real_seconds"), report.get("idle_seconds")
        if r and r.ok and i and i.ok and r.value:
            return f"{i.value / r.value:.0%}"
        return "—"
    m = report.get(key)
    if not isinstance(m, Metric):
        return "—"
    if key == "real_seconds" and m.ok:
        s = int(m.value)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return fmt(m, 2 if key in ("exp_per_mp", "mp_per_kill") else 0)


def compare(reports):
    """Rows of (label, [cell per session]) for a side-by-side table."""
    return [(label, [_cell(r, key) for r in reports]) for label, key in COMPARE_ROWS]


RANKINGS = {
    "exp_hr": ("Best EXP/hour (real time)", "real_rate"),
    "realized_mesos_hr": ("Best realized mesos/hour", "realized_net_hr"),
    "total_profit_hr": ("Best estimated total profit/hour", "estimated_total_hr"),
    "exp_per_meso": ("Best EXP per meso spent", "exp_per_meso_spent"),
    "exp_hr_profitable": ("Best EXP/hour without losing mesos", "real_rate"),
}
PROFIT_BASES = {"operating": "operating_profit_hr", "realized": "realized_net_hr", "estimated": "estimated_total_hr"}


def rank(reports, by="exp_hr", basis="operating", min_minutes=5):
    """(ranked [(report, value, note)], left out [(report, why)])."""
    title, key = RANKINGS[by]
    ranked, left = [], []
    for r in reports:
        if r.info.get("source") == "legacy" and key == "real_rate":
            left.append((r, "upstream log line: has active EXP/hr only"))
            continue
        secs = r.get("real_seconds")
        if not secs or not secs.ok or secs.value < min_minutes * 60:
            left.append((r, f"shorter than {min_minutes} min"))
            continue
        m = r.get(key)
        if not isinstance(m, Metric) or not m.ok:
            left.append((r, (m.note if isinstance(m, Metric) else "") or "not recorded"))
            continue
        note = ""
        if by == "exp_hr_profitable":
            p = r.get(PROFIT_BASES[basis])
            if not p or not p.ok:
                left.append((r, f"{basis} profit unknown: {p.note if p else ''}"))
                continue
            if p.value < 0:
                left.append((r, f"loses {-p.value:,.0f} mesos/hour ({basis})"))
                continue
            note = f"{basis} profit {fmt(p)}/hour"
        ranked.append((r, m.value, note))
    ranked.sort(key=lambda x: -x[1])
    return ranked, left


def group_by(reports, key="map"):
    """Map (or other) efficiency: sessions added up, so rates are over all their time."""
    groups = {}
    for r in reports:
        if r.info.get("source") == "legacy":
            continue
        k = r.info.get(key) if key != "map+party" else (r.info.get("map"), "solo" if r.info.get("party", 1) <= 1 else "party")
        g = groups.setdefault(k, {"sessions": 0, "exp": 0.0, "real": 0.0, "active": 0.0, "realized": 0.0,
                                  "realized_time": 0.0, "operating": 0.0, "operating_time": 0.0})
        g["sessions"] += 1
        g["exp"] += r.get_value("exp_gained") or 0
        g["real"] += r.get_value("real_seconds") or 0
        g["active"] += r.get_value("active_seconds") or 0
        if r.get_value("realized_net") is not None:
            g["realized"] += r.get_value("realized_net")
            g["realized_time"] += r.get_value("real_seconds") or 0
        if r.get_value("operating_profit") is not None:
            g["operating"] += r.get_value("operating_profit")
            g["operating_time"] += r.get_value("real_seconds") or 0
    out = []
    for k, g in groups.items():
        out.append({"key": k, "sessions": g["sessions"], "hours": g["real"] / 3600,
                    "real_rate": g["exp"] / g["real"] * 3600 if g["real"] else None,
                    "active_rate": g["exp"] / g["active"] * 3600 if g["active"] else None,
                    "realized_hr": g["realized"] / g["realized_time"] * 3600 if g["realized_time"] else None,
                    "operating_hr": g["operating"] / g["operating_time"] * 3600 if g["operating_time"] else None})
    out.sort(key=lambda d: -(d["real_rate"] or 0))
    return out


def drop_stats(reports_with_events, item, species):
    """Observed drops of `item` against personal kills of `species` across sessions."""
    from .stats import drop_rate_summary
    drops = kills = 0
    for report, ledger in reports_with_events:
        flows = ledger.item_flows()
        drops += flows.get(item, {}).get("looted", 0)
        for e in report.info.get("kill_events", []):
            if e.get("event") == "vanish" and e.get("confidence") in ("confirmed", "probable") \
                    and (e.get("species") == species or species in ("", None, "any")):
                kills += 1
    return {"item": item, "species": species, "drops": drops, "kills": kills, **drop_rate_summary(drops, kills)}


def manual_kind_note():
    return {OBSERVED: "read from the screen", MANUAL: "entered by you", CALCULATED: "worked out from the above",
            "inferred": "follows from the wallet and your notes", ESTIMATED: "a model or heuristic: treat with care"}
