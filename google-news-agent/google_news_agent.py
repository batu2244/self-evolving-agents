#!/usr/bin/env python3
"""Collect recent Google News articles about Alphabet, analyze them with Pioneer,
summarize them with Google Gemini, and print valid JSON to stdout.

All logging goes to stderr so stdout is always parseable JSON.

Usage:
    python google_news_agent.py
    python google_news_agent.py --hours 24 --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("google_news_agent")

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

QUERIES: list[str] = [
    "Alphabet",
    "Google stock",
    "GOOGL",
    "GOOG",
    "Google earnings",
    "Google Gemini",
    "Google Cloud",
    "Google advertising",
    "YouTube",
    "Waymo",
    "Google antitrust",
]

CATEGORIES: list[str] = [
    "Earnings",
    "Advertising",
    "Google Search",
    "Gemini and AI",
    "Google Cloud",
    "YouTube",
    "Waymo",
    "Regulation and antitrust",
    "Legal",
    "Product launch",
    "Competition",
    "Leadership",
    "Other",
]

BUSINESS_UNITS: list[str] = [
    "Google Search",
    "Google Ads",
    "Google Cloud",
    "YouTube",
    "Android",
    "Google DeepMind",
    "Waymo",
    "Other Bets",
    "Alphabet corporate",
]

SIGNIFICANCE_LABELS: dict[str, float] = {
    "major": 1.0,
    "moderate": 0.6,
    "minor": 0.25,
}

SENTIMENT_LABELS = ["negative", "neutral", "positive"]

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RETRIES = 3
PIONEER_CONCURRENCY = 4
GEMINI_CONCURRENCY = 4


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class Article(BaseModel):
    title: str
    source: str
    published_at: datetime
    url: str
    description: str = ""
    query: str


class PioneerAnalysis(BaseModel):
    is_relevant: bool
    significance_score: float = Field(ge=0.0, le=1.0)
    sentiment: str
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    category: str
    affected_business_units: list[str] = Field(default_factory=list)


class GeminiSummary(BaseModel):
    summary: str
    why_it_matters: str


# --------------------------------------------------------------------------
# Step 1: collect Google News RSS
# --------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_title(title: str) -> str:
    # Google News appends " - Publisher" to most headlines; drop it before comparing.
    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title or "")
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def _normalize_url(url: str) -> str:
    parsed = urlparse(url or "")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _entry_published(entry: Any) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


async def _fetch_feed(client: httpx.AsyncClient, query: str, hours: int) -> list[Article]:
    """Fetch one Google News RSS search feed. Returns [] on failure."""
    params = {
        "q": f"{query} when:{max(1, hours // 24) if hours >= 24 else 1}d",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(GOOGLE_NEWS_RSS, params=params)
            resp.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001 - one bad feed must not kill the run
            log.warning("RSS fetch failed for %r (attempt %d/%d): %s", query, attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                return []
            await asyncio.sleep(1.5 * attempt)

    feed = feedparser.parse(resp.content)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles: list[Article] = []
    for entry in feed.entries:
        published = _entry_published(entry)
        if published is None or published < cutoff:
            continue
        raw_title = _strip_html(entry.get("title", ""))
        source = ""
        if getattr(entry, "source", None) is not None:
            source = getattr(entry.source, "title", "") or ""
        if not source and " - " in raw_title:
            source = raw_title.rsplit(" - ", 1)[1]
        source = source or "Unknown"
        # Google News appends " - Publisher" to headlines; drop it when it is the source.
        title = raw_title
        if source != "Unknown" and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        try:
            articles.append(
                Article(
                    title=title,
                    source=source or "Unknown",
                    published_at=published,
                    url=entry.get("link", ""),
                    description=_strip_html(entry.get("summary", "")),
                    query=query,
                )
            )
        except ValidationError as exc:
            log.warning("Skipping malformed entry for %r: %s", query, exc)
    log.info("query=%r articles=%d", query, len(articles))
    return articles


def _dedupe(articles: Iterable[Article]) -> list[Article]:
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        title_key = _normalize_title(article.title)
        url_key = _normalize_url(article.url)
        if (title_key and title_key in seen_titles) or (url_key and url_key in seen_urls):
            continue
        seen_titles.add(title_key)
        seen_urls.add(url_key)
        unique.append(article)
    return unique


async def collect_articles(hours: int) -> list[Article]:
    headers = {"User-Agent": "DeltaDesk-GoogleNewsAgent/1.0 (RSS reader)"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        results = await asyncio.gather(*(_fetch_feed(client, q, hours) for q in QUERIES))
    collected = [a for batch in results for a in batch]
    unique = _dedupe(collected)
    log.info("collected=%d unique=%d", len(collected), len(unique))
    return unique


# --------------------------------------------------------------------------
# Step 2: analyze with Pioneer
# --------------------------------------------------------------------------


def _pioneer_schema() -> dict[str, Any]:
    """Unified encoder schema per https://docs.pioneer.ai/api-reference/inference/pioneer."""
    return {
        "classifications": [
            {
                "task": "alphabet_relevance",
                "labels": ["relevant to Alphabet", "not relevant to Alphabet"],
                "multi_label": False,
                "top_k": 1,
            },
            {
                "task": "significance",
                "labels": list(SIGNIFICANCE_LABELS.keys()),
                "multi_label": False,
                "top_k": 1,
            },
            {
                "task": "sentiment",
                "labels": SENTIMENT_LABELS,
                "multi_label": False,
                "top_k": 1,
            },
            {
                "task": "category",
                "labels": CATEGORIES,
                "multi_label": False,
                "top_k": 1,
            },
            {
                "task": "affected_business_units",
                "labels": BUSINESS_UNITS,
                "multi_label": True,
            },
        ]
    }


