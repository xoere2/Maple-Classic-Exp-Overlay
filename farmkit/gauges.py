"""HP and MP from the status bar, and what they say about potions and skills.

The status bar shows `HP[482/1178]` and `MP[...]` in the same white font and green
brackets as `EXP. 68,601 [71.68%]`, so the overlay's EXP digit examples read them too.
The '/' doesn't match any digit (it scores ~0.6 against them, digits ~0.99), which is how
the two numbers are told apart.

From a series of readings:
  - MP spent is the sum of drops in MP; with a skill's MP cost that's a cast count.
  - A jump up by much more than natural regeneration is a potion (or, for HP in a party,
    possibly a Cleric's Heal: those are labelled as such). The count is an estimate.
  - Level-ups refill HP/MP and raise the maximum: those jumps are ignored.
"""
from collections import namedtuple

import cv2
import numpy as np

from .metrics import ESTIMATED, OBSERVED, Metric, missing

Gauge = namedtuple("Gauge", "cur max x cy gh")
_Lay = namedtuple("_Lay", "gh cy exp pct box V")          # what DigitTemplates.match needs
_Blob = namedtuple("_Blob", "i x y w h")

MIN_SCORE, MIN_LEAD = 0.75, 0.03


def _blobs(mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return labels, [_Blob(i, *map(int, stats[i][:4])) for i in range(1, n)]


def _left_heavy(labels, b):
    m = labels[b.y:b.y + b.h, b.x:b.x + b.w] == b.i
    half = max(1, b.w // 2)
    return m[:, :half].sum() > m[:, b.w - half:].sum()


def _cells(blobs, gh):
    pitch = 0.84 * gh
    out = []
    for b in blobs:
        n = max(1, int(round((b.w + 0.1 * gh) / pitch)))
        out.extend((b.x + (k + 0.5) * b.w / n, b.w / n, "digit") for k in range(n))
    return out


def read_gauges(bgr, templates):
    """All `[cur/max]` readouts in a screenshot of the status line, left to right.
    Anything unclear is left out (never guessed)."""
    if templates is None or bgr is None or bgr.size == 0:
        return []
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    green = (sat >= 90) & (val >= 110) & (hue >= 26) & (hue <= 50)
    white = (sat < 70) & (val >= 140)
    glabels, gblobs = _blobs(green)
    _, wblobs = _blobs(white)
    brackets = sorted((b for b in gblobs if b.h >= 5 and b.w <= 0.6 * b.h), key=lambda b: b.x)
    V = val.astype(np.float32)
    out = []
    for o in brackets:
        if not _left_heavy(glabels, o):
            continue
        bh, cy = o.h, o.y + o.h / 2
        on_line = lambda b: abs(b.y + b.h / 2 - cy) <= 0.3 * bh
        right = [c for c in brackets if c.x > o.x + o.w and c.x - o.x <= 12 * bh and on_line(c)
                 and abs(c.h - bh) <= 0.25 * bh and not _left_heavy(glabels, c)]
        if not right:
            continue
        c = right[0]
        inside = sorted((b for b in wblobs if b.x >= o.x + o.w and b.x + b.w <= c.x and on_line(b)
                         and 0.5 * bh <= b.h <= 1.25 * bh), key=lambda b: b.x)
        if len(inside) < 3:
            continue
        tall = max(b.h for b in inside)
        gh = float(np.median([b.h for b in inside if b.h >= 0.85 * tall]))
        lay = _Lay(gh, cy, [], [], (0, 0, 0, 0), V)
        text, bad = "", []
        for cell in _cells(inside, gh):
            digit, score, lead = templates.match(lay, cell)
            if score >= MIN_SCORE and lead >= MIN_LEAD:
                text += digit
            else:
                bad.append(len(text))
                text += "/"
        if len(bad) != 1 or text.startswith("/") or text.endswith("/"):
            continue                                   # not exactly one clear '/' between digits
        cur_s, max_s = text.split("/")
        if (len(cur_s) > 1 and cur_s[0] == "0") or max_s[0] == "0":
            continue
        cur, mx = int(cur_s), int(max_s)
        if 0 < mx and cur <= mx:
            out.append(Gauge(cur, mx, o.x, cy, gh))
    return out


def label_gauges(gauges):
    """{'hp': Gauge, 'mp': Gauge} from the readouts left of the EXP number: HP comes first,
    then MP. With only one, it can't be told which it is, so nothing is returned."""
    if len(gauges) < 2:
        return {}
    hp, mp = gauges[-2], gauges[-1]              # the two right before EXP
    return {"hp": hp, "mp": mp}


class GaugeConfirm:
    """A reading counts once its maximum has been read the same twice in a row (a misread
    maximum would make every following number look wrong)."""

    def __init__(self):
        self._last = {}

    def feed(self, name, g):
        if g is None:
            return None
        last = self._last.get(name)
        self._last[name] = g
        return g if last and last.max == g.max else None


class GaugeSeries:
    """What a session's HP/MP readings say. `events` are 'gauge' events (any order)."""

    def __init__(self, events, levels=()):
        self.points = sorted(((e.t, e.data) for e in events if e.kind == "gauge"), key=lambda p: p[0])
        self.level_times = sorted(t for t in levels)
        self._cache = {}

    def _deltas(self, key):
        if key not in self._cache:
            self._cache[key] = self._work_out_deltas(key)
        return self._cache[key]

    def _work_out_deltas(self, key):
        out = []
        for (ta, a), (tb, b) in zip(self.points, self.points[1:]):
            if a.get(key) is None or b.get(key) is None:
                continue
            if a.get(key + "_max") != b.get(key + "_max"):
                continue                              # a level-up (or new gear): not a change of use
            if any(ta - 2 <= t <= tb + 2 for t in self.level_times):
                continue
            out.append((tb, b[key] - a[key], a[key], b.get(key + "_max")))
        return out

    def readings(self):
        return len(self.points)

    def spent(self, key):
        return sum(-d for _, d, _, _ in self._deltas(key) if d < 0)

    def jumps(self, key, threshold, full_floor=25):
        """Rises of at least `threshold` in one reading: potions (or heals). A rise that
        ends exactly at the maximum counts from `full_floor` up (more than regeneration),
        since a potion drunk near full fills only the room that's left."""
        return [(t, d, before, mx) for t, d, before, mx in self._deltas(key)
                if d >= threshold or (mx and before + d == mx and d >= full_floor)]

    def regen(self, key, threshold, full_floor=25):
        jumped = {t for t, _, _, _ in self.jumps(key, threshold, full_floor)}
        return sum(d for t, d, _, _ in self._deltas(key) if d > 0 and t not in jumped)

    def potions(self, key, restore, threshold=None):
        """Estimated potions drunk: each jump counts round(rise / restore), at least 1 (a
        potion drunk near full fills less than it restores)."""
        if not restore:
            return 0
        threshold = threshold or max(10, 0.5 * restore)
        n = 0
        for _, d, before, mx in self.jumps(key, threshold):
            room = (mx or before + d) - before
            n += 1 if d >= room - 1 else max(1, int(round(d / restore)))
        return n

    def casts(self, cost, tolerance=None, restore=None):
        """MP drops that are whole multiples of a skill's MP cost: (casts, MP not explained).
        With a potion's `restore`, a rise that falls short of it by whole casts (a cast and a
        potion between two readings) counts those casts too."""
        if not cost:
            return 0, self.spent("mp")
        tolerance = tolerance if tolerance is not None else max(2, 0.15 * cost)
        casts, other = 0, 0
        for _, d, before, mx in self._deltas("mp"):
            if d < 0:
                n = int(round(-d / cost))
                if n >= 1 and abs(-d - n * cost) <= tolerance * n:
                    casts += n
                else:
                    other += -d
            elif restore and d >= 0.5 * restore and (mx is None or before + d < mx):
                pots = max(1, int(round(d / restore)))
                short = pots * restore - d
                n = int(round(short / cost))
                if n >= 1 and abs(short - n * cost) <= tolerance * n:
                    casts += n
        return casts, other

    def summary(self, mp_potion=None, hp_potion=None, skill=None, party=1):
        """Metrics. mp_potion/hp_potion: (item name, restore); skill: (name, MP cost)."""
        if len(self.points) < 2:
            return {"gauge_readings": missing("HP/MP not read (keep the status bar uncovered)")}
        out = {"gauge_readings": Metric(len(self.points), OBSERVED, "high"),
               "mp_spent": Metric(self.spent("mp"), OBSERVED, "high", "sum of MP drops between readings"),
               "hp_lost": Metric(self.spent("hp"), OBSERVED, "high", "sum of HP drops (damage taken)")}
        if mp_potion and mp_potion[1]:
            n = self.potions("mp", mp_potion[1])
            out["mp_potions"] = Metric(n, ESTIMATED, "medium",
                                       f"MP jumps ÷ {mp_potion[1]} MP per {mp_potion[0]} (readings every ~1.5 s)",
                                       {"item": mp_potion[0]})
        if hp_potion and hp_potion[1]:
            n = self.potions("hp", hp_potion[1])
            note = f"HP jumps ÷ {hp_potion[1]} HP per {hp_potion[0]}"
            if party > 1:
                note += "; in a party some jumps may be a Cleric's Heal"
            out["hp_potions"] = Metric(n, ESTIMATED, "low" if party > 1 else "medium", note, {"item": hp_potion[0]})
        if skill and skill[1]:
            casts, other = self.casts(skill[1], restore=mp_potion[1] if mp_potion else None)
            out["skill_casts"] = Metric(casts, ESTIMATED, "medium",
                                        f"MP drops matching {skill[1]} MP per {skill[0]}; {other:,} MP spent otherwise",
                                        {"skill": skill[0], "other_mp": other})
        return out
