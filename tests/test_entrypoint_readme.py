"""Tests for README-derived entry points.

The convention-only heuristic identified a runnable entry point in roughly one
repository in eight, which measured our own conservatism as much as it measured
the literature. Consulting the README recovers the authors' own instructions.
Because that route interprets prose, it is fenced in carefully: the command must
name a target that actually exists, must not be an installation step, and must
not be a compound shell pipeline.
"""
from __future__ import annotations

from pathlib import Path

from exec_repro.runner import entrypoint_from_readme, find_entrypoint


def _repo(tmp_path: Path, readme: str, files: dict[str, str] | None = None) -> Path:
    (tmp_path / "README.md").write_text(readme)
    for name, body in (files or {}).items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def test_extracts_python_command_from_fenced_block(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "# Paper\n\nRun it:\n\n```bash\npython experiments/run_all.py\n```\n",
        {"experiments/run_all.py": "print(1)"},
    )
    e = entrypoint_from_readme(repo)
    assert e is not None
    assert e.argv == ["python", "experiments/run_all.py"]
    assert e.source == "readme"


def test_strips_shell_prompt_marker(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\n$ python go.py\n```\n", {"go.py": "print(1)"})
    e = entrypoint_from_readme(repo)
    assert e is not None and e.argv == ["python", "go.py"]


def test_skips_install_commands_and_finds_the_run(tmp_path: Path) -> None:
    readme = (
        "```bash\n"
        "git clone https://github.com/a/b\n"
        "pip install -r requirements.txt\n"
        "conda create -n env python=3.10\n"
        "python train.py\n"
        "```\n"
    )
    repo = _repo(tmp_path, readme, {"train.py": "print(1)"})
    e = entrypoint_from_readme(repo)
    assert e is not None and e.argv == ["python", "train.py"]


def test_ignores_pytest_invocation(tmp_path: Path) -> None:
    """Running the test suite is not running the analysis."""
    repo = _repo(tmp_path, "```\npytest tests/\n```\n", {"tests/test_x.py": ""})
    assert entrypoint_from_readme(repo) is None


def test_requires_the_target_to_exist(tmp_path: Path) -> None:
    """A README may document a script the repository never shipped."""
    repo = _repo(tmp_path, "```\npython missing.py\n```\n")
    assert entrypoint_from_readme(repo) is None


def test_module_invocation_requires_the_package(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\npython -m mypkg.cli\n```\n", {"mypkg/__init__.py": ""})
    e = entrypoint_from_readme(repo)
    assert e is not None and e.argv == ["python", "-m", "mypkg.cli"]


def test_module_invocation_rejected_when_package_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\npython -m nope.cli\n```\n")
    assert entrypoint_from_readme(repo) is None


def test_shell_script_entrypoint(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\nbash reproduce.sh\n```\n", {"reproduce.sh": "echo hi"})
    e = entrypoint_from_readme(repo)
    assert e is not None and e.argv == ["bash", "reproduce.sh"]


def test_make_requires_a_makefile(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\nmake reproduce\n```\n")
    assert entrypoint_from_readme(repo) is None
    (repo / "Makefile").write_text("reproduce:\n\techo hi\n")
    e = entrypoint_from_readme(repo)
    assert e is not None and e.argv == ["make", "reproduce"]


def test_compound_pipelines_are_refused(tmp_path: Path) -> None:
    """`cd x && python y.py` is not reproducibly parseable, so we decline it."""
    repo = _repo(tmp_path, "```\ncd src && python main.py\n```\n", {"src/main.py": ""})
    assert entrypoint_from_readme(repo) is None


def test_setup_py_in_readme_is_still_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\npython setup.py install\n```\n", {"setup.py": ""})
    assert entrypoint_from_readme(repo) is None


def test_prose_outside_fences_is_ignored(tmp_path: Path) -> None:
    """Only fenced blocks count; prose mentioning a script is not an instruction."""
    repo = _repo(tmp_path, "You could run python train.py if you wanted.\n",
                 {"train.py": "print(1)"})
    assert entrypoint_from_readme(repo) is None


def test_convention_wins_over_readme(tmp_path: Path) -> None:
    """Convention needs no interpretation of prose, so it is tried first."""
    repo = _repo(tmp_path, "```\npython other.py\n```\n",
                 {"main.py": "print(1)", "other.py": "print(2)"})
    e = find_entrypoint(repo)
    assert e is not None and e.source == "convention" and e.argv == ["python", "main.py"]


def test_readme_used_only_when_convention_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "```\npython scripts/go.py\n```\n",
                 {"scripts/go.py": "print(1)", "a.py": "", "b.py": ""})
    e = find_entrypoint(repo)
    assert e is not None and e.source == "readme"
    assert find_entrypoint(repo, use_readme=False) is None


def test_missing_readme_is_not_an_error(tmp_path: Path) -> None:
    assert entrypoint_from_readme(tmp_path) is None
