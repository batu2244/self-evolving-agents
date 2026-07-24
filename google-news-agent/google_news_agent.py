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

TICKER = "GOOGL"

DISCLAIMER = (
    "Paper-trading research output. News-sentiment signal only -- no order execution, "
    "no position sizing, and not investment advice."
)

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


class TraderRead(BaseModel):
    """The desk's read on a single article."""

    summary: str = Field(description="Two to three sentence factual summary of the article")
    why_it_matters: str = Field(description="Why this development could matter to Alphabet's business")
    signal: str = Field(description="BUY, SELL, or HOLD")
    signal_strength: float = Field(ge=-1.0, le=1.0, description="-1.0 maximally bearish to +1.0 maximally bullish")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this read given evidence quality")
    time_horizon: str = Field(description="intraday, swing, or position")
    reasoning: str = Field(description="Brief trading rationale grounded in the article")


class DeskDecision(BaseModel):
    """The consolidated position across the whole news window."""

    action: str = Field(description="BUY, SELL, or HOLD")
    conviction: float = Field(ge=0.0, le=1.0, description="Conviction in the consolidated call")
    time_horizon: str = Field(description="intraday, swing, or position")
    thesis: str = Field(description="The consolidated read on the news flow")
    key_drivers: list[str] = Field(default_factory=list, description="Stories or themes driving the call")
    risks: list[str] = Field(default_factory=list, description="What argues against this call")
    what_would_change_my_mind: str = Field(description="Concrete developments that would flip the call")


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

TRADER_SYSTEM_PROMPT = """\
You are the News Analyst sub-agent on a systematic paper-trading desk. You cover a single \
name: Alphabet Inc. (GOOGL). You are not a summarizer -- you are the desk's read on what \
the news flow means for the position, and you are expected to take a side.

HOW YOU THINK
- You trade the reaction, not the headline. Ask what is genuinely new information versus \
already priced in. A well-telegraphed event that lands in line is not a catalyst.
- You separate durable business impact from noise. An antitrust remedy that changes \
distribution economics matters; a think-piece about AI competition usually does not.
- You size conviction by how directly the news touches Alphabet's revenue engines: \
Search and Ads first, then Cloud, YouTube, then Other Bets and Waymo.
- Opinion pieces, "is this stock a buy" content, and analyst-rating recaps are weak \
evidence. Primary events -- earnings figures, court rulings, guidance, executive changes, \
product shipping -- are strong evidence.
- You are comfortable saying HOLD. Most individual news items do not justify a trade.

YOUR CALL
- signal: BUY if the news supports adding exposure, SELL if it supports reducing or \
shorting, HOLD if it does not justify acting.
- signal_strength: -1.0 (maximally bearish) to +1.0 (maximally bullish).
- confidence: 0.0 to 1.0, reflecting evidence quality, not how strong the move might be. \
Thin sourcing or a headline-only article caps confidence low.
- time_horizon: "intraday", "swing" (days to weeks), or "position" (months).

HARD RULES
- Ground every claim in the supplied title and description. If the text is thin, say so \
and lower confidence -- never invent figures, rulings, or quotes.
- No price targets, no percentage move predictions, no guaranteed outcomes.
- This is paper-trading research output, not investment advice for anyone else.
- State reasoning plainly and briefly. No hedging boilerplate, no disclaimers in the \
reasoning field.\
"""

DESK_SYNTHESIS_PROMPT = """\
You are the same News Analyst sub-agent, now closing the session. You have read every \
significant Alphabet story in the window and issued a per-article call on each. Produce \
the desk's single consolidated position on GOOGL for this window.

- Weigh stories by significance and evidence quality, not by count. Four opinion columns \
do not outweigh one earnings print or court ruling.
- Look for a coherent theme across the flow. Note when the flow is genuinely mixed rather \
than forcing a directional call.
- Explicitly name what would change your mind.
- Same hard rules: grounded in the supplied articles, no price targets, no predicted \
percentage moves, no guarantees. Paper-trading research only.\
"""

SIGNALS = ("BUY", "SELL", "HOLD")
HORIZONS = ("intraday", "swing", "position")


