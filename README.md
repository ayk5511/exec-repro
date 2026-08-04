# exec-repro

**Execution-based reproducibility measurement: run a paper's released code and record how far it gets.**

Most reproducibility scoring is *static*: does the paper claim code exists, and is a URL present? That is a property of the manuscript. This package measures the *dynamic* property: clone the artefact, build its environment, run it, and record the rung at which it stops.

## The Reproducibility Attrition Ladder

Eleven ordered, cumulative rungs. Reaching rung N implies every rung below it was cleared, so "percent clearing rung N or better" is unambiguous and the resulting curve is monotone.

| # | Rung | Meaning |
|---|---|---|
| 0 | `NO_CLAIM` | no code-availability claim |
| 1 | `CLAIM_ONLY` | claims code, gives no pointer |
| 2 | `URL_PRESENT` | a code URL exists |
| 3 | `URL_RESOLVES` | the URL still answers (no link rot) |
| 4 | `CLONED` | repository retrieved |
| 5 | `DEPS_DECLARED` | a dependency spec exists |
| 6 | `ENV_BUILDS` | those dependencies actually install |
| 7 | `ENTRYPOINT_FOUND` | something runnable is identifiable |
| 8 | `RUNS` | it executes to completion, exit 0 |
| 9 | `PRODUCES_OUTPUT` | it emitted artefacts or numbers |
| 10 | `MATCHES_PUBLISHED` | an emitted number matches the paper |

Rungs 0–9 are decided mechanically. Rung 10 requires reading the paper for a headline number, so it is scored on a manually curated subset and **never extrapolated** to the full corpus.

Failures are recorded against a **closed vocabulary** of 37 reasons grouped by blocking rung, so the failure mix is a reportable table rather than free text.

## Install

```bash
pip install -e ".[dev]"
```

## Use

```bash
# 1. build a corpus (arXiv q-fin, ML-filtered, merged with an existing seed)
exec-repro discover --out corpus/corpus.jsonl --seed corpus/seed_corpus.jsonl --per-category 300

# 2. walk it up the ladder (parallel, resumable)
exec-repro run --corpus corpus/corpus.jsonl --results results/ladder.jsonl \
               --workdir /Volumes/fast/scratch --only-with-url

# 3. aggregate into the paper's tables
exec-repro summarize --results results/ladder.jsonl
```

```python
from exec_repro import run_ladder, select_backend, attrition_curve

backend = select_backend()
results = [run_ladder(key, url, workdir, backend) for key, url in corpus]
print(attrition_curve(results))
```

## Isolation

We execute code written by people who never expected a stranger to run it, so two risks are handled separately:

- **Dependency isolation** — each repository gets its own virtualenv (via `uv`), so conflicting pins never interact.
- **Process isolation** — a container when Docker is available; otherwise macOS Seatbelt (`sandbox-exec`) confining writes to the repository and denying network during execution.

Network is *allowed* while installing and *denied* while running, by design: a repository that downloads its data at runtime is not self-contained, and that should surface as a failure rather than be masked by our connectivity.

The backend used is recorded on every result, so analyses can state the isolation regime honestly rather than implying containers throughout. **Docker is strongly recommended** for full-corpus runs — Seatbelt shares the kernel and user account.

## Reproducibility of this tool

```bash
PYTHONPATH=src python3 -m pytest tests/
```

The URL-normalisation suite exists because a regression there silently corrupts the headline number: one stray trailing period turns a live repository into a "404".

## Honest scope

- Code URLs are extracted from **abstracts**, not full texts, so the URL-present rate is a **lower bound** on true disclosure.
- arXiv over-represents preprint-friendly subfields; the corpus is "arXiv ML-finance", not "the ML-finance literature".
- A repository that fails to run is not necessarily wrong. It is not *independently executable as released*, which is the property being measured.

## Licence

MIT (see `LICENSE`).

## Trade-secret firewall

This repository contains only public methods, public data sources, and original analysis. No proprietary content from any private work appears here.
