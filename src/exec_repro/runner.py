"""Push one repository as far up the attrition ladder as it will go.

The runner is deliberately conservative: every rung is attempted only if the one
below it cleared, every failure is mapped into the closed reason vocabulary of
`stages.BLOCK_REASONS`, and nothing raises. A crash in the harness would silently
bias the corpus toward "reproducible", so failures are always recorded as data.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .isolate import Backend, RunResult
from .stages import LadderResult, Rung, StageOutcome

# Dependency specs in the order we prefer them. requirements.txt first because it
# is the most common in this literature and the cheapest to install.
DEP_SPECS = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "environment.yml",
    "Dockerfile",
)

#: Filenames that, if present at the repo root, are treated as the entry point.
ENTRYPOINT_CANDIDATES = (
    "main.py",
    "run.py",
    "train.py",
    "experiment.py",
    "reproduce.py",
    "run_all.py",
    "demo.py",
)

#: Files that are Python but are never the research entry point. Running a build
#: script or a config module tells us nothing about whether the paper's analysis
#: reproduces, and it silently converts packaging noise (setuptools deprecation
#: warnings, for instance) into apparent execution failures.
NON_ENTRYPOINTS = frozenset({
    "setup.py",
    "conf.py",
    "config.py",
    "settings.py",
    "__init__.py",
    "__main__.py",
    "versioneer.py",
    "_version.py",
})

#: Directories whose contents are never the paper's analysis. Test suites and
#: example scripts exercise the code rather than producing the result, and
#: vendored dependencies are not the authors' code at all: two repositories in
#: this corpus shipped a copy of a Chinese-tokenisation library, and its bundled
#: `test/demo.py` was selected as the entry point, so the harness recorded the
#: library's demo output as though the paper's pipeline had run.
NON_ENTRYPOINT_DIRS = frozenset({
    "test", "tests", "testing",
    "example", "examples", "demo", "demos",
    "vendor", "vendored", "third_party", "thirdparty", "external", "extern",
    "node_modules", "site-packages", "dist-packages",
    "docs", "doc", "build",
})


def _in_excluded_dir(path: Path, repo: Path) -> bool:
    """Whether any directory component of `path` is one we never run from."""
    rel = path.relative_to(repo)
    return any(part.lower() in NON_ENTRYPOINT_DIRS for part in rel.parts[:-1])


#: Artefact extensions that count as "produced output" at rung 9.
ARTEFACT_SUFFIXES = {".csv", ".json", ".parquet", ".png", ".pdf", ".txt", ".pkl", ".npy"}

_NUMBER_RE = re.compile(r"-?\d+\.\d+|-?\d+")


class HarnessConfigError(RuntimeError):
    """The harness itself is misconfigured.

    Raised instead of recording a failed rung, because a broken harness would
    otherwise be silently written into the corpus as evidence about the papers.
    Fail loudly; never contaminate the measurement.
    """


#: Trailing characters that come from prose extraction, not from the URL itself.
#: Left uncleaned these produce spurious 404s and would overstate link rot, so
#: normalisation happens before any rung is scored and is reported as a
#: preprocessing step in the paper. Two of the three repositories affected in the
#: v5 campaign are live once the residue is removed; the third is genuinely gone
#: and stays counted as rot on its cleaned address.
_URL_TRAILING_JUNK = ".,;:)]}>'\"\\"

#: Characters that cannot appear unescaped in an http(s) URL, so their first
#: occurrence marks where markup residue begins rather than part of the address.
#: The dominant source is a LaTeX \href{url}{text} flattened during PDF text
#: extraction, which yields "https://github.com/org/repo}{GitHub". Trailing-only
#: stripping cannot remove this, because the string ends in a letter. Three
#: repositories in the v5 campaign were recorded as http_404 for this reason
#: alone and are live; not cutting here overstates link rot.
_URL_CUT_CHARS = "{}\\<>\"`| \t\n"

#: A sentence period fused to the following word, e.g. ".../DeepLearningQHedging.The".
#: A period is legal inside a repository name, so this cannot be stripped
#: unconditionally: the candidate is only adopted if it is verified to resolve
#: (see `repair_candidates`). Guessing here would understate link rot in exactly
#: the way an unstripped artefact overstates it.
_URL_FUSED_SENTENCE = re.compile(r"^(.*?)\.[A-Z][A-Za-z]*$")


def normalize_url(url: str) -> str:
    """Strip prose artefacts and canonicalise a code URL for retrieval.

    Handles the three patterns that dominate URLs scraped from paper text:
    a trailing sentence period, a wrapping bracket or quote, and GitHub web
    suffixes (`/tree/main`, `.git`, `/blob/...`) that are not clone targets.
    """
    u = url.strip()
    while u and u[0] in "([{<'\"\\":
        u = u[1:]
    # Cut at the first character that cannot occur in a URL. Must precede the
    # trailing-junk loop: "repo}{GitHub" ends in a letter, so trailing stripping
    # alone leaves the residue in place and the address 404s.
    cut = min((i for i, ch in enumerate(u) if ch in _URL_CUT_CHARS), default=len(u))
    u = u[:cut]
    while u and u[-1] in _URL_TRAILING_JUNK:
        u = u[:-1]
    u = u.rstrip("/")
    # Reduce a GitHub web URL to its clonable repository root.
    m = re.match(r"(https?://(?:www\.)?github\.com/[^/]+/[^/]+)", u, re.I)
    if m:
        u = m.group(1)
        if u.lower().endswith(".git"):
            u = u[:-4]
    return u


def repair_candidates(url: str) -> list[str]:
    """Lexically plausible corrections for a URL that did not resolve.

    Extraction defects and genuine link rot are indistinguishable by status
    code: both answer 404. They are different claims, though. Rot is a fact
    about the literature; a mangled address is a fact about our extractor, and
    reporting the second as the first inflates the headline number.

    Every candidate here is ambiguous by construction, so none is adopted on
    lexical grounds alone: the caller retrieves each and keeps one only if it
    resolves. That asymmetry is deliberate. An unstripped artefact overstates
    link rot; an over-eager strip understates it, which is the worse error in a
    paper arguing that artefacts decay.
    """
    out: list[str] = []
    m = _URL_FUSED_SENTENCE.match(url)
    if m and m.group(1).count("/") >= 4:
        out.append(m.group(1))
    return out


_T = TypeVar("_T")


def _timed(fn: Callable[..., _T], *args: object, **kwargs: object) -> tuple[_T, float]:
    """Call `fn`, returning its result and wall-clock duration.

    Generic so callers keep the real return type and do not need `assert
    isinstance(...)` to narrow it.
    """
    start = time.monotonic()
    out = fn(*args, **kwargs)
    return out, time.monotonic() - start


def _ssl_context() -> ssl.SSLContext:
    """A context with a real CA bundle.

    Some Python installations ship without a usable trust store, which makes
    every HTTPS request raise URLError. Left unhandled that is indistinguishable
    from link rot and would fabricate a 100% dead-link rate, so we pin certifi's
    bundle explicitly rather than relying on the interpreter's defaults.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def check_url(
    url: str, timeout: int = 20, *, retries: int = 3, backoff: float = 4.0
) -> tuple[bool, str | None, str]:
    """Rung 3: does the code URL still resolve? Detects link rot.

    A GET is used rather than a HEAD because several forges answer HEAD with 403
    or 405 even for public repositories; scoring those as dead would overstate
    link rot.

    Transport-level failures are retried. A single attempt conflates a dead
    domain with a momentary resolver hiccup on our own machine: in one campaign
    a repository was recorded as `dns_failure` that a parallel run resolved
    normally seconds later. Link rot is a claim about the world, so it is only
    recorded once the failure has proved repeatable. HTTP statuses are answers
    from a live server and are never retried.
    """
    last: tuple[bool, str | None, str] | None = None
    for attempt in range(retries):
        result = _check_url_once(url, timeout)
        cleared, reason, _ = result
        if cleared or reason not in _TRANSPORT_REASONS:
            return result
        last = result
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    return last if last is not None else (False, "host_gone", "no attempt made")


