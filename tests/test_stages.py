"""Tests for the attrition ladder.

These guard the properties the paper's headline table depends on: that the
ladder is monotone, that the curve is non-increasing, and that a failure can
never be recorded with a reason outside the closed vocabulary (which would make
the failure-mix table uninterpretable).
"""
from __future__ import annotations

import pytest

from exec_repro.stages import (
    ALL_REASONS,
    BLOCK_REASONS,
    LadderResult,
    Rung,
    StageOutcome,
    attrition_curve,
    failure_mix,
)


def _cleared(*rungs: Rung) -> LadderResult:
    r = LadderResult(key="k", code_url="https://example.invalid/x")
    for rung in rungs:
        r.record(StageOutcome(rung, True))
    return r


def test_rungs_are_strictly_ordered_and_unique() -> None:
    values = [int(r) for r in Rung]
    assert values == sorted(values)
    assert len(values) == len(set(values))


def test_failure_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="without a reason"):
        StageOutcome(Rung.RUNS, cleared=False)


def test_failure_reason_must_be_in_closed_vocabulary() -> None:
    with pytest.raises(ValueError, match="closed vocabulary"):
        StageOutcome(Rung.RUNS, cleared=False, reason="it_broke_somehow")


def test_every_declared_reason_is_accepted_at_its_rung() -> None:
    for rung, reasons in BLOCK_REASONS.items():
        for reason in reasons:
            outcome = StageOutcome(rung, cleared=False, reason=reason)
            assert outcome.reason == reason


def test_reason_vocabulary_has_no_duplicates_across_rungs() -> None:
    flat = [r for reasons in BLOCK_REASONS.values() for r in reasons]
    assert len(flat) == len(set(flat)), "a reason string is reused across rungs"
    assert set(flat) == set(ALL_REASONS)


def test_highest_rung_is_max_cleared() -> None:
    r = _cleared(Rung.URL_PRESENT, Rung.URL_RESOLVES, Rung.CLONED)
    assert r.highest_rung is Rung.CLONED


def test_highest_rung_is_zero_when_nothing_cleared() -> None:
    r = LadderResult(key="k")
    r.record(StageOutcome(Rung.URL_PRESENT, False, "no_code_statement"))
    assert r.highest_rung is Rung.NO_CLAIM
    assert r.blocked_at is Rung.URL_PRESENT
    assert r.block_reason == "no_code_statement"


def test_blocked_at_reports_first_failure_only() -> None:
    r = _cleared(Rung.URL_PRESENT)
    r.record(StageOutcome(Rung.URL_RESOLVES, False, "http_404"))
    assert r.blocked_at is Rung.URL_RESOLVES
    assert r.block_reason == "http_404"


def test_attrition_curve_is_monotone_non_increasing() -> None:
    results = [
        _cleared(Rung.URL_PRESENT),
        _cleared(Rung.URL_PRESENT, Rung.URL_RESOLVES),
        _cleared(Rung.URL_PRESENT, Rung.URL_RESOLVES, Rung.CLONED),
        _cleared(*list(Rung)[1:]),
    ]
    curve = attrition_curve(results)
    counts = [curve[r.name]["n_clearing"] for r in Rung]
    assert counts == sorted(counts, reverse=True), "curve must never increase"


def test_attrition_curve_percentages_match_counts() -> None:
    results = [_cleared(Rung.URL_PRESENT) for _ in range(4)]
    curve = attrition_curve(results)
    assert curve["URL_PRESENT"]["n_clearing"] == 4
    assert curve["URL_PRESENT"]["pct_clearing"] == 100.0
    assert curve["CLONED"]["n_clearing"] == 0
    assert curve["CLONED"]["pct_clearing"] == 0.0


def test_attrition_curve_empty_corpus_is_empty_not_a_crash() -> None:
    assert attrition_curve([]) == {}


def test_failure_mix_groups_by_blocking_rung() -> None:
    a = _cleared(Rung.URL_PRESENT)
    a.record(StageOutcome(Rung.URL_RESOLVES, False, "http_404"))
    b = _cleared(Rung.URL_PRESENT)
    b.record(StageOutcome(Rung.URL_RESOLVES, False, "http_404"))
    c = _cleared(Rung.URL_PRESENT, Rung.URL_RESOLVES, Rung.CLONED)
    c.record(StageOutcome(Rung.DEPS_DECLARED, False, "no_dependency_spec"))
    mix = failure_mix([a, b, c])
    assert mix["URL_RESOLVES"]["http_404"] == 2
    assert mix["DEPS_DECLARED"]["no_dependency_spec"] == 1


def test_serialisation_roundtrips_the_key_fields() -> None:
    r = _cleared(Rung.URL_PRESENT, Rung.URL_RESOLVES)
    r.record(StageOutcome(Rung.CLONED, False, "clone_timeout", "took too long", 12.5))
    d = r.to_dict()
    assert d["key"] == "k"
    assert d["highest_rung_name"] == "URL_RESOLVES"
    assert d["blocked_at"] == "CLONED"
    assert d["block_reason"] == "clone_timeout"
    assert len(d["stages"]) == 3
