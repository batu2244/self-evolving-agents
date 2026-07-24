#!/usr/bin/env python3
"""Actian VectorAI persistence for the Alphabet news-analyst agent.

Stores every agent run in three collections:

    deltadesk_articles   one point per analyzed article (deduped by URL)
    deltadesk_decisions  one point per run's consolidated desk decision
    deltadesk_runs       one point per run, for the run log

Vectors are "bring your own": text is embedded with Gemini when GEMINI_API_KEY is
set, otherwise with a deterministic local hash embedder. Every stored point
records which one produced it in `embedding_provider`, so fallback data is never
mistaken for the real model's work.

Standalone usage:

    python actian_store.py health
    python actian_store.py store results.json
    python actian_store.py search "antitrust ruling" --collection deltadesk_articles
    python actian_store.py recent
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse, urlunparse

from actian_vectorai import (
    Distance,
    PointStruct,
    VectorAIClient,
    VectorAIError,
    VectorParams,
)

log = logging.getLogger("actian_store")

DEFAULT_URL = "localhost:6574"
DEFAULT_EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768

ARTICLES = "deltadesk_articles"
DECISIONS = "deltadesk_decisions"
RUNS = "deltadesk_runs"
COLLECTIONS = (ARTICLES, DECISIONS, RUNS)

# Deterministic ID namespace, so re-running the agent updates rows instead of
# duplicating them.
NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------


def _hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic bag-of-words hash embedding.

    Not semantically strong, but it is stable, offline, and lets the whole
    storage path be exercised without an API key.
    """
    vec = [0.0] * dim
    tokens = [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


class Embedder:
    """Embeds text with Gemini when configured, else with the hash fallback."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or os.getenv("ACTIAN_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self._key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
        self._client = None
        self.provider = "hash"
        if self._key:
            try:
                from google import genai

                self._client = genai.Client(api_key=self._key)
                self.provider = f"gemini:{self.model}"
            except Exception as exc:  # noqa: BLE001 - fall back rather than fail the run
                log.warning("Gemini embeddings unavailable (%s); using hash embeddings", exc)
                self._client = None

    def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], str]:
        """Return (vectors, provider_actually_used)."""
        texts = [t if t.strip() else "empty" for t in texts]
        if self._client is not None:
            try:
                from google.genai import types

                resp = self._client.models.embed_content(
                    model=self.model,
                    contents=list(texts),
                    config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
                )
                vectors = [list(e.values) for e in resp.embeddings]
                if len(vectors) == len(texts) and all(len(v) == EMBED_DIM for v in vectors):
                    # gemini-embedding-001 only normalizes 3072-dim output itself.
                    return [_l2(v) for v in vectors], self.provider
                log.warning("Gemini returned unexpected embedding shape; using hash embeddings")
            except Exception as exc:  # noqa: BLE001
                log.warning("Gemini embedding failed (%s); using hash embeddings", exc)
        return [_hash_embed(t) for t in texts], "hash"


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _canonical_url(url: str) -> str:
    """Strip query/fragment so the same story does not land twice."""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
    except Exception:  # noqa: BLE001
        return url


def article_id(url: str) -> str:
    return str(uuid.uuid5(NAMESPACE, _canonical_url(url)))


def run_id_for(as_of: str, window_hours: int, ticker: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"run|{ticker}|{as_of}|{window_hours}"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _article_text(a: dict[str, Any]) -> str:
    parts = [
        a.get("title", ""),
        a.get("summary", ""),
        a.get("why_it_matters", ""),
        a.get("reasoning", ""),
        a.get("category", ""),
        " ".join(a.get("affected_business_units", []) or []),
    ]
    return "\n".join(p for p in parts if p)


def _decision_text(d: dict[str, Any]) -> str:
    parts = [
        d.get("thesis", ""),
        " ".join(d.get("key_drivers", []) or []),
        " ".join(d.get("risks", []) or []),
        d.get("what_would_change_my_mind", ""),
    ]
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class ActianStore:
    """Thin wrapper over VectorAIClient with the agent's collections baked in."""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.url = url or os.getenv("ACTIAN_VECTORAI_URL", DEFAULT_URL)
        self.api_key = api_key if api_key is not None else (os.getenv("ACTIAN_VECTORAI_API_KEY") or None)
        self.embedder = embedder or Embedder()
        self.client: VectorAIClient | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "ActianStore":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def connect(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        self.client = VectorAIClient(self.url, **kwargs)
        self.client.connect()
        info = self.client.health_check()
        log.info("Connected to %s at %s", info.get("title", "VectorAI"), self.url)
        return info

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def _require(self) -> VectorAIClient:
        if self.client is None:
            raise RuntimeError("ActianStore.connect() has not been called")
        return self.client

    # -- schema ------------------------------------------------------------

    def ensure_collections(self) -> list[str]:
        """Create the collections if missing, and repair any left unusable.

        VectorAI 1.0.2 does not fully recover collections across a server
        restart: it still lists them, but every point operation returns 404. A
        collection in that state would silently break every later run, so it is
        recreated -- which costs the points it was holding. Returns the names
        that had to be repaired so the caller can report the loss.
        """
        client = self._require()
        config = VectorParams(size=EMBED_DIM, distance=Distance.Cosine)
        repaired: list[str] = []
        for name in COLLECTIONS:
            client.collections.get_or_create(name, vectors_config=config)
            try:
                client.points.count(name)
            except VectorAIError as exc:
                log.warning(
                    "Collection %s exists but cannot serve point operations (%s); "
                    "recreating it -- any points it held are lost",
                    name, type(exc).__name__,
                )
                client.collections.recreate(name, vectors_config=config)
                repaired.append(name)
            log.debug("collection ready: %s", name)
        return repaired

    # -- writes ------------------------------------------------------------

    def store_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Persist one agent run: its articles, its decision, and its run log."""
        client = self._require()
        repaired = self.ensure_collections()

        ticker = result.get("ticker", "GOOGL")
        as_of = result.get("as_of") or _utc_now()
        window_hours = int(result.get("window_hours", 0))
        run_id = run_id_for(as_of, window_hours, ticker)
        stored_at = _utc_now()
        articles = result.get("articles", []) or []
        decision = result.get("decision", {}) or {}
        signal_score = result.get("signal_score", {}) or {}

        # --- articles -----------------------------------------------------
        stored_articles = 0
        article_ids: list[str] = []
        embed_provider = self.embedder.provider
        if articles:
            vectors, embed_provider = self.embedder.embed([_article_text(a) for a in articles])
            points = []
            for art, vec in zip(articles, vectors):
                pid = article_id(art.get("url", "") or art.get("title", ""))
                article_ids.append(pid)
                points.append(
                    PointStruct(
                        id=pid,
                        vector=vec,
                        payload={
                            **art,
                            "ticker": ticker,
                            "run_id": run_id,
                            "as_of": as_of,
                            "window_hours": window_hours,
                            "stored_at": stored_at,
                            "embedding_provider": embed_provider,
                            "record_type": "article",
                        },
                    )
                )
            client.points.upsert(ARTICLES, points)
            stored_articles = len(points)

        # --- decision -----------------------------------------------------
        dec_text = _decision_text(decision) or f"{decision.get('action', 'HOLD')} {ticker}"
        dec_vec, dec_provider = self.embedder.embed([dec_text])
        client.points.upsert(
            DECISIONS,
            [
                PointStruct(
                    id=run_id,
                    vector=dec_vec[0],
                    payload={
                        **decision,
                        "ticker": ticker,
                        "run_id": run_id,
                        "as_of": as_of,
                        "window_hours": window_hours,
                        "stored_at": stored_at,
                        "mode": result.get("mode", "paper-trading-research"),
                        # The deterministic score is stored beside the narrative so the
                        # call can always be audited back to its inputs.
                        "signal_score": signal_score,
                        "weighted_score": signal_score.get("weighted_score"),
                        "implied_action": signal_score.get("implied_action"),
                        "article_ids": article_ids,
                        "article_count": len(article_ids),
                        "embedding_provider": dec_provider,
                        "disclaimer": result.get("disclaimer", ""),
                        "record_type": "decision",
                    },
                )
            ],
        )

        # --- run log ------------------------------------------------------
        status = "ok" if articles else "no_articles"
        run_summary = (
            f"{ticker} run {as_of} window={window_hours}h "
            f"action={decision.get('action', 'HOLD')} articles={len(articles)}"
        )
        run_vec, _ = self.embedder.embed([run_summary])
        client.points.upsert(
            RUNS,
            [
                PointStruct(
                    id=str(uuid.uuid5(NAMESPACE, f"runlog|{run_id}")),
                    vector=run_vec[0],
                    payload={
                        "record_type": "run",
                        "run_id": run_id,
                        "ticker": ticker,
                        "as_of": as_of,
                        "stored_at": stored_at,
                        "window_hours": window_hours,
                        "status": status,
                        "summary": run_summary,
                        "articles_stored": stored_articles,
                        "action": decision.get("action"),
                        "conviction": decision.get("conviction"),
                        "weighted_score": signal_score.get("weighted_score"),
                        "pioneer_status": result.get("pioneer_status"),
                        "decision_provider": decision.get("decision_provider"),
                        "embedding_provider": embed_provider,
                    },
                )
            ],
        )

        receipt = {
            "run_id": run_id,
            "stored_at": stored_at,
            "articles_stored": stored_articles,
            "decisions_stored": 1,
            "embedding_provider": embed_provider,
            "collections": list(COLLECTIONS),
            "url": self.url,
        }
        if repaired:
            receipt["repaired_collections"] = repaired
        log.info(
            "Stored run %s: %d articles, 1 decision (embeddings=%s)",
            run_id, stored_articles, embed_provider,
        )
        return receipt

    # -- reads -------------------------------------------------------------

    def search(self, query: str, collection: str = ARTICLES, limit: int = 5) -> list[dict[str, Any]]:
        client = self._require()
        vec, _ = self.embedder.embed([query])
        hits = client.points.search(collection, vector=vec[0], limit=limit)
        return [{"id": str(h.id), "score": h.score, "payload": h.payload} for h in hits]

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        client = self._require()
        points, _ = client.points.scroll(RUNS, limit=limit)
        rows = [p.payload or {} for p in points]
        rows.sort(key=lambda r: r.get("as_of", ""), reverse=True)
        return rows

    def counts(self) -> dict[str, int]:
        client = self._require()
        out: dict[str, int] = {}
        for name in COLLECTIONS:
            try:
                out[name] = client.points.count(name)
            except VectorAIError:
                out[name] = 0
        return out


def store_result_safe(result: dict[str, Any], url: str | None = None) -> dict[str, Any]:
    """Store a run, converting any storage failure into a reported error.

    The agent's stdout must stay valid JSON, so persistence never raises.
    """
    try:
        with ActianStore(url=url) as store:
            return store.store_result(result)
    except Exception as exc:  # noqa: BLE001 - storage is best-effort
        log.error("Actian storage failed: %s", exc)
        return {"stored": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Actian VectorAI store for the news agent")
    parser.add_argument("--url", help=f"VectorAI gRPC endpoint (default: env or {DEFAULT_URL})")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Health check, ensure collections, show counts")
    p_store = sub.add_parser("store", help="Store an agent result JSON file")
    p_store.add_argument("path", help="Path to agent output JSON ('-' for stdin)")
    p_search = sub.add_parser("search", help="Semantic search over stored records")
    p_search.add_argument("query")
    p_search.add_argument("--collection", default=ARTICLES, choices=list(COLLECTIONS))
    p_search.add_argument("--limit", type=int, default=5)
    p_recent = sub.add_parser("recent", help="Show recent agent runs")
    p_recent.add_argument("--limit", type=int, default=10)

    args = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    try:
        with ActianStore(url=args.url) as store:
            if args.command == "health":
                info = store.client.health_check() if store.client else {}
                store.ensure_collections()
                out = {
                    "url": store.url,
                    "server": info,
                    "collections": store.counts(),
                    "embedding_provider": store.embedder.provider,
                }
            elif args.command == "store":
                raw = sys.stdin.read() if args.path == "-" else open(args.path, encoding="utf-8").read()
                out = store.store_result(json.loads(raw))
            elif args.command == "search":
                out = store.search(args.query, collection=args.collection, limit=args.limit)
            else:
                out = store.recent_runs(limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        log.error("%s: %s", type(exc).__name__, exc)
        return 1

    json.dump(out, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
