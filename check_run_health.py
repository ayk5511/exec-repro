"""Live integrity checks on a campaign, runnable against partial results.

Every false result this project has produced looked plausible in aggregate and
was only detectable in the shape of the underlying records. These checks encode
those signatures so a bad run is caught while it is running rather than after it
is written up.

Exit status is non-zero if any check fails, so this can gate a campaign.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

RESULTS = Path(sys.argv[1] if len(sys.argv) > 1 else "results/ladder_v5.jsonl")


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # A partially flushed final line while the campaign is mid-write.
            continue
    return out


def main() -> int:
    recs = load(RESULTS)
    if not recs:
        print(f"no records yet at {RESULTS}")
        return 0

    problems: list[str] = []
    print(f"records: {len(recs)}\n")

    # 1. Ephemeral-container signature. Dependencies installed at ENV_BUILDS
    #    vanishing before RUNS produced a uniform sub-second ModuleNotFoundError
    #    across the whole corpus and read as "0% of papers execute".
    modnotfound = 0
    run_durations: list[float] = []
    for r in recs:
        for s in r.get("stages", []):
            if s["rung_name"] != "RUNS" or s["cleared"]:
                continue
            run_durations.append(float(s.get("duration_s", 0)))
            if "ModuleNotFoundError" in (s.get("detail") or ""):
                modnotfound += 1
    if run_durations:
        fast = sum(1 for d in run_durations if d < 0.5)
        print(f"RUNS failures: {len(run_durations)}  median={statistics.median(run_durations):.1f}s "
              f"max={max(run_durations):.1f}s  under-0.5s={fast}")
        print(f"  ModuleNotFoundError: {modnotfound}")
        if run_durations and modnotfound / len(run_durations) > 0.5:
            problems.append(
                f"{modnotfound}/{len(run_durations)} run failures are ModuleNotFoundError: "
                "the installed environment is probably not reaching execution"
            )
        if len(run_durations) >= 8 and fast / len(run_durations) > 0.8:
            problems.append(
                "over 80% of run failures are sub-second, which is the signature of a "
                "systematic import or interpreter failure rather than real defects"
            )

    # 2. Harness-attributable failures must stay at zero; they mean our own bug
    #    is being recorded as evidence about the papers.
    harness = [r for r in recs if r.get("block_reason") == "spec_unreadable_by_backend"]
    incomplete = [r for r in recs if r.get("harness_incomplete")]
    print(f"\nharness-attributable failures: {len(harness)}")
    print(f"harness-incomplete records   : {len(incomplete)}")
    if harness:
        problems.append(f"{len(harness)} spec_unreadable_by_backend (harness path bug)")
    if len(incomplete) > max(3, 0.05 * len(recs)):
        problems.append(f"{len(incomplete)} records the harness could not finish")

    # 3. Empty-mount signature: the container seeing an empty /work makes every
    #    repository look as though it shipped no files.
    dist = Counter(r["highest_rung_name"] for r in recs)
    cloned = sum(1 for r in recs if r["highest_rung"] >= 4)
    nodeps = sum(1 for r in recs if r.get("block_reason") == "no_dependency_spec")
    if cloned >= 20 and nodeps / cloned > 0.95:
        problems.append(
            f"{nodeps}/{cloned} cloned repos report no dependency spec: the container "
            "mount may be empty"
        )

    # 4. Monotonicity of the cumulative curve.
    print("\nhighest-rung distribution:")
    for name, count in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {name:20} {count:4}")
    order = ["URL_PRESENT", "URL_RESOLVES", "CLONED", "DEPS_DECLARED", "ENV_BUILDS",
             "ENTRYPOINT_FOUND", "RUNS", "PRODUCES_OUTPUT"]
    cum = [
        sum(1 for r in recs if r["highest_rung"] >= i)
        for i in range(2, 2 + len(order))
    ]
    if cum != sorted(cum, reverse=True):
        problems.append(f"attrition curve is not monotone: {cum}")
    print("\ncumulative clearing:", dict(zip(order, cum, strict=False)))

    # 5. Reason vocabulary must stay closed.
    sys.path.insert(0, "src")
    from exec_repro.stages import ALL_REASONS

    bad = {r.get("block_reason") for r in recs
           if r.get("block_reason") and r["block_reason"] not in ALL_REASONS}
    if bad:
        problems.append(f"block reasons outside the closed vocabulary: {bad}")

    # 6. Duplicate keys would double-count papers.
    keys = [r["key"] for r in recs]
    if len(keys) != len(set(keys)):
        dupes = [k for k, c in Counter(keys).items() if c > 1]
        problems.append(f"duplicate keys: {dupes[:5]}")

    print("\n" + "=" * 60)
    if problems:
        print("PROBLEMS DETECTED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("HEALTH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
