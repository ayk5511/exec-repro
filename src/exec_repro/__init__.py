"""exec-repro: execution-based reproducibility measurement for research artefacts.

Disclosure-based reproducibility scoring asks whether a paper released code.
This package asks the harder question: run it, and see how far it gets.

    from exec_repro import run_ladder, select_backend, attrition_curve

    backend = select_backend()
    results = [run_ladder(k, url, workdir, backend) for k, url in corpus]
    print(attrition_curve(results))
"""
from .campaign import CorpusEntry, load_corpus, load_results, run_campaign, summarize
from .discover import build_corpus, search_arxiv
from .isolate import Backend, DockerBackend, RunResult, UvSeatbeltBackend, select_backend
from .runner import HarnessConfigError, normalize_url, run_ladder, write_results
from .stages import (
    BLOCK_REASONS,
    LadderResult,
    Rung,
    StageOutcome,
    attrition_curve,
    failure_mix,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Backend",
    "BLOCK_REASONS",
    "CorpusEntry",
    "DockerBackend",
    "HarnessConfigError",
    "LadderResult",
    "RunResult",
    "Rung",
    "StageOutcome",
    "UvSeatbeltBackend",
    "attrition_curve",
    "build_corpus",
    "failure_mix",
    "load_corpus",
    "load_results",
    "normalize_url",
    "run_campaign",
    "run_ladder",
    "search_arxiv",
    "select_backend",
    "summarize",
    "write_results",
]
