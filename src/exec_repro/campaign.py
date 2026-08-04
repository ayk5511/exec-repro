"""Run the ladder across a whole corpus: parallel, resumable, crash-tolerant.

A full corpus run is hours of wall-clock and executes hundreds of untrusted
programs, so the driver assumes it *will* be interrupted:

  - Results append to JSONL after every repository, so a kill -9 costs at most
    one repo.
  - Restarting skips keys already present in the output file, so the campaign is
    resumable without bookkeeping.
  - Each repository runs in its own worker process. A segfault or OOM in
    third-party code kills that worker, not the campaign.
  - Per-repo wall-clock is bounded twice: once inside the runner (install and
    execute timeouts) and once here (a hard ceiling), because a repo can wedge in
    ways the inner timeouts miss.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

from .isolate import select_backend
from .runner import run_ladder
from .stages import LadderResult, Rung, StageOutcome, attrition_curve, failure_mix


@dataclass(slots=True)
class CorpusEntry:
    key: str
    code_url: str | None
    claims_code: bool = True
    title: str = ""
    year: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> CorpusEntry:
        return cls(
            key=str(d["key"]),
            code_url=(d.get("code_url") or None),
            claims_code=bool(d.get("claims_code", True)),
            title=str(d.get("title", "")),
            year=str(d.get("year", "")),
        )


def load_corpus(path: Path) -> list[CorpusEntry]:
    with path.open() as fh:
        return [CorpusEntry.from_dict(json.loads(line)) for line in fh if line.strip()]


def completed_keys(results_path: Path) -> set[str]:
    """Keys already written, so a resumed campaign does not redo work."""
    if not results_path.exists():
        return set()
    done: set[str] = set()
    with results_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError):
                # A partial final line from an interrupted write: ignore it and
                # let the key be retried rather than silently dropped.
                continue
    return done


def load_results(results_path: Path) -> list[LadderResult]:
    """Rehydrate results for aggregation. Stage detail is preserved verbatim."""
    out: list[LadderResult] = []
    if not results_path.exists():
        return out
    with results_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            r = LadderResult(
                key=d["key"],
                code_url=d.get("code_url"),
                entrypoint_source=d.get("entrypoint_source"),
                run_stdout=d.get("run_stdout", ""),
            )
            for s in d.get("stages", []):
                r.stages.append(
                    StageOutcome(
                        rung=Rung(s["rung"]),
                        cleared=bool(s["cleared"]),
                        reason=s.get("reason"),
                        detail=s.get("detail", ""),
                        duration_s=float(s.get("duration_s", 0.0)),
                    )
                )
            out.append(r)
    return out


#: Below this, a corpus run will run out of room part-way and leave a half-finished
#: results file. Cloning plus per-repo virtualenvs runs to a few GB per repository.
MIN_FREE_GB = 20.0


def _validate_workdir(workdir: Path) -> None:
    """Fail early and legibly on an unusable scratch directory.

    Campaigns are pointed at external volumes, which get unmounted between runs.
    Without this check `mkdir(parents=True)` tries to create the mount point
    itself and dies with a bare PermissionError hours into a session.
    """
    existing = workdir
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent

    # /Volumes/<name> missing means the drive is not mounted; creating a
    # directory there would silently write to the boot disk instead.
    parts = workdir.resolve().parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        mount = Path("/") / parts[1] / parts[2]
        if not mount.is_dir():
            raise RuntimeError(
                f"scratch volume {mount} is not mounted. Attach the drive, or pass "
                "a --workdir on an available filesystem."
            )

    if not os.access(existing, os.W_OK):
        raise RuntimeError(f"scratch directory {existing} is not writable")

    free_gb = shutil.disk_usage(existing).free / 1e9
    if free_gb < MIN_FREE_GB:
        raise RuntimeError(
            f"only {free_gb:.1f} GB free at {existing}; need at least {MIN_FREE_GB} GB "
            "for clones and per-repo virtualenvs"
        )


#: A results file touched more recently than this is probably still being
#: appended to by another campaign.
ACTIVE_WRITE_WINDOW_S = 120.0


def _warn_if_results_file_is_active(results_path: Path) -> None:
    """Refuse to resume onto a results file another campaign is still writing.

    Resuming reads the completed keys once, at startup. If the previous campaign
    is still flushing its last records, those keys are invisible and get run a
    second time, producing duplicate rows that double-count a paper in the
    attrition curve.
    """
    if not results_path.exists():
        return
    age = time.time() - results_path.stat().st_mtime
    if age < ACTIVE_WRITE_WINDOW_S:
        raise RuntimeError(
            f"{results_path} was modified {age:.0f}s ago and may still be receiving "
            "writes from another campaign. Wait for it to finish, or use a different "
            "--results path; resuming now would duplicate records."
        )


def _worker(args: tuple) -> dict:
    """Executed in a subprocess: one repository, one ladder walk."""
    (key, url, claims, workdir, install_timeout, exec_timeout,
     prefer_docker, use_readme) = args
    backend = select_backend(prefer_docker=prefer_docker)
    res = run_ladder(
        key, url, Path(workdir), backend,
        claims_code=claims,
        install_timeout=install_timeout,
        exec_timeout=exec_timeout,
        use_readme=use_readme,
    )
    d = res.to_dict()
    d["backend"] = backend.name
    return d


def run_campaign(
    corpus: Iterable[CorpusEntry],
    workdir: Path,
    results_path: Path,
    *,
    workers: int | None = None,
    install_timeout: int = 900,
    exec_timeout: int = 1800,
    hard_ceiling: int = 3600,
    prefer_docker: bool = True,
    use_readme: bool = True,
    resume: bool = True,
    progress: bool = True,
) -> Iterator[dict]:
    """Walk every corpus entry up the ladder, yielding each result as it lands."""
    _validate_workdir(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)

    _warn_if_results_file_is_active(results_path)

    entries = list(corpus)
    done = completed_keys(results_path) if resume else set()
    todo = [e for e in entries if e.key not in done]
    if progress:
        print(
            f"corpus={len(entries)} done={len(done)} todo={len(todo)} "
            f"workers={workers or (os.cpu_count() or 4) - 2}",
            flush=True,
        )

    # Leave headroom: third-party code is frequently multi-threaded and will
    # happily saturate every core it can see.
    n_workers = workers or max(1, (os.cpu_count() or 4) - 2)

    payloads = [
        (e.key, e.code_url, e.claims_code, str(workdir),
         install_timeout, exec_timeout, prefer_docker, use_readme)
        for e in todo
    ]

    started = time.monotonic()
    with results_path.open("a") as sink, ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_worker, p): p[0] for p in payloads}
        # Drain in completion order, not submission order. Iterating the
        # submission order blocks on a single slow repository while dozens of
        # finished ones sit unwritten, which both hides progress and defeats the
        # append-after-every-repo guarantee: a crash would discard everything
        # queued behind the straggler.
        for i, fut in enumerate(as_completed(futures, timeout=None), 1):
            key = futures[fut]
            try:
                record = fut.result(timeout=hard_ceiling)
            except FutureTimeout:
                record = _stalled_record(key, "execution_timeout",
                                         f"exceeded hard ceiling {hard_ceiling}s")
            except Exception as exc:  # a worker died on us
                record = _stalled_record(key, "runtime_exception",
                                         f"worker crashed: {type(exc).__name__}: {exc}")
            sink.write(json.dumps(record) + "\n")
            sink.flush()
            if progress:
                elapsed = time.monotonic() - started
                rate = i / max(elapsed / 60, 1e-9)
                eta = (len(payloads) - i) / rate if rate > 0 else 0
                print(
                    f"[{i}/{len(payloads)}] {key:20} -> {record['highest_rung_name']:18} "
                    f"({elapsed / 60:.1f}m elapsed, ~{eta:.0f}m left)",
                    flush=True,
                )
            yield record


def _stalled_record(key: str, reason: str, detail: str) -> dict:
    """Uniform record for a repo the harness could not finish.

    Recorded at RUNS, which is where a wedge realistically happens, and flagged
    `harness_incomplete` so these are separable from genuine RUNS failures during
    analysis instead of quietly inflating the failure count.
    """
    return {
        "key": key,
        "code_url": None,
        "highest_rung": int(Rung.ENTRYPOINT_FOUND),
        "highest_rung_name": Rung.ENTRYPOINT_FOUND.name,
        "blocked_at": Rung.RUNS.name,
        "block_reason": reason,
        "harness_incomplete": True,
        "total_duration_s": None,
        "stages": [],
        "detail": detail,
    }


def summarize(results_path: Path) -> dict:
    """Aggregate a completed (or partial) campaign into the paper's tables."""
    results = load_results(results_path)
    complete = [
        r for r in results
        if not any(s.detail.startswith("worker crashed") for s in r.stages)
    ]
    return {
        "n_total": len(results),
        "n_analysed": len(complete),
        "attrition_curve": attrition_curve(complete),
        "failure_mix": failure_mix(complete),
    }
