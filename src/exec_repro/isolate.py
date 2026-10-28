"""Isolation backends for running third-party research code.

We execute code from hundreds of repositories written by people who never
expected a stranger to run it. Two different risks have to be handled and they
need different mechanisms:

  1. *Dependency* isolation: repo A pins numpy 1.21, repo B needs numpy 2.1.
     Solved by giving every repo its own virtual environment. `uv` does this in
     roughly a second, which matters at corpus scale.

  2. *Process* isolation: the code may write outside its directory, phone home,
     or (rarely, but this is published research from arbitrary authors) do
     something actively hostile. Solved by a container, or on macOS by the
     kernel-level Seatbelt sandbox via `sandbox-exec`.

Docker is the strong option and is used when present. When it is absent we fall
back to uv + Seatbelt, which gives real filesystem and network confinement but
shares the kernel and the user account. That is weaker, and the harness records
which backend produced every result so the paper can state the isolation regime
honestly rather than implying containers were used throughout.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


#: Dependency specs a pure-Python backend can install directly.
PYTHON_INSTALLABLE = frozenset({"requirements.txt", "pyproject.toml", "setup.py"})


class Backend(Protocol):
    """Minimal interface every isolation backend implements."""

    name: str

    def can_install(self, spec: Path) -> bool:
        """Whether this backend can materialise the environment `spec` declares.

        Asked *before* `build_env`, so a spec the backend cannot handle (a conda
        environment.yml under a pip-only backend, say) is recorded as an honest
        non-clearance rather than being faked as a successful build.
        """
        ...

    def build_env(self, repo: Path, spec: Path | None, timeout: int) -> RunResult: ...

    def run(self, repo: Path, argv: list[str], timeout: int) -> RunResult:
        """Execute `argv` inside the isolated environment.

        `argv` is the complete command, including its interpreter, because entry
        points are not always Python scripts: a repository may document
        `python -m package`, `bash run.sh`, or `make reproduce`. Backends must
        resolve the interpreter against their own environment (the virtualenv's
        python, say) rather than assuming the host's.
        """
        ...

    def cleanup(self, repo: Path) -> None:
        """Release any resources held for `repo`. Must be safe to call twice."""
        ...


def _exec(
    argv: list[str],
    cwd: Path | None,
    timeout: int,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run a command, capturing output, never raising on failure."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return RunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout[-20000:],
            stderr=proc.stderr[-20000:],
            duration_s=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            exit_code=124,
            stdout=(exc.stdout or b"").decode(errors="replace")[-20000:]
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")[-20000:],
            stderr="TIMEOUT",
            duration_s=time.monotonic() - start,
            timed_out=True,
        )
    except (OSError, ValueError) as exc:
        return RunResult(
            exit_code=127,
            stdout="",
            stderr=f"launch_failed: {exc}",
            duration_s=time.monotonic() - start,
        )


# --------------------------------------------------------------------------
# Seatbelt profile
# --------------------------------------------------------------------------
# Deny by default; allow reads broadly (interpreters and libraries live all over
# the filesystem) but confine *writes* to the repo, its venv, and the temp dir.
# Network is denied during execution: a repo that silently downloads its data at
# runtime is not reproducible from its artefact, and we want that to surface as
# a failure rather than be masked by our own connectivity.
_SEATBELT_PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
  (subpath "{repo}")
  (subpath "{tmp}")
  (subpath "/private/var/folders")
  (subpath "/dev"))
"""


