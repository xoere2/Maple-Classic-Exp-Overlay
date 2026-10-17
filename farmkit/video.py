"""Offline analysis of a recording (OBS or any gameplay video), entirely on this PC.

It reads the same things the live overlay does, at the video's own timestamps:
  - EXP (with the overlay's checks: level table, confirmations, deaths, level-ups)
  - HP/MP from the status bar
  - the inventory's mesos (when the inventory is open in the recording)
  - party size
  - map name (if Tesseract is installed)
  - monsters (EXPERIMENTAL, optional): kills and respawns in a region you choose
and stores it all as a 'video' session in farm.db, next to live sessions.

The frame's EXP bar is found by itself; the recording should show the whole game window
(or at least its bottom bar). Nothing is uploaded.
"""
import tempfile
import time
from pathlib import Path

import cv2

from .gauges import GaugeConfirm, label_gauges, read_gauges
from .recorder import Recorder


class FrameReader:
    """The overlay's screen reading, for frames of a video instead of live screenshots."""

    def __init__(self):
        import maple_exp as m
        self.m = m
        self.templates = m.DigitTemplates.load()
        self.mesos_digits = m.MesosDigits(path=Path(tempfile.mkdtemp()) / "mesos_digits.npz")   # never the user's
        self.box_finder = m.MesosBoxFinder()
        self.tracker = m.ExpTracker()
        self.tracker.start()
        self.meso = m.MesoTracker()
        self.gconf = GaugeConfirm()
        self.gh = None
        self.mesos_box = None
        self.party_seen = (None, 0)
        self.map_votes = {}

    def exp(self, img):
        """(candidates, confident, layout) for one frame."""
        m = self.m
        h = img.shape[0]
        top = int(h * (1 - m.SEARCH_STRIP))
        strip = img[top:]
        lay = m.find_exp(strip)
        if not lay:
            lay = m.find_exp(img)
            top = 0
            if not lay:
                return [], False, None, 0
        got = m.read_layout(self.templates, lay) if self.templates else None
        if got and m.fits_exp_table(*got):
            self.gh = lay.gh
            return [got], True, lay, top
        cands = ([got] if got else []) + [c for c in m.tesseract_candidates(strip if top else img, lay) if c != got]
        cands.sort(key=lambda c: not m.fits_exp_table(*c))
        return cands, False, lay, top

    def gauges(self, img, lay, top):
        """HP/MP from the part of the status line left of the EXP number."""
        if lay is None:
            return None
        g = lay.gh
        y0, y1 = max(0, int(top + lay.cy - 1.6 * g)), int(top + lay.cy + 1.6 * g)
        x1 = max(0, int(lay.box[0] - 0.5 * g))
        found = label_gauges(read_gauges(img[y0:y1, :x1], self.templates))
        hp = self.gconf.feed("hp", found.get("hp"))
        mp = self.gconf.feed("mp", found.get("mp"))
        if hp or mp:
            return {"hp": hp.cur if hp else None, "hp_max": hp.max if hp else None,
                    "mp": mp.cur if mp else None, "mp_max": mp.max if mp else None}
        return None

    def mesos(self, img):
        m = self.m
        if not self.gh:
            return None
        learned = self.mesos_digits
        templates = learned.templates() if learned.complete else self.templates
        field = m.find_mesos_field(img, self.gh)
        if field:
            # Storage and trade windows show two mesos boxes (yours and theirs): skip those
            # frames rather than risk reading the wrong one.
            x, y, w, h = field
            other = img.copy()
            other[y:y + h, x:x + w] = 0
            if m.find_mesos_field(other, self.gh):
                return None
        if field and templates and m.read_mesos_positions(img, field, templates) is None:
            field = None
        if field is None:
            field = self.box_finder.find(img, self.gh)
            if field and not (templates and m.mesos_box_lines_up(img, field, templates)):
                field = None
        if field is None or not templates:
            return None
        got = m.read_mesos_positions(img, field, templates)
        return got[0] if got else None

    def party(self, img):
        if not self.gh:
            return None
        n = max(1, len(self.m.find_party_bars(img, self.gh)))
        seen, k = self.party_seen
        k = k + 1 if n == seen else 1
        self.party_seen = (n, k)
        return n if k >= 2 else None

    def map_name(self, img):
        m = self.m
        if not (m.tesseract_ok and self.gh):
            return None
        h, w = img.shape[:2]
        corner = img[:int(h * 0.3), :int(w * 0.4)]
        box = m.find_map_box(corner, self.gh)
        if not box:
            return None
        x, y, bw, bh = box
        return m.read_map_name(corner[y:y + bh, x:x + bw], self.gh)


