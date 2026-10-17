"""The Analytics window: sessions, comparisons, rankings, charts, prices and notes, monsters
(info, sprites, drop rates), recorded-video analysis and the weapon what-if."""
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from . import analytics, export
from .economy import ITEM_KINDS, Ledger, Price
from .events import TXN_TYPES
from .metrics import KINDS, Metric, fmt

SERIES = ("#ffcc33", "#33ccff", "#66dd66", "#ff7a7a", "#c792ea", "#f0a050")
KIND_HELP = ("Kinds:  observed = read from the screen  ·  ✎ manual = typed in by you  ·  calculated  ·  "
             "≈ inferred = follows from the wallet and your notes  ·  ~ estimated = a model or heuristic  ·  "
             "— = not available")


def _dur(seconds):
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class AnalyticsWindow:
    def __init__(self, farm):
        self.farm, self.store = farm, farm.store
        c = farm.colors
        self.BG, self.FG, self.DIM, self.FIELD = c["BG"], c["FG"], c["DIM"], c["FIELD"]
        self.fonts = farm.fonts
        root = farm.app.root
        w = self.win = tk.Toplevel(root)
        w.title("Farm analytics")
        w.configure(bg=self.BG)
        w.minsize(720, 480)
        r = farm.app._window_rect()
        if r:
            w.geometry(f"+{r[0] + 40}+{r[1] + 40}")
        self.btn = dict(bg=self.FIELD, fg=self.FG, activebackground="#3a3f49", activeforeground=self.FG,
                        relief="flat", padx=10, pady=2, font=self.fonts["text"], cursor="hand2")
        style = ttk.Style(root)
        style.configure("Farm.TNotebook", background=self.BG, borderwidth=0)
        style.configure("Farm.TNotebook.Tab", background=self.FIELD, foreground=self.FG, padding=(10, 3),
                        font=self.fonts["text"])
        style.map("Farm.TNotebook.Tab", background=[("selected", "#3a3f49")])
        self.nb = ttk.Notebook(w, style="Farm.TNotebook")
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.reports = {}
        self.selected = []
        for name, build in (("Sessions", self._sessions_tab), ("Compare", self._compare_tab),
                            ("Rankings", self._rank_tab), ("Charts", self._charts_tab),
                            ("Prices & notes", self._economy_tab), ("Monsters", self._monsters_tab),
                            ("Recorded video", self._video_tab), ("Weapon what-if", self._whatif_tab)):
            f = tk.Frame(self.nb, bg=self.BG)
            self.nb.add(f, text=name)
            build(f)
        tk.Label(w, text=KIND_HELP, bg=self.BG, fg=self.DIM, font=self.fonts["small"], wraplength=900,
                 justify="left").pack(fill="x", padx=10, pady=(0, 8))
        self.refresh()

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    # ---- helpers
    def _tree(self, parent, columns, height=12, select="extended"):
        frame = tk.Frame(parent, bg=self.BG)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height,
                            selectmode=select, style="Log.Treeview")
        for key, title, width, anchor in columns:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=int(width * self.farm.app._px_per_pt / 1.33), anchor=anchor, stretch=key == columns[-1][0])
        sb = tk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, tree

    def _text(self, parent, height=10):
        frame = tk.Frame(parent, bg=self.BG)
        t = tk.Text(frame, height=height, wrap="word", bg="#23262d", fg=self.FG, relief="flat",
                    font=self.fonts["text"], padx=8, pady=6, highlightthickness=0)
        sb = tk.Scrollbar(frame, command=t.yview)
        t.configure(yscrollcommand=sb.set, state="disabled")
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, t

    @staticmethod
    def _set_text(t, text):
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", text)
        t.configure(state="disabled")

    def _label(self, parent, text, **grid):
        tk.Label(parent, text=text, bg=self.BG, fg=self.FG, font=self.fonts["text"]).grid(**{"sticky": "w", **grid})

    def report(self, sid):
        s = self.store.session(sid)
        if not s:
            return None
        prices, skills, monsters = self.farm.lookups(force=True)
        if sid == self.farm.rec.sid:
            return self.farm.rec.report(time.monotonic(), prices, skills, monsters, self.farm.settings(), every=0)
        return analytics.session_report(s, self.store.events(sid), prices, skills, None, monsters)

    def all_reports(self, legacy=False):
        prices, skills, monsters = self.farm.lookups(force=True)
        out = []
        for s in self.store.sessions(include_legacy=legacy):
            if s["id"] == self.farm.rec.sid:
                rep = self.farm.rec.report(time.monotonic(), prices, skills, monsters, self.farm.settings(), every=0)
            else:
                rep = analytics.session_report(s, self.store.events(s["id"]), prices, skills, None, monsters)
            if rep is not None:
                out.append(rep)
        return out

    def refresh(self):
        self._fill_sessions()
        self._fill_prices()
        self._fill_monsters()
        self._fill_rank()

    # ---- Sessions
    def _sessions_tab(self, f):
        top = tk.Frame(f, bg=self.BG)
        top.pack(fill="x", pady=(6, 4))
        self.show_legacy = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="Show lines imported from the original log (active time only)",
                       variable=self.show_legacy, command=self._fill_sessions, bg=self.BG, fg=self.FG,
                       selectcolor=self.FIELD, activebackground=self.BG, font=self.fonts["text"]).pack(side="left")
        cols = (("id", "#", 40, "e"), ("when", "Started", 110, "w"), ("source", "From", 55, "w"),
                ("map", "Map", 170, "w"), ("lv", "Lv", 35, "center"), ("party", "Party", 50, "center"),
                ("time", "Real time", 70, "e"), ("real", "EXP/hr real", 85, "e"), ("active", "EXP/hr active", 90, "e"),
                ("net", "Net mesos/hr", 95, "e"), ("kills", "Kills/hr", 70, "e"), ("tags", "Tags", 200, "w"))
        frame, self.sess_tree = self._tree(f, cols, height=11)
        frame.pack(fill="both", expand=True)
        self.sess_tree.bind("<<TreeviewSelect>>", self._session_selected)
        bar = tk.Frame(f, bg=self.BG)
        bar.pack(fill="x", pady=4)
        for text, cmd in (("Compare selected", self._compare_selected), ("Tag...", self._tag_selected),
                          ("Notes & raw events", self._raw_events), ("Delete", self._delete_selected),
                          ("Export CSV...", lambda: self._export("csv")), ("Export JSON...", lambda: self._export("json")),
                          ("Refresh", self.refresh)):
            tk.Button(bar, text=text, command=cmd, **self.btn).pack(side="left", padx=2)
        frame, self.detail = self._text(f, height=12)
        frame.pack(fill="both", expand=True)

    def _fill_sessions(self):
        t = self.sess_tree
        t.delete(*t.get_children())
        self.reports = {}
        basis = analytics.PROFIT_BASES[self.farm.cfg["profit_basis"]]
        for rep in self.all_reports(legacy=self.show_legacy.get()):
            sid = rep.info["id"]
            self.reports[sid] = rep
            g = lambda k: rep.get(k) if isinstance(rep.get(k), Metric) else Metric()
            party = rep.info.get("party") or 1
            live = " ●" if sid == self.farm.rec.sid else ""
            t.insert("", "end", iid=str(sid), values=(
                sid, datetime.fromtimestamp(rep.info["started"]).strftime("%b %d %H:%M") + live,
                rep.info.get("source"), rep.info.get("map") or "?", rep.info.get("level") or "?",
                "Solo" if party <= 1 else f"{party}ppl", _dur(g("real_seconds").value) if g("real_seconds").ok else "—",
                fmt(g("real_rate")), fmt(g("active_rate")), fmt(g(basis)),
                fmt(g("personal_kills_hr")) if g("personal_kills_hr").ok else fmt(g("estimated_kills_hr")),
                ", ".join(f"{k}={v}" for k, v in (rep.info.get("tags") or {}).items())))

    def _session_selected(self, _e=None):
        self.selected = [int(i) for i in self.sess_tree.selection()]
        if not self.selected:
            return
        rep = self.reports.get(self.selected[0]) or self.report(self.selected[0])
        self._set_text(self.detail, self.describe(rep))
        self._draw_charts()

    def describe(self, rep):
        i = rep.info
        lines = [f"Session #{i['id']} · {i.get('source')} · {i.get('map') or '?'} · Lv {i.get('level') or '?'} · "
                 f"{'Solo' if (i.get('party') or 1) <= 1 else str(i.get('party')) + ' people'} · "
                 f"{i.get('job') or ''}", f"Tags: {i.get('tags') or 'none'}", ""]
        maps = i.get("maps") or {}
        if len(maps) > 1:
            lines.append("Per map:")
            for name, d in maps.items():
                rr = f"{d['real_rate']:,.0f}" if d.get("real_rate") else "—"
                lines.append(f"  {name}: {d['exp']:,.0f} EXP over {_dur(d['real_seconds'])} real ({rr}/hr)")
            lines.append("")
        svp = i.get("solo_vs_party")
        if svp and svp["party"]["real_seconds"] and svp["solo"]["real_seconds"]:
            for k in ("solo", "party"):
                d = svp[k]
                rr = f"{d['real_rate']:,.0f}" if d.get("real_rate") else "—"
                lines.append(f"  {k}: {d['exp']:,.0f} EXP over {_dur(d['real_seconds'])} ({rr}/hr)")
            lines.append("")
        for key, m in rep.items():
            if not isinstance(m, Metric):
                continue
            if isinstance(m.value, dict):
                value = ", ".join(f"{k}: {v if not isinstance(v, float) else f'{v:.0%}'}" for k, v in m.value.items()) or "—"
            elif key.endswith("seconds") and m.ok:
                value = _dur(m.value)
            else:
                value = fmt(m, 2 if key in ("exp_per_mp", "mp_per_kill", "attacks_per_kill", "competition",
                                            "your_share", "empty_share", "visible_avg") else 0)
            conf = f", {m.confidence}" if m.confidence else ""
            ci = m.detail.get("ci95") if isinstance(m.detail, dict) else None
            ci_text = f"  (95%: {ci[0]:,.1f}–{ci[1]:,.1f})" if ci else ""
            lines.append(f"{key:<26} {value:>18}{ci_text}   [{m.kind}{conf}]  {m.note}")
        return "\n".join(lines)

    def _selected_or_warn(self):
        if not self.selected:
            messagebox.showinfo("Sessions", "Select a session first (Ctrl+click for several).", parent=self.win)
        return self.selected

    def _compare_selected(self):
        if len(self._selected_or_warn()) < 1:
            return
        self._fill_compare()
        self.nb.select(1)

    def _tag_selected(self):
        sel = self._selected_or_warn()
        if not sel:
            return
        s = self.store.session(sel[0])
        w = tk.Toplevel(self.win)
        w.title("Tags")
        w.configure(bg=self.BG, padx=12, pady=10)
        entries = {}
        from .overlay import ATTACK_MODES, TAG_KEYS
        for r, key in enumerate(TAG_KEYS):
            self._label(w, key.replace("_", " "), row=r, column=0)
            if key == "attack_mode":
                e = ttk.Combobox(w, values=ATTACK_MODES, width=28)
                e.set(s["tags"].get(key, ""))
            else:
                e = tk.Entry(w, width=30)
                e.insert(0, s["tags"].get(key, ""))
            e.grid(row=r, column=1, pady=2)
            entries[key] = e
        title = tk.Entry(w, width=30)
        title.insert(0, s.get("title") or "")
        self._label(w, "title", row=len(TAG_KEYS), column=0)
        title.grid(row=len(TAG_KEYS), column=1, pady=2)

        def save():
            for sid in sel:
                for key, e in entries.items():
                    if len(sel) == 1 or e.get().strip():
                        self.store.set_tag(sid, key, e.get().strip())
                if len(sel) == 1:
                    self.store.update_session(sid, title=title.get().strip() or None)
            w.destroy()
            self.refresh()
        tk.Button(w, text=f"Save ({len(sel)} session{'s' * (len(sel) > 1)})", command=save, **self.btn).grid(
            row=len(TAG_KEYS) + 1, column=0, columnspan=2, pady=(6, 0))

    def _raw_events(self):
        sel = self._selected_or_warn()
        if not sel:
            return
        sid = sel[0]
        w = tk.Toplevel(self.win)
        w.title(f"Session #{sid}: raw observations and notes")
        w.configure(bg=self.BG)
        cols = (("t", "Time", 70, "e"), ("kind", "Kind", 80, "w"), ("data", "Data", 520, "w"))
        frame, tree = self._tree(w, cols, height=22, select="browse")
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        events = self.store.events(sid) if sid != self.farm.rec.sid else list(self.farm.rec.events)
        counts = {}
        for i, e in enumerate(events):
            counts[e.kind] = counts.get(e.kind, 0) + 1
            tree.insert("", "end", iid=str(i), values=(_dur(e.t), e.kind, ", ".join(f"{k}={v}" for k, v in e.data.items())))
        tk.Label(w, text="Counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), bg=self.BG, fg=self.DIM,
                 font=self.fonts["small"]).pack(anchor="w", padx=8)

        def delete_note():
            s = tree.selection()
            if not s:
                return
            e = events[int(s[0])]
            if e.kind not in ("txn", "snapshot", "tag"):
                messagebox.showinfo("Delete", "Only your own notes (txn, snapshot, tag) can be deleted; observations "
                                              "stay as they were recorded.", parent=w)
                return
            if sid == self.farm.rec.sid:
                self.farm.rec.flush()
                self.farm.rec.events.remove(e)
            self.store.delete_event(sid, e.t, e.kind)
            tree.delete(s[0])
        tk.Button(w, text="Delete selected note", command=delete_note, **self.btn).pack(anchor="w", padx=8, pady=6)

    def _delete_selected(self):
        sel = self._selected_or_warn()
        if not sel or self.farm.rec.sid in sel:
            if self.farm.rec.sid in sel:
                messagebox.showinfo("Delete", "The session going on can't be deleted. Press Reset first.", parent=self.win)
            return
        if messagebox.askyesno("Delete", f"Delete {len(sel)} session(s) and their raw data?", parent=self.win):
            for sid in sel:
                self.store.delete_session(sid)
            self.refresh()

    def _export(self, kind):
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=f".{kind}",
                                            filetypes=[(kind.upper(), f"*.{kind}")],
                                            initialfile=f"farm-sessions.{kind}")
        if not path:
            return
        self.farm.rec.flush()
        ids = set(self.selected) or None
        n = export.to_csv(self.store, path, ids) if kind == "csv" else export.to_json(self.store, path, ids)
        messagebox.showinfo("Export", f"{n} session(s) saved to\n{path}", parent=self.win)

    # ---- Compare
    def _compare_tab(self, f):
        tk.Label(f, text="Select sessions on the Sessions tab (Ctrl+click), then Compare selected.", bg=self.BG,
                 fg=self.DIM, font=self.fonts["small"]).pack(anchor="w", pady=(6, 2))
        self.cmp_frame = tk.Frame(f, bg=self.BG)
        self.cmp_frame.pack(fill="both", expand=True)
        self.cmp_tree = None

    def _fill_compare(self):
        reps = [self.reports.get(s) or self.report(s) for s in self.selected]
        reps = [r for r in reps if r]
        if self.cmp_tree:
            self.cmp_tree.master.destroy()
        cols = [("metric", "Metric", 220, "w")] + [(f"s{i}", f"#{r.info['id']} {(r.info.get('map') or '?')[:18]}", 150, "e")
                                                   for i, r in enumerate(reps)]
        frame, self.cmp_tree = self._tree(self.cmp_frame, cols, height=24, select="browse")
        frame.pack(fill="both", expand=True)
        for label, cells in analytics.compare(reps):
            self.cmp_tree.insert("", "end", values=(label, *cells))

    # ---- Rankings
    def _rank_tab(self, f):
        top = tk.Frame(f, bg=self.BG)
        top.pack(fill="x", pady=6)
        self.rank_by = ttk.Combobox(top, values=[v[0] for v in analytics.RANKINGS.values()], state="readonly", width=38)
        self.rank_by.set(analytics.RANKINGS["exp_hr_profitable"][0])
        self.rank_by.pack(side="left")
        tk.Label(top, text="  profit basis", bg=self.BG, fg=self.FG, font=self.fonts["text"]).pack(side="left")
        self.rank_basis = ttk.Combobox(top, values=list(analytics.PROFIT_BASES), state="readonly", width=11)
        self.rank_basis.set(self.farm.cfg["profit_basis"])
        self.rank_basis.pack(side="left")
        tk.Label(top, text="  at least", bg=self.BG, fg=self.FG, font=self.fonts["text"]).pack(side="left")
        self.rank_min = tk.Spinbox(top, from_=1, to=120, width=4)
        self.rank_min.delete(0, "end")
        self.rank_min.insert(0, "10")
        self.rank_min.pack(side="left")
        tk.Label(top, text="min", bg=self.BG, fg=self.FG, font=self.fonts["text"]).pack(side="left")
        tk.Button(top, text="Rank", command=self._fill_rank, **self.btn).pack(side="left", padx=6)
        cols = (("n", "#", 30, "e"), ("id", "Session", 60, "e"), ("map", "Map", 200, "w"), ("lv", "Lv", 35, "center"),
                ("party", "Party", 50, "center"), ("value", "Value", 100, "e"), ("note", "", 300, "w"))
        frame, self.rank_tree = self._tree(f, cols, height=10, select="browse")
        frame.pack(fill="both", expand=True)
        tk.Label(f, text="Left out, and why", bg=self.BG, fg=self.DIM, font=self.fonts["small"]).pack(anchor="w")
        frame, self.rank_left = self._text(f, height=4)
        frame.pack(fill="x")
        tk.Label(f, text="Map efficiency (all sessions on a map added up, real time)", bg=self.BG, fg=self.DIM,
                 font=self.fonts["small"]).pack(anchor="w", pady=(6, 0))
        cols = (("map", "Map · party", 260, "w"), ("n", "Sessions", 70, "e"), ("h", "Hours", 60, "e"),
                ("real", "EXP/hr real", 90, "e"), ("active", "EXP/hr active", 95, "e"),
                ("op", "Operating mesos/hr", 120, "e"), ("re", "Realized mesos/hr", 120, "e"))
        frame, self.map_tree = self._tree(f, cols, height=6, select="browse")
        frame.pack(fill="both", expand=True)

    def _fill_rank(self):
        if not hasattr(self, "rank_tree"):
            return
        by = next(k for k, v in analytics.RANKINGS.items() if v[0] == self.rank_by.get())
        try:
            minutes = int(self.rank_min.get())
        except ValueError:
            minutes = 10
        reps = self.all_reports()
        ranked, left = analytics.rank(reps, by, self.rank_basis.get(), minutes)
        t = self.rank_tree
        t.delete(*t.get_children())
        for n, (r, value, note) in enumerate(ranked, 1):
            party = r.info.get("party") or 1
            t.insert("", "end", values=(n, r.info["id"], r.info.get("map") or "?", r.info.get("level") or "?",
                                        "Solo" if party <= 1 else f"{party}ppl", f"{value:,.2f}" if value < 100 else f"{value:,.0f}",
                                        note))
        self._set_text(self.rank_left, "\n".join(f"#{r.info['id']} {r.info.get('map') or '?'}: {why}" for r, why in left)
                       or "nothing left out")
        m = self.map_tree
        m.delete(*m.get_children())
        f = lambda v: "—" if v is None else f"{v:,.0f}"
        for g in analytics.group_by(reps, "map+party"):
            key = g["key"]
            m.insert("", "end", values=(f"{key[0] or '?'} · {key[1]}", g["sessions"], f"{g['hours']:.1f}",
                                        f(g["real_rate"]), f(g["active_rate"]), f(g["operating_hr"]), f(g["realized_hr"])))

    # ---- Charts
    def _charts_tab(self, f):
        tk.Label(f, text="Select one or more sessions on the Sessions tab.", bg=self.BG, fg=self.DIM,
                 font=self.fonts["small"]).pack(anchor="w", pady=(6, 2))
        self.chart_exp = tk.Canvas(f, bg="#23262d", height=230, highlightthickness=0)
        self.chart_exp.pack(fill="both", expand=True, pady=(0, 6))
        self.chart_mesos = tk.Canvas(f, bg="#23262d", height=180, highlightthickness=0)
        self.chart_mesos.pack(fill="both", expand=True)
        for c in (self.chart_exp, self.chart_mesos):
            c.bind("<Configure>", lambda e: self._draw_charts())

    def _draw_charts(self):
        if not hasattr(self, "chart_exp"):
            return
        exp_series, wallet_series = [], []
        for i, sid in enumerate(self.selected[:6]):
            s = self.store.session(sid)
            if not s or s["source"] == "legacy":
                continue
            events = self.store.events(sid) if sid != self.farm.rec.sid else list(self.farm.rec.events)
            from .timing import Timeline
            tl = Timeline(events, gap=self.farm.cfg["gap_seconds"])
            total, pts = 0.0, [(0.0, 0.0)]
            for t, a in tl.gains:
                total += a
                pts.append((t / 60, total))
            pts.append((tl.real_seconds / 60, total))
            color = SERIES[i % len(SERIES)]
            exp_series.append((f"#{sid} {s.get('map') or '?'}", pts, color))
            w = [(tl.eff(e.t) / 60, e.get("value")) for e in events if e.kind == "wallet"]
            if w:
                base = w[0][1]
                wallet_series.append((f"#{sid}", [(x, v - base) for x, v in w], color))
        self._line_chart(self.chart_exp, exp_series, "EXP gained over real minutes (flat stretches = no EXP)")
        self._line_chart(self.chart_mesos, wallet_series, "Wallet change over real minutes (mesos)")

    def _line_chart(self, c, series, title):
        c.delete("all")
        W, H = max(200, c.winfo_width()), max(120, c.winfo_height())
        L, R, T, B = 70, 16, 24, 26
        c.create_text(L, 12, text=title, anchor="w", fill=self.DIM, font=self.fonts["small"])
        pts = [p for _, s, _ in series for p in s]
        if not pts:
            c.create_text(W / 2, H / 2, text="nothing to show", fill=self.DIM, font=self.fonts["text"])
            return
        x0, x1 = 0.0, max(p[0] for p in pts) or 1.0
        y0, y1 = min(0.0, min(p[1] for p in pts)), max(p[1] for p in pts)
        if y1 == y0:
            y1 = y0 + 1
        X = lambda x: L + (x - x0) / (x1 - x0) * (W - L - R)
        Y = lambda y: H - B - (y - y0) / (y1 - y0) * (H - T - B)
        c.create_line(L, H - B, W - R, H - B, fill="#3a3f49")
        c.create_line(L, T, L, H - B, fill="#3a3f49")
        for k in range(5):
            yv = y0 + (y1 - y0) * k / 4
            c.create_text(L - 6, Y(yv), text=f"{yv:,.0f}", anchor="e", fill=self.DIM, font=self.fonts["small"])
            xv = x0 + (x1 - x0) * k / 4
            c.create_text(X(xv), H - B + 12, text=f"{xv:.0f} min", fill=self.DIM, font=self.fonts["small"])
        for n, (label, s, color) in enumerate(series):
            flat = [v for x, y in s for v in (X(x), Y(y))]
            if len(flat) >= 4:
                c.create_line(*flat, fill=color, width=2)
            c.create_text(W - R - 4, T + 4 + 14 * n, text=label, anchor="ne", fill=color, font=self.fonts["small"])

    # ---- Prices & notes
    def _economy_tab(self, f):
        left = tk.Frame(f, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        cols = (("item", "Item", 180, "w"), ("price", "Price", 80, "e"), ("kind", "Kind", 80, "w"),
                ("hp", "HP", 45, "e"), ("mp", "MP", 45, "e"), ("src", "Source", 70, "w"))
        frame, self.price_tree = self._tree(left, cols, height=14, select="browse")
        frame.pack(fill="both", expand=True)
        self.price_tree.bind("<<TreeviewSelect>>", self._price_selected)
        form = tk.Frame(left, bg=self.BG)
        form.pack(fill="x", pady=4)
        self.pv = {k: tk.StringVar() for k in ("item", "price", "kind", "hp", "mp")}
        for i, (k, label, width) in enumerate((("item", "Item", 22), ("price", "Mesos each (empty = not priced)", 12),
                                               ("hp", "Restores HP", 6), ("mp", "Restores MP", 6))):
            self._label(form, label, row=i, column=0)
            tk.Entry(form, textvariable=self.pv[k], width=width).grid(row=i, column=1, sticky="w", pady=1)
        self._label(form, "Kind", row=4, column=0)
        ttk.Combobox(form, textvariable=self.pv["kind"], values=ITEM_KINDS, state="readonly", width=12).grid(
            row=4, column=1, sticky="w")
        bar = tk.Frame(left, bg=self.BG)
        bar.pack(fill="x")
        tk.Button(bar, text="Save price", command=self._save_price, **self.btn).pack(side="left", padx=2)
        tk.Button(bar, text="Delete", command=self._delete_price, **self.btn).pack(side="left", padx=2)
        tk.Button(bar, text="Import CSV...", command=self._import_prices, **self.btn).pack(side="left", padx=2)
        tk.Label(left, text="Prices are yours to set: nothing is fetched from the internet. CSV columns: "
                            "item, price, kind, restore_hp, restore_mp.", bg=self.BG, fg=self.DIM,
                 font=self.fonts["small"], wraplength=420, justify="left").pack(anchor="w", pady=4)
        right = tk.Frame(f, bg=self.BG)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text="Skills (MP per cast)", bg=self.BG, fg=self.FG, font=self.fonts["text"]).pack(anchor="w")
        cols = (("name", "Skill", 160, "w"), ("mp", "MP", 60, "e"))
        frame, self.skill_tree = self._tree(right, cols, height=5, select="browse")
        frame.pack(fill="x")
        sf = tk.Frame(right, bg=self.BG)
        sf.pack(fill="x", pady=4)
        self.skill_name, self.skill_mp = tk.StringVar(), tk.StringVar()
        tk.Entry(sf, textvariable=self.skill_name, width=18).pack(side="left")
        tk.Entry(sf, textvariable=self.skill_mp, width=6).pack(side="left", padx=4)
        tk.Button(sf, text="Save skill", command=self._save_skill, **self.btn).pack(side="left")
        tk.Label(right, text="Notes for the session going on: use '+ Note' and 'Inventory snapshot' on the "
                             "overlay. To see or delete a session's notes: Sessions > Notes & raw events.",
                 bg=self.BG, fg=self.DIM, font=self.fonts["small"], wraplength=360, justify="left").pack(anchor="w", pady=8)

    def _fill_prices(self):
        if not hasattr(self, "price_tree"):
            return
        t = self.price_tree
        t.delete(*t.get_children())
        for p in self.store.prices():
            t.insert("", "end", iid=p.item, values=(p.item, "—" if p.price is None else f"{p.price:,.0f}", p.kind,
                                                    p.restore_hp or "", p.restore_mp or "", p.source))
        s = self.skill_tree
        s.delete(*s.get_children())
        for name, cost in self.store.skills().items():
            s.insert("", "end", values=(name, cost))

    def _price_selected(self, _e=None):
        sel = self.price_tree.selection()
        p = next((x for x in self.store.prices() if x.item == (sel[0] if sel else None)), None)
        if p:
            self.pv["item"].set(p.item)
            self.pv["price"].set("" if p.price is None else f"{p.price:g}")
            self.pv["kind"].set(p.kind)
            self.pv["hp"].set(str(p.restore_hp or ""))
            self.pv["mp"].set(str(p.restore_mp or ""))

    def _save_price(self):
        item = self.pv["item"].get().strip()
        if not item:
            return
        try:
            price = float(self.pv["price"].get()) if self.pv["price"].get().strip() else None
            hp, mp = int(self.pv["hp"].get() or 0), int(self.pv["mp"].get() or 0)
        except ValueError:
            messagebox.showinfo("Price", "Price and restore values must be numbers.", parent=self.win)
            return
        self.store.set_price(Price(item, price, self.pv["kind"].get() or "loot", hp, mp, "manual"))
        self.farm.lookups(force=True)
        self._fill_prices()

    def _delete_price(self):
        item = self.pv["item"].get().strip()
        if item and messagebox.askyesno("Delete", f"Delete the price of {item}?", parent=self.win):
            self.store.delete_price(item)
            self._fill_prices()

    def _import_prices(self):
        path = filedialog.askopenfilename(parent=self.win, filetypes=[("CSV", "*.csv")])
        if path:
            try:
                n = self.store.import_prices_csv(path)
                messagebox.showinfo("Prices", f"{n} prices imported.", parent=self.win)
            except Exception as e:
                messagebox.showinfo("Prices", f"Couldn't read that file: {e}", parent=self.win)
            self._fill_prices()

    def _save_skill(self):
        try:
            self.store.set_skill(self.skill_name.get().strip(), int(self.skill_mp.get()))
        except ValueError:
            return
        self._fill_prices()

    # ---- Monsters
    def _monsters_tab(self, f):
        left = tk.Frame(f, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(left, text="Monster info (yours to fill in; used for EXP-based kill estimates and the what-if)",
                 bg=self.BG, fg=self.DIM, font=self.fonts["small"], wraplength=380, justify="left").pack(anchor="w")
        cols = (("name", "Monster", 150, "w"), ("exp", "EXP", 60, "e"), ("hp", "HP", 60, "e"), ("def", "Def", 50, "e"),
                ("spr", "Sprites", 60, "e"))
        frame, self.mon_tree = self._tree(left, cols, height=9, select="browse")
        frame.pack(fill="both", expand=True)
        self.mon_tree.bind("<<TreeviewSelect>>", self._monster_selected)
        form = tk.Frame(left, bg=self.BG)
        form.pack(fill="x", pady=4)
        from .vision import MONSTERS
        self.mv = {k: tk.StringVar() for k in ("name", "exp", "hp", "def")}
        self._label(form, "Monster", row=0, column=0)
        ttk.Combobox(form, textvariable=self.mv["name"], values=MONSTERS, width=20).grid(row=0, column=1, sticky="w")
        for i, k in enumerate(("exp", "hp", "def"), 1):
            self._label(form, k.upper(), row=i, column=0)
            tk.Entry(form, textvariable=self.mv[k], width=10).grid(row=i, column=1, sticky="w")
        bar = tk.Frame(left, bg=self.BG)
        bar.pack(fill="x")
        tk.Button(bar, text="Save", command=self._save_monster, **self.btn).pack(side="left", padx=2)
        tk.Button(bar, text="Add sprite from screen...", command=self._add_sprite, **self.btn).pack(side="left", padx=2)
        tk.Button(bar, text="Remove its sprites", command=self._remove_sprites, **self.btn).pack(side="left", padx=2)
        tk.Label(left, text="Sprites: drag a tight box around one monster in the game (a quiet background "
                            "works best). The background colour around it is made see-through. Add a few "
                            "per monster (walking, hit). No game files are used or shipped.",
                 bg=self.BG, fg=self.DIM, font=self.fonts["small"], wraplength=380, justify="left").pack(anchor="w", pady=4)
        right = tk.Frame(f, bg=self.BG)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text="Drop rate (observed)", bg=self.BG, fg=self.FG, font=self.fonts["text"]).pack(anchor="w")
        df = tk.Frame(right, bg=self.BG)
        df.pack(fill="x")
        self.drop_item, self.drop_species = ttk.Combobox(df, width=22), ttk.Combobox(df, width=18)
        self._label(df, "Item", row=0, column=0)
        self.drop_item.grid(row=0, column=1, sticky="w")
        self._label(df, "Monster", row=1, column=0)
        self.drop_species.grid(row=1, column=1, sticky="w")
        tk.Button(df, text="Work it out", command=self._drop_stats, **self.btn).grid(row=2, column=1, sticky="w", pady=4)
        frame, self.drop_text = self._text(right, height=8)
        frame.pack(fill="both", expand=True)
        tk.Label(right, text="Drops come from inventory snapshots or 'picked up' notes; kills from monster "
                             "vision (confirmed + probable). Small samples give wide ranges: that's the point.",
                 bg=self.BG, fg=self.DIM, font=self.fonts["small"], wraplength=360, justify="left").pack(anchor="w")

    def _fill_monsters(self):
        if not hasattr(self, "mon_tree"):
            return
        from .vision import MONSTERS
        mons = self.store.monsters()
        sprites = self.store.sprites()
        t = self.mon_tree
        t.delete(*t.get_children())
        for name in sorted(set(MONSTERS) | set(mons) | set(sprites)):
            d = mons.get(name, {})
            t.insert("", "end", iid=name, values=(name, d.get("exp") or "", d.get("hp") or "", d.get("def") or "",
                                                  len(sprites.get(name, []))))
        items = sorted(p.item for p in self.store.prices())
        self.drop_item.configure(values=items)
        self.drop_species.configure(values=["any"] + sorted(set(MONSTERS) | set(mons)))

    def _monster_selected(self, _e=None):
        sel = self.mon_tree.selection()
        if sel:
            d = self.store.monsters().get(sel[0], {})
            self.mv["name"].set(sel[0])
            for k in ("exp", "hp", "def"):
                self.mv[k].set(str(d.get(k) or ""))

    def _save_monster(self):
        name = self.mv["name"].get().strip()
        if not name:
            return
        try:
            num = lambda k: int(self.mv[k].get()) if self.mv[k].get().strip() else None
            self.store.set_monster(name, num("exp"), num("hp"), num("def"))
        except ValueError:
            messagebox.showinfo("Monster", "EXP, HP and Def must be whole numbers.", parent=self.win)
            return
        self.farm.lookups(force=True)
        self._fill_monsters()

    def _add_sprite(self):
        name = self.mv["name"].get().strip()
        if not name:
            messagebox.showinfo("Sprite", "Pick or type the monster's name first.", parent=self.win)
            return
        from maple_exp import RegionSelector, grab
        import mss

        def done(region):
            self.win.deiconify()
            if not region:
                return
            with mss.mss() as sct:
                shot = grab(sct, region["left"], region["top"], region["width"], region["height"])
            if not shot:
                return
            png = sprite_png(shot[0])
            self.store.add_sprite(name, png)
            self._fill_monsters()
            self.farm.restart_vision()
        self.win.withdraw()
        self.win.after(150, lambda: RegionSelector(self.farm.app.root, done))

    def _remove_sprites(self):
        name = self.mv["name"].get().strip()
        if name and messagebox.askyesno("Sprites", f"Remove all sprites of {name}?", parent=self.win):
            self.store.delete_sprites(name)
            self._fill_monsters()

    def _drop_stats(self):
        item, species = self.drop_item.get().strip(), self.drop_species.get().strip()
        if not item:
            return
        prices, skills, monsters = self.farm.lookups(force=True)
        pairs = []
        for s in self.store.sessions(include_legacy=False):
            ev = self.store.events(s["id"])
            pairs.append((analytics.session_report(s, ev, prices, skills, None, monsters), Ledger(ev, prices)))
        d = analytics.drop_stats(pairs, item, species)
        price = next((p.price for p in prices if p.item == item), None)
        text = d["text"]
        if price and d["rate"]:
            text += f"\n\nAt your price ({price:,.0f}), that's about {d['rate'] * price:,.1f} mesos per kill on average " \
                    f"(range {d['interval'][0] * price:,.1f}–{d['interval'][1] * price:,.1f})."
        if d["kills"] == 0:
            text += "\n\nNo kills of this monster were seen: turn on monster vision (Farm settings) with its " \
                    "sprites, or analyse a recording."
        self._set_text(self.drop_text, text)

    # ---- Recorded video
    def _video_tab(self, f):
        form = tk.Frame(f, bg=self.BG)
        form.pack(fill="x", pady=8)
        self.vid_path, self.vid_roi, self.vid_tags = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.vid_vision = tk.BooleanVar(value=False)
        self.vid_fps = tk.StringVar(value="4")
        self._label(form, "Recording", row=0, column=0)
        tk.Entry(form, textvariable=self.vid_path, width=60).grid(row=0, column=1, sticky="we")
        tk.Button(form, text="Browse...", command=lambda: self.vid_path.set(filedialog.askopenfilename(
            parent=self.win, filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi *.flv *.webm"), ("All", "*.*")]) or
            self.vid_path.get()), **self.btn).grid(row=0, column=2, padx=4)
        tk.Checkbutton(form, text="EXPERIMENTAL: monsters (kills, respawns)", variable=self.vid_vision, bg=self.BG,
                       fg=self.FG, selectcolor=self.FIELD, activebackground=self.BG, font=self.fonts["text"]).grid(
            row=1, column=1, sticky="w")
        self._label(form, "Region x,y,w,h (empty = whole frame)", row=2, column=0)
        tk.Entry(form, textvariable=self.vid_roi, width=24).grid(row=2, column=1, sticky="w")
        self._label(form, "Monster looks per second", row=3, column=0)
        tk.Entry(form, textvariable=self.vid_fps, width=5).grid(row=3, column=1, sticky="w")
        self._label(form, "Tags (key=value, ...)", row=4, column=0)
        tk.Entry(form, textvariable=self.vid_tags, width=40).grid(row=4, column=1, sticky="w")
        self.vid_btn = tk.Button(form, text="Analyse", command=self._analyse, **self.btn)
        self.vid_btn.grid(row=5, column=1, sticky="w", pady=6)
        self.vid_progress = ttk.Progressbar(f, maximum=1.0, length=400)
        self.vid_progress.pack(anchor="w", padx=4)
        self.vid_status = tk.StringVar(value="Reads EXP, HP/MP, mesos (inventory open), party and map from a recording "
                                              "of the game, on this PC. The recording should show the game's bottom bar.")
        tk.Label(f, textvariable=self.vid_status, bg=self.BG, fg=self.DIM, font=self.fonts["small"], wraplength=640,
                 justify="left").pack(anchor="w", padx=4, pady=6)
        self._vid_cancel = False

    def _analyse(self):
        path = self.vid_path.get().strip()
        if not path:
            return
        if self.vid_btn.cget("text") == "Cancel":
            self._vid_cancel = True
            return
        try:
            roi = tuple(int(x) for x in self.vid_roi.get().split(",")) if self.vid_roi.get().strip() else None
            fps = float(self.vid_fps.get() or 4)
            tags = dict(t.strip().split("=", 1) for t in self.vid_tags.get().split(",") if "=" in t)
        except ValueError:
            self.vid_status.set("Region must be four whole numbers: x,y,w,h.")
            return
        analyzer = None
        if self.vid_vision.get():
            from . import vision as v
            sprites = self.store.sprites()
            det = v.SpriteDetector(sprites, threshold=v.VIDEO_THRESHOLD) if sprites else v.ForegroundDetector()
            analyzer = v.VisionAnalyzer(det, damage=v.DamageDetector())
        self._vid_cancel = False
        self.vid_btn.config(text="Cancel")
        q = []

        def work():
            from .video import analyze_video
            try:
                sid, stats = analyze_video(path, self.store, roi=roi, vision=analyzer, vision_fps=fps, tags=tags,
                                           progress=lambda f_, t: q.append(("p", f_, t)),
                                           cancel=lambda: self._vid_cancel)
                q.append(("done", sid, stats))
            except Exception as e:
                q.append(("error", str(e), None))
        threading.Thread(target=work, daemon=True).start()

        def poll():
            while q:
                kind, a, b = q.pop(0)
                if kind == "p":
                    self.vid_progress["value"] = a
                    self.vid_status.set(f"Analysing... {b}")
                elif kind == "done":
                    self.vid_btn.config(text="Analyse")
                    self.vid_status.set(f"Done: session #{a}. {b['seconds'] / 60:.1f} min of video in "
                                        f"{b['wall_seconds']:.0f} s. EXP trusted in {b['exp_ok']}/{b['exp_reads']} "
                                        f"reads, HP/MP {b['gauge_reads']}, mesos {b['mesos_reads']}.")
                    self.refresh()
                    return
                elif kind == "error":
                    self.vid_btn.config(text="Analyse")
                    self.vid_status.set(f"Couldn't analyse it: {a}")
                    return
            if self.alive():
                self.win.after(300, poll)
        poll()

    # ---- What-if
    def _whatif_tab(self, f):
        form = tk.Frame(f, bg=self.BG)
        form.pack(fill="x", pady=8)
        defaults = (("luk", "LUK", "175"), ("dex", "DEX", "36"), ("str", "STR", "4"),
                    ("att", "Weapon + stars ATT now", "41"), ("new", "ATT after the upgrade", ""),
                    ("hp", "Monster HP (yours to enter)", ""), ("def", "Monster defence", "0"),
                    ("mastery", "Claw mastery (0.1 = none, 0.6 = max)", "0.1"), ("l7mp", "Lucky Seven MP", "16"),
                    ("l7pct", "Lucky Seven damage %", "150"))
        self.wi = {}
        for i, (k, label, value) in enumerate(defaults):
            self._label(form, label, row=i, column=0)
            v = tk.StringVar(value=value)
            tk.Entry(form, textvariable=v, width=10).grid(row=i, column=1, sticky="w", pady=1)
            self.wi[k] = v
        tk.Button(form, text="Work it out", command=self._whatif, **self.btn).grid(row=len(defaults), column=1,
                                                                                     sticky="w", pady=6)
        frame, self.wi_text = self._text(f, height=10)
        frame.pack(fill="both", expand=True)

    def _whatif(self):
        from .combat import upgrade_what_if
        try:
            g = lambda k: float(self.wi[k].get())
            r = upgrade_what_if(g("luk"), g("dex"), g("str"), g("att"), g("new"), g("hp"), g("def"), g("mastery"),
                                g("l7pct"), g("l7mp"))
        except ValueError:
            self._set_text(self.wi_text, "Fill in every box with a number (the monster's HP and the new ATT too).")
            return
        lines = []
        for k in ("now", "upgrade"):
            d = r[k]
            dist = ", ".join(f"{n} hit{'s' * (n > 1)}: {p:.0%}" for n, p in list(d["basic_dist"].items())[:5])
            lines.append(f"{k.upper()} (ATT {d['att']:.0f}):\n  basic attack {d['basic_range'][0]:.0f}–"
                         f"{d['basic_range'][1]:.0f}, {d['basic_attacks']:.2f} attacks per kill ({dist})\n"
                         f"  Lucky Seven {d['l7_range'][0]:.0f}–{d['l7_range'][1]:.0f} per star, "
                         f"{d['l7_casts']:.2f} casts per kill, one-shot {d['l7_one_shot']:.0%}, "
                         f"{d['l7_mp_per_kill']:.1f} MP per kill")
        lines.append(f"\nBasic attacks saved per kill: {r['basic_attacks_saved']:.0%}.  "
                     f"Lucky Seven MP saved per kill: {r['l7_mp_saved']:.0%}.")
        lines.append("\n" + r["note"])
        self._set_text(self.wi_text, "\n".join(lines))


def sprite_png(bgr, tolerance=28):
    """A PNG (with transparency) of a monster cropped from the screen: the colour around the
    crop's border is taken as background and made see-through."""
    import cv2
    import numpy as np
    border = np.concatenate([bgr[0], bgr[-1], bgr[:, 0], bgr[:, -1]]).astype(np.int16)
    bg = np.median(border, axis=0)
    diff = np.abs(bgr.astype(np.int16) - bg).max(axis=2)
    alpha = (diff > tolerance).astype(np.uint8) * 255
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    if alpha.mean() < 10:                       # nothing stood out: keep the whole crop
        alpha[:] = 255
    ok, png = cv2.imencode(".png", np.dstack([bgr, alpha]))
    return png.tobytes()


__all__ = ["AnalyticsWindow", "sprite_png", "KINDS", "TXN_TYPES"]
