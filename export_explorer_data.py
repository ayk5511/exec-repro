#!/usr/bin/env python3
"""Export the per-artifact ladder results as a single JSON for the public explorer.

The paper reports a curve. A curve is a claim about a population, and a reader
who wants to check it has to take the aggregate on trust or re-run 459 artifacts
themselves. This file is the middle option: every record, with the rung it
reached and the reason it stopped, in a form a browser can search.

Publishing per-artifact outcomes is the norm in artifact evaluation and is what
makes a study like this checkable rather than quotable. Two constraints follow
from that, and both are enforced here rather than left to the page:

1.  No captured stdout. The run output of someone else's code can contain
    absolutely anything, including credentials that were committed by mistake.
    It stays in the repository's JSONL for a reader who wants it, and out of a
    page designed to be easy to browse.

2.  Every record carries the harness version and the date it was scored, because
    a failure recorded today is not a permanent property of the artifact. The
    paper's own §sec:harness_defects exists because four of our failures turned
    out to be ours, not the literature's.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIMARY = ROOT / "results" / "ladder_v5.jsonl"
CONSERVATIVE = ROOT / "results" / "ladder_v5_conservative.jsonl"
CORPUS = ROOT / "corpus" / "corpus.jsonl"
WORKSHEET = ROOT / "results" / "rung10_worksheet.json"
OUT = ROOT / "explorer" / "data.json"

#: What each rung means in one line, for a reader who has not read the paper.
RUNG_LABELS = {
    2: ("URL present", "the paper advertises a code repository"),
    3: ("URL resolves", "the address still answers"),
    4: ("Clones", "the repository can be retrieved"),
    5: ("Declares dependencies", "it ships a dependency specification"),
    6: ("Environment builds", "those dependencies still install"),
    7: ("Entry point found", "something runnable is identifiable"),
    8: ("Runs", "it executes to completion"),
    9: ("Produces output", "it emits artifacts or numbers"),
    10: ("Matches published", "an emitted number matches the paper"),
}

#: Plain-English gloss for each failure reason. The closed vocabulary is precise
#: but opaque; a visitor looking up their own paper deserves a sentence.
REASON_GLOSS = {
    "http_404": "The repository URL returns 404. It was moved, renamed, or deleted.",
    "http_403_private": "The repository exists but is private or access-restricted.",
    "not_a_repository": "The URL resolves but is not a clonable repository.",
    "clone_timeout": "The clone did not finish inside the ten-minute budget.",
    "clone_auth_required": "Cloning requires credentials we do not have.",
    "no_dependency_spec": (
        "No requirements.txt, environment.yml, pyproject.toml, setup.py or "
        "Pipfile was found. Nothing tells a reader what to install."
    ),
    "package_not_found": "A pinned dependency is no longer on the package index.",
    "dependency_resolution_conflict": (
        "Installation failed for a reason the classifier could not resolve more "
        "finely; this bucket includes genuine version conflicts and build-backend "
        "errors."
    ),
    "build_from_source_failed": "A package with no current wheel failed to compile.",
    "non_python_toolchain_required": "The build needs a toolchain outside Python.",
    "python_version_unsupported": "The pins do not admit the Python version used.",
    "no_entrypoint_identifiable": (
        "No conventionally named script, and no runnable command in the README, "
        "could be identified. There may well be one a human would find."
    ),
    "runtime_exception": "The entry point started and raised.",
    "missing_data_file": (
        "An input file was absent, or the run tried to fetch data over a network "
        "that is denied during execution by design."
    ),
    "execution_timeout": "Execution exceeded the thirty-minute budget.",
    "requires_gpu": "The run requires a GPU that the harness does not allocate.",
    "requires_api_key": "The run requires a credential we do not hold.",
    "ran_but_no_artefact": "It exited cleanly but emitted no file and no number.",
    "install_timeout": "Dependency installation exceeded its budget.",
}


def load_jsonl(path: Path) -> dict[str, dict]:
    return {
        json.loads(line)["key"]: json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    }


def git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def cohort(key: str) -> int | None:
    m = re.match(r"^(\d{2})(\d{2})\.", key)
    return 2000 + int(m.group(1)) if m else None


def main() -> int:
    primary = load_jsonl(PRIMARY)
    conservative = load_jsonl(CONSERVATIVE)
    corpus = load_jsonl(CORPUS)
    verdicts = {
        row["key"]: row
        for row in json.loads(WORKSHEET.read_text())
        if (row.get("verdict") or "").strip()
    }

    records = []
    for key, rec in sorted(primary.items()):
        meta = corpus.get(key, {})
        cons = conservative.get(key, {})
        adjudication = verdicts.get(key)
        records.append({
            "key": key,
            "title": meta.get("title"),
            "year": cohort(key),
            "categories": meta.get("categories"),
            "url": rec.get("code_url"),
            # Where the URL was found matters: four-fifths of the advertised
            # repositories in this corpus appear only in the full text, so a
            # disclosure rate computed from abstracts is a severe underestimate.
            "url_source": meta.get("url_source"),
            "rung": rec.get("highest_rung"),
            "rung_name": rec.get("highest_rung_name"),
            "blocked_at": rec.get("blocked_at"),
            "reason": rec.get("block_reason"),
            "entrypoint_source": rec.get("entrypoint_source"),
            "rung_conservative": cons.get("highest_rung"),
            "seconds": rec.get("total_duration_s"),
            "repaired_from": rec.get("url_repaired_from"),
            "adjudication": (
                {
                    "verdict": adjudication["verdict"],
                    "justification": adjudication.get("justification"),
                }
                if adjudication else None
            ),
        })

    payload = {
        "meta": {
            "n": len(records),
            "scored_on": "2026-08-25",
            "harness_commit": git_rev(),
            "paper": "Does It Actually Run? An Execution-Based Reproducibility "
                     "Study of Published Machine-Learning Finance Research",
            "repo": "https://github.com/ayk5511/exec-repro",
            "caveat": (
                "A record here says what happened when this harness cloned the "
                "advertised address on a clean machine, installed the declared "
                "dependencies, and ran the documented entry point, with no data "
                "supplied and no author contacted. It does not say the work is "
                "wrong, and it is not a replication result. Several failures are "
                "ones an author could resolve in a message. Four defects in this "
                "harness were found and corrected before these figures were "
                "produced, and there may be more."
            ),
        },
        "rungs": {str(k): {"name": v[0], "gloss": v[1]} for k, v in RUNG_LABELS.items()},
        "reasons": REASON_GLOSS,
        "records": records,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, {len(records)} records)")

    unglossed = {
        r["reason"] for r in records
        if r["reason"] and r["reason"] not in REASON_GLOSS
    }
    if unglossed:
        raise SystemExit(f"reasons missing a plain-English gloss: {sorted(unglossed)}")
    print("every failure reason carries a gloss")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
