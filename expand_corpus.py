"""Expand the corpus on two axes: depth within q-fin, and breadth into ML venues.

The first corpus was 590 papers drawn only from the six q-fin categories at 400
papers each, which left it both shallow (heavily weighted to the last two years,
because arXiv returns newest-first) and narrow (finance machine-learning work
indexed under cs.LG or stat.ML was invisible).

This script addresses both. It goes deeper into q-fin, and it adds the general
machine-learning categories through server-side topic queries rather than local
filtering: sampling cs.LG and discarding non-finance papers locally kept one
paper per hundred fetched, whereas an abstract-level query keeps roughly half.

Existing entries are never overwritten, so previously harvested abstracts, URLs
and full-text-check flags survive and the enrichment pass does not repeat work.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, "src")
from exec_repro.discover import (  # noqa: E402
    QFIN_CATEGORIES,
    RateLimitError,
    _is_ml,
    finance_ml_queries,
    search_arxiv,
    search_arxiv_query,
)

CORPUS = Path("corpus/corpus.jsonl")
QFIN_DEPTH = 1200          # per q-fin category
QUERY_DEPTH = 400          # per finance topic query
BATCH = 100
PAUSE = 5.0                # arXiv asks for >=3s; we allow more headroom


def load() -> tuple[list[dict], set[str]]:
    rows = [json.loads(line) for line in CORPUS.open() if line.strip()]
    return rows, {str(r["key"]) for r in rows}


def save(rows: list[dict]) -> None:
    tmp = CORPUS.with_suffix(".tmp")
    with tmp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    tmp.replace(CORPUS)


def add(rows: list[dict], seen: set[str], papers, source: str) -> int:
    added = 0
    for p in papers:
        if p.key in seen:
            continue
        seen.add(p.key)
        d = asdict(p)
        d["url_source"] = "abstract" if d.get("code_url") else None
        d["harvest"] = source
        rows.append(d)
        added += 1
    return added


def main() -> int:
    rows, seen = load()
    failures: list[tuple[str, int]] = []
    print(f"starting corpus: {len(rows)} papers, "
          f"{sum(1 for r in rows if r.get('code_url'))} with code URL\n", flush=True)

    print("=== deepening q-fin categories ===", flush=True)
    for cat in QFIN_CATEGORIES:
        got = 0
        for start in range(0, QFIN_DEPTH, BATCH):
            try:
                page = search_arxiv(cat, start=start, batch=BATCH)
            except RateLimitError as exc:
                # Never treat a refusal as the end of the listing: that would
                # silently truncate this category and bias the corpus.
                print(f"  {cat} @{start}: RATE LIMITED, backing off: {exc}", flush=True)
                failures.append((cat, start))
                time.sleep(120)
                continue
            except Exception as exc:  # noqa: BLE001 - a flake must not abort the harvest
                print(f"  {cat} @{start}: {type(exc).__name__}: {exc}", flush=True)
                break
            if not page:
                break
            got += add(rows, seen, page, f"qfin:{cat}")
            time.sleep(PAUSE)
        print(f"  {cat}: +{got} (total {len(rows)})", flush=True)
        save(rows)

    print("\n=== adding finance topics from ML categories ===", flush=True)
    for q in finance_ml_queries():
        topic = q.split('abs:"')[-1].rstrip('"')
        got = 0
        for start in range(0, QUERY_DEPTH, BATCH):
            try:
                page = search_arxiv_query(q, start=start, batch=BATCH, keep=_is_ml)
            except RateLimitError as exc:
                print(f"  {topic} @{start}: RATE LIMITED, backing off: {exc}", flush=True)
                failures.append((topic, start))
                time.sleep(120)
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"  {topic} @{start}: {type(exc).__name__}: {exc}", flush=True)
                break
            if not page:
                break
            got += add(rows, seen, page, f"mlcat:{topic}")
            time.sleep(PAUSE)
        print(f"  {topic:24} +{got} (total {len(rows)})", flush=True)
        save(rows)

    save(rows)
    if failures:
        print(f"\nWARNING: {len(failures)} page(s) never retrieved: {failures}\n"
              "The corpus is incomplete for those slices; re-run to fill them.",
              flush=True)
    with_url = sum(1 for r in rows if r.get("code_url"))
    print(f"\nfinal corpus: {len(rows)} papers, {with_url} with code URL "
          f"({100 * with_url / max(len(rows), 1):.1f}%)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
