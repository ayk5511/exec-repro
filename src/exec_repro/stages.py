"""The Reproducibility Attrition Ladder (RAL).

Prior work on reproducibility in finance (Khan 2026a, 2026c) scores *disclosure*:
does the paper say code exists, and is a URL present? That is a static property of
the manuscript. This module defines the *dynamic* scale: how far does the released
artefact actually get when you try to run it?

Each repository is pushed as far up the ladder as it will go. The rung at which it
stops, plus the machine-readable reason it stopped, is the unit of analysis. The
histogram of highest-rung-reached across the corpus is the attrition curve, which
is the paper's headline result.

Design constraints:
  - Rungs are strictly ordered and cumulative: reaching rung N implies every rung
    below N was cleared. This makes the curve monotone and lets us report
    "X% of papers clear rung N or better" without ambiguity.
  - Every rung except MATCHES_PUBLISHED is decidable mechanically, so the bulk of
    the corpus is scored without human judgement. The final rung requires reading
    the paper for a headline number, so it is scored on a manually-curated subset
    and reported separately (never extrapolated to the full corpus).
  - Failure reasons are a closed vocabulary, not free text, so that the failure
    mix at each rung is itself reportable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Rung(IntEnum):
    """How far a paper's released artefact got. Strictly ordered."""

    # --- manuscript-level (no execution needed) ---
    NO_CLAIM = 0            # paper makes no code-availability claim at all
    CLAIM_ONLY = 1          # claims code exists but gives no resolvable pointer
    URL_PRESENT = 2         # a code URL appears in the paper or its metadata

    # --- retrieval ---
    URL_RESOLVES = 3        # the URL answers (not 404 / not dead host): no link rot
    CLONED = 4              # repository retrieved to local disk

    # --- environment ---
    DEPS_DECLARED = 5       # some dependency spec exists (requirements/pyproject/env.yml/Dockerfile)
    ENV_BUILDS = 6          # declared dependencies actually resolve and install

    # --- execution ---
    ENTRYPOINT_FOUND = 7    # a runnable entry point is identifiable
    RUNS = 8                # entry point executes to completion, exit code 0, within budget
    PRODUCES_OUTPUT = 9     # execution emitted artefacts or numeric stdout

    # --- scientific claim ---
    MATCHES_PUBLISHED = 10  # an emitted number matches a headline number in the paper


#: Rungs at or above this require executing third-party code.
EXECUTION_THRESHOLD = Rung.ENV_BUILDS

#: The only rung that is not decidable mechanically.
MANUAL_RUNG = Rung.MATCHES_PUBLISHED


# A closed vocabulary of blocking reasons, grouped by the rung they block at.
# Keeping this closed (rather than free-text) is what makes the failure mix
# reportable as a table rather than an anecdote.
BLOCK_REASONS: dict[Rung, tuple[str, ...]] = {
    Rung.URL_PRESENT: (
        "no_code_statement",
        "claim_without_url",
        "url_in_paper_but_unparseable",
    ),
    Rung.URL_RESOLVES: (
        "http_404",
        "http_403_private",
        "dns_failure",
        "host_gone",
        "requires_login",
    ),
    Rung.CLONED: (
        "clone_auth_required",
        "clone_timeout",
        "clone_too_large",
        "not_a_repository",
    ),
    Rung.DEPS_DECLARED: (
        "no_dependency_spec",
        "spec_empty",
    ),
    Rung.ENV_BUILDS: (
        "dependency_resolution_conflict",
        "package_version_yanked",
        "package_not_found",
        "python_version_unsupported",
        "build_from_source_failed",
        "install_timeout",
        "non_python_toolchain_required",
        # Harness-attributable, not paper-attributable: the backend could not read
        # a spec the discovery step had already located (a path-handling bug).
        # Kept as a distinct code so these are excluded from claims about the
        # literature rather than silently inflating dependency failures.
        "spec_unreadable_by_backend",
    ),
    Rung.ENTRYPOINT_FOUND: (
        "no_entrypoint_identifiable",
        "entrypoint_ambiguous",
        "notebook_only_no_kernel_spec",
    ),
    Rung.RUNS: (
        "missing_data_file",
        "requires_paid_data_vendor",
        "requires_api_key",
        "hardcoded_absolute_path",
        "runtime_exception",
        "execution_timeout",
        "out_of_memory",
        "requires_gpu",
    ),
    Rung.PRODUCES_OUTPUT: (
        "ran_but_no_artefact",
        "output_unparseable",
    ),
    Rung.MATCHES_PUBLISHED: (
        "no_headline_number_in_paper",
        "number_outside_tolerance",
        "output_not_comparable",
    ),
}

