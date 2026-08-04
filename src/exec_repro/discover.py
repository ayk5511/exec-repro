"""Build the corpus: find ML-finance papers and the code URLs they advertise.

The seed corpus (185 papers) yields only ~62 clonable URLs, which is too few to
say anything about a field. This module expands it from the arXiv API, which is
public, rate-limited politely, and gives us the two things the ladder needs: a
stable paper identity and whatever code URL the authors put in their abstract.

Two deliberate limitations, both reported in the paper rather than hidden:

  - We read the *abstract*, not the full text. A repository mentioned only in a
    footnote or a data-availability section is missed, so the URL-present rate
    measured here is a lower bound on true disclosure. The alternative (parsing
    every PDF) introduces its own extraction errors, and the ladder's later rungs
    are unaffected either way.
  - arXiv over-represents preprint-friendly subfields. The corpus is therefore
    described as "arXiv ML-finance", never as "the ML-finance literature".
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from .runner import _ssl_context, normalize_url

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"

#: Categories that carry quantitative-finance work. cs.LG is included only in
#: conjunction with a finance term, otherwise it swamps the corpus.
QFIN_CATEGORIES = ("q-fin.CP", "q-fin.ST", "q-fin.TR", "q-fin.PM", "q-fin.RM", "q-fin.MF")

#: Terms that mark a paper as machine-learning-based rather than purely analytic.
ML_TERMS = (
    "machine learning", "deep learning", "neural network", "random forest",
    "gradient boosting", "xgboost", "lightgbm", "lstm", "transformer",
    "reinforcement learning", "supervised learning", "convolutional",
    "large language model", "graph neural", "autoencoder", "attention mechanism",
)

#: Machine-learning categories that carry finance work without being indexed
#: under q-fin. These are enormous, so they are filtered by finance vocabulary
#: rather than harvested wholesale.
ML_CATEGORIES = ("cs.LG", "stat.ML", "econ.EM", "cs.CE")

#: Finance vocabulary used to pull the finance papers out of a general ML
#: category. Deliberately concrete: "market" or "risk" alone would admit most of
#: the machine-learning literature.
FINANCE_TERMS = (
    "stock", "equity", "portfolio", "volatility", "asset pricing", "trading",
    "financial market", "limit order book", "credit risk", "option pricing",
    "cryptocurrency", "hedging", "returns prediction", "return prediction",
    "financial time series", "risk management", "derivative pricing",
    "factor model", "backtest", "sharpe",
)

#: Code-hosting hosts we know how to clone from.
CODE_HOST_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)"
    r"/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+",
    re.I,
)

#: Phrases authors use when they claim code exists without giving a URL.
CLAIM_RE = re.compile(
    r"(code|implementation|source)\s+(is|are|will be|has been)?\s*"
    r"(available|released|provided|public|open[- ]sourced)",
    re.I,
)


@dataclass(slots=True)
class DiscoveredPaper:
    key: str
    title: str
    year: str
    categories: str
    code_url: str | None
    claims_code: bool
    abstract_chars: int
    #: Retained because rung-10 scoring compares emitted numbers against the
    #: paper's own numeric claims; a character count cannot support that.
    abstract: str = ""


class RateLimitError(RuntimeError):
    """The API refused the request for load reasons, not because of the query.

    Distinguished from a genuine empty result because the two mean opposite
    things: an empty page ends a harvest, whereas a refusal means the page was
    never seen and dropping it would silently bias the corpus toward whichever
    topics happened to be queried while the server was willing.
    """


#: Statuses that mean "come back later" rather than "no such thing".
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


def _fetch(url: str, timeout: int = 60, *, retries: int = 5, backoff: float = 8.0) -> bytes:
    """Fetch with exponential backoff on transient refusals."""
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url, headers={"User-Agent": "exec-repro/0.1 (research; contact via repository)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in _TRANSIENT_STATUS:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    raise RateLimitError(f"giving up on {url} after {retries} attempts: {last}")


def _extract_code_url(text: str) -> str | None:
    m = CODE_HOST_RE.search(text or "")
    if not m:
        return None
    url = normalize_url(m.group(0))
    # A bare host or a user page is not a repository.
    parts = urllib.parse.urlparse(url).path.strip("/").split("/")
    return url if len(parts) >= 2 and all(parts[:2]) else None


def _is_ml(text: str) -> bool:
    low = (text or "").lower()
    return any(t in low for t in ML_TERMS)


def _is_finance(text: str) -> bool:
    low = (text or "").lower()
    return any(t in low for t in FINANCE_TERMS)


def _relevance_filter(category: str):
    """Pick the filter appropriate to the category being harvested.

    A q-fin listing is already finance, so the question is whether the paper uses
    machine learning. A cs.LG listing is already machine learning, so the question
    is whether it is about finance. Applying the wrong test to either would let
    the corpus fill with irrelevant work.
    """
    if category.startswith("q-fin"):
        return _is_ml
    return lambda text: _is_finance(text) and _is_ml(text)


def search_arxiv(
    category: str,
    *,
    start: int = 0,
    batch: int = 100,
    timeout: int = 60,
) -> list[DiscoveredPaper]:
    """One page of results for a category, newest first."""
    query = urllib.parse.urlencode(
        {
            "search_query": f"cat:{category}",
            "start": start,
            "max_results": batch,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    keep = _relevance_filter(category)
    root = ET.fromstring(_fetch(f"{ARXIV_API}?{query}", timeout=timeout))
    out: list[DiscoveredPaper] = []
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = (entry.findtext(f"{ATOM}id") or "").rsplit("/", 1)[-1]
        key = raw_id.split("v")[0]
        title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
        summary = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
        published = entry.findtext(f"{ATOM}published") or ""
        cats = ",".join(
            c.attrib.get("term", "") for c in entry.findall(f"{ATOM}category")
        )
        blob = f"{title} {summary}"
        if not keep(blob):
            continue
        out.append(
            DiscoveredPaper(
                key=key,
                title=title[:300],
                year=published[:4],
                categories=cats,
                code_url=_extract_code_url(blob),
                claims_code=bool(CLAIM_RE.search(blob)) or _extract_code_url(blob) is not None,
                abstract_chars=len(summary),
                abstract=summary[:4000],
            )
        )
    return out


def search_arxiv_query(
    search_query: str,
    *,
    start: int = 0,
    batch: int = 100,
    timeout: int = 60,
    keep=None,
) -> list[DiscoveredPaper]:
    """One page for an arbitrary arXiv search expression.

    Scanning a general machine-learning category and filtering locally is
    hopelessly wasteful: a sample of cs.LG yielded one finance paper per hundred
    fetched. arXiv supports abstract-level search, so the finance restriction is
    pushed to the server and we retrieve only plausible candidates.
    """
    query = urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": start,
            "max_results": batch,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    root = ET.fromstring(_fetch(f"{ARXIV_API}?{query}", timeout=timeout))
    out: list[DiscoveredPaper] = []
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = (entry.findtext(f"{ATOM}id") or "").rsplit("/", 1)[-1]
        key = raw_id.split("v")[0]
        title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
        summary = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
        published = entry.findtext(f"{ATOM}published") or ""
        cats = ",".join(c.attrib.get("term", "") for c in entry.findall(f"{ATOM}category"))
        blob = f"{title} {summary}"
        if keep is not None and not keep(blob):
            continue
        out.append(
            DiscoveredPaper(
                key=key,
                title=title[:300],
                year=published[:4],
                categories=cats,
                code_url=_extract_code_url(blob),
                claims_code=bool(CLAIM_RE.search(blob)) or _extract_code_url(blob) is not None,
                abstract_chars=len(summary),
                abstract=summary[:4000],
            )
        )
    return out


def finance_ml_queries() -> list[str]:
    """Server-side queries pairing a finance topic with the ML categories.

    One query per topic rather than a single disjunction, because arXiv truncates
    long boolean expressions and a single query is capped at a few thousand
    results regardless of how broad it is.
    """
    cats = " OR ".join(f"cat:{c}" for c in ML_CATEGORIES)
    topics = [
        "stock return", "stock price", "portfolio optimization", "asset pricing",
        "volatility forecasting", "algorithmic trading", "limit order book",
        "credit risk", "option pricing", "cryptocurrency", "financial time series",
        "market microstructure", "factor investing", "hedging",
    ]
    return [f'({cats}) AND abs:"{t}"' for t in topics]


#: arXiv renders LaTeX submissions to HTML for most recent papers; ar5iv covers
#: much of the older backlog. Trying both is what makes full-text extraction
#: worth doing at all.
FULLTEXT_SOURCES = (
    "https://arxiv.org/html/{key}",
    "https://ar5iv.labs.arxiv.org/html/{key}",
)

#: Cheap tag-stripper. We only need URLs and the phrases around them, so a real
#: HTML parser would buy accuracy we do not use.
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    return _TAG_RE.sub(" ", text)


def fulltext_code_url(key: str, *, timeout: int = 45) -> tuple[str | None, bool, str]:
    """Look for a code URL in a paper's rendered full text.

    Returns (url, claims_code, source). Abstract-only extraction misses every
    repository that authors mention in a footnote, an appendix, or a data
    availability statement, which in this literature is most of them. Scanning
    the body turns the URL-present rate from a lower bound into a measurement.
    """
    refused = False
    for template in FULLTEXT_SOURCES:
        url = template.format(key=key)
        try:
            raw = _fetch(url, timeout=timeout)
        except RateLimitError:
            # The server refused us; we learned nothing about this paper. Marking
            # it "checked, no URL" would silently convert throttling into a
            # finding of non-disclosure.
            refused = True
            continue
        except Exception:  # noqa: BLE001 - absent HTML rendering is the common case
            continue
        text = _strip_html(raw)
        found = _extract_code_url(text)
        if found:
            return found, True, url
        if CLAIM_RE.search(text):
            return None, True, url
        return None, False, url
    if refused:
        raise RateLimitError(f"full text for {key} was refused, not absent")
    return None, False, ""


def enrich_from_fulltext(
    corpus_path: Path,
    *,
    pause_s: float = 1.5,
    limit: int | None = None,
    progress: bool = True,
) -> dict[str, int]:
    """Fill in code URLs for corpus entries that lack one, from full text.

    Rewrites the corpus file in place, recording `url_source` on every entry so
    the paper can report how many URLs came from abstracts versus full text.
    """
    rows = [json.loads(line) for line in corpus_path.open() if line.strip()]
    targets = [
        r for r in rows
        if not r.get("code_url")
        and not r.get("fulltext_checked")
        and re.match(r"^\d{4}\.\d{4,5}$", str(r.get("key", "")))
    ]
    if limit:
        targets = targets[:limit]
    if progress:
        print(f"full-text scan: {len(targets)} candidates of {len(rows)} papers", flush=True)

    by_key = {r["key"]: r for r in rows}
    stats = {"checked": 0, "url_found": 0, "claim_found": 0, "no_html": 0, "refused": 0}

    for i, row in enumerate(targets, 1):
        key = row["key"]
        try:
            found, claims, source = fulltext_code_url(key)
        except RateLimitError:
            # Leave `fulltext_checked` unset so a later pass retries this paper
            # rather than recording throttling as an absence of disclosure.
            stats["refused"] = stats.get("refused", 0) + 1
            time.sleep(30)
            continue
        target = by_key[key]
        target["fulltext_checked"] = True
        stats["checked"] += 1
        if found:
            target["code_url"] = found
            target["claims_code"] = True
            target["url_source"] = "fulltext"
            stats["url_found"] += 1
        elif claims:
            target["claims_code"] = True
            stats["claim_found"] += 1
        elif not source:
            stats["no_html"] += 1
        if progress and i % 25 == 0:
            print(
                f"  [{i}/{len(targets)}] +{stats['url_found']} urls, "
                f"+{stats['claim_found']} claims, {stats['no_html']} no-html",
                flush=True,
            )
        time.sleep(pause_s)

    with corpus_path.open("w") as fh:
        for r in rows:
            r.setdefault("url_source", "abstract" if r.get("code_url") else None)
            fh.write(json.dumps(r) + "\n")

    if progress:
        total_urls = sum(1 for r in rows if r.get("code_url"))
        print(
            f"\nfull-text scan complete: {stats}\n"
            f"  corpus now has {total_urls} code URLs of {len(rows)} papers "
            f"({100 * total_urls / max(len(rows), 1):.1f}%)",
            flush=True,
        )
    return stats


def build_corpus(
    out_path: Path,
    *,
    categories: tuple[str, ...] = QFIN_CATEGORIES + ML_CATEGORIES,
    per_category: int = 300,
    batch: int = 100,
    pause_s: float = 3.0,
    seed_path: Path | None = None,
    progress: bool = True,
) -> list[DiscoveredPaper]:
    """Harvest, de-duplicate against the seed corpus, and write JSONL."""
    seen: set[str] = set()
    merged: list[DiscoveredPaper] = []

    if seed_path and seed_path.exists():
        with seed_path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                d = json.loads(line)
                key = str(d["key"])
                seen.add(key)
                merged.append(
                    DiscoveredPaper(
                        key=key,
                        title=str(d.get("title", ""))[:300],
                        year=str(d.get("year", "")),
                        categories="seed",
                        code_url=normalize_url(d["code_url"]) if d.get("code_url") else None,
                        claims_code=bool(d.get("claims_code", False)),
                        abstract_chars=0,
                    )
                )
        if progress:
            print(f"seed: {len(merged)} papers", flush=True)

    for cat in categories:
        got = 0
        for start in range(0, per_category, batch):
            try:
                page = search_arxiv(cat, start=start, batch=batch)
            except Exception as exc:  # noqa: BLE001 - network flake must not abort
                if progress:
                    print(f"  {cat} start={start}: {type(exc).__name__}: {exc}", flush=True)
                break
            if not page:
                break
            for p in page:
                if p.key in seen:
                    continue
                seen.add(p.key)
                merged.append(p)
                got += 1
            time.sleep(pause_s)  # arXiv asks for >=3s between requests
        if progress:
            print(f"  {cat}: +{got} new (total {len(merged)})", flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        for p in merged:
            fh.write(json.dumps(asdict(p)) + "\n")

    if progress:
        with_url = sum(1 for p in merged if p.code_url)
        print(
            f"\ncorpus written: {out_path}\n"
            f"  papers        : {len(merged)}\n"
            f"  with code URL : {with_url} ({100 * with_url / max(len(merged), 1):.1f}%)",
            flush=True,
        )
    return merged