#: Failures that may be transient and therefore warrant a retry, as opposed to a
#: definite answer from a reachable server.
_TRANSPORT_REASONS = frozenset({"dns_failure", "host_gone"})


def _check_url_once(url: str, timeout: int) -> tuple[bool, str | None, str]:
    ctx = _ssl_context()
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; exec-repro/0.1)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = resp.getcode()
            if code and 200 <= code < 400:
                return True, None, f"http_{code}"
            return False, "http_404", f"http_{code}"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, "http_404", "http_404"
        if exc.code in (401, 403):
            return False, "http_403_private", f"http_{exc.code}"
        if exc.code == 405:  # method rejected; the resource itself exists
            return True, None, "http_405_method_not_allowed"
        if exc.code == 429:  # rate limited by the forge, not a dead link
            return True, None, "http_429_rate_limited"
        return False, "http_404", f"http_{exc.code}"
    except urllib.error.URLError as exc:
        # URLError is a catch-all: TLS misconfiguration on *our* side, a genuine
        # DNS failure, and a refused connection all land here. They mean very
        # different things for the paper, so classify rather than lump.
        reason_text = str(getattr(exc, "reason", exc))
        low = reason_text.lower()
        if "certificate" in low or "ssl" in low or "tls" in low:
            raise HarnessConfigError(
                f"TLS verification failed for {url}: {reason_text}. This is a harness "
                "misconfiguration, not link rot; install certifi or set SSL_CERT_FILE."
            ) from exc
        if "name or service not known" in low or "nodename nor servname" in low or "getaddrinfo" in low:
            return False, "dns_failure", f"urlerror: {reason_text}"
        return False, "host_gone", f"urlerror: {reason_text}"
    except (TimeoutError, OSError) as exc:
        return False, "host_gone", f"{type(exc).__name__}: {exc}"


