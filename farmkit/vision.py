"""EXPERIMENTAL: monsters, kills and respawns from what's visible on screen.

Off by default (Settings > Experimental: monster vision). What it can and can't know:

  - It only sees the captured region (a platform you select, or the game view). Every
    count is "observed in this region", never map-wide.
  - A monster that disappears in the middle of the region (not at its edge) has died or
    despawned. Damage numbers on it just before, and an EXP gain right after, are the
    evidence used to say whose kill it probably was.
  - In a party, EXP is shared, so EXP after a death doesn't mean you killed it. Party
    kills are never reported as yours.
  - A monster that appears in the middle of the region (not walking or scrolling in from
    an edge) is a respawn. Monsters already there when watching starts (or right after a
    teleport/map change) are "present", not respawns.

Confidence labels for each disappearance:
  confirmed     solo, damage numbers on it, and an EXP gain right after it vanished
  probable      solo with an EXP gain (no damage seen), or damage + EXP in a party
  party_credit  in a party: EXP came in, but anyone in the party may have killed it
  unattributed  no evidence: someone else's kill, a despawn, or a detection miss

Detection is pluggable:
  SpriteDetector     template matching against monster sprites YOU cropped from your own
                     screenshots (Analytics > Monsters > Add sprite). No game files ship.
  ForegroundDetector a scroll-compensated background model: finds anything that moves
                     (monsters, but also players and effects), so species is "unknown".
"""
import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

MONSTERS = ("Jr. Necki", "Jr. Wraith", "Stirge", "Zombie Mushroom", "Wild Boar", "Fire Boar")


@dataclass
class Detection:
    x: float              # centre, in region pixels
    y: float
    w: float
    h: float
    label: str = "unknown"
    score: float = 1.0


# --------------------------------------------------------------------------- camera

def estimate_shift(prev_gray, gray):
    """How far the view scrolled between two frames: (dx, dy, response). The world moves
    by (dx, dy) on screen, so a still monster's position changes by the same."""
    if prev_gray is None or prev_gray.shape != gray.shape:
        return 0.0, 0.0, 0.0
    a = prev_gray.astype(np.float32)
    b = gray.astype(np.float32)
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, win)
    return float(dx), float(dy), float(response)


def _ncc(a, b):
    """Correlation of two same-size frames' edges, unshifted: ~1 for the same scene, ~0 for
    another. (Edges, so big flat areas like sky or ground don't make two scenes look alike.)"""
    a = np.abs(cv2.Laplacian(a, cv2.CV_32F))
    b = np.abs(cv2.Laplacian(b, cv2.CV_32F))
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 1.0


# --------------------------------------------------------------------------- detectors

def _nms(dets, iou=0.3, centre=0.6):
    """Best detections first; drop any that overlap a kept one, or whose centre is within
    `centre` of its size (a mirrored or shifted match of the same monster)."""
    dets = sorted(dets, key=lambda d: -d.score)
    keep = []
    for d in dets:
        if all(_iou(d, k) < iou and math.hypot(d.x - k.x, d.y - k.y) > centre * max(k.w, k.h) for k in keep):
            keep.append(d)
    return keep


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a.x - a.w / 2, a.y - a.h / 2, a.x + a.w / 2, a.y + a.h / 2
    bx0, by0, bx1, by1 = b.x - b.w / 2, b.y - b.h / 2, b.x + b.w / 2, b.y + b.h / 2
    iw, ih = max(0.0, min(ax1, bx1) - max(ax0, bx0)), max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    return inter / (a.w * a.h + b.w * b.h - inter + 1e-9)


