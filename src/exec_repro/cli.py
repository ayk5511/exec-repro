"""Command line interface.

    exec-repro discover  --out corpus/corpus.jsonl --per-category 300
    exec-repro run       --corpus corpus/corpus.jsonl --results results/ladder.jsonl
    exec-repro summarize --results results/ladder.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .campaign import load_corpus, run_campaign, summarize
from .discover import QFIN_CATEGORIES, build_corpus, enrich_from_fulltext
from .isolate import select_backend
from .rung10 import build_worksheet
from .stages import Rung


def _cmd_discover(args: argparse.Namespace) -> int:
    build_corpus(
        Path(args.out),
        categories=tuple(args.categories) if args.categories else QFIN_CATEGORIES,
        per_category=args.per_category,
        seed_path=Path(args.seed) if args.seed else None,
    )
    return 0


def _cmd_enrich(args: argparse.Namespace) -> int:
    enrich_from_fulltext(Path(args.corpus), limit=args.limit, pause_s=args.pause)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    corpus = load_corpus(Path(args.corpus))
    if args.only_with_url:
        corpus = [e for e in corpus if e.code_url]
    if args.limit:
        corpus = corpus[: args.limit]
    backend = select_backend(prefer_docker=not args.no_docker)
    print(f"backend={backend.name}", flush=True)
    if backend.name != "docker":
        print(
            "WARNING: running without container isolation. Third-party code will "
            "execute under your user account with filesystem writes confined to "
            "the repo. Install colima/docker for stronger isolation.",
            file=sys.stderr,
            flush=True,
        )
    for _ in run_campaign(
        corpus,
        workdir=Path(args.workdir),
        results_path=Path(args.results),
        workers=args.workers,
        install_timeout=args.install_timeout,
        exec_timeout=args.exec_timeout,
        prefer_docker=not args.no_docker,
        use_readme=not args.no_readme,
        resume=not args.no_resume,
    ):
        pass
    return 0


def _cmd_worksheet(args: argparse.Namespace) -> int:
    cands = build_worksheet(
        Path(args.results), Path(args.corpus),
        Path(args.out), Path(args.out).with_suffix(".csv"),
    )
    print(f"{len(cands)} candidate(s) reached PRODUCES_OUTPUT and need manual scoring")
    print(f"  JSON: {args.out}")
    print(f"  CSV : {Path(args.out).with_suffix('.csv')}")
    return 0


def _cmd_summarize(args: argparse.Namespace) -> int:
    summary = summarize(Path(args.results))
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    n = summary["n_analysed"]
    print(f"corpus analysed: {n}\n")
    print(f"{'rung':22} {'n':>5}  {'%':>6}")
    print("-" * 36)
    for name, d in summary["attrition_curve"].items():
        if d["rung"] >= int(Rung.URL_PRESENT):
            print(f"{name:22} {d['n_clearing']:5}  {d['pct_clearing']:6.1f}")
    print("\nfailure mix:")
    for rung, reasons in summary["failure_mix"].items():
        print(f"  {rung}")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:38} {count:4}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="exec-repro", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="harvest a corpus from arXiv")
    d.add_argument("--out", required=True)
    d.add_argument("--seed", default=None, help="existing corpus JSONL to merge")
    d.add_argument("--per-category", type=int, default=300)
    d.add_argument("--categories", nargs="*", default=None)
    d.set_defaults(func=_cmd_discover)

    e = sub.add_parser("enrich", help="find code URLs in full text for entries lacking one")
    e.add_argument("--corpus", required=True)
    e.add_argument("--limit", type=int, default=None)
    e.add_argument("--pause", type=float, default=1.5)
    e.set_defaults(func=_cmd_enrich)

    r = sub.add_parser("run", help="walk the corpus up the ladder")
    r.add_argument("--corpus", required=True)
    r.add_argument("--results", required=True)
    r.add_argument("--workdir", default="/tmp/exec_repro")
    r.add_argument("--workers", type=int, default=None)
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--install-timeout", type=int, default=900)
    r.add_argument("--exec-timeout", type=int, default=1800)
    r.add_argument("--only-with-url", action="store_true")
    r.add_argument("--no-docker", action="store_true")
    r.add_argument("--no-readme", action="store_true",
                   help="conservative entry points only (sensitivity check)")
    r.add_argument("--no-resume", action="store_true")
    r.set_defaults(func=_cmd_run)

    w = sub.add_parser("worksheet", help="build the manual rung-10 scoring worksheet")
    w.add_argument("--results", required=True)
    w.add_argument("--corpus", required=True)
    w.add_argument("--out", required=True)
    w.set_defaults(func=_cmd_worksheet)

    s = sub.add_parser("summarize", help="aggregate results into the paper's tables")
    s.add_argument("--results", required=True)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_summarize)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