def analyze_video(path, store, roi=None, vision=None, exp_every=1.5, vision_fps=4.0, scan_every=4.0,
                  map_every=15.0, settings=None, tags=None, progress=None, max_seconds=None, cancel=None):
    """Analyzes a recording into a new 'video' session. Returns (session id, stats).

    roi: (x, y, w, h) in video pixels for monster vision, or None for the whole frame.
    vision: a farmkit.vision.VisionAnalyzer, or None to skip monsters.
    progress(fraction, text) is called now and then; cancel() -> True stops early.
    """
    import maple_exp as m
    if not m.tesseract_ok:
        tess = m.find_tesseract()
        if tess:
            m.pytesseract.pytesseract.tesseract_cmd = tess
            m.tesseract_ok = True
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Can't open {path}: not a video OpenCV can read")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if total else None
    wall = Path(path).stat().st_mtime - (duration or 0)
    reader = FrameReader()
    rec = Recorder(store, m.VERSION)
    sid = rec.begin(0.0, settings={**(settings or {}), "video": str(Path(path).name), "roi": roi}, tags=tags,
              source="video", wall=wall)
    next_exp = next_vis = next_scan = next_map = 0.0
    stats = {"frames": 0, "exp_reads": 0, "exp_ok": 0, "gauge_reads": 0, "mesos_reads": 0, "vision_frames": 0,
             "seconds": 0.0, "fps": fps}
    emitted = 0
    pending_mesos = None            # (value, when to look again): a reading is confirmed by the
    last_mesos = None               # same number a moment later (pickups change it between scans)
    t_start = time.perf_counter()
    idx = 0
    try:
        while True:
            if cancel and cancel():
                break
            t = idx / fps
            if max_seconds and t > max_seconds:
                break
            want = t >= next_exp or (vision is not None and t >= next_vis) or                 (pending_mesos is not None and t >= pending_mesos[1])
            ok = cap.grab()
            if not ok:
                break
            idx += 1
            if not want:
                continue
            ok, img = cap.retrieve()
            if not ok:
                break
            stats["frames"] += 1
            if t >= next_exp:
                next_exp = t + exp_every
                cands, confident, lay, top = reader.exp(img)
                stats["exp_reads"] += 1
                if cands:
                    tr = reader.tracker
                    before = tr.raw_gained
                    ok_, note = tr.observe(cands, confident)
                    stats["exp_ok"] += bool(ok_)
                    level = m.level_for_req(tr.req)
                    if tr.raw_gained != before:
                        rec.exp(t, tr.raw_gained - before, tr.exp, tr.pct, level)
                    if note == "died" and tr.last_loss and tr.req:
                        rec.death(t, round(tr.last_loss / 100 * tr.req), level)
                    if note == "LEVEL UP!":
                        rec.level_up(t, level)
                    g = reader.gauges(img, lay, top)
                    if g:
                        stats["gauge_reads"] += 1
                        rec.gauges(t, **g)
                if t >= next_scan:
                    next_scan = t + scan_every
                    value = reader.mesos(img)
                    if value is not None:
                        stats["mesos_reads"] += 1
                        pending_mesos = (value, t + 0.2)
                    party = reader.party(img)
                    if party:
                        rec.party(t, party)
                if t >= next_map:
                    next_map = t + map_every
                    name = reader.map_name(img)
                    if name and name == reader.__dict__.get("_last_map"):
                        rec.map(t, name)            # read the same twice in a row
                    reader._last_map = name
            if pending_mesos is not None and t >= pending_mesos[1]:
                again = reader.mesos(img)
                if again == pending_mesos[0]:
                    import maple_exp as m_
                    # Accept the first number, or one that could follow the last (no digit
                    # lost or added); a big change needs to be seen at the next scan too.
                    big = getattr(reader, "_big", None)
                    if last_mesos is None or m_._agrees(again, last_mesos) or (big is not None and m_._agrees(again, big)):
                        rec.wallet(t, again)
                        last_mesos = again
                        stats["mesos_ok"] = stats.get("mesos_ok", 0) + 1
                        reader._big = None
                    else:
                        reader._big = again         # a jump (shop, storage): believed if the next scan agrees
                pending_mesos = None
            if vision is not None and t >= next_vis:
                next_vis = t + 1.0 / vision_fps
                crop = img if roi is None else img[roi[1]:roi[1] + roi[3], roi[0]:roi[0] + roi[2]]
                if crop.size:
                    vision.process(crop, t)
                    stats["vision_frames"] += 1
                    for e in vision.events[emitted:]:
                        rec.vision(e["t"], {k: v for k, v in e.items() if k != "t"})
                    emitted = len(vision.events)
                    rec.vision_frame(t, len(vision.tracker.visible()))
            if progress and stats["frames"] % 25 == 0:
                progress(min(1.0, t / duration) if duration else 0.0, f"{t / 60:.1f} min read")
    finally:
        cap.release()
        t_end = idx / fps
        if vision is not None:
            vision.finish(t_end)
            for e in vision.events[emitted:]:
                rec.vision(e["t"], {k: v for k, v in e.items() if k != "t"})
        rec.end(t_end)
    stats["seconds"] = t_end
    stats["wall_seconds"] = time.perf_counter() - t_start
    if progress:
        progress(1.0, "done")
    return sid, stats