def _iter_predictions(result: Any) -> dict[str, list[tuple[str, float]]]:
    """Normalize Pioneer's encoder `result` into {task: [(label, score), ...]}.

    The response shape for classification heads is not pinned down in the public
    docs, so accept the plausible variants rather than guessing one.
    """
    heads: dict[str, list[tuple[str, float]]] = {}

    def add(task: str, label: Any, score: Any) -> None:
        if not isinstance(label, str):
            return
        try:
            value = float(score)
        except (TypeError, ValueError):
            value = 1.0
        heads.setdefault(task, []).append((label, value))

    def absorb(task: str, payload: Any) -> None:
        if isinstance(payload, str):
            add(task, payload, 1.0)
        elif isinstance(payload, dict):
            if "label" in payload:
                add(task, payload.get("label"), payload.get("score", payload.get("confidence", 1.0)))
            else:  # {"positive": 0.9, "negative": 0.1}
                for label, score in payload.items():
                    add(task, label, score)
        elif isinstance(payload, list):
            for item in payload:
                absorb(task, item)

    if isinstance(result, list):  # batch response -> first element
        result = result[0] if result else {}
    if not isinstance(result, dict):
        return heads

    node = result.get("classifications", result)
    if isinstance(node, dict):
        for task, payload in node.items():
            if task in {"entities", "structures", "relations"}:
                continue
            absorb(task, payload)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and "task" in item:
                task = item["task"]
                if "labels" in item:
                    absorb(task, item["labels"])
                else:
                    absorb(task, item)
    return heads


def _top(heads: dict[str, list[tuple[str, float]]], task: str) -> tuple[str | None, float]:
    ranked = sorted(heads.get(task, []), key=lambda pair: pair[1], reverse=True)
    return (ranked[0][0], ranked[0][1]) if ranked else (None, 0.0)


def _to_analysis(heads: dict[str, list[tuple[str, float]]]) -> PioneerAnalysis:
    relevance_label, relevance_score = _top(heads, "alphabet_relevance")
    is_relevant = bool(relevance_label) and "not relevant" not in relevance_label.lower()

    significance_label, significance_conf = _top(heads, "significance")
    base = SIGNIFICANCE_LABELS.get((significance_label or "").lower(), 0.5)
    # Blend the head's own confidence in so weak predictions score lower.
    significance = round(min(1.0, max(0.0, base * (0.6 + 0.4 * min(1.0, significance_conf)))), 3)

    sentiment_label, sentiment_conf = _top(heads, "sentiment")
    sentiment = sentiment_label if sentiment_label in SENTIMENT_LABELS else "neutral"
    direction = {"negative": -1.0, "neutral": 0.0, "positive": 1.0}[sentiment]
    sentiment_score = round(direction * min(1.0, max(0.0, sentiment_conf or 1.0)), 3)

    category_label, _ = _top(heads, "category")
    category = category_label if category_label in CATEGORIES else "Other"

    units = [label for label, _ in sorted(heads.get("affected_business_units", []), key=lambda p: p[1], reverse=True)]
    units = [u for u in dict.fromkeys(units) if u in BUSINESS_UNITS]

    return PioneerAnalysis(
        is_relevant=is_relevant,
        significance_score=significance,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        category=category,
        affected_business_units=units,
    )


