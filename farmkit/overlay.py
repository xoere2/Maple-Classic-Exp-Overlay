"""The fork's additions to the overlay window: recording sessions into farm.db, extra rows
(real/active EXP/hr, net mesos, potions, loot, kills, respawns, HP/MP), AFK, notes,
inventory snapshots, session tags, farm settings and the optional live monster vision.

maple_exp.App creates one FarmOverlay and calls its hooks; everything else is here, so the
upstream file stays easy to merge.
"""
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .economy import Price
from .events import TXN_TYPES
from .gauges import GaugeConfirm, label_gauges, read_gauges
from .metrics import Metric, fmt
from .recorder import Recorder
from .store import Store

FARM_DEFAULTS = {"headline": "real", "auto_pause": True, "split_by_map": True, "read_hpmp": True,
                 "mp_potion": "Blue Potion", "hp_potion": "", "skill": "Lucky Seven", "profit_basis": "operating",
                 "farm_expanded": False, "vision_on": False, "vision_fps": 3, "farm_monster": ""}
TAG_KEYS = ("attack_mode", "weapon", "monster", "note")
ATTACK_MODES = ("", "Basic attack", "Lucky Seven", "Mixed")
TXN_LABELS = {"purchase": "Bought items", "recharge": "Recharged stars", "equipment": "Bought equipment",
              "expense": "Other expense", "sale": "Sold items", "deposit": "Put mesos in storage",
              "withdraw": "Took mesos from storage", "transfer_out": "Gave mesos (mule/friend)",
              "transfer_in": "Got mesos (mule/friend)", "use": "Used items (counted by hand)",
              "loot": "Picked up items (counted by hand)"}


def farm_config(cfg):
    """Fills in (and type-checks) the fork's settings inside the overlay's config dict."""
    for k, default in FARM_DEFAULTS.items():
        if type(cfg.get(k)) is not type(default):
            cfg[k] = default
    if not 1 <= cfg["vision_fps"] <= 10:
        cfg["vision_fps"] = 3
    roi = cfg.get("vision_roi")
    if not (isinstance(roi, list) and len(roi) == 4 and all(type(x) is int for x in roi) and roi[2] > 8 and roi[3] > 8):
        cfg.pop("vision_roi", None)
    tags = cfg.get("farm_tags")
    cfg["farm_tags"] = {k: str(v) for k, v in tags.items() if k in TAG_KEYS and isinstance(v, str)} \
        if isinstance(tags, dict) else {}
    return cfg


class LiveVision(threading.Thread):
    """EXPERIMENTAL: watches the selected region of the game window a few times a second
    and sends monster events to the overlay's queue. Off unless turned on in Farm settings."""

    def __init__(self, app, roi, fps, analyzer):
        super().__init__(daemon=True)
        self.app, self.roi, self.fps, self.analyzer = app, roi, fps, analyzer
        self.stop_evt = threading.Event()
        self.ms_per_frame = None
        self.skipped = 0

    def run(self):
        import mss
        from maple_exp import client_rect, grab
        emitted = 0
        with mss.mss() as sct:
            while not self.stop_evt.is_set():
                t0 = time.monotonic()
                try:
                    hwnd = self.app.reader.hwnd
                    rect = client_rect(hwnd) if hwnd else None
                    if rect:
                        x, y, w, h = rect[0] + self.roi[0], rect[1] + self.roi[1], self.roi[2], self.roi[3]
                        own = self.app.reader.own_rect
                        covered = own and (x < own[0] + own[2] and own[0] < x + w and y < own[1] + own[3] and own[1] < y + h)
                        shot = None if covered else grab(sct, x, y, w, h)
                        if shot:
                            self.analyzer.process(shot[0], t0)
                            for e in self.analyzer.events[emitted:]:
                                self.app.q.put(("vision", (e["t"], {k: v for k, v in e.items() if k != "t"})))
                            emitted = len(self.analyzer.events)
                            self.app.q.put(("vision_frame", (t0, len(self.analyzer.tracker.visible()))))
                            fp = self.analyzer.frames_processed
                            self.ms_per_frame = 1000 * self.analyzer.seconds_processed / fp if fp else None
                        else:
                            self.skipped += 1
                except Exception as e:                  # never take the overlay down
                    from maple_exp import log_error
                    log_error("monster vision", e)
                self.stop_evt.wait(max(0.05, 1.0 / self.fps - (time.monotonic() - t0)))


