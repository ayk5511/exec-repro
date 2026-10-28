"""Tests for URL normalisation, failure classification, and artefact detection.

URL normalisation gets its own suite because a regression there silently
corrupts the paper's link-rot number: a stray trailing period turns a live
repository into a 404.
"""
from __future__ import annotations

import pytest

from exec_repro import runner
from exec_repro.isolate import RunResult
from exec_repro.runner import (
    _classify_install_failure,
    _classify_run_failure,
    find_dep_spec,
    find_entrypoint,
    normalize_url,
    snapshot_artefacts,
)
from exec_repro.stages import BLOCK_REASONS, Rung


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The dominant real-world artefact: a sentence-ending period.
        ("https://github.com/a/b.", "https://github.com/a/b"),
        ("https://github.com/a/b,", "https://github.com/a/b"),
        ("https://github.com/a/b;", "https://github.com/a/b"),
        # Wrapped in prose punctuation.
        ("(https://github.com/a/b)", "https://github.com/a/b"),
        ("[https://github.com/a/b]", "https://github.com/a/b"),
        ("'https://github.com/a/b'", "https://github.com/a/b"),
        # Web URLs reduced to the clone root.
        ("https://github.com/a/b/tree/main", "https://github.com/a/b"),
        ("https://github.com/a/b/tree/main/src/deep", "https://github.com/a/b"),
        ("https://github.com/a/b/blob/main/README.md", "https://github.com/a/b"),
        ("https://github.com/a/b.git", "https://github.com/a/b"),
        # Trailing slash and whitespace.
        ("  https://github.com/a/b/  ", "https://github.com/a/b"),
        # Non-GitHub hosts pass through, only de-punctuated.
        ("https://gitlab.com/a/b.", "https://gitlab.com/a/b"),
        ("https://zenodo.org/record/123.", "https://zenodo.org/record/123"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_normalize_url_is_idempotent() -> None:
    once = normalize_url("https://github.com/a/b/tree/main.")
    assert normalize_url(once) == once


def test_normalize_url_preserves_case_of_repo_path() -> None:
    # GitHub paths are case-sensitive for cloning.
    assert normalize_url("https://github.com/AI4Finance-Foundation/FinRL.") == (
        "https://github.com/AI4Finance-Foundation/FinRL"
    )


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("ERROR: No matching distribution found for foo==1.0", "package_not_found"),
        ("ERROR: Could not find a version that satisfies", "package_not_found"),
        ("Reason: the package is yanked", "package_version_yanked"),
        ("requires-python >=3.12 but you have 3.10", "python_version_unsupported"),
        ("ResolutionImpossible: conflicting deps", "dependency_resolution_conflict"),
        ("Failed building wheel for scipy", "build_from_source_failed"),
        # Harness-attributable: must not be blamed on the paper.
        (
            "ERROR: Could not open requirements file: [Errno 2] No such file",
            "spec_unreadable_by_backend",
        ),
        ("ERROR: x is not a valid editable requirement", "spec_unreadable_by_backend"),
    ],
)
def test_classify_install_failure(stderr: str, expected: str) -> None:
    assert _classify_install_failure(RunResult(1, "", stderr, 1.0)) == expected


def test_harness_path_failure_is_not_attributed_to_dependencies() -> None:
    """Regression guard for a real bug.

    The docker backend once passed a bare filename for a spec found in a
    subdirectory; pip's "could not open requirements file" was then recorded as
    `dependency_resolution_conflict`, i.e. the harness's own path bug was
    reported as evidence about the paper.
    """
    r = RunResult(1, "", "ERROR: Could not open requirements file: 'requirements.txt'", 1.0)
    reason = _classify_install_failure(r)
    assert reason == "spec_unreadable_by_backend"
    assert reason != "dependency_resolution_conflict"