def clone(url: str, dest: Path, timeout: int = 600, depth: int = 0) -> tuple[bool, str | None, str]:
    """Rung 4: clone the repository. `depth=0` means a full clone.

    Cloning shallow is the obvious optimisation and it silently corrupted the
    measurement. `git clone --depth 1` fetches no tags, and a package whose
    version is derived by `setuptools-scm` then fails to install with
    "unable to detect version", which the installer reports as a dependency
    problem. Seven repositories in the v5 campaign were recorded as failing to
    build for that reason alone. The failure belonged to our clone flag, not to
    the artefact, and the study's own policy is that harness defects are excluded
    from claims about the literature.

    A reader following the paper's URL clones normally and gets the tags, so
    that is what we do. The cost is bandwidth; each repository is deleted after
    scoring, so peak disk is unchanged.
    """
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git is None:
        raise HarnessConfigError("git not found on PATH; cannot clone repositories")
    try:
        proc = subprocess.run(
            [git, "clone", *(["--depth", str(depth)] if depth else []),
             "--quiet", url, str(dest)],
            capture_output=True, text=True, timeout=timeout, check=False,
            # Inherit the environment so git finds its helpers, but never let it
            # prompt for credentials: a private repo must fail fast rather than
            # hang the campaign waiting on stdin.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"},
        )
    except subprocess.TimeoutExpired:
        return False, "clone_timeout", f"timeout after {timeout}s"
    if proc.returncode == 0:
        return True, None, "cloned"
    err = (proc.stderr or "").lower()
    if "authentication" in err or "could not read username" in err or "permission denied" in err:
        return False, "clone_auth_required", proc.stderr[:400]
    if "not found" in err or "repository" in err and "does not exist" in err:
        return False, "not_a_repository", proc.stderr[:400]
    return False, "not_a_repository", proc.stderr[:400]


def find_dep_spec(repo: Path) -> Path | None:
    """Rung 5: locate a dependency declaration, root first then one level down."""
    for name in DEP_SPECS:
        candidate = repo / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    for name in DEP_SPECS:
        for candidate in sorted(repo.glob(f"*/{name}")):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


#: README filenames, in the order we try them.
README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")

#: Commands that appear in a README but set up an environment rather than run
#: the analysis. Treating one of these as the entry point would measure
#: installation a second time.
#: Make targets that exercise or package the code rather than producing the
#: paper's result. Excluded on the same principle as a documented `pytest`
#: invocation: running a repository's tests is not running its analysis.
_NON_ANALYSIS_MAKE_TARGETS = frozenset({
    "test", "tests", "check", "lint", "format", "fmt", "typecheck",
    "build", "dist", "clean", "install", "uninstall", "docs", "doc",
    "help", "all", "release", "publish", "coverage",
})

_SETUP_COMMAND_MARKERS = (
    "pip install", "pip3 install", "conda create", "conda install", "conda env",
    "git clone", "apt-get", "brew install", "npm install", "poetry install",
    "uv pip", "virtualenv", "python -m venv", "python3 -m venv", "docker build",
    "docker run", "make install", "pytest", "python -m pytest", "cd ",
)

#: Interpreters we are willing to invoke from a README.
_RUNNABLE_HEADS = ("python", "python3", "bash", "sh", "make")

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


