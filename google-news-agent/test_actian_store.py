#!/usr/bin/env python3
"""Tests for the Actian VectorAI store.

Pure-logic tests always run. The round-trip tests need a live VectorAI at
ACTIAN_VECTORAI_URL (default localhost:6574) and skip when it is unreachable.

    pytest test_actian_store.py -v
"""

from __future__ import annotations

import json
import uuid

import pytest

import actian_store as store
from actian_store import ARTICLES, DECISIONS, EMBED_DIM, RUNS, ActianStore, Embedder


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


def test_hash_embed_is_deterministic_and_normalized():
    a = store._hash_embed("Alphabet beats earnings expectations")
    b = store._hash_embed("Alphabet beats earnings expectations")
    assert a == b
    assert len(a) == EMBED_DIM
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9


def test_hash_embed_distinguishes_text():
    assert store._hash_embed("antitrust ruling") != store._hash_embed("cloud revenue growth")


def test_hash_embed_handles_empty_text():
    vec = store._hash_embed("")
    assert len(vec) == EMBED_DIM
    assert abs(sum(v * v for v in vec) - 1.0) < 1e-9


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://News.Example.com/story?utm_source=x", "https://news.example.com/story"),
        ("https://news.example.com/story/", "https://news.example.com/story"),
        ("https://news.example.com/story#top", "https://news.example.com/story"),
    ],
)
def test_article_id_dedupes_url_variants(a, b):
    assert store.article_id(a) == store.article_id(b)


def test_article_id_differs_across_stories():
    assert store.article_id("https://x.com/a") != store.article_id("https://x.com/b")


def test_ids_are_valid_uuids():
    uuid.UUID(store.article_id("https://x.com/a"))
    uuid.UUID(store.run_id_for("2026-07-24T12:00:00Z", 24, "GOOGL"))


def test_run_id_is_stable_per_window():
    same = store.run_id_for("2026-07-24T12:00:00Z", 24, "GOOGL")
    assert same == store.run_id_for("2026-07-24T12:00:00Z", 24, "GOOGL")
    assert same != store.run_id_for("2026-07-24T12:00:00Z", 48, "GOOGL")


def test_article_text_joins_present_fields_only():
    text = store._article_text({"title": "T", "summary": "S", "affected_business_units": []})
    assert "T" in text and "S" in text
    assert not text.endswith("\n")


def test_hash_embedder_reports_provider_when_no_key():
    emb = Embedder(api_key="")
    assert emb.provider == "hash"
    vecs, provider = emb.embed(["hello world"])
    assert provider == "hash"
    assert len(vecs) == 1 and len(vecs[0]) == EMBED_DIM


def test_store_result_safe_reports_failure_instead_of_raising():
    receipt = store.store_result_safe({"ticker": "GOOGL"}, url="localhost:1")
    assert receipt["stored"] is False
    assert "error" in receipt


# --------------------------------------------------------------------------
# Round trip against a live server
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_store():
    st = ActianStore(embedder=Embedder(api_key=""))  # hash embeddings: offline + deterministic
    try:
        st.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"No VectorAI server at {st.url}: {exc}")
    st.ensure_collections()
    yield st
    st.close()