def test_classify_install_timeout_wins_over_text() -> None:
    r = RunResult(124, "", "No matching distribution", 900.0, timed_out=True)
    assert _classify_install_failure(r) == "install_timeout"


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("FileNotFoundError: 'data/prices.csv'", "missing_data_file"),
        ("FileNotFoundError: '/Users/someone/data/x.csv'", "hardcoded_absolute_path"),
        ("raise ValueError('API key required')", "requires_api_key"),
        ("could not connect to Bloomberg terminal", "requires_paid_data_vendor"),
        ("MemoryError", "out_of_memory"),
        ("CUDA driver not found", "requires_gpu"),
        ("ZeroDivisionError: division by zero", "runtime_exception"),
    ],
)
def test_classify_run_failure(stderr: str, expected: str) -> None:
    assert _classify_run_failure(RunResult(1, "", stderr, 1.0)) == expected


def test_classify_run_timeout() -> None:
    r = RunResult(124, "", "TIMEOUT", 1800.0, timed_out=True)
    assert _classify_run_failure(r) == "execution_timeout"


def test_all_classifier_outputs_are_in_the_closed_vocabulary() -> None:
    """A classifier returning an unlisted string would crash StageOutcome."""
    install_reasons = BLOCK_REASONS[Rung.ENV_BUILDS]
    run_reasons = BLOCK_REASONS[Rung.RUNS]
    samples = [
        "no matching distribution", "yanked", "requires-python", "conflict",
        "failed building wheel", "something totally unexpected",
    ]
    for s in samples:
        assert _classify_install_failure(RunResult(1, "", s, 0.0)) in install_reasons
    for s in ["filenotfounderror", "api key", "bloomberg", "memoryerror",
              "cuda", "something totally unexpected"]:
        assert _classify_run_failure(RunResult(1, "", s, 0.0)) in run_reasons


