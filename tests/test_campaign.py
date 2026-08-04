"""Tests for campaign resumability and completion-order draining."""
from __future__ import annotations

import json
from pathlib import Path

from exec_repro.campaign import completed_keys, load_corpus, load_results


def test_completed_keys_skips_finished_work(tmp_path: Path) -> None:
    p = tmp_path / "r.jsonl"
    p.write_text('{"key": "a"}\n{"key": "b"}\n')
    assert completed_keys(p) == {"a", "b"}


def test_completed_keys_tolerates_a_torn_final_line(tmp_path: Path) -> None:
    """A kill mid-write leaves a partial line; that key must be retried."""
    p = tmp_path / "r.jsonl"
    p.write_text('{"key": "a"}\n{"key": "b"\n')
    assert completed_keys(p) == {"a"}


def test_completed_keys_on_missing_file(tmp_path: Path) -> None:
    assert completed_keys(tmp_path / "nope.jsonl") == set()


def test_load_results_carries_entrypoint_source_and_stdout(tmp_path: Path) -> None:
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps({
        "key": "a", "code_url": "u", "entrypoint_source": "readme",
        "run_stdout": "rmse 0.5",
        "stages": [{"rung": 2, "rung_name": "URL_PRESENT", "cleared": True,
                    "reason": None, "detail": "", "duration_s": 0.1}],
    }) + "\n")
    r = load_results(p)[0]
    assert r.entrypoint_source == "readme"
    assert r.run_stdout == "rmse 0.5"


def test_load_corpus_filters_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text('{"key": "a", "code_url": "u"}\n\n{"key": "b", "code_url": null}\n')
    entries = load_corpus(p)
    assert [e.key for e in entries] == ["a", "b"]
    assert entries[1].code_url is None


def test_refuses_to_resume_onto_an_actively_written_file(tmp_path: Path) -> None:
    """Guards the race that produced a duplicate record in campaign v5.

    Resuming while a prior campaign is still flushing makes its last keys
    invisible to `completed_keys`, so they get run twice and double-count in the
    attrition curve.
    """
    import pytest

    from exec_repro.campaign import _warn_if_results_file_is_active

    p = tmp_path / "r.jsonl"
    p.write_text('{"key": "a"}\n')  # just written, so it looks active
    with pytest.raises(RuntimeError, match="may still be receiving writes"):
        _warn_if_results_file_is_active(p)


def test_allows_resume_onto_a_settled_file(tmp_path: Path) -> None:
    import os
    import time

    from exec_repro.campaign import _warn_if_results_file_is_active

    p = tmp_path / "r.jsonl"
    p.write_text('{"key": "a"}\n')
    old = time.time() - 3600
    os.utime(p, (old, old))
    _warn_if_results_file_is_active(p)  # must not raise


def test_allows_a_fresh_results_path(tmp_path: Path) -> None:
    from exec_repro.campaign import _warn_if_results_file_is_active

    _warn_if_results_file_is_active(tmp_path / "does_not_exist.jsonl")
