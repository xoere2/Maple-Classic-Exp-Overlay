"""Mesos accounting that never counts the same mesos twice.

The wallet is the ground truth: buying 100 Blue Potions mid-session already lowered it.
So spending is never subtracted from the wallet change again. Instead, the wallet change
is explained:

    wallet change   = pickups + sales - spending + transfers in - transfers out
    realized net    = wallet change - net transfers         (storage, mules, gifts aren't income)
    pickups (≈)     = realized net + spending - sales       (inferred: needs your notes)

Items are valued at the prices you set:

    consumables used      = what you used up, at your price    (an operating cost)
    stock change          = consumables bought but not used    (still yours: not a loss)
    loot gained           = items picked up and not sold       (estimated market value)

    operating profit      = pickups + sales of loot - consumables used
    estimated total       = realized net + stock change + loot gained (unsold)

Both profits come out the same however the potions were paid for (this session or
earlier), because a potion only costs something when it's used. Realized net is the
cash-only view: it drops when you stock up, so it isn't used for "is this map profitable"
unless you choose it.

Quantities come from inventory snapshots when there are any (observed), otherwise from
your purchase/sale/use/loot notes (manual), and potion use can be estimated from HP/MP
(see gauges.py) when nothing better exists. Each number says which.
"""
from dataclasses import dataclass

from .events import SPENDING, TRANSFERS, TXN_TYPES
from .metrics import (CALCULATED, ESTIMATED, INFERRED, MANUAL, OBSERVED, Metric, missing, weakest)

ITEM_KINDS = ("consumable", "loot", "ammo", "equipment", "scroll", "other")


@dataclass
class Price:
    item: str
    price: float = None          # mesos each; None: not priced yet
    kind: str = "loot"
    restore_hp: int = 0
    restore_mp: int = 0
    source: str = "manual"       # manual / csv / default
    updated: float = 0.0


# Starting entries. Only the numbers the player gave (Blue Potion, Lucky Seven) are filled
# in; everything else starts unpriced so nothing is made up. All of it can be edited.
DEFAULT_PRICES = [
    Price("Blue Potion", 220, "consumable", 0, 200, "default"),
    Price("Red Potion", None, "consumable", 50, 0, "default"),
    Price("Orange Potion", None, "consumable", 150, 0, "default"),
    Price("White Potion", None, "consumable", 300, 0, "default"),
    Price("Mana Elixir", None, "consumable", 0, 300, "default"),
    Price("Subi Throwing-Stars", None, "ammo", 0, 0, "default"),
    Price("Mokbi Throwing-Stars", None, "ammo", 0, 0, "default"),
    Price("Kumbi Throwing-Stars", None, "ammo", 0, 0, "default"),
    Price("Iron Ore", None, "loot", 0, 0, "default"),
    Price("Garnet Ore", None, "loot", 0, 0, "default"),
    Price("Mithril Ore", None, "loot", 0, 0, "default"),
    Price("Topaz Ore", None, "loot", 0, 0, "default"),
    Price("Adamantium Ore", None, "loot", 0, 0, "default"),
]
DEFAULT_SKILLS = {"Lucky Seven": 16}     # MP per cast, as given for the test character