class PioneerStage:
    """Shared state for the Pioneer stage so one fatal error stops the whole batch."""

    def __init__(self) -> None:
        self.fatal: str | None = None


# Status codes worth retrying; everything else in 4xx is a client-side problem.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


async def analyze_with_pioneer(
    client: httpx.AsyncClient,
    article: Article,
    api_key: str,
    model: str,
    base_url: str,
    stage: PioneerStage | None = None,
) -> PioneerAnalysis | None:
    if stage is not None and stage.fatal:
        return None

    payload = {
        "model_id": model,
        "text": f"{article.title}\n\n{article.description}".strip(),
        "schema": _pioneer_schema(),
        "threshold": 0.3,
    }
    url = base_url.rstrip("/") + "/inference"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                log.warning("Pioneer %s for %r, retrying", resp.status_code, article.title[:60])
                await asyncio.sleep(1.5 * attempt)
                continue
            if 400 <= resp.status_code < 500 and resp.status_code not in RETRYABLE_STATUS:
                # Auth, billing, or a malformed request: retrying cannot help, and
                # it will fail identically for every other article.
                message = f"Pioneer HTTP {resp.status_code}: {resp.text[:300]}"
                if stage is not None and not stage.fatal:
                    stage.fatal = message
                    log.error("%s -- skipping Pioneer analysis for the remaining articles", message)
                return None
            resp.raise_for_status()
            body = resp.json()
            heads = _iter_predictions(body.get("result"))
            if not heads:
                log.warning("Pioneer returned no classification heads for %r", article.title[:60])
                return None
            return _to_analysis(heads)
        except Exception as exc:  # noqa: BLE001 - skip this article, keep the rest
            log.warning("Pioneer failed for %r (attempt %d/%d): %s", article.title[:60], attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(1.5 * attempt)
    return None


# --------------------------------------------------------------------------
# Optional local fallback analysis (opt-in, never labelled as Pioneer)
# --------------------------------------------------------------------------

_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("Earnings", ("earnings", "quarterly results", "revenue", "eps", "guidance"), ("Alphabet corporate",)),
    ("Regulation and antitrust", ("antitrust", "regulator", "monopoly", "eu commission", "doj"), ("Google Search", "Google Ads")),
    ("Legal", ("lawsuit", "court", "sues", "settlement", "trial"), ("Alphabet corporate",)),
    ("Gemini and AI", ("gemini", "deepmind", " ai ", "model"), ("Google DeepMind",)),
    ("Google Cloud", ("google cloud", "gcp", "data center"), ("Google Cloud",)),
    ("YouTube", ("youtube",), ("YouTube",)),
    ("Waymo", ("waymo", "robotaxi", "self-driving"), ("Waymo",)),
    ("Advertising", ("ad revenue", "advertising", "ad tech", "adsense"), ("Google Ads",)),
    ("Google Search", ("search engine", "google search", "search results"), ("Google Search",)),
    ("Product launch", ("launches", "unveils", "announces", "rolls out"), ("Alphabet corporate",)),
    ("Competition", ("openai", "microsoft", "rival", "competitor", "market share"), ("Alphabet corporate",)),
    ("Leadership", ("ceo", "pichai", "executive", "resigns", "appoints"), ("Alphabet corporate",)),
]

_NEGATIVE_WORDS = ("fell", "drop", "loss", "lawsuit", "antitrust", "fine", "probe", "decline", "cut", "warns", "slump", "ban")
_POSITIVE_WORDS = ("rose", "gain", "beat", "record", "growth", "surge", "wins", "approval", "expands", "upgrade", "rally")

_ALPHABET_TERMS = ("alphabet", "google", "googl", "goog", "youtube", "waymo", "gemini", "deepmind", "android")