class SpriteDetector:
    """Finds monsters by their sprites (BGRA images with transparency, or BGR).
    Both facing directions are tried, at the given scales (game window size vs the size the
    sprites were cropped at)."""

    def __init__(self, sprites, threshold=0.75, scales=(1.0,), work_scale=1.0, coarse=0.35, max_candidates=24):
        self.threshold, self.work_scale = threshold, work_scale
        self.coarse, self.max_candidates = coarse, max_candidates
        self.templates = []                     # (label, gray template, mask or None)
        for label, images in sprites.items():
            for img in images:
                for s in scales:
                    for flip in (False, True):
                        self._add(label, img, s * work_scale, flip)

    def _add(self, label, img, s, flip):
        if img is None or img.size == 0:
            return
        if flip:
            img = img[:, ::-1]
        if s != 1.0:
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if img.shape[0] < 4 or img.shape[1] < 4:
            return
        mask = None
        if img.ndim == 3 and img.shape[2] == 4:
            mask = (img[..., 3] > 127).astype(np.uint8) * 255
            img = img[..., :3]
        img = np.ascontiguousarray(img)
        filled = img.copy()
        if mask is not None:
            filled[mask == 0] = img[mask > 0].mean(axis=0).astype(np.uint8) if (mask > 0).any() else 0
        self.templates.append((label, img, None if mask is None else np.ascontiguousarray(mask), filled))

    def detect(self, bgr):
        """Two steps, for speed: a fast search with the sprite's see-through parts filled
        in finds candidates, then each is scored properly with the transparency mask."""
        if self.work_scale != 1.0:
            bgr = cv2.resize(bgr, None, fx=self.work_scale, fy=self.work_scale, interpolation=cv2.INTER_AREA)
        dets = []
        k = 1 / self.work_scale
        for label, tpl, mask, filled in self.templates:
            th, tw = tpl.shape[:2]
            if th > bgr.shape[0] or tw > bgr.shape[1]:
                continue
            coarse = np.nan_to_num(cv2.matchTemplate(bgr, filled, cv2.TM_CCOEFF_NORMED), nan=0.0)
            peaks = (coarse >= self.coarse) & (coarse == cv2.dilate(coarse, np.ones((5, 5), np.uint8)))
            ys, xs = np.where(peaks)
            order = np.argsort(-coarse[ys, xs])[:self.max_candidates]
            for y, x in zip(ys[order], xs[order]):
                if mask is None:
                    score = float(coarse[y, x])
                else:                                       # the exact score, around the candidate
                    y0, x0 = max(0, y - 2), max(0, x - 2)
                    win = bgr[y0:min(bgr.shape[0], y + th + 2), x0:min(bgr.shape[1], x + tw + 2)]
                    if win.shape[0] < th or win.shape[1] < tw:
                        continue
                    r = np.nan_to_num(cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED, mask=mask),
                                      nan=0.0, posinf=0.0, neginf=0.0)
                    _, score, _, (bx, by) = cv2.minMaxLoc(r)
                    x, y = x0 + bx, y0 + by
                if score >= self.threshold:
                    dets.append(Detection((x + tw / 2) * k, (y + th / 2) * k, tw * k, th * k, label, float(score)))
        return _nms(dets)


class ForegroundDetector:
    """Anything that differs from the learned (scroll-aligned) background. Learns over
    `learn_frames` frames; until then it reports nothing."""

    def __init__(self, min_area=60, max_area=20000, learn_frames=15, threshold=28, alpha=0.05):
        self.min_area, self.max_area = min_area, max_area
        self.learn_frames, self.threshold, self.alpha = learn_frames, threshold, alpha
        self.bg, self.frames = None, 0

    def shift(self, dx, dy):
        if self.bg is not None and (dx or dy):
            m = np.float32([[1, 0, dx], [0, 1, dy]])
            self.bg = cv2.warpAffine(self.bg, m, self.bg.shape[1::-1], borderMode=cv2.BORDER_REPLICATE)

    def detect(self, bgr):
        f = cv2.GaussianBlur(bgr, (3, 3), 0).astype(np.float32)
        if self.bg is None or self.bg.shape != f.shape:
            self.bg, self.frames = f.copy(), 0
        diff = np.abs(f - self.bg).max(axis=2)
        fg = (diff > self.threshold).astype(np.uint8)
        # Update the background only where it's background, so standing monsters stay found.
        a = np.where(fg[..., None] > 0, self.alpha * 0.1, self.alpha).astype(np.float32)
        self.bg = self.bg * (1 - a) + f * a
        self.frames += 1
        if self.frames < self.learn_frames:
            return []
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((5, 5), np.uint8))
        n, _, stats, cents = cv2.connectedComponentsWithStats(fg, connectivity=8)
        out = []
        for i in range(1, n):
            x, y, w, h, area = map(int, stats[i])
            if self.min_area <= area <= self.max_area:
                out.append(Detection(x + w / 2, y + h / 2, w, h, "unknown", 1.0))
        return out


