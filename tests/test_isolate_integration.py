"""Integration tests for the isolation backends.

These need a working container runtime and network access, so they skip when
either is missing. They exist because the most damaging bug found in this
project was invisible to unit tests: every layer worked in isolation, and the
defect only appeared in the *handoff* between installing an environment and
running in it.
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from exec_repro.isolate import DockerBackend, UvSeatbeltBackend

pytestmark = pytest.mark.integration


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20, check=False
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture()
def tiny_repo() -> Iterator[Path]:
    """A repository with one small dependency and one import of it.

    Deliberately *not* pytest's `tmp_path`: on macOS that lives under
    /private/var/folders, which container runtimes do not share with their VM,
    so the mount would come up empty. The scratch directory sits under the
    user's home, which is shared by default.
    """
    root = Path(__file__).resolve().parent.parent / "scratch" / "_integration"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "requirements.txt").write_text("six==1.16.0\n")
    (root / "main.py").write_text("import six; print('IMPORT_OK', six.__version__)\n")
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")
def test_docker_install_persists_into_run(tiny_repo: Path) -> None:
    """Regression guard for the bug that fabricated a '0% execute' result.

    `docker run --rm` starts a fresh container per invocation, so packages
    installed during build_env were discarded before run() executed and every
    repository failed with ModuleNotFoundError regardless of its quality. The
    backend now keeps one container alive per repository.
    """
    backend = DockerBackend()
    try:
        build = backend.build_env(tiny_repo, tiny_repo / "requirements.txt", 300)
        assert build.ok, f"install failed: {build.stderr[:300]}"

        result = backend.run(tiny_repo, ["python", "main.py"], 120)
        assert result.ok, f"run failed: {result.stderr[:300]}"
        assert "IMPORT_OK" in result.stdout, (
            "dependency installed during build_env was not importable at run time; "
            "the container is being recreated between steps"
        )
        assert "ModuleNotFoundError" not in result.stderr
    finally:
        backend.cleanup(tiny_repo)


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")
def test_docker_cleanup_is_idempotent(tiny_repo: Path) -> None:
    backend = DockerBackend()
    backend.build_env(tiny_repo, tiny_repo / "requirements.txt", 300)
    backend.cleanup(tiny_repo)
    backend.cleanup(tiny_repo)  # must not raise


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_uv_install_persists_into_run(tiny_repo: Path) -> None:
    """The same handoff property must hold for the fallback backend."""
    backend = UvSeatbeltBackend()
    try:
        build = backend.build_env(tiny_repo, tiny_repo / "requirements.txt", 300)
        assert build.ok, f"install failed: {build.stderr[:300]}"

        result = backend.run(tiny_repo, ["python", "main.py"], 120)
        assert "IMPORT_OK" in result.stdout, (
            f"dependency not importable at run time: {result.stderr[:300]}"
        )
    finally:
        backend.cleanup(tiny_repo)