def heuristic_analysis(article: Article) -> PioneerAnalysis:
    """Deterministic keyword fallback used only with --fallback-analysis."""
    text = f"{article.title} {article.description}".lower()
    is_relevant = any(term in text for term in _ALPHABET_TERMS)

    category = "Other"
    units: list[str] = []
    for name, keywords, mapped in _CATEGORY_KEYWORDS:
        if any(k in text for k in keywords):
            category = name
            units = list(mapped)
            break

    negatives = sum(word in text for word in _NEGATIVE_WORDS)
    positives = sum(word in text for word in _POSITIVE_WORDS)
    if negatives > positives:
        sentiment, sentiment_score = "negative", -round(min(1.0, 0.3 + 0.2 * negatives), 3)
    elif positives > negatives:
        sentiment, sentiment_score = "positive", round(min(1.0, 0.3 + 0.2 * positives), 3)
    else:
        sentiment, sentiment_score = "neutral", 0.0

    significance = 0.4
    if category in {"Earnings", "Regulation and antitrust", "Legal"}:
        significance = 0.75
    elif category != "Other":
        significance = 0.55
    if any(term in article.title.lower() for term in ("alphabet", "googl", "goog")):
        significance = min(1.0, significance + 0.1)

    return PioneerAnalysis(
        is_relevant=is_relevant,
        significance_score=round(significance, 3),
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        category=category,
        affected_business_units=units,
    )


# --------------------------------------------------------------------------
# Step 3: summarize with Gemini
# --------------------------------------------------------------------------

GEMINI_SYSTEM = (
    "You are a factual financial news summarizer for a paper-trading research project. "
    "Summarize only what the provided article title and description state. "
    "Never give buy, sell, or hold recommendations. Never predict prices or guarantee market outcomes. "
    "Never state facts that are not supported by the provided text. "
    "If the provided text is thin, say plainly what is and is not known."
)


def _gemini_prompt(article: Article, analysis: PioneerAnalysis) -> str:
    return (
        f"Title: {article.title}\n"
        f"Source: {article.source}\n"
        f"Published (UTC): {article.published_at.isoformat()}\n"
        f"Description: {article.description or '(none provided)'}\n\n"
        "Prior classification:\n"
        f"- category: {analysis.category}\n"
        f"- sentiment: {analysis.sentiment} ({analysis.sentiment_score})\n"
        f"- significance: {analysis.significance_score}\n"
        f"- affected business units: {', '.join(analysis.affected_business_units) or 'unspecified'}\n\n"
        "Write a two-to-three-sentence factual summary, and a short explanation of why this "
        "development could matter to Alphabet's business. No recommendations, no price targets."
    )


