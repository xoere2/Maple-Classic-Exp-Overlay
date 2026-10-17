"""Command line: py -3 -m farmkit <command> ...

  analyze-video FILE [--roi X,Y,W,H] [--vision] [--sprites DIR] [--tag key=value] [--fps 4]
      Analyses a recording into a new session (everything stays on this PC).
      --sprites DIR: one sub-folder per monster with PNG crops (transparent background
      preferred), e.g. sprites/Jr. Necki/1.png. Without it, --vision uses the experimental
      foreground detector (species unknown).
  sessions                     Lists sessions.
  report ID                    Prints one session's report, every metric with its kind.
  compare ID ID [ID...]        Side-by-side comparison.
  rank [--by exp_hr|realized_mesos_hr|total_profit_hr|exp_per_meso|exp_hr_profitable]
       [--basis operating|realized|estimated]
  export --csv FILE | --json FILE
  import-legacy                Imports the upstream exp_log.json lines (read-only).
  what-if --new-att N --hp MONSTER_HP [--luk 175 --dex 36 --str 4 --att 41 --def 0 --mastery 0.1]
      Basic attack vs Lucky Seven, now vs after a weapon change (THEORY: pre-BB formulas).
      ATT is claw + stars (26 + 15 = 41 for the test character). The monster's HP and
      defence must come from you: none are built in.

--db PATH picks the database (default: farm.db in the overlay's data folder).
"""
import argparse
import os
import sys
from pathlib import Path


def data_dir():
    return Path(os.environ.get("MAPLE_EXP_DATA") or Path(os.environ.get("APPDATA") or Path.home()) / "MapleEXP")


