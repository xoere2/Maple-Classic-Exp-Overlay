"""CSV and JSON export of sessions (everything stays local: you choose where the file goes)."""
import csv
import json
from datetime import datetime

from .analytics import session_report
from .metrics import Metric

CSV_METRICS = ["real_seconds", "active_seconds", "idle_seconds", "afk_seconds", "exp_gained", "exp_lost", "deaths",
               "real_rate", "active_rate", "rate_1m", "rate_5m", "rate_15m",
               "wallet_change", "transfers_net", "spending", "sales", "realized_net", "realized_net_hr",
               "pickups", "pickups_hr", "consumables_used", "potion_cost_hr", "stock_change", "loot_value",
               "loot_value_hr", "operating_profit", "operating_profit_hr", "estimated_total", "estimated_total_hr",
               "mp_spent", "hp_lost", "mp_potions", "hp_potions", "skill_casts",
               "personal_kills", "personal_kills_hr", "estimated_kills_hr", "observed_deaths_hr", "spawns_hr",
               "exp_per_kill", "mp_per_kill", "exp_per_mp", "mesos_per_mp", "exp_per_meso_spent", "attacks_per_kill"]


def reports_for(store, ids=None, settings=None):
    prices, skills, monsters = store.prices(), store.skills(), store.monsters()
    out = []
    for s in store.sessions():
        if ids and s["id"] not in ids:
            continue
        out.append((s, session_report(s, store.events(s["id"]), prices, skills, settings, monsters)))
    return out


def _plain(v):
    if isinstance(v, float):
        return round(v, 3)
    return v


def to_csv(store, path, ids=None, settings=None):
    rows = reports_for(store, ids, settings)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        head = ["id", "source", "started", "map", "level", "party", "class", "tags"]
        for k in CSV_METRICS:
            head += [k, k + "_kind"]
        w.writerow(head)
        for s, r in rows:
            line = [s["id"], s["source"], datetime.fromtimestamp(s["started"]).isoformat(timespec="seconds"),
                    r.info.get("map") or "", r.info.get("level") or "", r.info.get("party") or "",
                    r.info.get("job") or "", json.dumps(r.info.get("tags") or {})]
            for k in CSV_METRICS:
                m = r.get(k)
                ok = isinstance(m, Metric) and m.ok and not isinstance(m.value, dict)
                line += [_plain(m.value) if ok else "", m.kind if isinstance(m, Metric) else "unavailable"]
            w.writerow(line)
    return len(rows)


def to_json(store, path, ids=None, settings=None, with_events=True):
    rows = reports_for(store, ids, settings)
    data = {"exported": datetime.now().isoformat(timespec="seconds"),
            "about": "Maple Classic Exp Overlay, farm analytics fork. Each metric has a 'kind': observed, manual, "
                     "calculated, inferred, estimated or unavailable.",
            "sessions": []}
    for s, r in rows:
        info = {k: v for k, v in r.info.items() if k not in ("kill_events",)}
        item = {"session": {k: s[k] for k in ("id", "source", "title", "started", "ended", "map", "job",
                                               "level_start", "level_end", "party", "tags", "settings")},
                "info": info, "metrics": {k: m.to_dict() for k, m in r.items() if isinstance(m, Metric)}}
        if with_events:
            item["events"] = [{"t": e.t, "kind": e.kind, **e.data} for e in store.events(s["id"])]
        data["sessions"].append(item)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)
    return len(rows)