async def summarize_with_gemini(
    genai_client: Any,
    article: Article,
    analysis: PioneerAnalysis,
    model: str,
) -> GeminiSummary | None:
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=GEMINI_SYSTEM,
        response_mime_type="application/json",
        response_schema=GeminiSummary,
        temperature=0.2,
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await genai_client.aio.models.generate_content(
                model=model,
                contents=_gemini_prompt(article, analysis),
                config=config,
            )
            parsed = getattr(resp, "parsed", None)
            if isinstance(parsed, GeminiSummary):
                return parsed
            if resp.text:
                return GeminiSummary.model_validate_json(resp.text)
            raise ValueError("empty Gemini response")
        except Exception as exc:  # noqa: BLE001 - fall back to the RSS description
            log.warning("Gemini failed for %r (attempt %d/%d): %s", article.title[:60], attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(1.5 * attempt)
    return None


def fallback_summary(article: Article, analysis: PioneerAnalysis) -> GeminiSummary:
    units = ", ".join(analysis.affected_business_units) or "Alphabet overall"
    return GeminiSummary(
        summary=article.description or article.title,
        why_it_matters=(
            f"Classified as {analysis.category} with {analysis.sentiment} sentiment; "
            f"potentially relevant to {units}. Summary unavailable, so this reflects "
            "the classification only."
        ),
    )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


async def process(args: argparse.Namespace) -> list[dict[str, Any]]:
    pioneer_key = os.getenv("PIONEER_API_KEY", "").strip()
    pioneer_model = os.getenv("PIONEER_MODEL", "fastino/gliner2-base-v1").strip()
    pioneer_base = os.getenv("PIONEER_BASE_URL", "https://api.pioneer.ai").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    articles = await collect_articles(args.hours)
    if not articles:
        log.info("No articles collected")
        return []

    if args.max_analyze > 0 and len(articles) > args.max_analyze:
        # One analysis call per article, so cap the batch; newest articles win.
        articles.sort(key=lambda a: a.published_at, reverse=True)
        log.info("Analyzing the %d newest of %d articles (--max-analyze)", args.max_analyze, len(articles))
        articles = articles[: args.max_analyze]

    # --- Step 2: Pioneer analysis -----------------------------------------
    analyses: list[tuple[Article, PioneerAnalysis, str]] = []
    if pioneer_key:
        semaphore = asyncio.Semaphore(PIONEER_CONCURRENCY)
        stage = PioneerStage()
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:

            async def run(article: Article) -> tuple[Article, PioneerAnalysis | None]:
                async with semaphore:
                    return article, await analyze_with_pioneer(
                        client, article, pioneer_key, pioneer_model, pioneer_base, stage
                    )

            for article, analysis in await asyncio.gather(*(run(a) for a in articles)):
                if analysis is not None:
                    analyses.append((article, analysis, "pioneer"))
    else:
        log.warning("PIONEER_API_KEY not set")

    if not analyses and args.fallback_analysis:
        log.warning("Pioneer produced no analyses; using local heuristic fallback")
        analyses = [(a, heuristic_analysis(a), "heuristic") for a in articles]

    kept = [
        (a, an, provider)
        for a, an, provider in analyses
        if an.is_relevant and an.significance_score >= args.min_significance
    ]
    log.info("analyzed=%d kept=%d", len(analyses), len(kept))
    if not kept:
        return []

    kept.sort(key=lambda item: (item[1].significance_score, item[0].published_at), reverse=True)
    kept = kept[: args.limit]

    # --- Step 3: Gemini summaries -----------------------------------------
    summaries: list[tuple[GeminiSummary, str]] = []
    if gemini_key:
        from google import genai

        genai_client = genai.Client(api_key=gemini_key)
        semaphore = asyncio.Semaphore(GEMINI_CONCURRENCY)

        async def run_summary(article: Article, analysis: PioneerAnalysis) -> tuple[GeminiSummary, str]:
            async with semaphore:
                result = await summarize_with_gemini(genai_client, article, analysis, gemini_model)
            if result is None:
                return fallback_summary(article, analysis), "fallback"
            return result, "gemini"

        summaries = list(await asyncio.gather(*(run_summary(a, an) for a, an, _ in kept)))
    else:
        log.warning("GEMINI_API_KEY not set; using RSS descriptions as summaries")
        summaries = [(fallback_summary(a, an), "fallback") for a, an, _ in kept]

    output: list[dict[str, Any]] = []
    for (article, analysis, analysis_provider), (summary, summary_provider) in zip(kept, summaries):
        output.append(
            {
                "title": article.title,
                "source": article.source,
                "published_at": article.published_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "url": article.url,
                "query": article.query,
                "summary": summary.summary,
                "why_it_matters": summary.why_it_matters,
                "significance_score": analysis.significance_score,
                "sentiment": analysis.sentiment,
                "sentiment_score": analysis.sentiment_score,
                "category": analysis.category,
                "affected_business_units": analysis.affected_business_units,
                "analysis_provider": analysis_provider,
                "summary_provider": summary_provider,
            }
        )
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alphabet news agent: Google News -> Pioneer -> Gemini -> JSON")
    parser.add_argument("--hours", type=int, default=24, help="Look-back window in hours (default: 24)")
    parser.add_argument("--limit", type=int, default=10, help="Max articles to output (default: 10)")
    parser.add_argument(
        "--min-significance",
        type=float,
        default=0.4,
        help="Drop articles below this significance score (default: 0.4)",
    )
    parser.add_argument(
        "--max-analyze",
        type=int,
        default=60,
        help="Max articles sent to Pioneer, newest first; 0 disables the cap (default: 60)",
    )
    parser.add_argument(
        "--fallback-analysis",
        action="store_true",
        help="If Pioneer returns nothing, use a local keyword heuristic (output is tagged analysis_provider=heuristic)",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging on stderr")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    try:
        results = asyncio.run(process(args))
    except Exception as exc:  # noqa: BLE001 - stdout must stay valid JSON
        log.error("Agent failed: %s", exc, exc_info=True)
        print("[]")
        return 1
    json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