def test_find_dep_spec_prefers_requirements_at_root(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("numpy\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert find_dep_spec(tmp_path).name == "requirements.txt"


def test_find_dep_spec_ignores_empty_files(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert find_dep_spec(tmp_path).name == "pyproject.toml"


def test_find_dep_spec_returns_none_when_absent(tmp_path) -> None:
    (tmp_path / "README.md").write_text("hello")
    assert find_dep_spec(tmp_path) is None


def test_find_entrypoint_prefers_named_candidates(tmp_path) -> None:
    (tmp_path / "main.py").write_text("print(1)")
    (tmp_path / "other.py").write_text("print(2)")
    e = find_entrypoint(tmp_path)
    assert e.argv == ["python", "main.py"] and e.source == "convention"


def test_find_entrypoint_accepts_lone_top_level_script(tmp_path) -> None:
    (tmp_path / "analysis.py").write_text("print(1)")
    assert find_entrypoint(tmp_path).argv == ["python", "analysis.py"]


def test_find_entrypoint_is_none_when_ambiguous(tmp_path) -> None:
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    assert find_entrypoint(tmp_path, use_readme=False) is None


def test_snapshot_artefacts_excludes_venv_and_git(tmp_path) -> None:
    (tmp_path / "out.csv").write_text("a,b\n")
    venv = tmp_path / ".exec_repro_venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "junk.json").write_text("{}")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "index.pkl").write_text("x")
    found = {p.name for p in snapshot_artefacts(tmp_path)}
    assert found == {"out.csv"}


def test_setup_py_is_never_an_entrypoint(tmp_path) -> None:  # noqa: D401
    """Regression guard: running setup.py measures packaging, not research.

    Five of nineteen executions in an early campaign ran setup.py and recorded
    setuptools deprecation warnings as though the paper's code had failed.
    """
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup()")
    assert find_entrypoint(tmp_path, use_readme=False) is None


def test_lone_real_script_still_found_alongside_setup_py(tmp_path) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup; setup()")
    (tmp_path / "analysis.py").write_text("print(1)")
    found = find_entrypoint(tmp_path)
    assert found is not None and found.argv == ["python", "analysis.py"]


def test_config_modules_are_not_entrypoints(tmp_path) -> None:
    for name in ("conf.py", "settings.py", "__init__.py"):
        (tmp_path / name).write_text("X = 1")
    assert find_entrypoint(tmp_path, use_readme=False) is None


@pytest.mark.parametrize(
    "stderr",
    [
        "curl: (6) Could not resolve host: guce.yahoo.com",
        "requests.exceptions.ConnectionError: Max retries exceeded",
        "socket.gaierror: [Errno -2] Name or service not known",
        "Temporary failure in name resolution",
    ],
)
def test_runtime_network_access_is_missing_data_not_generic_error(stderr: str) -> None:
    """A runtime fetch means the artefact is not self-contained.

    Network is denied during execution by design, so these must be attributed to
    the artefact depending on a live endpoint, not lumped into runtime_exception.
    """
    assert _classify_run_failure(RunResult(1, "", stderr, 1.0)) == "missing_data_file"


def test_check_url_retries_transport_failures_but_not_http_answers(monkeypatch) -> None:
    """Link rot is a claim about the world, so it must survive a retry.

    A campaign once recorded `dns_failure` for a URL that a parallel run resolved
    seconds later; a single attempt cannot distinguish a dead domain from a local
    resolver hiccup.
    """
    from exec_repro import runner

    calls = {"n": 0}

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return (False, "dns_failure", "transient")
        return (True, None, "http_200")

    monkeypatch.setattr(runner, "_check_url_once", flaky)
    monkeypatch.setattr(runner.time, "sleep", lambda *_: None)
    cleared, reason, _ = runner.check_url("https://example.invalid/x")
    assert cleared and reason is None
    assert calls["n"] == 2, "a transient failure must be retried"


def test_check_url_does_not_retry_a_404(monkeypatch) -> None:
    """A 404 is a definite answer from a live server; retrying wastes time."""
    from exec_repro import runner

    calls = {"n": 0}

    def always_404(url, timeout):
        calls["n"] += 1
        return (False, "http_404", "http_404")

    monkeypatch.setattr(runner, "_check_url_once", always_404)
    monkeypatch.setattr(runner.time, "sleep", lambda *_: None)
    cleared, reason, _ = runner.check_url("https://example.invalid/x")
    assert not cleared and reason == "http_404"
    assert calls["n"] == 1, "an HTTP answer must not be retried"


def test_check_url_gives_up_after_repeated_transport_failures(monkeypatch) -> None:
    from exec_repro import runner

    monkeypatch.setattr(runner, "_check_url_once",
                        lambda url, timeout: (False, "dns_failure", "down"))
    monkeypatch.setattr(runner.time, "sleep", lambda *_: None)
    cleared, reason, _ = runner.check_url("https://example.invalid/x")
    assert not cleared and reason == "dns_failure"


# --- Regression: markup residue in extracted URLs -------------------------
# Three v5 records were recorded http_404 solely because a flattened
# \href{url}{text} left "}{GitHub" fused to the address. All three are live.
# Reporting our extraction defect as the literature's link rot inflated the
# headline rot figure, in the same subsection that claimed normalisation had
# eliminated exactly this failure.


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://github.com/Open-Finance-Lab/AgenticTrading}{GitHub",
            "https://github.com/Open-Finance-Lab/AgenticTrading",
        ),
        (
            "https://github.com/org/repo}{GitHub repository}",
            "https://github.com/org/repo",
        ),
        ("https://github.com/org/repo\\footnotemark", "https://github.com/org/repo"),
        ("https://github.com/org/repo<br>", "https://github.com/org/repo"),
    ],
)
def test_normalize_url_cuts_markup_residue(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_normalize_url_keeps_a_period_inside_a_repo_name() -> None:
    """A period is legal in a repository name, so it must not be cut blindly.

    ".The" is a fused sentence period in one corpus record, but ".js" and ".py"
    are real repository names. Disambiguating needs retrieval, not lexing, which
    is why this case is handled by verified repair rather than here.
    """
    assert (
        normalize_url("https://github.com/janrems/DeepLearningQHedging.The")
        == "https://github.com/janrems/DeepLearningQHedging.The"
    )
    assert normalize_url("https://github.com/org/vue.js") == "https://github.com/org/vue.js"


def test_repair_candidates_offers_the_fused_sentence_form() -> None:
    assert runner.repair_candidates("https://github.com/janrems/DeepLearningQHedging.The") == [
        "https://github.com/janrems/DeepLearningQHedging"
    ]


def test_repair_candidates_declines_to_strip_a_bare_host() -> None:
    """Never propose a repair that would climb above the repository root."""
    assert runner.repair_candidates("https://github.com/Org") == []


def test_repair_is_adopted_only_when_it_resolves(monkeypatch, tmp_path) -> None:
    """A candidate that also 404s must leave the original verdict standing.

    The asymmetry is the point: an unverified repair would convert measured link
    rot into a spurious live repository, understating decay in a paper whose
    subject is decay.
    """
    seen: list[str] = []

    def only_repaired_resolves(url: str, timeout: int = 20, **kw):
        seen.append(url)
        if url.endswith("DeepLearningQHedging"):
            return True, None, "http_200"
        return False, "http_404", "http_404"

    monkeypatch.setattr(runner, "check_url", only_repaired_resolves)
    monkeypatch.setattr(runner, "clone", lambda u, r: (False, "clone_timeout", "stub"))

    res = runner.run_ladder(
        "k",
        "https://github.com/janrems/DeepLearningQHedging.The",
        tmp_path,
        backend=None,  # type: ignore[arg-type]
    )
    assert res.url_repaired_from == "https://github.com/janrems/DeepLearningQHedging.The"
    assert res.code_url == "https://github.com/janrems/DeepLearningQHedging"
    assert int(res.highest_rung) >= 3


def test_unrepairable_404_stays_link_rot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        runner, "check_url", lambda u, timeout=20, **kw: (False, "http_404", "http_404")
    )
    res = runner.run_ladder("k", "https://github.com/org/gone.The", tmp_path, backend=None)  # type: ignore[arg-type]
    assert res.url_repaired_from is None
    assert res.block_reason == "http_404"