class UvSeatbeltBackend:
    """uv virtualenvs for dependency isolation + macOS Seatbelt for confinement.

    Network is permitted while *installing* (pip must reach the index) and denied
    while *executing*, so runtime data fetches are recorded as failures.
    """

    name = "uv+seatbelt"

    def __init__(self, *, use_sandbox: bool = True) -> None:
        self.use_sandbox = use_sandbox and shutil.which("sandbox-exec") is not None
        if shutil.which("uv") is None:
            raise RuntimeError("uv is required for the UvSeatbeltBackend")

    @staticmethod
    def venv_path(repo: Path) -> Path:
        return repo / ".exec_repro_venv"

    def can_install(self, spec: Path) -> bool:
        return spec.name in PYTHON_INSTALLABLE

    def cleanup(self, repo: Path) -> None:
        # The virtualenv lives inside the repository, which the runner removes
        # wholesale, so there is nothing separate to release.
        return None

    def _resolve_interpreter(self, repo: Path, argv: list[str]) -> list[str]:
        """Point a leading `python`/`python3` at this repo's virtualenv.

        Without this the command would run against the host interpreter, which
        does not have the repository's dependencies installed, and every run
        would fail with ModuleNotFoundError regardless of packaging quality.
        """
        if not argv:
            return argv
        venv_python = self.venv_path(repo) / "bin" / "python"
        if argv[0] in {"python", "python3"} and venv_python.exists():
            return [str(venv_python), *argv[1:]]
        return list(argv)

    def build_env(self, repo: Path, spec: Path | None, timeout: int) -> RunResult:
        venv = self.venv_path(repo)
        created = _exec(["uv", "venv", str(venv)], cwd=repo, timeout=180)
        if not created.ok or spec is None:
            return created
        if not self.can_install(spec):
            raise ValueError(
                f"{self.name} cannot install {spec.name}; callers must check "
                "can_install() first"
            )

        python = str(venv / "bin" / "python")
        if spec.name == "requirements.txt":
            argv = ["uv", "pip", "install", "--python", python, "-r", str(spec)]
        else:  # pyproject.toml / setup.py
            argv = ["uv", "pip", "install", "--python", python, "-e", str(repo)]
        return _exec(argv, cwd=repo, timeout=timeout,
                     env={**os.environ, "VIRTUAL_ENV": str(venv)})

    def run(self, repo: Path, argv: list[str], timeout: int) -> RunResult:
        cmd = self._resolve_interpreter(repo, argv)

        if not self.use_sandbox:
            return _exec(cmd, cwd=repo, timeout=timeout)

        tmp = Path(tempfile.gettempdir())
        profile = _SEATBELT_PROFILE.format(repo=repo.resolve(), tmp=tmp.resolve())
        with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as fh:
            fh.write(profile)
            profile_path = fh.name
        try:
            return _exec(["sandbox-exec", "-f", profile_path, *cmd], cwd=repo, timeout=timeout)
        finally:
            os.unlink(profile_path)


