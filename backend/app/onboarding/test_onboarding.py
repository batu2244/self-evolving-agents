"""Onboarding module tests — mounts only this module's router so the suite
stays independent of the rest of the API. Runs without ANTHROPIC_API_KEY
(the chat falls back to deterministic extraction)."""

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.onboarding.chat import Slots, respond, rule_extract
from app.onboarding.router import get_committee_mandates, router

os.environ.pop("ANTHROPIC_API_KEY", None)  # force the deterministic path

app = FastAPI()
app.include_router(router)
client = TestClient(app)

ENVELOPE = {
    "riskLevel": "balanced",
    "targetReturnPct": 2.0,
    "capitalUsd": 10_000,
    "market": "us",
}


def _chat(messages, slots=None):
    res = client.post("/api/onboarding/chat", json={"messages": messages, "slots": slots or {}})
    assert res.status_code == 200, res.text
    return res.json()


def test_chat_learns_from_popular_tickers():
    body = _chat([{"role": "user", "content": "I'd buy NVDA and Tesla if I had to pick today"}])
    assert body["slots"]["riskLevel"] == "aggressive"
    assert body["slots"]["market"] == "us"
    assert not body["done"]
    assert body["suggestions"]  # asks the next question with quick replies


def test_chat_full_conversation_reaches_proposal():
    slots = {}
    messages = [{"role": "user", "content": "keep it safe, EU stocks"}]
    body = _chat(messages, slots)
    assert body["slots"]["riskLevel"] == "conservative"
    assert body["slots"]["market"] == "eu"

    messages += [
        {"role": "assistant", "content": body["reply"]},
        {"role": "user", "content": "let's do $25k"},
    ]
    body = _chat(messages, body["slots"])
    assert body["done"]
    assert body["proposal"]["trackerSymbol"] == "EXSA"
    assert body["slots"]["capitalUsd"] == 25_000
    # target defaulted from risk level
    assert body["slots"]["targetReturnPct"] == 1.0


def test_chat_defaults_when_user_delegates():
    body = _chat([{"role": "user", "content": "no idea, just pick for me"}])
    assert body["done"]
    assert body["slots"]["riskLevel"] == "balanced"
    assert body["slots"]["market"] == "us"
    assert body["slots"]["capitalUsd"] == 10_000


def test_chat_doubles_capital_after_proposal():
    slots = {"riskLevel": "balanced", "targetReturnPct": 2.0, "capitalUsd": 10_000, "market": "us"}
    body = _chat([{"role": "user", "content": "Double the capital"}], slots)
    assert body["slots"]["capitalUsd"] == 20_000
    assert body["done"]


def test_chat_revises_after_proposal():
    slots = {"riskLevel": "balanced", "targetReturnPct": 2.0, "capitalUsd": 10_000, "market": "us"}
    body = _chat([{"role": "user", "content": "switch to crypto"}], slots)
    assert body["done"]
    assert body["slots"]["market"] == "crypto"
    assert body["proposal"]["trackerSymbol"] == "BTC/USD"


def test_rule_extract_bounds():
    ex = rule_extract("put in $500")
    assert ex.capital_usd is None and ex.notes
    ex = rule_extract("beat it by 40%")
    assert ex.target_return_pct is None and ex.notes
    ex = rule_extract("100k, 3% above")
    assert ex.capital_usd == 100_000 and ex.target_return_pct == 3.0


def test_rule_extract_dollar_words_and_negatives():
    assert rule_extract("I have 500 dollars").notes  # below minimum, but recognized
    assert rule_extract("50000 bucks").capital_usd == 50_000
    ex = rule_extract("-5000 please")
    assert ex.capital_usd is None  # negative amounts are not a commitment


def test_respond_never_downgrades_stated_answers():
    slots = Slots(risk_level="conservative")
    turn = respond("maybe some NVDA too", slots)  # aggressive tilt must not override
    assert turn.slots.risk_level == "conservative"


def test_universe_endpoint_still_works():
    res = client.post("/api/onboarding/universe", json=ENVELOPE)
    assert res.status_code == 200
    body = res.json()
    assert body["trackerSymbol"] == "SPY"
    assert 5 <= len(body["universe"]) <= 8


def test_ratify_with_stock_selection():
    proposal = client.post("/api/onboarding/universe", json=ENVELOPE).json()
    picks = [a["symbol"] for a in proposal["universe"][:3]]

    res = client.post(
        "/api/onboarding/envelope",
        json={"envelope": ENVELOPE, "proposal": proposal, "selected": picks},
    )
    assert res.status_code == 201
    assert res.json()["committee_mandates"] == picks
    assert get_committee_mandates() == picks

    current = client.get("/api/onboarding/envelope")
    assert current.status_code == 200
    assert current.json()["selected"] == picks


def test_ratify_rejects_unknown_or_empty_selection():
    proposal = client.post("/api/onboarding/universe", json=ENVELOPE).json()
    res = client.post(
        "/api/onboarding/envelope",
        json={"envelope": ENVELOPE, "proposal": proposal, "selected": ["ENRON"]},
    )
    assert res.status_code == 422
    res = client.post(
        "/api/onboarding/envelope",
        json={"envelope": ENVELOPE, "proposal": proposal, "selected": []},
    )
    assert res.status_code == 422