class Ledger:
    def __init__(self, events, prices=None, jump_threshold=0.5):
        self.prices = {p.item: p for p in (prices or [])}
        self.wallet = [(e.t, int(e.get("value"))) for e in events if e.kind == "wallet" and e.get("value") is not None]
        self.txns = [e for e in events if e.kind == "txn" and e.get("type") in TXN_TYPES]
        self.snapshots = [e for e in events if e.kind == "snapshot" and isinstance(e.get("items"), dict)]
        self.jump_threshold = jump_threshold

    # ---- cash
    def _cash(self, types):
        return sum(TXN_TYPES[e.get("type")] * float(e.get("amount") or 0) for e in self.txns if e.get("type") in types)

    def wallet_change(self):
        if len(self.wallet) < 2:
            return missing("needs the inventory's mesos read at least twice (keep the inventory open)")
        (t0, v0), (t1, v1) = self.wallet[0], self.wallet[-1]
        return Metric(v1 - v0, OBSERVED, "high", f"{v0:,} → {v1:,} mesos", {"first": v0, "last": v1})

    def unexplained_jumps(self):
        """Big wallet changes with no transaction noted near them (likely a shop visit,
        storage or trade nobody wrote down). They make 'realized' numbers less certain."""
        if getattr(self, "_jumps", None) is None:
            self._jumps = self._find_jumps()
        return self._jumps

    def _find_jumps(self):
        jumps, notes = [], [e.t for e in self.txns if e.get("type") not in ("loot", "use")]
        for (ta, va), (tb, vb) in zip(self.wallet, self.wallet[1:]):
            # Farming never lowers the wallet, so any real drop is spending, storage or a trade;
            # a rise is only suspicious when it's far beyond pickups.
            big = (va - vb) > 5000 or (vb - va) > max(5000, self.jump_threshold * max(va, vb, 1))
            if big and not any(ta - 120 <= t <= tb + 120 for t in notes):
                jumps.append({"t": tb, "from": va, "to": vb, "change": vb - va})
        return jumps

    def transfers_net(self):
        return self._cash(TRANSFERS)

    def spending(self):
        """Mesos spent while farming (shown as a positive number), by type."""
        return {t: -self._cash((t,)) for t in SPENDING}

    def sales(self):
        return self._cash(("sale",))

    def realized_net(self):
        w = self.wallet_change()
        if not w.ok:
            return w
        jumps = self.unexplained_jumps()
        conf = "high" if not jumps else "low"
        note = "wallet change minus storage/transfers you noted"
        if jumps:
            note += f"; {len(jumps)} big unexplained change(s): add a note if it was a shop, storage or trade"
        return Metric(w.value - self.transfers_net(), weakest(w.kind, MANUAL if self.txns else OBSERVED),
                      conf, note, {"unexplained": jumps})

    def pickups(self):
        """Mesos picked up from monsters: inferred from the wallet and your notes."""
        r = self.realized_net()
        if not r.ok:
            return r
        value = r.value + sum(self.spending().values()) - self.sales()
        return Metric(value, INFERRED, r.confidence,
                      "realized net + spending - sales (exact if every purchase, sale and transfer was noted)")

    # ---- items
    def _snapshot_pair(self):
        starts = [s for s in self.snapshots if s.get("phase") == "start"] or self.snapshots[:1]
        ends = [s for s in self.snapshots if s.get("phase") == "end"] or self.snapshots[-1:]
        if not starts or not ends or starts[0] is ends[-1]:
            return None, None
        return starts[0], ends[-1]

    def item_flows(self):
        """Per item: bought, sold, used and looted quantities, and how each was known."""
        flows = {}

        def f(item):
            return flows.setdefault(item, {"bought": 0, "sold": 0, "used": 0, "looted": 0,
                                           "start": None, "end": None, "source": MANUAL})
        for e in self.txns:
            item, qty, kind = e.get("item"), int(e.get("qty") or 0), e.get("type")
            if not item or not qty:
                continue
            key = {"purchase": "bought", "equipment": "bought", "sale": "sold", "use": "used", "loot": "looted"}.get(kind)
            if key:
                f(item)[key] += qty
        start, end = self._snapshot_pair()
        if start and end:
            s_items, e_items = start.get("items"), end.get("items")
            for item in set(s_items) | set(e_items):
                d = f(item)
                d["start"], d["end"] = int(s_items.get(item, 0)), int(e_items.get(item, 0))
                # What the snapshots show changed, apart from what was bought or sold:
                change = d["end"] - d["start"] - d["bought"] + d["sold"]
                if change < 0:
                    d["used"], d["looted"] = -change, 0       # (a loot item that went down: used/dropped)
                else:
                    d["looted"], d["used"] = change, 0
                d["source"] = OBSERVED
        return flows

    def price(self, item):
        p = self.prices.get(item)
        return p.price if p and p.price is not None else None

    def kind(self, item):
        p = self.prices.get(item)
        return p.kind if p else "loot"

    def consumables_used(self, estimates=None):
        """Value of consumables used (operating cost). `estimates` adds potion counts
        estimated from HP/MP ({item: count}) for items with no better record."""
        flows = self.item_flows()
        total, kinds, unpriced, lines = 0.0, [], [], {}
        for item, d in flows.items():
            if self.kind(item) not in ("consumable", "ammo") or not d["used"]:
                continue
            price = self.price(item)
            if price is None:
                unpriced.append(item)
                continue
            total += d["used"] * price
            kinds.append(d["source"])
            lines[item] = {"qty": d["used"], "value": d["used"] * price, "source": d["source"]}
        for item, count in (estimates or {}).items():
            if item in lines or not count:
                continue
            price = self.price(item)
            if price is None:
                unpriced.append(item)
                continue
            total += count * price
            kinds.append(ESTIMATED)
            lines[item] = {"qty": count, "value": count * price, "source": ESTIMATED}
        if not lines:
            note = "no consumable use recorded"
            if unpriced:
                note += f" (unpriced: {', '.join(sorted(set(unpriced)))})"
            return Metric(0.0, MANUAL if not unpriced else ESTIMATED, "low", note, {"items": {}}) \
                if not unpriced else missing(note)
        note = "used × your prices"
        if unpriced:
            note += f"; not counted (no price): {', '.join(sorted(set(unpriced)))}"
        return Metric(total, weakest(CALCULATED, *kinds), "high" if not unpriced and ESTIMATED not in kinds else "medium",
                      note, {"items": lines, "unpriced": sorted(set(unpriced))})

    def stock_change(self, estimates=None):
        """Consumables bought minus used, at your price: potions stocked up are kept, not lost."""
        flows = self.item_flows()
        total, unpriced = 0.0, []
        for item, d in flows.items():
            if self.kind(item) not in ("consumable", "ammo"):
                continue
            used = d["used"] or (estimates or {}).get(item, 0)
            change = d["bought"] - d["sold"] - used if d["source"] != OBSERVED else d["end"] - d["start"]
            if change and self.price(item) is None:
                unpriced.append(item)
                continue
            total += change * (self.price(item) or 0)
        return Metric(total, CALCULATED, "medium", "consumables bought minus used, at your prices",
                      {"unpriced": unpriced})

    def loot_value(self):
        """Estimated market value of loot gained and still held (not sold)."""
        flows = self.item_flows()
        total, kinds, unpriced, lines = 0.0, [], [], {}
        for item, d in flows.items():
            if self.kind(item) in ("consumable", "ammo") or self.kind(item) == "equipment" and d["bought"]:
                continue
            kept = d["looted"] - d["sold"]
            if kept <= 0:
                continue
            price = self.price(item)
            if price is None:
                unpriced.append(item)
                continue
            total += kept * price
            kinds.append(d["source"])
            lines[item] = {"qty": kept, "value": kept * price, "source": d["source"]}
        if not lines and not unpriced:
            return Metric(0.0, MANUAL, "low", "no loot recorded (take inventory snapshots, or note loot)",
                          {"items": {}})
        note = "unsold loot × your prices (an estimate until it's sold)"
        if unpriced:
            note += f"; not counted (no price): {', '.join(sorted(unpriced))}"
        return Metric(total, ESTIMATED, "medium", note, {"items": lines, "unpriced": sorted(unpriced)})

    def operating_profit(self, estimates=None):
        p, c = self.pickups(), self.consumables_used(estimates)
        if not p.ok:
            return p
        if not c.ok:
            return missing(f"consumable costs unknown: {c.note}")
        loot_sales = sum(float(e.get("amount") or 0) for e in self.txns
                         if e.get("type") == "sale" and self.kind(e.get("item")) not in ("consumable", "ammo"))
        spent = self.spending()
        running = spent["recharge"] + spent["expense"]          # paid and used up straight away
        return Metric(p.value + loot_sales - c.value - running, weakest(p.kind, c.kind), p.confidence,
                      "pickups + loot sold - consumables used - recharges and other expenses",
                      {"running_costs": running})

    def estimated_total(self, estimates=None):
        r, s, l = self.realized_net(), self.stock_change(estimates), self.loot_value()
        if not r.ok:
            return r
        equipment = self.spending()["equipment"]                 # an investment, not a farming cost
        return Metric(r.value + s.value + l.value + equipment, ESTIMATED, "medium",
                      "realized net + consumable stock change + unsold loot (estimated values); "
                      "equipment bought isn't counted as a farming cost", {"equipment": equipment})

    def summary(self, real_seconds, estimates=None):
        from .metrics import per_hour
        out = {
            "wallet_change": self.wallet_change(),
            "transfers_net": Metric(self.transfers_net(), MANUAL, "high", "storage and transfers you noted"),
            "spending": Metric(sum(self.spending().values()), MANUAL, "high", "purchases, recharges, equipment and expenses noted",
                               {"by_type": self.spending()}),
            "sales": Metric(self.sales(), MANUAL, "high", "sales noted"),
            "realized_net": self.realized_net(),
            "pickups": self.pickups(),
            "consumables_used": self.consumables_used(estimates),
            "stock_change": self.stock_change(estimates),
            "loot_value": self.loot_value(),
            "operating_profit": self.operating_profit(estimates),
            "estimated_total": self.estimated_total(estimates),
        }
        for k in ("realized_net", "pickups", "consumables_used", "loot_value", "operating_profit", "estimated_total"):
            out[k + "_hr"] = per_hour(out[k], real_seconds)
        return out
