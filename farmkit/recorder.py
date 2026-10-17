"""Turns what the overlay sees into events in farm.db.

The overlay calls these methods with its own monotonic clock (`now`); events are stored
as seconds since the session began. Writes are batched (every few seconds) so the disk
isn't touched on every reading.
"""
import time

from . import FARMKIT_VERSION
from .analytics import session_report
from .events import Event, txn as make_txn


class Recorder:
    FLUSH_SECONDS = 5.0

    def __init__(self, store, version=""):
        self.store, self.version = store, version
        self.sid = None
        self.t0 = None
        self.events = []                 # this session's events, in memory (for live numbers)
        self._buffer = []
        self._last_flush = 0.0
        self._state = {}
        self.paused = self.afk = False
        self._report, self._report_at = None, 0.0

    @property
    def active(self):
        return self.sid is not None

    def t(self, now):
        return max(0.0, now - self.t0)

    def _emit(self, now, kind, data=None):
        if not self.active:
            return None
        e = Event(self.t(now), kind, data or {})
        self.events.append(e)
        self._buffer.append(e)
        self._report_at = 0.0
        if now - self._last_flush >= self.FLUSH_SECONDS:
            self.flush(now)
        return e

    def flush(self, now=None):
        if self.active and self._buffer:
            self.store.add_events(self.sid, self._buffer)
            self._buffer = []
        self._last_flush = now if now is not None else time.monotonic()

    # ---- session
    def begin(self, now, settings=None, tags=None, map_name=None, party=1, job=None, level=None, source="live",
              wall=None):
        if self.active:
            self.end(now)
        self.sid = self.store.new_session(started=wall or time.time(), source=source, settings=settings or {},
                                          tags=tags or {}, version=f"{self.version}+farmkit{FARMKIT_VERSION}")
        self.t0, self.events, self._buffer, self._state = now, [], [], {}
        self.paused = self.afk = False
        self._emit(now, "start")
        self.store.update_session(self.sid, level_start=level, job=job)
        if map_name:
            self.map(now, map_name)
        self.party(now, party or 1)
        if job:
            self.job(now, job)
        return self.sid

    def end(self, now):
        if not self.active:
            return None
        if self.afk:
            self.set_afk(now, False)
        self._emit(now, "stop")
        self.flush(now)
        sid = self.sid
        maps = [e.get("name") for e in self.events if e.kind == "map"]
        levels = [e.get("level") for e in self.events if e.kind == "exp" and e.get("level")]
        self.store.update_session(sid, ended=time.time(), map=maps[-1] if maps else None,
                                  level_end=levels[-1] if levels else None, party=self._state.get("party"))
        self.sid = None
        return sid

    def has_gains(self):
        return any(e.kind == "exp" and (e.get("amount") or 0) > 0 for e in self.events)

    # ---- timing
    def pause(self, now):
        if self.active and not self.paused:
            self.paused = True
            self._emit(now, "pause")

    def resume(self, now):
        if self.active and self.paused:
            self.paused = False
            self._emit(now, "resume")

    def set_afk(self, now, on):
        if self.active and on != self.afk:
            self.afk = on
            self._emit(now, "afk_on" if on else "afk_off")

    # ---- observations
    def exp(self, now, amount, exp=None, pct=None, level=None):
        if amount:
            self._emit(now, "exp", {"amount": amount, "exp": exp, "pct": pct, "level": level})

    def death(self, now, loss, level=None):
        self._emit(now, "death", {"loss": loss, "level": level})

    def level_up(self, now, level):
        self._emit(now, "level", {"level": level})

    def _changed(self, key, value):
        if value is None or self._state.get(key) == value:
            return False
        self._state[key] = value
        return True

    def map(self, now, name):
        if self.active and self._changed("map", name):
            self._emit(now, "map", {"name": name})
            self.store.update_session(self.sid, map=name)
            return True
        return False

    def current(self, key):
        return self._state.get(key)

    def party(self, now, size):
        if self.active and self._changed("party", int(size or 1)):
            self._emit(now, "party", {"size": int(size or 1)})

    def job(self, now, name):
        if self.active and self._changed("job", name):
            self._emit(now, "job", {"name": name})
            self.store.update_session(self.sid, job=name)

    def wallet(self, now, value):
        if self.active and self._changed("wallet", int(value)):
            self._emit(now, "wallet", {"value": int(value)})

    def gauges(self, now, hp=None, hp_max=None, mp=None, mp_max=None):
        if self.active and self._changed("gauge", (hp, hp_max, mp, mp_max)):
            self._emit(now, "gauge", {"hp": hp, "hp_max": hp_max, "mp": mp, "mp_max": mp_max})

    def vision(self, now, event):
        self._emit(now, "vision", dict(event))

    def vision_frame(self, now, visible):
        self._emit(now, "vision_frame", {"visible": int(visible)})

    # ---- notes from the player
    def txn(self, now, type_, amount=0, item=None, qty=0, unit_price=None, note=""):
        if not self.active:
            return None
        e = make_txn(self.t(now), type_, amount, item, qty, unit_price, note)
        return self._emit(now, "txn", e.data)

    def snapshot(self, now, items, phase="mid", note=""):
        clean = {k.strip(): int(v) for k, v in items.items() if k and k.strip() and int(v) >= 0}
        return self._emit(now, "snapshot", {"phase": phase, "items": clean, "note": note})

    def tag(self, now, key, value):
        if self.active:
            self.store.set_tag(self.sid, key, value)
            self._emit(now, "tag", {"key": key, "value": value})

    # ---- live numbers
    def report(self, now, prices, skills, monsters, settings, every=2.0):
        """This session's report, worked out at most every `every` seconds."""
        if not self.active:
            return None
        if self._report is None or now - self._report_at >= every:
            session = self.store.session(self.sid) or {}
            self._report = session_report(session, list(self.events), prices, skills, settings, monsters,
                                          now=self.t(now))
            self._report_at = now
        return self._report