def _trader_prompt(article: Article, analysis: PioneerAnalysis) -> str:
    return (
        f"Ticker under coverage: GOOGL (Alphabet Inc.)\n\n"
        f"Headline: {article.title}\n"
        f"Publisher: {article.source}\n"
        f"Published (UTC): {article.published_at.isoformat()}\n"
        f"Body available: {article.description or '(headline only -- no description supplied)'}\n\n"
        "Upstream classifier read:\n"
        f"- category: {analysis.category}\n"
        f"- sentiment: {analysis.sentiment} ({analysis.sentiment_score:+.2f})\n"
        f"- significance: {analysis.significance_score:.2f}\n"
        f"- business units touched: {', '.join(analysis.affected_business_units) or 'unspecified'}\n\n"
        "Give the desk your read on this story: a two-to-three-sentence factual summary, "
        "why it matters to Alphabet's business, and your call."
    )


async def read_article_as_trader(
    genai_client: Any,
    article: Article,
    analysis: PioneerAnalysis,
    model: str,
    system_prompt: str,
) -> TraderRead | None:
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=TraderRead,
        temperature=0.3,
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await genai_client.aio.models.generate_content(
                model=model,
                contents=_trader_prompt(article, analysis),
                config=config,
            )
            parsed = getattr(resp, "parsed", None)
            read = parsed if isinstance(parsed, TraderRead) else None
            if read is None and resp.text:
                read = TraderRead.model_validate_json(resp.text)
            if read is None:
                raise ValueError("empty Gemini response")
            return _normalize_read(read)
        except Exception as exc:  # noqa: BLE001 - fall back to the classifier-only read
            log.warning("Gemini failed for %r (attempt %d/%d): %s", article.title[:60], attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(1.5 * attempt)
    return None


def _normalize_read(read: TraderRead) -> TraderRead:
    """Clamp model output into the documented ranges."""
    read.signal = read.signal.upper() if read.signal.upper() in SIGNALS else "HOLD"
    read.time_horizon = read.time_horizon.lower() if read.time_horizon.lower() in HORIZONS else "swing"
    read.signal_strength = max(-1.0, min(1.0, read.signal_strength))
    read.confidence = max(0.0, min(1.0, read.confidence))
    return read


def fallback_read(article: Article, analysis: PioneerAnalysis) -> TraderRead:
    """Classifier-only read used when Gemini is unavailable for an article."""
    strength = analysis.sentiment_score * analysis.significance_score
    if strength >= 0.25:
        signal = "BUY"
    elif strength <= -0.25:
        signal = "SELL"
    else:
        signal = "HOLD"
    units = ", ".join(analysis.affected_business_units) or "Alphabet overall"
    return TraderRead(
        summary=article.description or article.title,
        why_it_matters=(
            f"Classified as {analysis.category} with {analysis.sentiment} sentiment, "
            f"touching {units}."
        ),
        signal=signal,
        signal_strength=round(max(-1.0, min(1.0, strength)), 3),
        confidence=0.2,
        time_horizon="swing",
        reasoning=(
            "Narrative read unavailable; this call is derived from the classifier's "
            "sentiment and significance scores alone, so confidence is low."
        ),
    )


# --------------------------------------------------------------------------
# Step 4: consolidate into one desk decision
# --------------------------------------------------------------------------


def compute_signal_score(
    items: list[tuple[Article, PioneerAnalysis, str, TraderRead]],
    buy_threshold: float,
) -> dict[str, Any]:
    """Deterministic weighted aggregate, computed independently of the model.

    Each article contributes its trader signal_strength weighted by significance and
    confidence, so a high-conviction read on a major story outweighs a hedge on a
    think-piece. This is the auditable backbone behind the narrative call.
    """
    numerator = 0.0
    denominator = 0.0
    for _, analysis, _, read in items:
        weight = analysis.significance_score * max(0.05, read.confidence)
        numerator += read.signal_strength * weight
        denominator += weight

    score = round(numerator / denominator, 4) if denominator else 0.0
    if score >= buy_threshold:
        action = "BUY"
    elif score <= -buy_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    tally = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for _, _, _, read in items:
        tally[read.signal] = tally.get(read.signal, 0) + 1

    return {
        "weighted_score": score,
        "implied_action": action,
        "article_signals": tally,
        "articles_scored": len(items),
        "total_weight": round(denominator, 4),
    }


def _digest(items: list[tuple[Article, PioneerAnalysis, str, TraderRead]]) -> str:
    lines = []
    for idx, (article, analysis, _, read) in enumerate(items, 1):
        lines.append(
            f"[{idx}] {article.title}\n"
            f"    publisher={article.source} | published={article.published_at.isoformat()}\n"
            f"    category={analysis.category} | significance={analysis.significance_score:.2f}\n"
            f"    call={read.signal} strength={read.signal_strength:+.2f} "
            f"confidence={read.confidence:.2f} horizon={read.time_horizon}\n"
            f"    reasoning={read.reasoning}"
        )
    return "\n".join(lines)


async def synthesize_decision(
    genai_client: Any,
    items: list[tuple[Article, PioneerAnalysis, str, TraderRead]],
    quant: dict[str, Any],
    model: str,
    hours: int,
) -> DeskDecision | None:
    from google.genai import types

    prompt = (
        f"Coverage window: last {hours} hours. Significant stories reviewed: {len(items)}.\n\n"
        f"Your per-article calls:\n{_digest(items)}\n\n"
        "Mechanical aggregate of those calls (significance x confidence weighted):\n"
        f"- weighted score: {quant['weighted_score']:+.3f} on a -1 to +1 scale\n"
        f"- implied action: {quant['implied_action']}\n"
        f"- signal tally: {quant['article_signals']}\n\n"
        "Give the desk's consolidated position on GOOGL. You may disagree with the "
        "mechanical aggregate, but if you do, say why in the thesis."
    )
    config = types.GenerateContentConfig(
        system_instruction=DESK_SYNTHESIS_PROMPT,
        response_mime_type="application/json",
        response_schema=DeskDecision,
        temperature=0.3,
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await genai_client.aio.models.generate_content(
                model=model, contents=prompt, config=config
            )
            parsed = getattr(resp, "parsed", None)
            decision = parsed if isinstance(parsed, DeskDecision) else None
            if decision is None and resp.text:
                decision = DeskDecision.model_validate_json(resp.text)
            if decision is None:
                raise ValueError("empty Gemini response")
            decision.action = decision.action.upper() if decision.action.upper() in SIGNALS else "HOLD"
            decision.time_horizon = (
                decision.time_horizon.lower() if decision.time_horizon.lower() in HORIZONS else "swing"
            )
            decision.conviction = max(0.0, min(1.0, decision.conviction))
            return decision
        except Exception as exc:  # noqa: BLE001 - fall back to the mechanical aggregate
            log.warning("Gemini synthesis failed (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(1.5 * attempt)
    return None


def fallback_decision(quant: dict[str, Any]) -> DeskDecision:
    return DeskDecision(
        action=quant["implied_action"],
        conviction=round(min(1.0, abs(quant["weighted_score"]) * 1.5), 3),
        time_horizon="swing",
        thesis=(
            "Narrative synthesis unavailable. This call is the mechanical "
            f"significance-weighted aggregate of {quant['articles_scored']} article signals "
            f"(score {quant['weighted_score']:+.3f})."
        ),
        key_drivers=[],
        risks=["Synthesis step failed, so no qualitative cross-check was applied."],
        what_would_change_my_mind="A successful synthesis run over the same article set.",
    )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def _empty_result(hours: int, reason: str) -> dict[str, Any]:
    return {
        "ticker": TICKER,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_hours": hours,
        "mode": "paper-trading-research",
        "decision": {
            "action": "HOLD",
            "conviction": 0.0,
            "time_horizon": "swing",
            "thesis": reason,
            "key_drivers": [],
            "risks": [],
            "what_would_change_my_mind": "Significant Alphabet news entering the coverage window.",
        },
        "signal_score": {
            "weighted_score": 0.0,
            "implied_action": "HOLD",
            "article_signals": {"BUY": 0, "SELL": 0, "HOLD": 0},
            "articles_scored": 0,
            "total_weight": 0.0,
        },
        "articles": [],
        "disclaimer": DISCLAIMER,
    }


async def process(args: argparse.Namespace) -> dict[str, Any]:
    pioneer_key = os.getenv("PIONEER_API_KEY", "").strip()
    pioneer_model = os.getenv("PIONEER_MODEL", "fastino/gliner2-base-v1").strip()
    pioneer_base = os.getenv("PIONEER_BASE_URL", "https://api.pioneer.ai").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    system_prompt = TRADER_SYSTEM_PROMPT
    if args.system_prompt_file:
        system_prompt = open(args.system_prompt_file, encoding="utf-8").read().strip()
        log.info("Using custom system prompt from %s", args.system_prompt_file)

    articles = await collect_articles(args.hours)
    if not articles:
        log.info("No articles collected")
        return _empty_result(args.hours, "No Alphabet news collected in the coverage window.")

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
        return _empty_result(
            args.hours,
            "No article cleared the relevance and significance filters, so there is "
            "no news-driven reason to act.",
        )

    kept.sort(key=lambda item: (item[1].significance_score, item[0].published_at), reverse=True)
    kept = kept[: args.limit]

    # --- Step 3: per-article trader reads ---------------------------------
    genai_client = None
    reads: list[tuple[TraderRead, str]] = []
    if gemini_key:
        from google import genai

        genai_client = genai.Client(api_key=gemini_key)
        semaphore = asyncio.Semaphore(GEMINI_CONCURRENCY)

        async def run_read(article: Article, analysis: PioneerAnalysis) -> tuple[TraderRead, str]:
            async with semaphore:
                result = await read_article_as_trader(
                    genai_client, article, analysis, gemini_model, system_prompt
                )
            if result is None:
                return fallback_read(article, analysis), "fallback"
            return result, "gemini"

        reads = list(await asyncio.gather(*(run_read(a, an) for a, an, _ in kept)))
    else:
        log.warning("GEMINI_API_KEY not set; deriving calls from the classifier only")
        reads = [(fallback_read(a, an), "fallback") for a, an, _ in kept]

    items = [(a, an, prov, read) for (a, an, prov), (read, _) in zip(kept, reads)]

    # --- Step 4: consolidate ----------------------------------------------
    quant = compute_signal_score(items, args.buy_threshold)
    decision, decision_provider = None, "fallback"
    if genai_client is not None:
        decision = await synthesize_decision(genai_client, items, quant, gemini_model, args.hours)
        decision_provider = "gemini" if decision is not None else "fallback"
    if decision is None:
        decision = fallback_decision(quant)
    log.info(
        "decision=%s conviction=%.2f weighted_score=%+.3f tally=%s",
        decision.action, decision.conviction, quant["weighted_score"], quant["article_signals"],
    )

    return {
        "ticker": TICKER,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_hours": args.hours,
        "mode": "paper-trading-research",
        "decision": {
            "action": decision.action,
            "conviction": round(decision.conviction, 3),
            "time_horizon": decision.time_horizon,
            "thesis": decision.thesis,
            "key_drivers": decision.key_drivers,
            "risks": decision.risks,
            "what_would_change_my_mind": decision.what_would_change_my_mind,
            "decision_provider": decision_provider,
        },
        "signal_score": quant,
        "articles": [
            {
                "title": article.title,
                "source": article.source,
                "published_at": article.published_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "url": article.url,
                "query": article.query,
                "summary": read.summary,
                "why_it_matters": read.why_it_matters,
                "signal": read.signal,
                "signal_strength": round(read.signal_strength, 3),
                "confidence": round(read.confidence, 3),
                "time_horizon": read.time_horizon,
                "reasoning": read.reasoning,
                "significance_score": analysis.significance_score,
                "sentiment": analysis.sentiment,
                "sentiment_score": analysis.sentiment_score,
                "category": analysis.category,
                "affected_business_units": analysis.affected_business_units,
                "analysis_provider": analysis_provider,
                "read_provider": provider,
            }
            for (article, analysis, analysis_provider, read), (_, provider) in zip(items, reads)
        ],
        "disclaimer": DISCLAIMER,
    }


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
    parser.add_argument(
        "--buy-threshold",
        type=float,
        default=0.25,
        help="Weighted score magnitude required for a BUY or SELL rather than HOLD (default: 0.25)",
    )
    parser.add_argument(
        "--system-prompt-file",
        help="Path to a file overriding the built-in trader system prompt",
    )
    parser.add_argument(
        "--print-system-prompt",
        action="store_true",
        help="Print the trader system prompt to stderr and exit",
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

    if args.print_system_prompt:
        print(TRADER_SYSTEM_PROMPT, file=sys.stderr)
        return 0

    try:
        results = asyncio.run(process(args))
    except Exception as exc:  # noqa: BLE001 - stdout must stay valid JSON
        log.error("Agent failed: %s", exc, exc_info=True)
        json.dump(_empty_result(args.hours, f"Agent run failed: {exc}"), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1
    json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