def load_sprites(folder):
    import cv2
    out = {}
    for sub in sorted(Path(folder).iterdir()):
        if sub.is_dir():
            imgs = [cv2.imread(str(f), cv2.IMREAD_UNCHANGED) for f in sorted(sub.glob("*.png"))]
            imgs = [i for i in imgs if i is not None]
            if imgs:
                out[sub.name] = imgs
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="farmkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze-video")
    a.add_argument("file")
    a.add_argument("--roi", default=None)
    a.add_argument("--vision", action="store_true")
    a.add_argument("--sprites", default=None)
    a.add_argument("--tag", action="append", default=[])
    a.add_argument("--fps", type=float, default=4.0)
    a.add_argument("--max-seconds", type=float, default=None)
    sub.add_parser("sessions")
    r = sub.add_parser("report")
    r.add_argument("id", type=int)
    c = sub.add_parser("compare")
    c.add_argument("ids", type=int, nargs="+")
    k = sub.add_parser("rank")
    k.add_argument("--by", default="exp_hr")
    k.add_argument("--basis", default="operating")
    e = sub.add_parser("export")
    e.add_argument("--csv")
    e.add_argument("--json")
    sub.add_parser("import-legacy")
    w = sub.add_parser("what-if")
    for name, default in (("luk", 175), ("dex", 36), ("str", 4), ("att", 41), ("new-att", None), ("hp", None),
                          ("def", 0), ("mastery", 0.1), ("l7-mp", 16), ("l7-pct", 150)):
        w.add_argument(f"--{name}", type=float, default=default, required=default is None)
    args = ap.parse_args(argv)

    from . import analytics, export
    from .metrics import Metric, fmt
    from .store import Store
    store = Store(args.db or data_dir() / "farm.db")

    if args.cmd == "analyze-video":
        from . import vision as v
        from .video import analyze_video
        roi = tuple(int(x) for x in args.roi.split(",")) if args.roi else None
        analyzer = None
        if args.vision or args.sprites:
            sprites = load_sprites(args.sprites) if args.sprites else store.sprites()
            det = v.SpriteDetector(sprites, threshold=v.VIDEO_THRESHOLD) if sprites else v.ForegroundDetector()
            analyzer = v.VisionAnalyzer(det, damage=v.DamageDetector())
            print(f"Monster vision (EXPERIMENTAL): {', '.join(sprites) if sprites else 'foreground detector'}")
        tags = dict(t.split("=", 1) for t in args.tag if "=" in t)

        def progress(f, text):
            print(f"\r  {f:5.1%}  {text}   ", end="", flush=True)
        sid, stats = analyze_video(args.file, store, roi=roi, vision=analyzer, vision_fps=args.fps, tags=tags,
                                   progress=progress, max_seconds=args.max_seconds)
        print(f"\nSession {sid}: {stats['seconds'] / 60:.1f} min of video in {stats['wall_seconds']:.0f} s; "
              f"EXP trusted in {stats['exp_ok']}/{stats['exp_reads']} reads, HP/MP {stats['gauge_reads']}, "
              f"mesos {stats['mesos_reads']}")
        args.cmd, args.id = "report", sid

    if args.cmd == "sessions":
        for s in store.sessions():
            from datetime import datetime
            when = datetime.fromtimestamp(s["started"]).strftime("%Y-%m-%d %H:%M")
            print(f"{s['id']:>5}  {when}  {s['source']:<6}  {s.get('map') or '?':<28}  Lv {s.get('level_end') or '?':<3}"
                  f"  {s.get('title') or ''}  {s.get('tags') or ''}")
    elif args.cmd == "report":
        s = store.session(args.id)
        if not s:
            sys.exit(f"No session {args.id}")
        rep = analytics.session_report(s, store.events(args.id), store.prices(), store.skills(),
                                       monsters=store.monsters())
        print(f"Session {args.id}: {rep.info.get('map')}, Lv {rep.info.get('level')}, party {rep.info.get('party')}, "
              f"source {rep.info.get('source')}, tags {rep.info.get('tags')}")
        for key, m in rep.items():
            if isinstance(m, Metric) and not isinstance(m.value, dict):
                print(f"  {key:<26} {fmt(m, 2):>16}  [{m.kind}{', ' + m.confidence if m.confidence else ''}]  {m.note}")
        print("  (~ estimated, ≈ inferred from wallet + notes, ✎ entered by hand, — not available)")
    elif args.cmd == "compare":
        reps = [r for _, r in export.reports_for(store, set(args.ids))]
        for label, cells in analytics.compare(reps):
            print(f"{label:<34}" + "".join(f"{c:>20}" for c in cells))
    elif args.cmd == "rank":
        reps = [r for _, r in export.reports_for(store)]
        ranked, left = analytics.rank(reps, args.by, args.basis)
        print(analytics.RANKINGS[args.by][0])
        for i, (r, value, note) in enumerate(ranked, 1):
            print(f"{i:>3}. #{r.info['id']:<4} {r.info.get('map') or '?':<28} {value:>14,.0f}  {note}")
        for r, why in left:
            print(f"   -  #{r.info['id']:<4} {r.info.get('map') or '?':<28} left out: {why}")
    elif args.cmd == "export":
        if args.csv:
            print(f"{export.to_csv(store, args.csv)} sessions -> {args.csv}")
        if args.json:
            print(f"{export.to_json(store, args.json)} sessions -> {args.json}")
    elif args.cmd == "import-legacy":
        print(f"{store.import_legacy(data_dir() / 'exp_log.json')} upstream log lines imported/updated")
    elif args.cmd == "what-if":
        from .combat import upgrade_what_if
        r = upgrade_what_if(args.luk, args.dex, getattr(args, "str"), args.att, args.new_att, args.hp, getattr(args, "def"),
                            args.mastery, args.l7_pct, args.l7_mp)
        for k in ("now", "upgrade"):
            d = r[k]
            print(f"{k:>8} (ATT {d['att']:.0f}): basic {d['basic_range'][0]:.0f}-{d['basic_range'][1]:.0f} dmg, "
                  f"{d['basic_attacks']:.2f} attacks/kill, one-shot {d['basic_one_shot']:.0%}; "
                  f"Lucky Seven {d['l7_casts']:.2f} casts/kill, one-shot {d['l7_one_shot']:.0%}, "
                  f"{d['l7_mp_per_kill']:.1f} MP/kill")
        print(f"Basic attacks saved per kill: {r['basic_attacks_saved']:.0%}; Lucky Seven MP saved: {r['l7_mp_saved']:.0%}")
        print(r["note"])


if __name__ == "__main__":
    main()
