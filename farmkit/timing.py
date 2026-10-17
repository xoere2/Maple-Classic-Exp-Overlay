"""Session time: real time vs active time, and EXP rates over both.

Real time is everything from Start to Stop except time paused by hand (Pause) or marked
AFK: waiting for respawns, map congestion, looting and walking all count. Active time is
the upstream overlay's idea of farming time: the clock stops `gap` seconds after the
last EXP gain and only restarts at the next gain (or at Start/Resume), so idle time is
left out. Both come from the same events, so the inactivity rule can be changed after
the fact. Real EXP/hr is the default for comparisons: a map that looks fast but where
monsters are scarce is slow in real time.

All times are worked out on an "effective" clock with the paused/AFK time cut out.
"""
from bisect import bisect_right

from .events import sort_events
from .metrics import CALCULATED, OBSERVED, Metric, missing

WINDOWS = (60, 300, 900)            # the 1, 5 and 15-minute rates


def _merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


class Timeline:
    def __init__(self, events, gap=15.0, auto_pause=True, now=None):
        ev = sort_events(events)
        self.gap, self.auto_pause = float(gap), bool(auto_pause)
        starts = [e.t for e in ev if e.kind == "start"]
        stops = [e.t for e in ev if e.kind == "stop"]
        self.t0 = starts[0] if starts else (ev[0].t if ev else 0.0)
        last = ev[-1].t if ev else self.t0
        self.t1 = stops[-1] if stops else max(last, now if now is not None else last)
        self.t1 = max(self.t1, self.t0)

        # Time left out: Pause..Resume and AFK on..off (open ones run to the end).
        out, paused, afk, resumes = [], None, None, []
        afk_spans = []
        for e in ev:
            if e.kind == "pause" and paused is None:
                paused = e.t
            elif e.kind == "resume" and paused is not None:
                out.append((paused, e.t))
                resumes.append(e.t)
                paused = None
            elif e.kind == "afk_on" and afk is None:
                afk = e.t
            elif e.kind == "afk_off" and afk is not None:
                out.append((afk, e.t))
                afk_spans.append((afk, e.t))
                resumes.append(e.t)
                afk = None
        if paused is not None:
            out.append((paused, self.t1))
        if afk is not None:
            out.append((afk, self.t1))
            afk_spans.append((afk, self.t1))
        self.excluded = _merge([(max(a, self.t0), min(b, self.t1)) for a, b in out])
        self.afk_seconds = sum(b - a for a, b in _merge(afk_spans))
        self._ex_starts = [a for a, _ in self.excluded]
        self._ex_cum = []
        total = 0.0
        for a, b in self.excluded:
            self._ex_cum.append(total)
            total += b - a

        self.gains = [(self.eff(e.t), float(e.get("amount", 0))) for e in ev
                      if e.kind == "exp" and self.t0 <= e.t <= self.t1 and not self.is_excluded(e.t)]
        self.ignored_gains = sum(1 for e in ev if e.kind == "exp" and self.is_excluded(e.t))
        self.deaths = [(self.eff(e.t), float(e.get("loss") or 0)) for e in ev if e.kind == "death"]
        self.anchors = [0.0] + [self.eff(t) for t in resumes]
        self.maps = [(self.eff(e.t), e.get("name")) for e in ev if e.kind == "map" and e.get("name")]
        self.parties = [(self.eff(e.t), int(e.get("size") or 1)) for e in ev if e.kind == "party"]
        self.levels = [(self.eff(e.t), e.get("level")) for e in ev if e.kind == "level"]
        self.last_exp = next((e.data for e in reversed(ev) if e.kind == "exp" and "exp" in e.data), None)
        self._splits = {}

    # ---- the effective clock
    def is_excluded(self, t):
        i = bisect_right(self._ex_starts, t) - 1
        return i >= 0 and t < self.excluded[i][1]

    def eff(self, t):
        """Seconds of counted (not paused, not AFK) time from the start to t."""
        t = min(max(t, self.t0), self.t1)
        i = bisect_right(self._ex_starts, t) - 1
        cut = 0.0
        if i >= 0:
            a, b = self.excluded[i]
            cut = self._ex_cum[i] + (min(t, b) - a)
        return t - self.t0 - cut

    @property
    def real_seconds(self):
        return self.eff(self.t1)

    @property
    def paused_seconds(self):
        return sum(b - a for a, b in self.excluded) - self.afk_seconds

    # ---- active time (upstream's rule, recomputable)
    def _active_pairs(self):
        """(from, to) stretches of active time on the effective clock."""
        end = self.real_seconds
        points = sorted(set(self.anchors) | {g for g, _ in self.gains})
        pairs = []
        for a, b in zip(points, points[1:]):
            if b - a <= self.gap:
                pairs.append((a, b))
        if points and end - points[-1] <= self.gap:
            pairs.append((points[-1], end))       # still within the gap: the clock is running
        return pairs

    @property
    def active_seconds(self):
        if not self.auto_pause:
            return self.real_seconds
        return sum(b - a for a, b in self._active_pairs())

    @property
    def idle_seconds(self):
        return max(0.0, self.real_seconds - self.active_seconds)

    # ---- EXP
    @property
    def exp_gained(self):
        return sum(a for _, a in self.gains)

    @property
    def exp_lost(self):
        return sum(loss for _, loss in self.deaths)

    def exp_between(self, a, b):
        return sum(x for g, x in self.gains if a < g <= b)

    def real_rate(self):
        t = self.real_seconds
        if t < 10:
            return missing("less than 10 s recorded")
        return Metric(self.exp_gained / t * 3600, CALCULATED, "high",
                      "EXP over real time (paused and AFK time left out; idle time counts)")

    def active_rate(self):
        t = self.active_seconds
        if t < 10:
            return missing("less than 10 s of active time")
        rule = f"no EXP for {self.gap:g} s pauses the clock" if self.auto_pause else "auto-pause off"
        return Metric(self.exp_gained / t * 3600, CALCULATED, "high", f"EXP over active time ({rule})")

    def window_rate(self, seconds):
        """EXP/hr over the last `seconds` of real time (over less, labelled, early on)."""
        end = self.real_seconds
        span = min(seconds, end)
        if span < min(30, seconds):
            return missing(f"needs {min(30, seconds)} s of data")
        rate = self.exp_between(end - span, end) / span * 3600
        note = f"last {seconds // 60} min" + ("" if span >= seconds else f" (only {span / 60:.1f} min so far)")
        return Metric(rate, CALCULATED, "high" if span >= seconds else "medium", note)

    def windows(self):
        return {s: self.window_rate(s) for s in WINDOWS}

    def time_to_level(self, rate, req):
        """Seconds to the next level at `rate` EXP/hr, from the last EXP reading."""
        last = self.last_exp
        if not last or not req or not rate.ok or rate.value <= 0:
            return missing("needs an EXP reading, the level and a rate")
        left = max(0.0, req - float(last.get("exp") or 0))
        return Metric(left / rate.value * 3600, CALCULATED, rate.confidence, f"at {rate.note}")

    # ---- splits by map and by party size
    def _segments(self, changes, default):
        """[(from, to, value)] over the effective clock for a step function of changes."""
        end = self.real_seconds
        if not changes:
            return [(0.0, end, default)]
        segs, cur, start = [], changes[0][1], 0.0
        for t, value in changes[1:]:
            if value != cur:
                segs.append((start, t, cur))
                start, cur = t, value
        segs.append((start, end, cur))
        return segs

    def _value_at(self, segs, t, starts=None):
        if not segs:
            return None
        starts = starts or [a for a, _, _ in segs]
        i = max(0, bisect_right(starts, t) - 1)
        if i > 0 and t == segs[i][0] and t <= segs[i - 1][1]:
            i -= 1                                  # a boundary belongs to the earlier stretch
        return segs[i][2]

    def _split(self, segs):
        out = {}
        for a, b, v in segs:
            d = out.setdefault(v, {"exp": 0.0, "real_seconds": 0.0, "active_seconds": 0.0})
            d["real_seconds"] += b - a
        starts = [a for a, _, _ in segs]
        for g, amount in self.gains:
            v = self._value_at(segs, g, starts)
            out.setdefault(v, {"exp": 0.0, "real_seconds": 0.0, "active_seconds": 0.0})["exp"] += amount
        pairs = self._active_pairs() if self.auto_pause else [(0.0, self.real_seconds)]
        for a, b in pairs:
            for sa, sb, v in segs:                  # split each stretch at the boundaries
                lo, hi = max(a, sa), min(b, sb)
                if hi > lo:
                    out[v]["active_seconds"] += hi - lo
        for d in out.values():
            d["real_rate"] = d["exp"] / d["real_seconds"] * 3600 if d["real_seconds"] >= 10 else None
            d["active_rate"] = d["exp"] / d["active_seconds"] * 3600 if d["active_seconds"] >= 10 else None
        return out

    def by_map(self):
        if "map" not in self._splits:
            self._splits["map"] = self._split(self._segments(self.maps, "Unknown map"))
        return self._splits["map"]

    def by_party(self):
        """Per party size (1 = solo)."""
        if "party" not in self._splits:
            self._splits["party"] = self._split(self._segments(self.parties, 1))
        return self._splits["party"]

    def solo_vs_party(self):
        sizes = self.by_party()
        out = {"solo": {"exp": 0.0, "real_seconds": 0.0, "active_seconds": 0.0},
               "party": {"exp": 0.0, "real_seconds": 0.0, "active_seconds": 0.0}}
        for size, d in sizes.items():
            k = "solo" if (size or 1) <= 1 else "party"
            for f in ("exp", "real_seconds", "active_seconds"):
                out[k][f] += d[f]
        for d in out.values():
            d["real_rate"] = d["exp"] / d["real_seconds"] * 3600 if d["real_seconds"] >= 10 else None
        return out

    def main_party(self):
        """The party size played for the most real time."""
        sizes = self.by_party()
        return max(sizes, key=lambda k: sizes[k]["real_seconds"]) if sizes else 1

    def main_map(self):
        maps = self.by_map()
        known = {k: v for k, v in maps.items() if k != "Unknown map"} or maps
        return max(known, key=lambda k: known[k]["real_seconds"]) if known else None

    def summary(self):
        return {
            "real_seconds": Metric(self.real_seconds, OBSERVED, "high", "Start to now, minus Pause and AFK"),
            "active_seconds": Metric(self.active_seconds, CALCULATED, "high",
                                     f"gap rule {self.gap:g} s" if self.auto_pause else "auto-pause off"),
            "idle_seconds": Metric(self.idle_seconds, CALCULATED, "high", "real minus active"),
            "afk_seconds": Metric(self.afk_seconds, OBSERVED, "high", "marked AFK by hand"),
            "paused_seconds": Metric(self.paused_seconds, OBSERVED, "high", "Pause pressed"),
            "exp_gained": Metric(self.exp_gained, OBSERVED, "high", "trusted EXP gains"),
            "exp_lost": Metric(self.exp_lost, OBSERVED, "high", f"{len(self.deaths)} death(s), not taken off EXP/hr"),
            "deaths": Metric(len(self.deaths), OBSERVED, "high"),
            "real_rate": self.real_rate(),
            "active_rate": self.active_rate(),
            **{f"rate_{s // 60}m": m for s, m in self.windows().items()},
        }
