"""farm.db: the fork's own SQLite database, next to the upstream files in the data folder.

The upstream files (config.json, exp_log.json) are never rewritten by anything here.
Their log lines can be imported (read-only, as often as you like) as 'legacy' sessions
for rankings. Those only have totals, measured over active time.
"""
import hashlib
import json
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

from .economy import DEFAULT_PRICES, DEFAULT_SKILLS, Price
from .events import Event

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started     REAL NOT NULL,                 -- wall clock (Unix time) at t = 0
  ended       REAL,
  source      TEXT NOT NULL DEFAULT 'live',  -- live / video / legacy / manual
  title       TEXT,
  map         TEXT, job TEXT, level_start INTEGER, level_end INTEGER, party INTEGER,
  tags        TEXT NOT NULL DEFAULT '{}',    -- e.g. {"attack_mode": "Lucky Seven", "weapon": "26 att claw"}
  settings    TEXT NOT NULL DEFAULT '{}',    -- the rules it was recorded with (gap, auto-pause, ...)
  summary     TEXT,                          -- legacy sessions only: their totals
  legacy_key  TEXT UNIQUE,
  version     TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  t           REAL NOT NULL,
  kind        TEXT NOT NULL,
  data        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_by_session ON events (session_id, t);
CREATE TABLE IF NOT EXISTS prices (
  item TEXT PRIMARY KEY, price REAL, kind TEXT NOT NULL DEFAULT 'loot',
  restore_hp INTEGER NOT NULL DEFAULT 0, restore_mp INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'manual', updated REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, mp_cost INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS monsters (name TEXT PRIMARY KEY, exp INTEGER, hp INTEGER, def INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS sprites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, species TEXT NOT NULL, png BLOB NOT NULL, added REAL NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        with self._lock, self.db:
            self.db.executescript(SCHEMA)
            v = self.db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            if v is None:
                self.db.execute("INSERT INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
                self._seed()

    def _seed(self):
        for p in DEFAULT_PRICES:
            self.db.execute("INSERT OR IGNORE INTO prices (item, price, kind, restore_hp, restore_mp, source, updated)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (p.item, p.price, p.kind, p.restore_hp, p.restore_mp, p.source, time.time()))
        for name, cost in DEFAULT_SKILLS.items():
            self.db.execute("INSERT OR IGNORE INTO skills (name, mp_cost) VALUES (?, ?)", (name, cost))

    def close(self):
        with self._lock:
            self.db.close()

    # ---- sessions
    def new_session(self, started=None, source="live", settings=None, title=None, tags=None, version=None):
        with self._lock, self.db:
            cur = self.db.execute(
                "INSERT INTO sessions (started, source, settings, title, tags, version) VALUES (?, ?, ?, ?, ?, ?)",
                (started or time.time(), source, json.dumps(settings or {}), title, json.dumps(tags or {}), version))
            return cur.lastrowid

    def update_session(self, sid, **fields):
        allowed = {"ended", "title", "map", "job", "level_start", "level_end", "party", "settings", "tags", "summary"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise KeyError(k)
            sets.append(f"{k} = ?")
            vals.append(json.dumps(v) if k in ("settings", "tags", "summary") and not isinstance(v, str) else v)
        if sets:
            with self._lock, self.db:
                self.db.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", (*vals, sid))

    def set_tag(self, sid, key, value):
        s = self.session(sid)
        tags = s["tags"]
        if value in (None, ""):
            tags.pop(key, None)
        else:
            tags[key] = value
        self.update_session(sid, tags=tags)

    def _row(self, r):
        d = dict(r)
        for k in ("tags", "settings"):
            d[k] = json.loads(d[k] or "{}")
        d["summary"] = json.loads(d["summary"]) if d.get("summary") else None
        return d

    def session(self, sid):
        with self._lock:
            r = self.db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return self._row(r) if r else None

    def sessions(self, source=None, include_legacy=True):
        q, args = "SELECT * FROM sessions", []
        cond = []
        if source:
            cond.append("source = ?")
            args.append(source)
        if not include_legacy:
            cond.append("source != 'legacy'")
        if cond:
            q += " WHERE " + " AND ".join(cond)
        with self._lock:
            return [self._row(r) for r in self.db.execute(q + " ORDER BY started DESC", args)]

    def delete_session(self, sid):
        with self._lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE id = ?", (sid,))

    # ---- events
    def add_events(self, sid, events):
        rows = [(sid, float(e.t), e.kind, json.dumps(e.data, separators=(",", ":"))) for e in events]
        if rows:
            with self._lock, self.db:
                self.db.executemany("INSERT INTO events (session_id, t, kind, data) VALUES (?, ?, ?, ?)", rows)

    def add_event(self, sid, event):
        self.add_events(sid, [event])

    def events(self, sid, kinds=None):
        q, args = "SELECT t, kind, data FROM events WHERE session_id = ?", [sid]
        if kinds:
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        with self._lock:
            return [Event(r["t"], r["kind"], json.loads(r["data"])) for r in self.db.execute(q + " ORDER BY t, id", args)]

    def delete_event(self, sid, t, kind):
        with self._lock, self.db:
            self.db.execute("DELETE FROM events WHERE session_id = ? AND t = ? AND kind = ?", (sid, t, kind))

    # ---- prices, skills, monsters, sprites
    def prices(self):
        with self._lock:
            return [Price(r["item"], r["price"], r["kind"], r["restore_hp"], r["restore_mp"], r["source"], r["updated"])
                    for r in self.db.execute("SELECT * FROM prices ORDER BY kind, item")]

    def set_price(self, p):
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO prices (item, price, kind, restore_hp, restore_mp, source, updated)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (p.item, p.price, p.kind, int(p.restore_hp or 0), int(p.restore_mp or 0),
                             p.source or "manual", time.time()))

    def delete_price(self, item):
        with self._lock, self.db:
            self.db.execute("DELETE FROM prices WHERE item = ?", (item,))

    def import_prices_csv(self, path):
        """item,price[,kind,restore_hp,restore_mp]. Returns how many were set."""
        import csv
        n = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                item = (row.get("item") or "").strip()
                if not item:
                    continue
                price = row.get("price")
                price = float(price) if price not in (None, "") else None
                old = next((p for p in self.prices() if p.item == item), None)
                self.set_price(Price(item, price, (row.get("kind") or (old.kind if old else "loot")).strip(),
                                     int(row.get("restore_hp") or (old.restore_hp if old else 0)),
                                     int(row.get("restore_mp") or (old.restore_mp if old else 0)), "csv"))
                n += 1
        return n

    def skills(self):
        with self._lock:
            return {r["name"]: r["mp_cost"] for r in self.db.execute("SELECT * FROM skills ORDER BY name")}

    def set_skill(self, name, mp_cost):
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO skills (name, mp_cost) VALUES (?, ?)", (name, int(mp_cost)))

    def monsters(self):
        with self._lock:
            return {r["name"]: dict(r) for r in self.db.execute("SELECT * FROM monsters ORDER BY name")}

    def set_monster(self, name, exp=None, hp=None, def_=None, note=None):
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO monsters (name, exp, hp, def, note) VALUES (?, ?, ?, ?, ?)",
                            (name, exp, hp, def_, note))

    def add_sprite(self, species, png_bytes):
        with self._lock, self.db:
            self.db.execute("INSERT INTO sprites (species, png, added) VALUES (?, ?, ?)", (species, png_bytes, time.time()))

    def sprites(self):
        """{species: [BGRA images]}"""
        import cv2
        import numpy as np
        out = {}
        with self._lock:
            rows = list(self.db.execute("SELECT species, png FROM sprites ORDER BY id"))
        for r in rows:
            img = cv2.imdecode(np.frombuffer(r["png"], np.uint8), cv2.IMREAD_UNCHANGED)
            if img is not None:
                out.setdefault(r["species"], []).append(img)
        return out

    def delete_sprites(self, species):
        with self._lock, self.db:
            self.db.execute("DELETE FROM sprites WHERE species = ?", (species,))

    # ---- upstream log import
    def import_legacy(self, exp_log_path):
        """Adds (or updates) one 'legacy' session per upstream log line. Safe to repeat: a
        line already imported is updated in place (lines grow during the day)."""
        path = Path(exp_log_path)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        n = 0
        for e in data.get("entries", []) if isinstance(data, dict) else []:
            try:
                day = date.fromisoformat(e["date"])
                exp, secs = float(e["exp"]), float(e["seconds"])
            except Exception:
                continue
            key_src = json.dumps([e.get("date"), e.get("map_read") or e.get("map"), e.get("level"), e.get("party"),
                                  e.get("class")])
            key = hashlib.sha1(key_src.encode()).hexdigest()[:20]
            summary = {"exp": exp, "seconds": secs, "mesos": e.get("mesos"), "mesos_seconds": e.get("mesos_seconds"),
                       "basis": "active", "upstream_line": True}
            started = datetime(day.year, day.month, day.day, 12).timestamp()
            with self._lock, self.db:
                row = self.db.execute("SELECT id FROM sessions WHERE legacy_key = ?", (key,)).fetchone()
                if row:
                    self.db.execute("UPDATE sessions SET summary = ?, map = ? WHERE id = ?",
                                    (json.dumps(summary), e.get("map"), row["id"]))
                else:
                    self.db.execute(
                        "INSERT INTO sessions (started, ended, source, title, map, job, level_start, level_end, party,"
                        " summary, legacy_key, version) VALUES (?, ?, 'legacy', ?, ?, ?, ?, ?, ?, ?, ?, 'upstream')",
                        (started, started + secs, f"Log: {e.get('map')}", e.get("map"), e.get("class"), e.get("level"),
                         e.get("level"), e.get("party") or 1, json.dumps(summary), key))
                n += 1
        return n