def _sample_result(as_of: str = "2026-07-24T12:00:00Z") -> dict:
    return {
        "ticker": "TESTX",
        "as_of": as_of,
        "window_hours": 24,
        "mode": "paper-trading-research",
        "decision": {
            "action": "BUY",
            "conviction": 0.62,
            "time_horizon": "swing",
            "thesis": "Cloud momentum outweighs the regulatory overhang.",
            "key_drivers": ["cloud backlog"],
            "risks": ["antitrust remedy"],
            "what_would_change_my_mind": "A structural breakup order.",
            "decision_provider": "test",
        },
        "signal_score": {
            "weighted_score": 0.41,
            "implied_action": "BUY",
            "article_signals": {"BUY": 1, "SELL": 0, "HOLD": 0},
            "articles_scored": 1,
            "total_weight": 1.0,
        },
        "pioneer_status": "ok",
        "articles": [
            {
                "title": "Test cloud backlog story",
                "source": "TestWire",
                "published_at": "2026-07-24T09:00:00Z",
                "url": "https://test.example.com/cloud-backlog",
                "query": "Google Cloud",
                "summary": "Cloud backlog grew sharply.",
                "why_it_matters": "Backlog leads revenue.",
                "signal": "BUY",
                "signal_strength": 0.5,
                "confidence": 0.7,
                "time_horizon": "swing",
                "reasoning": "Backlog conversion supports estimates.",
                "significance_score": 0.8,
                "sentiment": "positive",
                "sentiment_score": 0.6,
                "category": "Google Cloud",
                "affected_business_units": ["Google Cloud"],
                "analysis_provider": "test",
                "read_provider": "test",
            }
        ],
        "disclaimer": "Paper-trading research output.",
    }


def test_ensure_collections_is_idempotent_when_healthy(live_store):
    """Healthy collections must not be recreated -- that would drop stored points."""
    live_store.store_result(_sample_result())
    before = live_store.counts()
    assert live_store.ensure_collections() == []
    assert live_store.counts() == before


def test_round_trip_stores_and_reads_back(live_store):
    result = _sample_result()
    receipt = live_store.store_result(result)

    assert receipt["articles_stored"] == 1
    assert receipt["decisions_stored"] == 1
    assert receipt["embedding_provider"] == "hash"

    got = live_store.client.points.get(ARTICLES, ids=[store.article_id("https://test.example.com/cloud-backlog")])
    assert len(got) == 1
    payload = got[0].payload
    assert payload["title"] == "Test cloud backlog story"
    assert payload["signal"] == "BUY"
    assert payload["run_id"] == receipt["run_id"]
    assert payload["embedding_provider"] == "hash"  # provenance is recorded


def test_decision_keeps_deterministic_score_beside_narrative(live_store):
    receipt = live_store.store_result(_sample_result())
    got = live_store.client.points.get(DECISIONS, ids=[receipt["run_id"]])
    payload = got[0].payload
    assert payload["action"] == "BUY"
    assert payload["weighted_score"] == pytest.approx(0.41)
    assert payload["implied_action"] == "BUY"
    assert payload["article_count"] == 1


def test_run_log_written(live_store):
    receipt = live_store.store_result(_sample_result())
    runs = [r for r in live_store.recent_runs(limit=50) if r.get("run_id") == receipt["run_id"]]
    assert runs, "run log entry missing"
    assert runs[0]["status"] == "ok"
    assert runs[0]["articles_stored"] == 1


def test_restoring_same_run_does_not_duplicate(live_store):
    before = live_store.counts()
    live_store.store_result(_sample_result())
    live_store.store_result(_sample_result())
    after = live_store.counts()
    assert after[ARTICLES] == before[ARTICLES]
    assert after[DECISIONS] == before[DECISIONS]
    assert after[RUNS] == before[RUNS]


def test_search_finds_stored_article(live_store):
    live_store.store_result(_sample_result())
    hits = live_store.search("cloud backlog growth", collection=ARTICLES, limit=10)
    assert any(h["payload"].get("title") == "Test cloud backlog story" for h in hits)


def test_empty_run_still_logs_a_run(live_store):
    result = _sample_result(as_of="2026-07-24T18:00:00Z")
    result["articles"] = []
    receipt = live_store.store_result(result)
    assert receipt["articles_stored"] == 0
    runs = [r for r in live_store.recent_runs(limit=50) if r.get("run_id") == receipt["run_id"]]
    assert runs and runs[0]["status"] == "no_articles"


def test_agent_output_shape_is_storable(live_store):
    """Guards against the agent's real output drifting from what the store expects."""
    try:
        with open("sample_output.json", encoding="utf-8") as fh:
            real = json.load(fh)
    except FileNotFoundError:
        pytest.skip("sample_output.json not present")
    receipt = live_store.store_result(real)
    assert receipt["articles_stored"] == len(real.get("articles", []))