@dataclass(slots=True)
class Entrypoint:
    """A runnable command plus how we came to choose it.

    `source` is recorded because the two discovery routes have very different
    standing. A conventionally named script is found without reading anything a
    human wrote; a README command reflects the authors' own instructions. Keeping
    them distinct lets the paper report the attrition curve both ways, which
    turns a heuristic choice into a measured sensitivity.
    """

    argv: list[str]
    source: str  # "convention" | "readme"
    display: str


def _readme_text(repo: Path) -> str:
    for name in README_NAMES:
        p = repo / name
        if p.is_file():
            try:
                return p.read_text(errors="replace")
            except OSError:
                return ""
    for p in sorted(repo.glob("*")):
        if p.is_file() and p.name.lower().startswith("readme"):
            try:
                return p.read_text(errors="replace")
            except OSError:
                return ""
    return ""


def _candidate_commands(readme: str) -> list[str]:
    """Shell commands from fenced blocks, in document order."""
    out: list[str] = []
    for block in _FENCE_RE.findall(readme):
        for raw in block.splitlines():
            line = raw.strip().lstrip("$").strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)
    return out


def entrypoint_from_readme(repo: Path) -> Entrypoint | None:
    """Rung 7, documentation route: the command the authors tell you to run.

    Restricted to commands whose target actually exists in the repository, so a
    README that documents an aspirational script does not count as a runnable
    entry point.
    """
    commands = _candidate_commands(_readme_text(repo))
    for line in commands:
        low = line.lower()
        if any(m in low for m in _SETUP_COMMAND_MARKERS):
            continue
        parts = line.split()
        if not parts or parts[0] not in _RUNNABLE_HEADS:
            continue
        if "&&" in parts or "|" in parts:
            continue  # compound shell pipelines are not reproducibly parseable

        if parts[0] in {"python", "python3"}:
            if len(parts) >= 3 and parts[1] == "-m":
                module = parts[2]
                pkg = module.split(".")[0]
                if (repo / pkg).is_dir() or (repo / f"{pkg}.py").is_file():
                    return Entrypoint(["python", "-m", module], "readme", line)
                continue
            target = next((p for p in parts[1:] if p.endswith(".py")), None)
            if (
                target
                and (repo / target).is_file()
                and Path(target).name not in NON_ENTRYPOINTS
                and not _in_excluded_dir(repo / target, repo)
            ):
                return Entrypoint(["python", target], "readme", line)
            continue

        if parts[0] in {"bash", "sh"}:
            target = next((p for p in parts[1:] if p.endswith(".sh")), None)
            if target and (repo / target).is_file():
                return Entrypoint([parts[0], target], "readme", line)
            continue

        if parts[0] == "make":
            # A bare `make` runs whatever the Makefile's first target happens to
            # be, which contradicts the rule stated just above: the command must
            # name a target. In one corpus repository the default target printed
            # the Makefile's own usage message, and the harness recorded that as
            # the artefact producing output. Require an explicit target, require
            # it to be declared in the Makefile, and refuse the housekeeping
            # targets for the same reason a documented `pytest` is refused.
            makefile = repo / "Makefile"
            if len(parts) < 2 or not makefile.is_file():
                continue
            target = parts[1]
            if target in _NON_ANALYSIS_MAKE_TARGETS:
                continue
            declared = re.compile(rf"^{re.escape(target)}\s*:", re.M)
            if not declared.search(makefile.read_text(errors="replace")):
                continue
            return Entrypoint(["make", target], "readme", line)
    return None


def entrypoint_by_convention(repo: Path) -> Entrypoint | None:
    """Rung 7, convention route: a conventionally named script.

    Build scripts and config modules are excluded: executing `setup.py` exercises
    the packaging toolchain rather than the paper's analysis, and its output
    (typically a setuptools deprecation warning) would be recorded as though the
    research code had failed.
    """
    def _mk(path: Path) -> Entrypoint:
        rel = path.relative_to(repo)
        return Entrypoint(["python", str(rel)], "convention", str(rel))

    for name in ENTRYPOINT_CANDIDATES:
        candidate = repo / name
        if candidate.is_file() and name not in NON_ENTRYPOINTS:
            return _mk(candidate)
    for name in ENTRYPOINT_CANDIDATES:
        matches = sorted(
            p for p in repo.glob(f"*/{name}")
            if p.name not in NON_ENTRYPOINTS and not _in_excluded_dir(p, repo)
        )
        if matches:
            return _mk(matches[0])
    top_level = [
        p for p in repo.glob("*.py")
        if not p.name.startswith("_") and p.name not in NON_ENTRYPOINTS
    ]
    if len(top_level) == 1:
        return _mk(top_level[0])
    return None


