"""The event model: every observation and note, with the time it happened.

`t` is seconds since the session started (the overlay's monotonic clock, or a video's
timestamp), so live and recorded sessions work the same way. `data` is a small dict.

Kinds and their data:
  start / stop                      {}                          the session's ends
  pause / resume                    {}                          Pause / Resume pressed (manual)
  afk_on / afk_off                  {}                          the player marked themselves AFK
  exp                               {amount, exp, pct, level}   a trusted EXP gain (negative =
                                                                 a misread taken back)
  death                             {loss, level}               EXP lost on dying (not counted)
  level                             {level}                     a confirmed level-up
  map                               {name}                      the map changed (or was first read)
  party                             {size}                      the party size changed (1 = solo)
  job                               {name}                      the class, from the status bar
  wallet                            {value}                     a confirmed mesos count
  gauge                             {hp, hp_max, mp, mp_max}    a confirmed HP/MP reading
  txn                               {type, amount, item, qty, unit_price, note}   entered by hand
  snapshot                          {phase, items: {name: qty}, note}            entered by hand
  tag                               {key, value}                e.g. attack_mode=Lucky Seven
  vision                            {event, species, confidence, x, y, track, ...}
  vision_frame                      {visible, roi, seconds}     sampled monster counts
"""
from dataclasses import dataclass, field

KINDS = ("start", "stop", "pause", "resume", "afk_on", "afk_off", "exp", "death", "level", "map",
         "party", "job", "wallet", "gauge", "txn", "snapshot", "tag", "vision", "vision_frame")

# Transactions entered by hand. Cash sign: what it does to the wallet.
TXN_TYPES = {
    "purchase": -1,       # bought items (potions, stars, scrolls...) from an NPC or player
    "recharge": -1,       # throwing-star recharge at a shop
    "equipment": -1,      # bought equipment (an investment, kept out of farming costs)
    "expense": -1,        # anything else spent while farming (e.g. taxi)
    "sale": +1,           # sold items
    "deposit": -1,        # mesos into storage: not spent, just moved
    "withdraw": +1,       # mesos out of storage
    "transfer_out": -1,   # mesos to another character / player (not a cost)
    "transfer_in": +1,    # mesos from another character / player (not income)
    "loot": 0,            # picked up an item (no mesos change): counts toward loot value
    "use": 0,             # used up an item (e.g. potions counted by hand)
}
TRANSFERS = ("deposit", "withdraw", "transfer_out", "transfer_in")
SPENDING = ("purchase", "recharge", "equipment", "expense")


@dataclass
class Event:
    t: float
    kind: str
    data: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return self.data.get(key, default)


def txn(t, type_, amount=0, item=None, qty=0, unit_price=None, note=""):
    """A transaction event. amount is the size of the cash change (always >= 0); its sign
    comes from the type. With a unit price and quantity, amount defaults to their product."""
    if type_ not in TXN_TYPES:
        raise ValueError(f"unknown transaction type {type_!r}")
    if not amount and unit_price is not None and qty:
        amount = unit_price * qty
    if amount < 0 or qty < 0:
        raise ValueError("amount and qty are sizes: use the type for the direction")
    return Event(t, "txn", {"type": type_, "amount": amount, "item": item, "qty": qty,
                            "unit_price": unit_price, "note": note})


def sort_events(events):
    """Events in time order (stable, so same-time events keep the order they were made in)."""
    return sorted(events, key=lambda e: e.t)
