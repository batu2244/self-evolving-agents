"""Agents select analysis equations from the data regime and explain why."""

import pytest

import analysis_modes
import config
from agents import historical_agent, news_agent, realtime_agent
from agents.forecaster_agent import tally
from contracts import Provenance, Signal


@pytest.fixture(autouse=True)
def restore_analysis_config():
    equations = config.equation_snapshot()
    policies = config.analysis_policy_snapshot()
    yield
    config.apply_equation_overrides(equations)
    config.apply_analysis_policy_overrides(policies)


def sig(source, direction, confidence=0.8):
    return Signal(
        ticker="GOOGL",
        source=source,
        direction=direction,
        confidence=confidence,
        rationale="test",
        provenance=Provenance(source_run_id=f"{source}-run"),
    )


def test_historical_auto_uses_slope_when_history_is_short():
    closes = [100.0 + i for i in range(config.MA_LONG - 1)]
    choice = analysis_modes.select_historical(closes, policy="auto")
    assert choice.equation == "slope_only"
    assert choice.evidence["required_for_ma"] == config.MA_LONG


def test_realtime_auto_uses_the_only_available_reference():
    choice = analysis_modes.select_realtime(
        {"price": 101.0, "open": None, "previous_close": 100.0},
        policy="auto",
    )
    assert choice.equation == "previous_close_only"


def test_news_auto_fades_thin_coverage():
    choice = analysis_modes.select_news(
        {"action": "BUY", "conviction": 0.8},
        {"weighted_score": 0.6, "articles_scored": 1},
        policy="auto",
    )
    assert choice.equation == "article_count_fade"


def test_forecaster_auto_uses_consensus_when_analysts_disagree():
    signals = {
        "news": sig("news", 0.8),
        "historical": sig("historical", -0.6),
        "realtime": sig("realtime", 0.4),
    }
    forecast = tally("GOOGL", signals, selection_policy="auto")
    assert forecast.equation_snapshot["forecaster"] == "consensus"
    assert forecast.agent_trace["selection_evidence"]["direction_signs"] == [-1, 1]


def test_configured_policy_honors_locked_equation():
    config.apply_equation_overrides({"realtime": "open_weighted"})
    config.apply_analysis_policy_overrides({"realtime": "configured"})
    signal = realtime_agent.build_signal(
        "GOOGL",
        {"price": 101.0, "open": 100.0, "previous_close": 99.0, "source": "test"},
        "run",
    )
    assert signal.equation_snapshot["realtime"] == "open_weighted"
    assert signal.agent_trace["selection_policy"] == "configured"


def test_agent_trace_explains_automatic_selection():
    config.apply_analysis_policy_overrides({"historical": "auto"})
    bars = [
        {"bar_date": f"2026-01-{i + 1:02d}", "close": 100 + i}
        for i in range(10)
    ]
    signal = historical_agent.build_signal("GOOGL", bars, "test", "run")
    assert signal.agent_trace["selection_policy"] == "auto"
    assert signal.agent_trace["selection_reason"]
    assert signal.agent_trace["selection_evidence"]["closes"] == 10


def test_news_agent_records_selected_equation_not_configured_default():
    config.apply_analysis_policy_overrides({"news": "auto"})
    payload = {
        "decision": {"action": "BUY", "conviction": 0.8},
        "signal_score": {"weighted_score": 0.5, "articles_scored": 1},
        "as_of": "2026-07-24T00:00:00Z",
    }
    signal = news_agent.build_signal("GOOGL", payload, "run", True)
    assert signal.equation_snapshot["news"] == "article_count_fade"