def find_entrypoint(repo: Path, *, use_readme: bool = True) -> Entrypoint | None:
    """Rung 7: identify something runnable.

    Convention is tried first because it needs no interpretation of prose; the
    README is consulted only when that fails. `use_readme=False` reproduces the
    conservative behaviour, which the paper reports alongside as a sensitivity
    check on this very heuristic.
    """
    found = entrypoint_by_convention(repo)
    if found is not None:
        return found
    if use_readme:
        return entrypoint_from_readme(repo)
    return None


def snapshot_artefacts(repo: Path) -> set[Path]:
    return {
        p for p in repo.rglob("*")
        if p.is_file()
        and p.suffix.lower() in ARTEFACT_SUFFIXES
        and ".exec_repro_venv" not in p.parts
        and ".git" not in p.parts
    }


def run_ladder(
    key: str,
    code_url: str | None,
    workdir: Path,
    backend: Backend,
    *,
    claims_code: bool = True,
    install_timeout: int = 900,
    exec_timeout: int = 1800,
    keep_repo: bool = False,
    use_readme: bool = True,
) -> LadderResult:
    """Walk one paper's artefact up the ladder, recording every rung."""
    result = LadderResult(key=key, code_url=code_url)

    # ---- Rung 2: is there a URL at all? -----------------------------------
    if not code_url:
        reason = "claim_without_url" if claims_code else "no_code_statement"
        result.record(StageOutcome(Rung.URL_PRESENT, False, reason))
        return result
    code_url = normalize_url(code_url)
    result.code_url = code_url
    if not code_url.lower().startswith(("http://", "https://")):
        result.record(
            StageOutcome(Rung.URL_PRESENT, False, "url_in_paper_but_unparseable", code_url[:200])
        )
        return result
    result.record(StageOutcome(Rung.URL_PRESENT, True))

    # ---- Rung 3: link rot --------------------------------------------------
    (ok, reason, detail), dt = _timed(check_url, code_url)
    if not ok and reason == "http_404":
        # Before recording rot, test whether the address is merely mangled.
        # Only a candidate that actually resolves is adopted, so this can
        # convert a false 404 into a live repository but can never manufacture
        # one: an unresolvable candidate leaves the original verdict standing.
        for candidate in repair_candidates(code_url):
            (c_ok, c_reason, c_detail), c_dt = _timed(check_url, candidate)
            if c_ok:
                result.url_repaired_from = code_url
                code_url = candidate
                result.code_url = candidate
                ok, reason, detail, dt = c_ok, c_reason, c_detail, dt + c_dt
                break
    result.record(StageOutcome(Rung.URL_RESOLVES, ok, reason, detail, dt))
    if not ok:
        return result

    # ---- Rung 4: clone -----------------------------------------------------
    repo = workdir / key
    (ok, reason, detail), dt = _timed(clone, code_url, repo)
    result.record(StageOutcome(Rung.CLONED, ok, reason, detail, dt))
    if not ok:
        return result

    try:
        # ---- Rung 5: dependency declaration --------------------------------
        spec, dt = _timed(find_dep_spec, repo)
        if spec is None:
            result.record(StageOutcome(Rung.DEPS_DECLARED, False, "no_dependency_spec", "", dt))
            return result
        result.record(StageOutcome(Rung.DEPS_DECLARED, True, None, spec.name, dt))

        # ---- Rung 6: environment builds ------------------------------------
        if not backend.can_install(spec):
            # A declaration this backend cannot materialise (conda env, Dockerfile).
            # Recorded as an honest non-clearance attributed to the toolchain, not
            # silently passed off as a successful build.
            result.record(
                StageOutcome(
                    Rung.ENV_BUILDS, False, "non_python_toolchain_required",
                    f"spec={spec.name}; backend={backend.name}",
                )
            )
            return result
        build, dt = _timed(backend.build_env, repo, spec, install_timeout)
        if not build.ok:
            result.record(
                StageOutcome(
                    Rung.ENV_BUILDS, False,
                    _classify_install_failure(build), build.stderr[-1500:], dt,
                )
            )
            return result
        result.record(StageOutcome(Rung.ENV_BUILDS, True, None, spec.name, dt))

        # ---- Rung 7: entry point -------------------------------------------
        entry, dt = _timed(find_entrypoint, repo, use_readme=use_readme)
        if entry is None:
            result.record(
                StageOutcome(Rung.ENTRYPOINT_FOUND, False, "no_entrypoint_identifiable", "", dt)
            )
            return result
        result.entrypoint_source = entry.source
        result.record(
            StageOutcome(
                Rung.ENTRYPOINT_FOUND, True, None, f"{entry.source}:{entry.display}", dt
            )
        )

        # ---- Rung 8: it runs ------------------------------------------------
        before = snapshot_artefacts(repo)
        run, dt = _timed(backend.run, repo, entry.argv, exec_timeout)
        if not run.ok:
            result.record(
                StageOutcome(Rung.RUNS, False, _classify_run_failure(run), run.stderr[-1500:], dt)
            )
            return result
        result.run_stdout = (run.stdout or "")[-8000:]
        result.record(StageOutcome(Rung.RUNS, True, None, "", dt))

        # ---- Rung 9: it produced something ----------------------------------
        after = snapshot_artefacts(repo)
        new_files = after - before
        has_numeric_stdout = bool(_NUMBER_RE.search(run.stdout or ""))
        if new_files or has_numeric_stdout:
            detail = f"{len(new_files)} new artefact(s); numeric_stdout={has_numeric_stdout}"
            result.record(StageOutcome(Rung.PRODUCES_OUTPUT, True, None, detail))
        else:
            result.record(
                StageOutcome(Rung.PRODUCES_OUTPUT, False, "ran_but_no_artefact", "")
            )
        # Rung 10 (MATCHES_PUBLISHED) is scored manually against the paper's
        # headline number; the harness never asserts it.
        return result
    finally:
        # Release backend resources (a live container, for instance) before the
        # directory disappears, otherwise containers leak for the whole campaign.
        try:
            backend.cleanup(repo)
        except Exception:  # noqa: BLE001 - cleanup must never mask a result
            pass
        if not keep_repo:
            shutil.rmtree(repo, ignore_errors=True)


