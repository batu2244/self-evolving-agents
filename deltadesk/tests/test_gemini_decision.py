"""Every agent uses the shared structured BUY/SELL/HOLD decision contract."""

import asyncio

import pytest

import config
import gemini_decision
from agents import historical_agent
from agents.forecaster_agent import tally
from contracts import Provenance, Signal


def result(action="BUY", equation="slope_only", confidence=0.8):
    return gemini_decision.DecisionResult(
        decision=gemini_decision.ThinkingDecision(
            action=action,
            selected_equation=equation,
            confidence=confidence,
            rationale="Structured thinking decision.",
            evidence=["candidate evidence"],
        ),
        provider="gemini",
        model="gemini-test-thinking",
        thinking_level="high",
    )


def test_mock_decision_returns_explicit_action(monkeypatch):
    monkeypatch.setattr(config, "MOCK_MODE", True)
    decision = asyncio.run(gemini_decision.decide(
        agent="historical",
        ticker="GOOGL",
        system_prompt="test",
        input_summary={"closes": 30},
        candidates=[
            {
                "equation": "trend_blend",
                "direction": 0.8,
                "confidence": 0.7,
                "rationale": "uptrend",
            }
        ],
        allowed_equations=("trend_blend",),
        fallback_equation="trend_blend",
    ))
    assert decision.decision.action == "BUY"
    assert decision.provider == "mock"
    assert decision.model_snapshot()["thinking_level"] == config.GEMINI_THINKING_LEVEL


def test_live_decision_requires_api_key(monkeypatch):
    monkeypatch.setattr(config, "MOCK_MODE", False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gemini_decision.GeminiDecisionError, match="GEMINI_API_KEY"):
        asyncio.run(gemini_decision.decide(
            agent="historical",
            ticker="GOOGL",
            system_prompt="test",
            input_summary={},
            candidates=[],
            allowed_equations=("trend_blend",),
            fallback_equation="trend_blend",
        ))


def test_historical_agent_uses_gemini_action_and_equation():
    bars = [{"bar_date": f"2026-01-{i + 1:02d}", "close": 100 + i} for i in range(20)]
    signal = historical_agent.build_signal(
        "GOOGL",
        bars,
        "test",
        "run",
        thinking=result(),
    )
    assert signal.action == "BUY"
    assert signal.equation_snapshot == {"historical": "slope_only"}
    assert signal.deterministic is False
    assert signal.model_snapshot["provider"] == "gemini"
    assert signal.agent_trace["thinking_decision"]["action"] == "BUY"


def test_forecaster_uses_gemini_action_as_final_call():
    signals = {
        "historical": Signal(
            ticker="GOOGL",
            source="historical",
            action="BUY",
            direction=0.8,
            confidence=0.8,
            rationale="test",
            provenance=Provenance(source_run_id="run"),
        )
    }
    thinking = result(action="HOLD", equation="confidence_weighted", confidence=0.6)
    forecast = tally(
        "GOOGL",
        signals,
        equation="confidence_weighted",
        thinking=thinking,
    )
    assert forecast.action == "HOLD"
    assert forecast.direction == "UP"
    assert forecast.model_snapshot["model"] == "gemini-test-thinking"