class FarmOverlay:
    def __init__(self, app, data_dir, version, log_path=None):
        self.app = app
        self.cfg = farm_config(app.cfg)
        self.store = Store(data_dir / "farm.db")
        self.rec = Recorder(self.store, version)
        self.version = version
        try:
            if log_path:
                self.store.import_legacy(log_path)
        except Exception as e:
            from maple_exp import log_error
            log_error("importing the upstream log into farm.db", e)
        self.report = None
        self._prices = self._skills = self._monsters = None
        self._prices_at = 0.0
        self.vision = None
        self.analytics = None
        self.rows = {}
        self.expanded = bool(self.cfg["farm_expanded"])

    # ---- settings snapshot stored with each session
    def settings(self):
        c = self.cfg
        return {"gap": c["gap_seconds"], "auto_pause": c["auto_pause"], "mp_potion": c["mp_potion"],
                "hp_potion": c["hp_potion"], "skill": c["skill"], "profit_basis": c["profit_basis"],
                "species": c.get("farm_monster") or c["farm_tags"].get("monster", "")}

    def lookups(self, force=False):
        now = time.monotonic()
        if force or self._prices is None or now - self._prices_at > 10:
            self._prices, self._skills, self._monsters = self.store.prices(), self.store.skills(), self.store.monsters()
            self._prices_at = now
        return self._prices, self._skills, self._monsters

    # ---- building the window
    def build(self, parent, fonts, colors, row):
        BG, FG, DIM, FIELD = colors["BG"], colors["FG"], colors["DIM"], colors["FIELD"]
        self.colors, self.fonts = colors, fonts
        f = self.frame = tk.Frame(parent, bg=BG)
        f.grid(row=row, column=0, columnspan=2, sticky="we")
        f.columnconfigure(1, weight=1)

        def add(name, r, section):
            label = tk.Label(f, text=name, bg=BG, fg=DIM, font=fonts["row"])
            label.grid(row=r, column=0, sticky="w", padx=(12, 8))
            v = tk.StringVar(value="—")
            value = tk.Label(f, textvariable=v, bg=BG, fg=FG, font=fonts["value"])
            value.grid(row=r, column=1, sticky="e", padx=(8, 12))
            self.rows[name] = (v, label, value, section)
        other = "Active EXP / hr" if self.cfg["headline"] == "real" else "Real EXP / hr"
        self.other_rate_name = other
        names = [(other, "full"), ("Net mesos / hr", "full"),
                 ("Map · party", "more"), ("Session · idle", "more"), ("Last 1 / 15 min", "more"),
                 ("Picked up / hr", "more"), ("Potions / hr", "more"), ("Loot value / hr", "more"),
                 ("Kills / hr", "more"), ("Respawns / hr", "more"), ("HP · MP", "more")]
        for i, (name, section) in enumerate(names):
            add(name, i, section)
        bar = tk.Frame(f, bg=BG)
        bar.grid(row=len(names), column=0, columnspan=2, pady=(6, 0), padx=8, sticky="w")
        btn = dict(bg=FIELD, fg=FG, activebackground="#3a3f49", activeforeground=FG, relief="flat", padx=8, pady=2,
                   font=fonts["small"], cursor="hand2")
        self.afk_btn = tk.Button(bar, text="AFK", command=self.toggle_afk, **btn)
        self.afk_btn.pack(side="left", padx=2)
        tk.Button(bar, text="+ Note", command=self.note_dialog, **btn).pack(side="left", padx=2)
        tk.Button(bar, text="Tags", command=self.tags_dialog, **btn).pack(side="left", padx=2)
        tk.Button(bar, text="Analytics", command=self.open_analytics, **btn).pack(side="left", padx=2)
        self.more_btn = tk.Button(bar, text="", command=self.toggle_more, **btn)
        self.more_btn.pack(side="left", padx=2)
        self._btn = btn
        self._show_more()
        return row + 1

    def toggle_more(self):
        self.expanded = self.cfg["farm_expanded"] = not self.expanded
        self._show_more()
        self.app.root.geometry("")
        from maple_exp import save_config
        save_config(self.cfg)

    def _show_more(self):
        for name, (_, label, value, section) in self.rows.items():
            show = section == "full" or self.expanded
            for w in (label, value):
                w.grid() if show else w.grid_remove()
        self.more_btn.config(text="Less ▴" if self.expanded else "More ▾")

    def set_compact(self, on):
        self.frame.grid_remove() if on else self.frame.grid()

    # ---- session lifecycle (called by App)
    def on_start(self, now):
        a = self.app
        if self.rec.active:
            self.rec.resume(now)
        else:
            self.rec.begin(now, settings=self.settings(), tags=dict(self.cfg["farm_tags"]), map_name=a.map_name,
                           party=a.party, job=a.job, level=a.level)
        self._start_vision()

    def on_pause(self, now):
        self.rec.pause(now)
        self._stop_vision()

    def on_reset(self, now):
        self.end_session(now)

    def end_session(self, now):
        self._stop_vision()
        sid = self.rec.end(now)
        self._update_afk()
        if self.analytics and self.analytics.alive():
            self.analytics.refresh()
        return sid

    def on_close(self, now):
        self.end_session(now)
        self.store.close()

    def toggle_afk(self):
        now = time.monotonic()
        if not self.rec.active:
            self.app.status.set("Press Start first: AFK marks time inside a session.")
            return
        self.rec.set_afk(now, not self.rec.afk)
        self._update_afk()
        self.app.status.set("AFK: this time doesn't count toward real EXP/hr." if self.rec.afk
                            else "Back from AFK.")

    def _update_afk(self):
        BG, FIELD, FG = self.colors["BG"], self.colors["FIELD"], self.colors["FG"]
        if self.rec.active and self.rec.afk:
            self.afk_btn.config(text="AFK (on)", bg=self.colors["ACCENT_SOFT"], fg=BG)
        else:
            self.afk_btn.config(text="AFK", bg=FIELD, fg=FG)

    # ---- readings (called by App._handle)
    def on_exp(self, now, raw_delta, note, tracker, level):
        if not self.rec.active:
            return
        if raw_delta:
            self.rec.exp(now, raw_delta, tracker.exp, tracker.pct, level)
        if note == "died" and tracker.last_loss and tracker.req:
            self.rec.death(now, round(tracker.last_loss / 100 * tracker.req), level)
        if note == "LEVEL UP!":
            self.rec.level_up(now, level)

    def on_reading(self, r, now):
        a = self.app
        if not self.rec.active:
            return
        if a.meso.value is not None:
            self.rec.wallet(now, a.meso.value)
        self.rec.party(now, a.party)
        if a.job:
            self.rec.job(now, a.job)
        if a.map_name:
            old = self.rec.current("map")
            if old and a.map_name != old and self.cfg["split_by_map"] and self.rec.has_gains():
                # A new map gets its own session, so maps can be compared one to one.
                paused, afk = self.rec.paused, self.rec.afk
                self.rec.end(now)
                self.rec.begin(now, settings=self.settings(), tags=dict(self.cfg["farm_tags"]),
                               map_name=a.map_name, party=a.party, job=a.job, level=a.level)
                if paused:
                    self.rec.pause(now)
                if afk:
                    self.rec.set_afk(now, True)
            else:
                self.rec.map(now, a.map_name)
        g = r.gauges
        if g:
            self.rec.gauges(now, **g)

    def on_vision(self, payload):
        t, event = payload
        if self.rec.active and not self.rec.paused:
            self.rec.vision(t, event)

    def on_vision_frame(self, payload):
        t, visible = payload
        if self.rec.active and not self.rec.paused and not self.rec.afk:
            self.rec.vision_frame(t, visible)

    # ---- live vision
    def _start_vision(self):
        if not self.cfg["vision_on"] or self.vision or not self.cfg.get("vision_roi"):
            return
        from . import vision as v
        sprites = self.store.sprites()
        det = v.SpriteDetector(sprites) if sprites else v.ForegroundDetector()
        self.vision = LiveVision(self.app, self.cfg["vision_roi"], self.cfg["vision_fps"],
                                 v.VisionAnalyzer(det, damage=v.DamageDetector()))
        self.vision.start()

    def _stop_vision(self):
        if self.vision:
            self.vision.stop_evt.set()
            self.vision = None

    def restart_vision(self):
        self._stop_vision()
        if self.rec.active and not self.rec.paused:
            self._start_vision()

    # ---- numbers on screen
    def headline_rate(self, tracker):
        """The big number: real EXP/hr (default) or the upstream active EXP/hr."""
        if self.cfg["headline"] == "active" or not self.rec.active:
            return tracker.rate()
        rep = self.report
        if rep and rep.get("real_rate") and rep["real_rate"].ok:
            return rep["real_rate"].value
        return None

    def headline_title(self):
        return "EXP / hr (real)" if self.cfg["headline"] == "real" else "EXP / hr (active)"

    def refresh(self, now, tracker):
        prices, skills, monsters = self.lookups()
        if not self.rec.active:
            self.report = None
            for v, *_ in self.rows.values():
                v.set("—")
            return
        rep = self.report = self.rec.report(now, prices, skills, monsters, self.settings())
        if rep is None:
            return
        g = lambda k: rep.get(k) if isinstance(rep.get(k), Metric) else Metric()
        S = lambda name, text: self.rows[name][0].set(text)
        other = "active_rate" if self.cfg["headline"] == "real" else "real_rate"
        S(self.other_rate_name, fmt(g(other)))
        basis = {"operating": "operating_profit_hr", "realized": "realized_net_hr",
                 "estimated": "estimated_total_hr"}[self.cfg["profit_basis"]]
        net = g(basis)
        S("Net mesos / hr", fmt(net) if net.ok else fmt(g("realized_net_hr")))
        party = rep.info.get("party") or 1
        S("Map · party", f"{(rep.info.get('map') or '?')[:22]} · {'Solo' if party <= 1 else f'{party}ppl'}")
        real, idle = g("real_seconds"), g("idle_seconds")
        if real.ok:
            s = int(real.value)
            share = f" · {idle.value / real.value:.0%} idle" if idle.ok and real.value else ""
            S("Session · idle", f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}{share}")
        S("Last 1 / 15 min", f"{fmt(g('rate_1m'))} / {fmt(g('rate_15m'))}")
        S("Picked up / hr", fmt(g("pickups_hr")))
        S("Potions / hr", fmt(g("potion_cost_hr")))
        S("Loot value / hr", fmt(g("loot_value_hr")))
        S("Kills / hr", fmt(g("personal_kills_hr")) if g("personal_kills_hr").ok else fmt(g("estimated_kills_hr")))
        S("Respawns / hr", fmt(g("spawns_hr")))
        last = next((e.data for e in reversed(self.rec.events) if e.kind == "gauge"), None)
        if last:
            hp = f"{last['hp']}/{last['hp_max']}" if last.get("hp") is not None else "?"
            mp = f"{last['mp']}/{last['mp_max']}" if last.get("mp") is not None else "?"
            S("HP · MP", f"{hp} · {mp}")
        else:
            S("HP · MP", "status bar not read")

    def compact_text(self):
        rep = self.report
        if not rep:
            return ""
        kills = rep.get("personal_kills_hr")
        net = rep.get({"operating": "operating_profit_hr", "realized": "realized_net_hr",
                       "estimated": "estimated_total_hr"}[self.cfg["profit_basis"]])
        parts = []
        if isinstance(net, Metric) and net.ok:
            parts.append(f"Net {fmt(net)}/hr")
        if isinstance(kills, Metric) and kills.ok:
            parts.append(f"Kills {fmt(kills)}/hr")
        return "    ".join(parts)

    # ---- dialogs
    def _dialog(self, title):
        BG = self.colors["BG"]
        w = tk.Toplevel(self.app.root)
        w.title(title)
        w.configure(bg=BG, padx=14, pady=12)
        w.transient(self.app.root)
        w.attributes("-topmost", bool(self.cfg["topmost"]))
        r = self.app._window_rect()
        if r:
            w.geometry(f"+{r[0] + 30}+{r[1] + 30}")
        return w

    def _label(self, w, text, row, col=0, **kw):
        tk.Label(w, text=text, bg=self.colors["BG"], fg=self.colors["FG"], font=self.fonts["text"], **kw).grid(
            row=row, column=col, sticky="w", pady=2)

    def note_dialog(self):
        """A transaction: bought potions, sold ore, storage, transfers... (what the wallet alone
        can't explain)."""
        if not self.rec.active:
            messagebox.showinfo("Notes", "Press Start first: notes belong to the session going on.",
                                parent=self.app.root)
            return
        w = self._dialog("Add a note to this session")
        prices = {p.item: p for p in self.lookups(force=True)[0]}
        kind = tk.StringVar(value="purchase")
        item, qty, unit, amount, note = (tk.StringVar() for _ in range(5))
        self._label(w, "What happened", 0)
        labels = list(TXN_LABELS.values())
        kind_box = ttk.Combobox(w, values=labels, state="readonly", width=30)
        kind_box.set(TXN_LABELS["purchase"])
        kind_box.grid(row=0, column=1, sticky="we", pady=2)
        self._label(w, "Item", 1)
        ttk.Combobox(w, textvariable=item, values=sorted(prices), width=30).grid(row=1, column=1, sticky="we", pady=2)
        self._label(w, "Quantity", 2)
        tk.Entry(w, textvariable=qty, width=10).grid(row=2, column=1, sticky="w", pady=2)
        self._label(w, "Price each", 3)
        tk.Entry(w, textvariable=unit, width=12).grid(row=3, column=1, sticky="w", pady=2)
        self._label(w, "Total mesos", 4)
        tk.Entry(w, textvariable=amount, width=14).grid(row=4, column=1, sticky="w", pady=2)
        self._label(w, "Note", 5)
        tk.Entry(w, textvariable=note, width=32).grid(row=5, column=1, sticky="we", pady=2)
        hint = tk.StringVar(value="The wallet already shows purchases and sales: notes only explain them, "
                                  "so nothing is counted twice.")
        tk.Label(w, textvariable=hint, bg=self.colors["BG"], fg=self.colors["DIM"], font=self.fonts["small"],
                 wraplength=320, justify="left").grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 6))

        def fill(*_):
            p = prices.get(item.get().strip())
            if p and p.price is not None and not unit.get():
                unit.set(f"{p.price:g}")
            try:
                if qty.get() and unit.get():
                    amount.set(f"{float(qty.get()) * float(unit.get()):.0f}")
            except ValueError:
                pass
        for var in (item, qty, unit):
            var.trace_add("write", fill)

        def save():
            type_ = next(k for k, v in TXN_LABELS.items() if v == kind_box.get())
            try:
                q = int(float(qty.get() or 0))
                u = float(unit.get()) if unit.get() else None
                amt = float(amount.get() or 0)
                if TXN_TYPES[type_] and not amt:
                    raise ValueError("enter the total mesos")
                if type_ in ("use", "loot", "purchase", "sale") and item.get().strip() and not q:
                    raise ValueError("enter the quantity")
                self.rec.txn(time.monotonic(), type_, amt, item.get().strip() or None, q, u, note.get().strip())
                if item.get().strip() and item.get().strip() not in prices and u is not None:
                    self.store.set_price(Price(item.get().strip(), u, "consumable" if type_ in ("purchase", "use")
                                               else "loot"))
                self.app.status.set(f"Noted: {kind_box.get().lower()}.")
                w.destroy()
            except ValueError as e:
                hint.set(f"Can't save: {e}")
        buttons = tk.Frame(w, bg=self.colors["BG"])
        buttons.grid(row=7, column=0, columnspan=2, sticky="we")
        tk.Button(buttons, text="Save", command=save, **self._btn).pack(side="left")
        tk.Button(buttons, text="Inventory snapshot...", command=lambda: (w.destroy(), self.snapshot_dialog()),
                  **self._btn).pack(side="left", padx=6)
        tk.Button(buttons, text="Cancel", command=w.destroy, **self._btn).pack(side="right")

    def snapshot_dialog(self):
        """Counts of items in the inventory, typed in (start and end of a session is enough):
        loot and potions used come out exactly from the difference."""
        if not self.rec.active:
            messagebox.showinfo("Snapshot", "Press Start first.", parent=self.app.root)
            return
        w = self._dialog("Inventory snapshot")
        prices = sorted(p.item for p in self.lookups(force=True)[0])
        has_start = any(e.kind == "snapshot" and e.get("phase") == "start" for e in self.rec.events)
        phase = tk.StringVar(value="end" if has_start else "start")
        self._label(w, "This is the", 0)
        ttk.Combobox(w, textvariable=phase, values=("start", "mid", "end"), state="readonly", width=8).grid(
            row=0, column=1, sticky="w")
        last = next((e.get("items") for e in reversed(self.rec.events) if e.kind == "snapshot"), {}) or {}
        rows = []
        names = list(last) or [n for n in prices if "Potion" in n or "Ore" in n][:8]
        for i in range(max(8, len(names) + 2)):
            name, count = tk.StringVar(value=names[i] if i < len(names) else ""), tk.StringVar(
                value=str(last.get(names[i], "")) if i < len(names) else "")
            ttk.Combobox(w, textvariable=name, values=prices, width=26).grid(row=i + 1, column=0, pady=1, sticky="we")
            tk.Entry(w, textvariable=count, width=8).grid(row=i + 1, column=1, pady=1, sticky="w")
            rows.append((name, count))
        msg = tk.StringVar(value="Leave the count empty to skip an item. Counts at the start and end give exact "
                                 "loot and potion use.")
        tk.Label(w, textvariable=msg, bg=self.colors["BG"], fg=self.colors["DIM"], font=self.fonts["small"],
                 wraplength=300, justify="left").grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=4)

        def save():
            items = {}
            try:
                for name, count in rows:
                    if name.get().strip() and count.get().strip():
                        items[name.get().strip()] = int(count.get())
            except ValueError:
                msg.set("Counts must be whole numbers.")
                return
            self.rec.snapshot(time.monotonic(), items, phase.get())
            self.app.status.set(f"Inventory snapshot ({phase.get()}) saved: {len(items)} items.")
            w.destroy()
        tk.Button(w, text="Save", command=save, **self._btn).grid(row=len(rows) + 2, column=0, sticky="w")
        tk.Button(w, text="Cancel", command=w.destroy, **self._btn).grid(row=len(rows) + 2, column=1, sticky="e")

    def tags_dialog(self):
        """Labels for comparing sessions: attack mode, weapon, target monster."""
        w = self._dialog("Session tags")
        tags = dict(self.cfg["farm_tags"])
        from .vision import MONSTERS
        mons = sorted(set(MONSTERS) | set(self.lookups()[2]))
        attack = ttk.Combobox(w, values=ATTACK_MODES, width=24)
        attack.set(tags.get("attack_mode", ""))
        weapon = tk.StringVar(value=tags.get("weapon", ""))
        monster = ttk.Combobox(w, values=[""] + mons, width=24)
        monster.set(tags.get("monster", ""))
        note = tk.StringVar(value=tags.get("note", ""))
        self._label(w, "Attack mode", 0)
        attack.grid(row=0, column=1, sticky="we", pady=2)
        self._label(w, "Weapon (e.g. 26 att claw)", 1)
        tk.Entry(w, textvariable=weapon, width=26).grid(row=1, column=1, sticky="we", pady=2)
        self._label(w, "Monster farmed", 2)
        monster.grid(row=2, column=1, sticky="we", pady=2)
        self._label(w, "Note", 3)
        tk.Entry(w, textvariable=note, width=26).grid(row=3, column=1, sticky="we", pady=2)
        tk.Label(w, text="Tags stay on for the next sessions until you change them, and are added to the "
                         "session going on now.", bg=self.colors["BG"], fg=self.colors["DIM"],
                 font=self.fonts["small"], wraplength=300, justify="left").grid(row=4, column=0, columnspan=2,
                                                                                  sticky="w", pady=4)

        def save():
            new = {"attack_mode": attack.get().strip(), "weapon": weapon.get().strip(),
                   "monster": monster.get().strip(), "note": note.get().strip()}
            self.cfg["farm_tags"] = {k: v for k, v in new.items() if v}
            from maple_exp import save_config
            save_config(self.cfg)
            if self.rec.active:
                for k, v in new.items():
                    if tags.get(k, "") != v:
                        self.rec.tag(time.monotonic(), k, v)
            w.destroy()
        tk.Button(w, text="Save", command=save, **self._btn).grid(row=5, column=0, sticky="w")
        tk.Button(w, text="Cancel", command=w.destroy, **self._btn).grid(row=5, column=1, sticky="e")

    def settings_dialog(self, parent=None):
        from maple_exp import RegionSelector, client_rect, save_config
        w = self._dialog("Farm analytics settings")
        c = self.cfg
        BG, FG, FIELD = self.colors["BG"], self.colors["FG"], self.colors["FIELD"]
        check = dict(bg=BG, fg=FG, selectcolor=FIELD, activebackground=BG, activeforeground=FG, font=self.fonts["text"])
        headline = tk.StringVar(value=c["headline"])
        self._label(w, "Big number", 0)
        ttk.Combobox(w, textvariable=headline, values=("real", "active"), state="readonly", width=10).grid(
            row=0, column=1, sticky="w")
        tk.Label(w, text="Real counts all farming time (waiting for respawns too); active leaves out time with no "
                         "EXP, like the original overlay.", bg=BG, fg=self.colors["DIM"], font=self.fonts["small"],
                 wraplength=330, justify="left").grid(row=1, column=0, columnspan=2, sticky="w")
        auto = tk.BooleanVar(value=c["auto_pause"])
        tk.Checkbutton(w, text=f"Pause the active clock after no EXP for {c['gap_seconds']} s (Settings)",
                       variable=auto, **check).grid(row=2, column=0, columnspan=2, sticky="w")
        split = tk.BooleanVar(value=c["split_by_map"])
        tk.Checkbutton(w, text="Start a new session on each new map", variable=split, **check).grid(
            row=3, column=0, columnspan=2, sticky="w")
        hpmp = tk.BooleanVar(value=c["read_hpmp"])
        tk.Checkbutton(w, text="Read HP and MP from the status bar", variable=hpmp, **check).grid(
            row=4, column=0, columnspan=2, sticky="w")
        prices, skills, monsters = self.lookups(force=True)
        cons = [""] + sorted(p.item for p in prices if p.kind == "consumable")
        mp_pot, hp_pot = ttk.Combobox(w, values=cons, width=22), ttk.Combobox(w, values=cons, width=22)
        mp_pot.set(c["mp_potion"])
        hp_pot.set(c["hp_potion"])
        self._label(w, "MP potion you use", 5)
        mp_pot.grid(row=5, column=1, sticky="w")
        self._label(w, "HP potion you use", 6)
        hp_pot.grid(row=6, column=1, sticky="w")
        skill = ttk.Combobox(w, values=[""] + sorted(skills), width=22)
        skill.set(c["skill"])
        self._label(w, "Main attack skill", 7)
        skill.grid(row=7, column=1, sticky="w")
        basis = tk.StringVar(value=c["profit_basis"])
        self._label(w, "Net mesos means", 8)
        ttk.Combobox(w, textvariable=basis, values=("operating", "realized", "estimated"), state="readonly",
                     width=12).grid(row=8, column=1, sticky="w")
        tk.Label(w, text="operating: pickups + loot sold - consumables used. realized: wallet change (drops when "
                         "you stock up). estimated: also counts unsold loot at your prices.", bg=BG,
                 fg=self.colors["DIM"], font=self.fonts["small"], wraplength=330, justify="left").grid(
            row=9, column=0, columnspan=2, sticky="w")
        ttk.Separator(w).grid(row=10, column=0, columnspan=2, sticky="we", pady=6)
        vis = tk.BooleanVar(value=c["vision_on"])
        tk.Checkbutton(w, text="EXPERIMENTAL: watch monsters (kills, respawns)", variable=vis, **check).grid(
            row=11, column=0, columnspan=2, sticky="w")
        fps = tk.StringVar(value=str(c["vision_fps"]))
        self._label(w, "Looks per second", 12)
        tk.Spinbox(w, from_=1, to=10, textvariable=fps, width=4).grid(row=12, column=1, sticky="w")
        roi_text = tk.StringVar(value=f"Region: {c.get('vision_roi') or 'none selected'}")
        tk.Label(w, textvariable=roi_text, bg=BG, fg=FG, font=self.fonts["small"]).grid(row=13, column=0, sticky="w")

        def pick_region():
            hwnd = self.app.reader.hwnd
            rect = client_rect(hwnd) if hwnd else None
            if not rect:
                messagebox.showinfo("Region", "Start MapleStory first: the region is part of the game window.",
                                    parent=w)
                return

            def done(region):
                if region:
                    c["vision_roi"] = [region["left"] - rect[0], region["top"] - rect[1], region["width"],
                                       region["height"]]
                    roi_text.set(f"Region: {c['vision_roi']}")
                    save_config(c)
                    self.restart_vision()
            RegionSelector(self.app.root, done)
        tk.Button(w, text="Select region (e.g. one platform)...", command=pick_region, **self._btn).grid(
            row=13, column=1, sticky="w")
        tk.Label(w, text="Only what's inside the region is counted, and only while you're on screen with it. "
                         "Add monster pictures in Analytics > Monsters for species; without them it counts "
                         "anything that moves.", bg=BG, fg=self.colors["DIM"], font=self.fonts["small"],
                 wraplength=330, justify="left").grid(row=14, column=0, columnspan=2, sticky="w")

        def save():
            c.update(headline=headline.get(), auto_pause=bool(auto.get()), split_by_map=bool(split.get()),
                     read_hpmp=bool(hpmp.get()), mp_potion=mp_pot.get().strip(), hp_potion=hp_pot.get().strip(),
                     skill=skill.get().strip(), profit_basis=basis.get(), vision_on=bool(vis.get()))
            try:
                c["vision_fps"] = max(1, min(10, int(fps.get())))
            except ValueError:
                pass
            self.app.session.auto_pause = c["auto_pause"]
            self.app.reader.read_hpmp = c["read_hpmp"]
            self.app.big_title.config(text=self.headline_title())
            other = "Active EXP / hr" if c["headline"] == "real" else "Real EXP / hr"
            v, label, value, section = self.rows.pop(self.other_rate_name)
            label.config(text=other)
            self.rows = {other: (v, label, value, section), **self.rows}
            self.other_rate_name = other
            save_config(c)
            self.restart_vision() if c["vision_on"] else self._stop_vision()
            w.destroy()
        tk.Button(w, text="Save", command=save, **self._btn).grid(row=15, column=0, sticky="w", pady=(8, 0))
        tk.Button(w, text="Cancel", command=w.destroy, **self._btn).grid(row=15, column=1, sticky="e", pady=(8, 0))

    def open_analytics(self):
        from .ui import AnalyticsWindow
        if self.analytics and self.analytics.alive():
            self.analytics.win.lift()
            self.analytics.refresh()
            return
        self.analytics = AnalyticsWindow(self)


def read_status_gauges(sct, rect, anchor, gh, templates, confirm, covered, grab):
    """HP/MP from the live status line, left of the EXP number. Returns a dict or None."""
    wx, wy, _, _ = rect
    ax, ay = anchor
    x0, y0, w, h = wx, wy + int(ay - 1.6 * gh), max(1, int(ax - 0.5 * gh)), int(3.2 * gh)
    if covered(x0, y0, w, h):
        return None
    shot = grab(sct, x0, y0, w, h)
    if not shot:
        return None
    found = label_gauges(read_gauges(shot[0], templates))
    hp, mp = confirm.feed("hp", found.get("hp")), confirm.feed("mp", found.get("mp"))
    if not (hp or mp):
        return None
    return {"hp": hp.cur if hp else None, "hp_max": hp.max if hp else None,
            "mp": mp.cur if mp else None, "mp_max": mp.max if mp else None}


__all__ = ["FarmOverlay", "farm_config", "read_status_gauges", "GaugeConfirm", "FARM_DEFAULTS"]
