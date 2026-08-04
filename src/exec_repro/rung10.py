"""Rung 10: does an emitted number match a headline number in the paper?

This is the only rung a machine does not decide. Judging whether
`test RMSE 0.0412` in a program's stdout corresponds to `RMSE of 0.041` in an
abstract requires knowing what the paper claimed, which quantity it claimed it
for, and whether the two are the same thing at all. The harness therefore stops
at rung 9 and this module prepares the evidence a human needs to adjudicate.

The design goal is to make the human's job narrow and auditable: for each
candidate the worksheet shows the paper's numeric claims with their surrounding
words, the numbers the artefact actually printed, and nothing else. The scorer
records a verdict and a short justification. Verdicts are read back and attached
to the ladder, never inferred.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .stages import LadderResult, Rung, StageOutcome

#: A number with enough context on either side to tell what it refers to.
#: The trailing context is a lookahead so it is not consumed: otherwise the words
#: introducing the *next* claim would be swallowed by this match, and a sentence
#: like "RMSE 0.50 and Sharpe 0.50" would yield only the first number.
_CLAIM_RE = re.compile(
    r"(?P<before>[A-Za-z][A-Za-z\s\-/%()\.,]{0,60}?)"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|bps|basis points)?"
    r"(?=(?P<after>[A-Za-z\s\-/%()\.,]{0,40}))",
)

#: Values that carry no evidential weight: years, small counts, section numbers.
def _is_substantive(value: str) -> bool:
    try:
        v = float(value)
    except ValueError:
        return False
    if v.is_integer():
        iv = int(v)
        if 1900 <= iv <= 2100:      # a year
            return False
        if abs(iv) < 3:             # counts, indices
            return False
    return True


@dataclass(slots=True)
class NumericClaim:
    value: str
    context: str

    def to_dict(self) -> dict:
        return {"value": self.value, "context": self.context}


def extract_numeric_claims(text: str, *, limit: int = 25) -> list[NumericClaim]:
    """Numeric claims with surrounding words, in document order."""
    out: list[NumericClaim] = []
    seen: set[tuple[str, str]] = set()
    for m in _CLAIM_RE.finditer(text or ""):
        value = m.group("value")
        if not _is_substantive(value):
            continue
        # Rebuild the context from the named groups: group(0) stops at the
        # lookahead, so it no longer carries the trailing words.
        # `\s*` after the value is consumed by the match, so re-insert a
        # separator; this text is read by a human scorer.
        raw_context = (
            f"{m.group('before')}{value}{m.group('unit') or ''} {m.group('after') or ''}"
        )
        context = " ".join(raw_context.split())[:120]
        # Deduplicate on the value together with the word that introduces it,
        # so "Sharpe 1.34" repeated across an abstract collapses to one row while
        # "RMSE 0.5" and "Sharpe 0.5" remain distinct claims.
        lead = re.findall(r"[A-Za-z]+", m.group("before") or "")
        key = (value, lead[-1].lower() if lead else "")
        if key in seen:
            continue
        seen.add(key)
        out.append(NumericClaim(value=value, context=context))
        if len(out) >= limit:
            break
    return out


@dataclass(slots=True)
class ScoringCandidate:
    """One repository that produced output, ready for human adjudication."""

    key: str
    title: str
    code_url: str | None
    entrypoint_source: str | None
    paper_claims: list[NumericClaim] = field(default_factory=list)
    emitted_numbers: list[str] = field(default_factory=list)
    emitted_excerpt: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "code_url": self.code_url,
            "entrypoint_source": self.entrypoint_source,
            "paper_claims": [c.to_dict() for c in self.paper_claims],
            "emitted_numbers": self.emitted_numbers,
            "emitted_excerpt": self.emitted_excerpt,
            # Filled in by the scorer.
            "verdict": "",          # match | no_match | not_comparable
            "matched_value": "",
            "justification": "",
        }


#: Verdicts a scorer may record, mapped onto the ladder.
VERDICTS = {
    "match": True,
    "no_match": False,
    "not_comparable": False,
}

#: The reason recorded when a candidate does not clear rung 10.
VERDICT_REASONS = {
    "no_match": "number_outside_tolerance",
    "not_comparable": "output_not_comparable",
}


def build_worksheet(
    results_path: Path,
    corpus_path: Path,
    out_json: Path,
    out_csv: Path | None = None,
) -> list[ScoringCandidate]:
    """Assemble the manual-scoring worksheet from a completed campaign.

    Only repositories that reached `PRODUCES_OUTPUT` are eligible: rung 10 asks
    whether the emitted number matches, so an artefact that emitted nothing
    cannot be adjudicated and is not put in front of a human.
    """
    corpus = {}
    with corpus_path.open() as fh:
        for line in fh:
            if line.strip():
                d = json.loads(line)
                corpus[str(d["key"])] = d

    candidates: list[ScoringCandidate] = []
    with results_path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("highest_rung", 0) < int(Rung.PRODUCES_OUTPUT):
                continue
            meta = corpus.get(r["key"], {})
            paper_text = f"{meta.get('title', '')}. {meta.get('abstract', '')}"
            # The program's actual stdout, not the rung-9 summary line: the
            # summary says whether output appeared, not what it said.
            excerpt = r.get("run_stdout", "")
            candidates.append(
                ScoringCandidate(
                    key=r["key"],
                    title=meta.get("title", ""),
                    code_url=r.get("code_url"),
                    entrypoint_source=r.get("entrypoint_source"),
                    paper_claims=extract_numeric_claims(paper_text),
                    emitted_numbers=sorted(
                        {n for n in re.findall(r"-?\d+\.\d+", excerpt)}
                    )[:25],
                    emitted_excerpt=excerpt[:2000],
                )
            )

    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = [c.to_dict() for c in candidates]
    out_json.write_text(json.dumps(payload, indent=2))

    if out_csv is not None:
        with out_csv.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow([
                "key", "title", "code_url", "entrypoint_source",
                "paper_claims", "emitted_numbers",
                "verdict(match|no_match|not_comparable)", "matched_value", "justification",
            ])
            for c in candidates:
                w.writerow([
                    c.key, c.title[:120], c.code_url or "", c.entrypoint_source or "",
                    " | ".join(f"{x.value} ({x.context})" for x in c.paper_claims[:10]),
                    " | ".join(c.emitted_numbers[:10]),
                    "", "", "",
                ])
    return candidates


def apply_scores(
    results: list[LadderResult],
    worksheet_path: Path,
) -> tuple[int, int]:
    """Attach human verdicts to ladder results. Returns (scored, matched).

    Unscored candidates are left untouched rather than defaulted, so an
    incomplete worksheet reduces the denominator instead of silently counting as
    failures.
    """
    scored_rows = json.loads(worksheet_path.read_text())
    by_key = {r.key: r for r in results}
    scored = matched = 0
    for row in scored_rows:
        verdict = (row.get("verdict") or "").strip().lower()
        if not verdict:
            continue
        if verdict not in VERDICTS:
            raise ValueError(
                f"{row.get('key')}: verdict {verdict!r} is not one of {sorted(VERDICTS)}"
            )
        result = by_key.get(row["key"])
        if result is None:
            continue
        scored += 1
        cleared = VERDICTS[verdict]
        result.record(
            StageOutcome(
                Rung.MATCHES_PUBLISHED,
                cleared,
                None if cleared else VERDICT_REASONS[verdict],
                (row.get("justification") or "")[:400],
            )
        )
        matched += int(cleared)
    return scored, matched
