"""Audit script for Paper 7 (execution-based reproducibility study).

Re-derives every numerical claim in the manuscript from the campaign results on
disk, so that no number reaches the PDF unless the artefacts still support it.
Must print ``ALL CHECKS PASSED`` (exit 0) before the paper is considered
ship-ready.

The checks that matter most here are the ones that would silently corrupt the
headline finding:

  - the attrition curve must be monotone (a non-monotone curve means the ladder
    was scored inconsistently);
  - harness-attributable failures must be reported separately from
    paper-attributable ones, never pooled;
  - every failure reason present in the results must belong to the closed
    vocabulary the paper documents.

Usage:
    python3 paper/audit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED = REPO_ROOT.parent.parent / "shared" / "lib"
if SHARED.exists():
    sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(REPO_ROOT / "src"))

from exec_repro.stages import ALL_REASONS, Rung  # noqa: E402

RESULTS = REPO_ROOT / "results" / "ladder_v5.jsonl"
SENSITIVITY = REPO_ROOT / "results" / "ladder_v5_conservative.jsonl"
CORPUS = REPO_ROOT / "corpus" / "corpus.jsonl"

#: Reasons that indicate a harness defect rather than a property of the paper.
#: These are excluded from every claim about the literature.
HARNESS_REASONS = {"spec_unreadable_by_backend"}


class Audit:
    def __init__(self) -> None:
        self.failures = 0

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===")

    def ok(self, msg: str) -> None:
        print(f"  ok:   {msg}")

    def fail(self, msg: str) -> None:
        print(f"  FAIL: {msg}")
        self.failures += 1

    def check(self, condition: bool, msg: str) -> None:
        self.ok(msg) if condition else self.fail(msg)

    def finish(self) -> int:
        print("\n=== FINAL ===\n")
        if self.failures:
            print(f"FAILED ({self.failures} checks)")
            return 1
        print("ALL CHECKS PASSED")
        return 0


def main() -> int:
    a = Audit()

    a.section("ARTEFACTS PRESENT")
    if not RESULTS.exists():
        a.fail(f"results not found at {RESULTS}; run the campaign first")
        return a.finish()
    a.ok(f"{RESULTS.name} exists ({RESULTS.stat().st_size} bytes)")
    a.check(CORPUS.exists(), f"{CORPUS.name} exists")

    records = [json.loads(line) for line in RESULTS.open() if line.strip()]
    a.check(len(records) > 0, f"results contain {len(records)} records")

    a.section("RESULT INTEGRITY")
    keys = [r["key"] for r in records]
    a.check(len(keys) == len(set(keys)), f"no duplicate keys ({len(set(keys))} unique)")

    bad_reasons = {
        r.get("block_reason") for r in records
        if r.get("block_reason") and r["block_reason"] not in ALL_REASONS
    }
    a.check(not bad_reasons, f"all block reasons are in the closed vocabulary {bad_reasons or ''}")

    bad_rungs = [r for r in records if not 0 <= r["highest_rung"] <= int(Rung.MATCHES_PUBLISHED)]
    a.check(not bad_rungs, "all highest_rung values are within the ladder")

    a.section("HARNESS VS PAPER ATTRIBUTION")
    harness_hit = [r for r in records if r.get("block_reason") in HARNESS_REASONS]
    incomplete = [r for r in records if r.get("harness_incomplete")]
    a.ok(f"harness-attributable failures: {len(harness_hit)} (excluded from literature claims)")
    a.ok(f"harness-incomplete records:    {len(incomplete)} (excluded from analysis)")
    analysable = [
        r for r in records
        if r.get("block_reason") not in HARNESS_REASONS and not r.get("harness_incomplete")
    ]
    a.check(len(analysable) > 0, f"analysable records: {len(analysable)}")

    a.section("ATTRITION CURVE IS MONOTONE")
    counts = {
        rung: sum(1 for r in analysable if r["highest_rung"] >= int(rung))
        for rung in Rung
    }
    ordered = [counts[rung] for rung in Rung]
    a.check(
        ordered == sorted(ordered, reverse=True),
        "clearing counts never increase as the ladder rises",
    )
    n = len(analysable)
    for rung in Rung:
        if int(rung) >= int(Rung.URL_PRESENT):
            pct = 100.0 * counts[rung] / n if n else 0.0
            print(f"        {rung.name:20} {counts[rung]:4}/{n}  {pct:5.1f}%")

    a.section("SENSITIVITY RUN AGREES ON THE MECHANICAL RUNGS")
    if SENSITIVITY.exists():
        alt = [json.loads(line) for line in SENSITIVITY.open() if line.strip()]
        alt_counts = {
            rung: sum(1 for r in alt if r["highest_rung"] >= int(rung)) for rung in Rung
        }
        # Rungs 2-6 are decided before any entry-point heuristic runs, so the two
        # configurations must agree exactly. A discrepancy means the run was not
        # deterministic and no number below is safe to quote.
        for rung in Rung:
            if int(Rung.URL_PRESENT) <= int(rung) <= int(Rung.ENV_BUILDS):
                a.check(
                    counts[rung] == alt_counts[rung],
                    f"{rung.name}: main={counts[rung]} sensitivity={alt_counts[rung]}",
                )
        a.ok(
            f"entry-point heuristic moves ENTRYPOINT_FOUND "
            f"{alt_counts[Rung.ENTRYPOINT_FOUND]} -> {counts[Rung.ENTRYPOINT_FOUND]} "
            f"and RUNS {alt_counts[Rung.RUNS]} -> {counts[Rung.RUNS]}"
        )
    else:
        a.fail("sensitivity run missing; the entry-point heuristic is unaudited")

    a.section("MANUSCRIPT NUMBERS MATCH THE DATA")
    # Every count the results section prints is re-derived here and compared
    # against the LaTeX source. A transcription slip in either direction fails
    # the build rather than reaching the PDF.
    from collections import Counter

    mix: dict[str, Counter] = {}
    for r in analysable:
        if r.get("blocked_at"):
            mix.setdefault(r["blocked_at"], Counter())[r["block_reason"]] += 1

    tex = ""
    for name in ("04_results", "05_discussion"):
        path = REPO_ROOT / "paper" / "sections" / f"{name}.tex"
        if path.exists():
            tex += path.read_text()

    def cited(fragment: str, label: str) -> None:
        a.check(fragment in tex, f"manuscript states {label} ({fragment.strip()!r})")

    # Corpus provenance.
    sources = Counter()
    if CORPUS.exists():
        for line in CORPUS.open():
            if line.strip():
                d = json.loads(line)
                if d.get("code_url"):
                    sources[d.get("url_source")] += 1
        a.ok(f"code URLs by source: {dict(sources)}")
        cited(f"found {sources['abstract']}", "abstract-derived URL count")
        cited(f"a further {sources['fulltext']}", "full-text-derived URL count")

    # Every failure reason and count that appears in the results table.
    missing = []
    for rung_name, counter in sorted(mix.items()):
        for reason, count in counter.items():
            escaped = reason.replace("_", r"\_")
            if f"\\texttt{{{escaped}}}" not in tex:
                missing.append(f"{rung_name}/{reason} absent from the table")
            elif f"& {count} \\\\" not in tex and f"& {count}  \\\\" not in tex:
                missing.append(f"{rung_name}/{reason}: count {count} not found")
    a.check(not missing, f"failure-mix table is complete and correct {missing or ''}")

    total_blocked = sum(sum(c.values()) for c in mix.values())
    a.check(
        total_blocked == len(analysable) - counts[Rung.PRODUCES_OUTPUT],
        f"blocked ({total_blocked}) + cleared ({counts[Rung.PRODUCES_OUTPUT]}) "
        f"= corpus ({len(analysable)})",
    )

    a.section("RUNG 10 IS NOT MACHINE-ASSERTED")
    auto_matched = [
        r for r in analysable if r["highest_rung"] >= int(Rung.MATCHES_PUBLISHED)
    ]
    a.check(
        not auto_matched,
        "no record claims MATCHES_PUBLISHED from the harness "
        "(that rung is manual by design)",
    )

    return a.finish()


if __name__ == "__main__":
    raise SystemExit(main())