#: Flat set for validation.
ALL_REASONS: frozenset[str] = frozenset(
    r for reasons in BLOCK_REASONS.values() for r in reasons
)


@dataclass(slots=True)
class StageOutcome:
    """Result of attempting a single rung."""

    rung: Rung
    cleared: bool
    reason: str | None = None        # from BLOCK_REASONS[rung] when not cleared
    detail: str = ""                 # free-text context, never used for aggregation
    duration_s: float = 0.0

    def __post_init__(self) -> None:
        if not self.cleared:
            if self.reason is None:
                raise ValueError(f"rung {self.rung.name} failed without a reason")
            allowed = BLOCK_REASONS.get(self.rung, ())
            if allowed and self.reason not in allowed:
                raise ValueError(
                    f"reason {self.reason!r} is not in the closed vocabulary for "
                    f"{self.rung.name}: {allowed}"
                )


@dataclass(slots=True)
class LadderResult:
    """The full attempt record for one paper's artefact."""

    key: str                              # corpus key, e.g. "GuKellyXiu2020"
    code_url: str | None = None
    stages: list[StageOutcome] = field(default_factory=list)
    #: How the entry point was identified ("convention" | "readme" | None).
    #: Recorded so the attrition curve can be reported with and without the
    #: documentation route, making the heuristic itself auditable.
    entrypoint_source: str | None = None
    #: Trailing stdout of a successful run. Rung 10 asks whether an emitted
    #: number matches the paper, which cannot be adjudicated from a summary
    #: string, so the actual output is retained for the human scorer.
    run_stdout: str = ""

    def record(self, outcome: StageOutcome) -> None:
        self.stages.append(outcome)

    @property
    def highest_rung(self) -> Rung:
        """Highest rung cleared. Rung 0 if nothing cleared."""
        cleared = [s.rung for s in self.stages if s.cleared]
        return max(cleared) if cleared else Rung.NO_CLAIM

    @property
    def blocked_at(self) -> Rung | None:
        """The first rung that failed, if any."""
        for s in self.stages:
            if not s.cleared:
                return s.rung
        return None

    @property
    def block_reason(self) -> str | None:
        for s in self.stages:
            if not s.cleared:
                return s.reason
        return None

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.stages)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "code_url": self.code_url,
            "entrypoint_source": self.entrypoint_source,
            "run_stdout": self.run_stdout[:8000],
            "highest_rung": int(self.highest_rung),
            "highest_rung_name": self.highest_rung.name,
            "blocked_at": self.blocked_at.name if self.blocked_at else None,
            "block_reason": self.block_reason,
            "total_duration_s": round(self.total_duration_s, 2),
            "stages": [
                {
                    "rung": int(s.rung),
                    "rung_name": s.rung.name,
                    "cleared": s.cleared,
                    "reason": s.reason,
                    "detail": s.detail[:500],
                    "duration_s": round(s.duration_s, 2),
                }
                for s in self.stages
            ],
        }


def attrition_curve(results: list[LadderResult]) -> dict[str, dict]:
    """Fraction of the corpus clearing each rung or better.

    This is the paper's headline table. Monotone non-increasing by construction,
    because the ladder is cumulative.
    """
    n = len(results)
    if n == 0:
        return {}
    curve: dict[str, dict] = {}
    for rung in Rung:
        count = sum(1 for r in results if r.highest_rung >= rung)
        curve[rung.name] = {
            "rung": int(rung),
            "n_clearing": count,
            "pct_clearing": round(100.0 * count / n, 1),
        }
    return curve


def failure_mix(results: list[LadderResult]) -> dict[str, dict[str, int]]:
    """Why artefacts stop, grouped by the rung they stop at."""
    mix: dict[str, dict[str, int]] = {}
    for r in results:
        blocked = r.blocked_at
        if blocked is None:
            continue
        bucket = mix.setdefault(blocked.name, {})
        reason = r.block_reason or "unspecified"
        bucket[reason] = bucket.get(reason, 0) + 1
    return mix