def _classify_install_failure(r: RunResult) -> str:
    if r.timed_out:
        return "install_timeout"
    blob = f"{r.stdout}\n{r.stderr}".lower()
    # Check harness-attributable failures first. If the installer cannot open a
    # spec that discovery already found on disk, that is our path handling, not
    # the paper's dependencies, and must never be counted against the paper.
    if "could not open requirements file" in blob or "is not a valid editable requirement" in blob:
        return "spec_unreadable_by_backend"
    if "no matching distribution" in blob or "could not find a version" in blob:
        return "package_not_found"
    if "yanked" in blob:
        return "package_version_yanked"
    if "requires-python" in blob or "requires a different python" in blob:
        return "python_version_unsupported"
    if "resolutionimpossible" in blob or "conflict" in blob or "incompatible" in blob:
        return "dependency_resolution_conflict"
    if "failed building wheel" in blob or "metadata-generation-failed" in blob:
        return "build_from_source_failed"
    return "dependency_resolution_conflict"


def _classify_run_failure(r: RunResult) -> str:
    if r.timed_out:
        return "execution_timeout"
    blob = f"{r.stdout}\n{r.stderr}".lower()
    if "filenotfounderror" in blob or "no such file or directory" in blob:
        # Distinguish a hardcoded local path from a genuinely absent data file.
        if re.search(r"/(users|home)/[a-z0-9_.-]+/", blob):
            return "hardcoded_absolute_path"
        return "missing_data_file"
    if "api key" in blob or "apikey" in blob or "unauthorized" in blob:
        return "requires_api_key"
    if any(v in blob for v in ("bloomberg", "refinitiv", "wrds", "datastream", "eikon")):
        return "requires_paid_data_vendor"
    if "memoryerror" in blob or "out of memory" in blob or "killed" in blob:
        return "out_of_memory"
    if "cuda" in blob or "no gpu" in blob or "nvidia" in blob:
        return "requires_gpu"
    # Network is denied during execution by design, so any attempt to reach the
    # outside world means the artefact fetches its inputs at run time and is not
    # self-contained. These strings are what that looks like across requests,
    # urllib, curl, and yfinance.
    network_markers = (
        "urlerror", "connection", "network is unreachable", "could not resolve host",
        "temporary failure in name resolution", "nodename nor servname",
        "max retries exceeded", "connectionerror", "failed to establish a new connection",
        "name or service not known", "no address associated with hostname",
    )
    if any(m in blob for m in network_markers):
        return "missing_data_file"
    return "runtime_exception"


def write_results(results: list[LadderResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in results:
            fh.write(json.dumps(r.to_dict()) + "\n")
