"""A number together with where it came from, so the UI never shows a guess as a fact."""
from dataclasses import dataclass, field

# Where a value came from, from most to least trustworthy.
OBSERVED = "observed"        # read straight from the screen (EXP, wallet, HP/MP, party)
MANUAL = "manual"            # typed in by the player (transactions, snapshots, prices, tags)
CALCULATED = "calculated"    # arithmetic on observed/manual values only
INFERRED = "inferred"        # follows from an accounting identity (e.g. pickups from wallet + notes)
ESTIMATED = "estimated"      # a model or heuristic (potions from MP jumps, kills from vision)
UNAVAILABLE = "unavailable"  # can't be worked out from what was recorded

KINDS = (OBSERVED, MANUAL, CALCULATED, INFERRED, ESTIMATED, UNAVAILABLE)
_RANK = {k: i for i, k in enumerate(KINDS)}


@dataclass
class Metric:
    value: object = None
    kind: str = UNAVAILABLE
    confidence: str = ""         # "high" / "medium" / "low" where it means something
    note: str = ""               # how it was worked out, or why it's missing
    detail: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.value is not None and self.kind != UNAVAILABLE

    def to_dict(self):
        d = {"value": self.value, "kind": self.kind}
        if self.confidence:
            d["confidence"] = self.confidence
        if self.note:
            d["note"] = self.note
        if self.detail:
            d["detail"] = self.detail
        return d

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return cls()
        return cls(d.get("value"), d.get("kind", UNAVAILABLE), d.get("confidence", ""),
                   d.get("note", ""), d.get("detail") or {})


def missing(note):
    return Metric(None, UNAVAILABLE, "", note)


def weakest(*kinds):
    """The least trustworthy of several kinds: a sum of observed + estimated is estimated."""
    kinds = [k for k in kinds if k]
    return max(kinds, key=lambda k: _RANK.get(k, len(KINDS))) if kinds else UNAVAILABLE


def per_hour(metric, seconds, note=None):
    """metric / hours, keeping its provenance."""
    if not metric.ok or not seconds or seconds <= 0:
        return missing(note or metric.note or "no time recorded")
    kind = metric.kind if metric.kind in (ESTIMATED, INFERRED, UNAVAILABLE) else CALCULATED
    return Metric(metric.value / seconds * 3600, kind, metric.confidence, note or metric.note)


SYMBOL = {OBSERVED: "", MANUAL: "✎", CALCULATED: "", INFERRED: "≈", ESTIMATED: "~", UNAVAILABLE: ""}


def fmt(metric, digits=0, suffix=""):
    """'12,345', '~1,234' (estimated), '≈567' (inferred) or '—'."""
    if not metric.ok:
        return "—"
    v = metric.value
    text = f"{v:,.{digits}f}" if isinstance(v, (int, float)) else str(v)
    return f"{SYMBOL.get(metric.kind, '')}{text}{suffix}"