class DockerBackend:
    """Container isolation. Preferred when available.

    The container is created once per repository and kept alive across the
    install and the execute step. This is not an optimisation: `docker run --rm`
    starts a *fresh* container each time, so packages installed by `build_env`
    would be discarded before `run` ever saw them, and every repository would
    fail with ModuleNotFoundError no matter how well it was packaged. That bug
    produced a clean-looking "0% of papers execute" result before it was caught,
    which is precisely the kind of artefact this study exists to avoid.
    """

    name = "docker"

    #: Base image for every artefact container.
    #:
    #: `python:3.11-slim` alone ships neither `git` nor a C toolchain, so a
    #: repository whose requirements name a `git+https://` dependency, or whose
    #: wheel must be compiled, failed for a reason belonging to us rather than to
    #: the artefact. Six records in an earlier campaign were classified as
    #: dependency conflicts on exactly that basis. Since the study's own policy
    #: is that harness defects are excluded from claims about the literature, the
    #: image supplies what an ordinary reader's machine would have. It installs
    #: no Python package: what a repository can install is the measurement.
    DEFAULT_IMAGE = "exec-repro-base:1"

    def __init__(self, image: str = DEFAULT_IMAGE) -> None:
        if shutil.which("docker") is None:
            raise RuntimeError("docker not available")
        self.image = image
        self._containers: dict[str, str] = {}

    @staticmethod
    def _container_name(repo: Path) -> str:
        digest = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
        return f"exec_repro_{digest}"

    def _ensure_container(self, repo: Path) -> str:
        key = str(repo.resolve())
        if key in self._containers:
            return self._containers[key]
        name = self._container_name(repo)
        _exec(["docker", "rm", "-f", name], cwd=None, timeout=60)  # stale from a prior crash
        created = _exec(
            [
                "docker", "run", "-d", "--name", name,
                "--memory", "8g", "--cpus", "2",
                "-v", f"{repo.resolve()}:/work",
                "-w", "/work",
                self.image,
                "sleep", "infinity",
            ],
            cwd=None, timeout=180,
        )
        if not created.ok:
            raise RuntimeError(f"could not start container for {repo}: {created.stderr[:300]}")
        self._containers[key] = name
        self._assert_mount_visible(repo, name)
        return name

    def _assert_mount_visible(self, repo: Path, name: str) -> None:
        """Verify the repository is actually visible inside the container.

        Container runtimes on macOS (colima, Docker Desktop) only share selected
        host paths with their VM. A workdir outside those paths mounts as an
        empty directory, and every repository then looks as though it shipped no
        files at all: the corpus would silently fill with `no_dependency_spec`.
        That is a fabricated finding, so an invisible mount is a hard error.
        """
        host_entries = {p.name for p in repo.iterdir()}
        if not host_entries:
            return  # nothing to compare against
        listed = _exec(["docker", "exec", name, "ls", "-A", "/work"], cwd=None, timeout=60)
        seen = set(listed.stdout.split())
        if not seen & host_entries:
            raise RuntimeError(
                f"container mount is empty: {repo} is not visible at /work. The "
                "container runtime is probably not sharing this path with its VM. "
                "Use a workdir under your home directory, or extend the runtime's "
                "shared paths."
            )

    def _set_network(self, name: str, connected: bool) -> None:
        """Attach or detach the default bridge for this container."""
        verb = "connect" if connected else "disconnect"
        _exec(["docker", "network", verb, "bridge", name], cwd=None, timeout=60)

    def cleanup(self, repo: Path) -> None:
        key = str(repo.resolve())
        name = self._containers.pop(key, None)
        if name:
            _exec(["docker", "rm", "-f", name], cwd=None, timeout=120)

    def _exec_in(self, repo: Path, argv: list[str], timeout: int) -> RunResult:
        name = self._ensure_container(repo)
        return _exec(["docker", "exec", "-w", "/work", name, *argv], cwd=None, timeout=timeout)

    def can_install(self, spec: Path) -> bool:
        # A Dockerfile is installable in principle, but building it would change
        # the base image and make results incomparable across the corpus, so the
        # comparison is held fixed at the declared Python spec.
        return spec.name in PYTHON_INSTALLABLE

    def build_env(self, repo: Path, spec: Path | None, timeout: int) -> RunResult:
        if spec is None:
            return RunResult(0, "", "", 0.0)
        if not self.can_install(spec):
            raise ValueError(
                f"{self.name} cannot install {spec.name}; callers must check "
                "can_install() first"
            )
        # The spec is not always at the repository root: `find_dep_spec` also
        # looks one level down. The container mounts the repo at /work, so the
        # path handed to pip must be relative to the repo, not a bare filename.
        rel = spec.resolve().relative_to(repo.resolve())
        if spec.name == "requirements.txt":
            argv = ["pip", "install", "--no-cache-dir", "-r", str(rel)]
        else:  # pyproject.toml / setup.py -- install the directory containing it
            argv = ["pip", "install", "--no-cache-dir", "-e", str(rel.parent)]
        name = self._ensure_container(repo)
        self._set_network(name, True)  # the index must be reachable to install
        return self._exec_in(repo, argv, timeout)

    def run(self, repo: Path, argv: list[str], timeout: int) -> RunResult:
        name = self._ensure_container(repo)
        # Detach the network so a repository that fetches its data at runtime is
        # recorded as not self-contained rather than silently rescued by our
        # connectivity. stdin is closed, so a script that prompts fails fast
        # instead of blocking the campaign.
        self._set_network(name, False)
        return self._exec_in(repo, list(argv), timeout)


def select_backend(prefer_docker: bool = True) -> Backend:
    """Pick the strongest isolation available on this machine."""
    if prefer_docker and shutil.which("docker"):
        try:
            return DockerBackend()
        except RuntimeError:
            pass
    return UvSeatbeltBackend()