class DamageDetector:
    """Damage numbers: small clusters of saturated orange/red pixels. The colours are a
    best guess, NOT verified against Classic World: calibrate them (hue range etc.) on a
    recording before trusting hit counts."""

    def __init__(self, hue=(0, 28), sat=150, val=170, min_area=12, max_area=4000):
        self.hue, self.sat, self.val, self.min_area, self.max_area = hue, sat, val, min_area, max_area

    def detect(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        m = ((h >= self.hue[0]) & (h <= self.hue[1]) & (s >= self.sat) & (v >= self.val)).astype(np.uint8)
        m = cv2.dilate(m, np.ones((1, 7), np.uint8))      # join a number's digits, not stacked numbers
        n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        return [Detection(x + w / 2, y + hh / 2, w, hh, "damage")
                for x, y, w, hh, a in (map(int, stats[i]) for i in range(1, n)) if self.min_area <= a <= self.max_area]


# --------------------------------------------------------------------------- tracking

@dataclass
class Track:
    id: int
    x: float
    y: float
    w: float
    h: float
    first_t: float
    last_t: float
    first_xy: tuple
    vx: float = 0.0
    vy: float = 0.0
    hits: int = 1
    missed: int = 0
    labels: dict = field(default_factory=dict)
    damage: list = field(default_factory=list)       # times damage numbers appeared on it
    confirmed: bool = False
    spawn_kind: str = ""

    @property
    def label(self):
        return max(self.labels, key=self.labels.get) if self.labels else "unknown"


class Tracker:
    """Greedy nearest-neighbour tracking with a constant-velocity guess, in region pixels.
    The view's scroll is subtracted first, so walking doesn't look like monsters moving."""

    def __init__(self, max_dist=1.2, confirm_hits=3, max_missed=4):
        self.max_dist, self.confirm_hits, self.max_missed = max_dist, confirm_hits, max_missed
        self.tracks, self.next_id = [], 1

    def shift(self, dx, dy):
        # (first_xy stays where it was first seen *on screen*: that's what tells walking or
        # scrolling in at the edge from spawning in the middle.)
        for tr in self.tracks:
            tr.x += dx
            tr.y += dy

    def update(self, dets, t):
        """Returns (new confirmed tracks, ended tracks)."""
        pairs = []
        for i, tr in enumerate(self.tracks):
            px, py = tr.x + tr.vx, tr.y + tr.vy
            for j, d in enumerate(dets):
                size = max(tr.w, tr.h, d.w, d.h, 8)
                dist = math.hypot(d.x - px, d.y - py) / size
                same = d.label == "unknown" or tr.label == "unknown" or d.label == tr.label
                if dist <= self.max_dist and same:
                    pairs.append((dist, i, j))
        used_t, used_d = set(), set()
        for _, i, j in sorted(pairs):
            if i in used_t or j in used_d:
                continue
            used_t.add(i)
            used_d.add(j)
            tr, d = self.tracks[i], dets[j]
            tr.vx, tr.vy = 0.5 * tr.vx + 0.5 * (d.x - tr.x), 0.5 * tr.vy + 0.5 * (d.y - tr.y)
            tr.x, tr.y, tr.w, tr.h, tr.last_t = d.x, d.y, d.w, d.h, t
            tr.hits += 1
            tr.missed = 0
            tr.labels[d.label] = tr.labels.get(d.label, 0) + d.score
        new, ended = [], []
        for i, tr in enumerate(self.tracks):
            if i not in used_t:
                tr.missed += 1
                tr.x, tr.y = tr.x + tr.vx * 0.5, tr.y + tr.vy * 0.5
            if not tr.confirmed and tr.hits >= self.confirm_hits:
                tr.confirmed = True
                new.append(tr)
        for j, d in enumerate(dets):
            if j not in used_d:
                tr = Track(self.next_id, d.x, d.y, d.w, d.h, t, t, (d.x, d.y), labels={d.label: d.score})
                self.next_id += 1
                self.tracks.append(tr)
        keep = []
        for tr in self.tracks:
            if tr.missed > self.max_missed:
                if tr.confirmed:
                    ended.append(tr)
            else:
                keep.append(tr)
        self.tracks = keep
        return new, ended

    def visible(self):
        return [tr for tr in self.tracks if tr.confirmed and tr.missed == 0]


# --------------------------------------------------------------------------- analysis

class VisionAnalyzer:
    """Feed frames of one region (with their times); get spawn and disappearance events.

    edge: how close (as a share of the region, at least 1 monster width) to the edge counts
    as walking/scrolling in or out rather than spawning/dying.
    """

    def __init__(self, detector, damage=None, edge=0.08, max_dist=1.2, confirm_hits=3,
                 max_missed=4, big_scroll=0.25, cut_threshold=0.6, settle=0.5):
        self.detector, self.damage = detector, damage
        self.edge, self.big_scroll, self.cut_threshold = edge, big_scroll, cut_threshold
        self.settle, self.settle_until = settle, None    # monsters seen right away were already there
        self.tracker = Tracker(max_dist, confirm_hits, max_missed)
        self.events, self.frames = [], []
        self.prev_gray, self.size = None, None
        self.prev_damage = []
        self.seconds_processed, self.frames_processed = 0.0, 0
        self.resets = 0

    def _near_edge(self, x, y, w, h):
        W, H = self.size
        mx, my = max(self.edge * W, w * 0.75), max(self.edge * H, h * 0.75)
        return x < mx or y < my or x > W - mx or y > H - my

    def process(self, bgr, t):
        t0 = time.perf_counter()
        if self.settle_until is None:
            self.settle_until = t + self.settle
        self.size = (bgr.shape[1], bgr.shape[0])
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        prev = self.prev_gray
        dx, dy, resp = estimate_shift(prev, small)
        self.prev_gray = small
        cut = False
        if resp < 0.3:
            dx = dy = 0.0                            # no clear answer: assume the view stayed put...
            # ...unless the whole picture changed (a teleport, portal or map change)
            cut = prev is not None and prev.shape == small.shape and _ncc(prev, small) < self.cut_threshold
        dx, dy = dx * 2, dy * 2
        if cut or abs(dx) > self.big_scroll * self.size[0] or abs(dy) > self.big_scroll * self.size[1]:
            next_id = self.tracker.next_id
            self.tracker = Tracker(self.tracker.max_dist, self.tracker.confirm_hits, self.tracker.max_missed)
            self.tracker.next_id = next_id
            self.settle_until = t + self.settle
            self.resets += 1                         # a teleport or map change: start over, no events
            dx = dy = 0.0
        if abs(dx) >= 0.5 or abs(dy) >= 0.5:
            self.tracker.shift(dx, dy)
            if hasattr(self.detector, "shift"):
                self.detector.shift(dx, dy)
        dets = self.detector.detect(bgr)
        new, ended = self.tracker.update(dets, t)
        if self.damage is not None:
            found = self.damage.detect(bgr)
            for d in found:
                # A number already there last frame floats up a little: not a new hit.
                old = any(abs(d.x - p.x) <= 15 and -4 <= p.y - d.y <= 40 for p in self.prev_damage)
                if old:
                    continue
                for tr in self.tracker.tracks:          # a new number above (or on) a monster
                    if abs(d.x - tr.x) <= max(tr.w, 12) and tr.y - 2.5 * tr.h <= d.y <= tr.y + 0.5 * tr.h:
                        tr.damage.append(t)
                        break
            self.prev_damage = [Detection(d.x + dx, d.y + dy, d.w, d.h) for d in found]
        for tr in new:
            x, y = tr.first_xy
            if tr.first_t <= self.settle_until:
                tr.spawn_kind = "present"                # on screen when watching (re)started
            else:
                tr.spawn_kind = "entered" if self._near_edge(x, y, tr.w, tr.h) else "spawn"
            self.events.append({"t": tr.first_t, "event": tr.spawn_kind, "species": tr.label, "track": tr.id,
                                "x": round(x), "y": round(y)})
        for tr in ended:
            gone = "left" if self._near_edge(tr.x, tr.y, tr.w, tr.h) else "vanish"
            hits = sum(1 for d in tr.damage if d >= tr.last_t - 4.0)
            self.events.append({"t": tr.last_t, "event": gone, "species": tr.label, "track": tr.id,
                                "x": round(tr.x), "y": round(tr.y), "hits": hits,
                                "lifetime": round(tr.last_t - tr.first_t, 2)})
        self.frames.append((t, len(self.tracker.visible())))
        self.seconds_processed += time.perf_counter() - t0
        self.frames_processed += 1

    def finish(self, t):
        """End of the recording: monsters still in view simply stop being watched."""
        self.frames.append((t, len(self.tracker.visible())))


def classify_vanishes(events, gains, party_at, window=2.5):
    """Labels each 'vanish' with whose kill it probably was. gains: [(t, amount)] EXP gains;
    party_at(t) -> party size then. An EXP gain may confirm several deaths at once
    (multiple kills between two EXP readings)."""
    gains = sorted(gains)
    out = []
    for e in events:
        e = dict(e)
        if e.get("event") == "vanish":
            t = e["t"]
            exp = [a for g, a in gains if t - 0.5 <= g <= t + window and a > 0]
            damage = (e.get("hits") or 0) > 0
            party = party_at(t) or 1
            if party <= 1:
                e["confidence"] = "confirmed" if exp and damage else "probable" if exp else "unattributed"
            else:
                e["confidence"] = "probable" if exp and damage else "party_credit" if exp else "unattributed"
            e["exp_linked"] = bool(exp)
            e["party"] = party
        out.append(e)
    return out


def summarize(events, frames, real_seconds, species_exp=None):
    """Kill, spawn and competition metrics for a session (or one region of it)."""
    from .metrics import ESTIMATED, Metric, missing
    from .stats import poisson_rate_interval
    h = real_seconds / 3600 if real_seconds else 0
    if not frames and not events:
        return {"vision": missing("monster vision was off (Settings > Experimental)")}
    van = [e for e in events if e.get("event") == "vanish"]
    by_conf = {}
    for e in van:
        by_conf[e.get("confidence", "unattributed")] = by_conf.get(e.get("confidence", "unattributed"), 0) + 1
    spawns = sorted(e["t"] for e in events if e.get("event") == "spawn")
    personal = by_conf.get("confirmed", 0) + by_conf.get("probable", 0)

    def rate(n, note, conf="low"):
        if not h:
            return missing("no time recorded")
        ci = poisson_rate_interval(n, real_seconds)
        return Metric(n / h, ESTIMATED, conf, note, {"count": n, "ci95": ci})
    out = {
        "observed_deaths_hr": rate(len(van), "monsters that vanished mid-region (deaths/despawns), observed region only"),
        "personal_kills_hr": rate(personal, "confirmed + probable kills (EXPERIMENTAL)"),
        "confirmed_kills_hr": rate(by_conf.get("confirmed", 0), "solo, damage seen, EXP right after"),
        "unattributed_hr": rate(by_conf.get("unattributed", 0), "vanished with no evidence of being yours"),
        "spawns_hr": rate(len(spawns), "observed respawns/hour, selected region only"),
        "kills_by_confidence": by_conf,
    }
    species = {}
    for e in van:
        s = species.setdefault(e.get("species") or "unknown", {"vanished": 0, "personal": 0})
        s["vanished"] += 1
        s["personal"] += e.get("confidence") in ("confirmed", "probable")
    out["by_species"] = species
    if van:
        out["competition"] = Metric(by_conf.get("unattributed", 0) / len(van), ESTIMATED, "low",
                                    "share of observed deaths with no sign they were yours (only meaningful solo)")
        out["your_share"] = Metric(personal / len(van), ESTIMATED, "low", "share of observed deaths that were probably yours")
    # Spawn waves: spawns within 4 s of each other are one wave.
    waves = []
    for t in spawns:
        if waves and t - waves[-1][-1] <= 4.0:
            waves[-1].append(t)
        else:
            waves.append([t])
    if len(waves) >= 2:
        gaps = [b[0] - a[0] for a, b in zip(waves, waves[1:])]
        gaps.sort()
        out["wave_interval"] = Metric(gaps[len(gaps) // 2], ESTIMATED, "low",
                                      f"median time between {len(waves)} observed spawn waves (s)")
        between = [sum(1 for e in van if a[0] <= e["t"] < b[0]) for a, b in zip(waves, waves[1:])]
        out["deaths_per_wave"] = Metric(sum(between) / len(between), ESTIMATED, "low", "observed deaths between waves")
    if frames:
        vis = [n for _, n in frames]
        out["visible_avg"] = Metric(sum(vis) / len(vis), ESTIMATED, "medium", f"over {len(vis)} sampled frames")
        out["visible_max"] = Metric(max(vis), ESTIMATED, "medium")
        empty = sum(1 for n in vis if n == 0) / len(vis)
        out["empty_share"] = Metric(empty, ESTIMATED, "medium", "share of frames with no monster in the region")
    return out