# --- Regression: a bare `make` is not an entry point ----------------------
# One corpus repository documented "make" in its README. The default target
# printed the Makefile's own usage text, and the harness scored that as the
# artefact producing output at rung 9. The documented rule has always been that
# a README command must name a target that exists; the code did not enforce it.


def _repo_with_makefile(tmp_path, readme_cmd: str, makefile: str):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "README.md").write_text(f"## Usage\n\n```\n{readme_cmd}\n```\n")
    (repo / "Makefile").write_text(makefile)
    return repo


def test_bare_make_is_rejected(tmp_path) -> None:
    repo = _repo_with_makefile(tmp_path, "make", "help:\n\t@echo usage\n")
    assert runner.entrypoint_from_readme(repo) is None


def test_make_with_undeclared_target_is_rejected(tmp_path) -> None:
    repo = _repo_with_makefile(tmp_path, "make experiment", "help:\n\t@echo usage\n")
    assert runner.entrypoint_from_readme(repo) is None


def test_make_housekeeping_targets_are_rejected(tmp_path) -> None:
    for target in ("test", "lint", "build", "clean", "docs"):
        sub = tmp_path / target
        sub.mkdir()
        repo = _repo_with_makefile(sub, f"make {target}", f"{target}:\n\t@echo x\n")
        assert runner.entrypoint_from_readme(repo) is None, target


def test_make_with_a_declared_analysis_target_is_accepted(tmp_path) -> None:
    repo = _repo_with_makefile(
        tmp_path, "make experiment", "experiment:\n\tpython run.py\n"
    )
    ep = runner.entrypoint_from_readme(repo)
    assert ep is not None
    assert ep.argv == ["make", "experiment"]
