"""Tests for the manual rung-10 scoring path.

Rung 10 is the one place a human decision enters the data, so the tests here
guard the boundary: verdicts must come from the worksheet, unscored rows must
not be silently counted as failures, and an unrecognised verdict must be an
error rather than a quiet default.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from exec_repro.rung10 import (
    apply_scores,
    build_worksheet,
    extract_numeric_claims,
)
from exec_repro.stages import LadderResult, Rung, StageOutcome


def test_extract_numeric_claims_keeps_context() -> None:
    claims = extract_numeric_claims("We report an RMSE of 0.0412 on the test set.")
    assert any(c.value == "0.0412" for c in claims)
    assert any("RMSE" in c.context for c in claims)


def test_extract_numeric_claims_drops_years_and_tiny_counts() -> None:
    claims = extract_numeric_claims("Data from 2019 to 2024 across 2 markets.")
    assert all(c.value not in {"2019", "2024", "2"} for c in claims)


def test_extract_numeric_claims_deduplicates() -> None:
    claims = extract_numeric_claims("Sharpe 1.34. Sharpe 1.34. Sharpe 1.34.")
    assert sum(1 for c in claims if c.value == "1.34") == 1


def _write(tmp_path: Path, results: list[dict], corpus: list[dict]) -> tuple[Path, Path]:
    rp = tmp_path / "ladder.jsonl"
    cp = tmp_path / "corpus.jsonl"
    rp.write_text("".join(json.dumps(r) + "\n" for r in results))
    cp.write_text("".join(json.dumps(c) + "\n" for c in corpus))
    return rp, cp


def test_worksheet_includes_only_repos_that_produced_output(tmp_path: Path) -> None:
    results = [
        {"key": "a", "highest_rung": int(Rung.PRODUCES_OUTPUT), "stages": [
            {"rung_name": "PRODUCES_OUTPUT", "detail": "rmse 0.0412"}]},
        {"key": "b", "highest_rung": int(Rung.RUNS), "stages": []},
        {"key": "c", "highest_rung": int(Rung.CLONED), "stages": []},
    ]
    corpus = [
        {"key": "a", "title": "Paper A", "abstract": "We report an RMSE of 0.041."},
        {"key": "b", "title": "Paper B", "abstract": ""},
    ]
    rp, cp = _write(tmp_path, results, corpus)
    cands = build_worksheet(rp, cp, tmp_path / "ws.json", tmp_path / "ws.csv")
    assert [c.key for c in cands] == ["a"]
    assert (tmp_path / "ws.csv").exists()


def test_worksheet_rows_start_unscored(tmp_path: Path) -> None:
    results = [{"key": "a", "highest_rung": int(Rung.PRODUCES_OUTPUT),
                "stages": [{"rung_name": "PRODUCES_OUTPUT", "detail": "0.5"}]}]
    corpus = [{"key": "a", "title": "T", "abstract": "value 0.5"}]
    rp, cp = _write(tmp_path, results, corpus)
    build_worksheet(rp, cp, tmp_path / "ws.json")
    rows = json.loads((tmp_path / "ws.json").read_text())
    assert rows[0]["verdict"] == ""


def _result(key: str) -> LadderResult:
    r = LadderResult(key=key)
    for rung in list(Rung)[2:10]:
        r.record(StageOutcome(rung, True))
    return r


def test_apply_scores_records_a_match(tmp_path: Path) -> None:
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps([
        {"key": "a", "verdict": "match", "justification": "RMSE 0.0412 == 0.041"}
    ]))
    results = [_result("a")]
    scored, matched = apply_scores(results, ws)
    assert (scored, matched) == (1, 1)
    assert results[0].highest_rung is Rung.MATCHES_PUBLISHED


def test_apply_scores_records_a_non_match_with_a_reason(tmp_path: Path) -> None:
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps([{"key": "a", "verdict": "no_match"}]))
    results = [_result("a")]
    scored, matched = apply_scores(results, ws)
    assert (scored, matched) == (1, 0)
    assert results[0].block_reason == "number_outside_tolerance"


def test_not_comparable_is_distinct_from_no_match(tmp_path: Path) -> None:
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps([{"key": "a", "verdict": "not_comparable"}]))
    results = [_result("a")]
    apply_scores(results, ws)
    assert results[0].block_reason == "output_not_comparable"


def test_unscored_rows_are_left_alone(tmp_path: Path) -> None:
    """An incomplete worksheet must shrink the denominator, not add failures."""
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps([{"key": "a", "verdict": ""}]))
    results = [_result("a")]
    scored, matched = apply_scores(results, ws)
    assert (scored, matched) == (0, 0)
    assert results[0].highest_rung is Rung.PRODUCES_OUTPUT
    assert results[0].blocked_at is None


def test_unknown_verdict_is_an_error(tmp_path: Path) -> None:
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps([{"key": "a", "verdict": "probably?"}]))
    with pytest.raises(ValueError, match="not one of"):
        apply_scores([_result("a")], ws)


def test_same_value_under_different_labels_is_kept() -> None:
    """Deduplication must not merge distinct claims that share a number."""
    claims = extract_numeric_claims("RMSE 0.50 and Sharpe 0.50 were observed.")
    values = [(c.value, c.context) for c in claims if c.value == "0.50"]
    assert len(values) == 2
