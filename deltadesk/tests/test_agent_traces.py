"""Agents emit google-news-agent-style traces, not only final scores."""

from agents import historical_agent, realtime_agent
from agents.forecaster_agent import tally
from contracts import Provenance, Signal


def test_historical_signal_contains_candidate_reads():
    bars = [{"bar_date": f"2026-01-{i + 1:02d}", "close": 100 + i} for i in range(30)]
    signal = historical_agent.build_signal("GOOGL", bars, "test", "historical-run")
    trace = signal.agent_trace
    assert trace["workflow"] == ["collect_historical_bars", "evaluate_equations", "select_signal"]
    assert trace["selected_equation"] == signal.equation_snapshot["historical"]
    assert {c["equation"] for c in trace["candidate_reads"]} == {
        "trend_blend",
        "slope_only",
        "ma_cross",
    }


def test_realtime_signal_contains_candidate_reads():
    quote = {
        "price": 102.0,
        "open": 100.0,
        "previous_close": 104.0,
        "source": "test",
    }
    signal = realtime_agent.build_signal("GOOGL", quote, "realtime-run")
    trace = signal.agent_trace
    assert trace["workflow"] == ["collect_quote", "evaluate_equations", "select_signal"]
    assert trace["selected_read"]["equation"] == signal.equation_snapshot["realtime"]
    assert len(trace["candidate_reads"]) == 3


def test_forecaster_contains_candidate_forecasts():
    signals = {
        "historical": Signal(
            ticker="GOOGL",
            source="historical",
            direction=1.0,
            confidence=0.5,
            rationale="test",
            provenance=Provenance(source_run_id="historical-run"),
        )
    }
    forecast = tally("GOOGL", signals)
    trace = forecast.agent_trace
    assert trace["workflow"] == ["read_signals", "evaluate_forecast_equations", "select_forecast"]
    assert trace["selected_equation"] == forecast.equation_snapshot["forecaster"]
    assert {c["equation"] for c in trace["candidate_forecasts"]} == {
        "confidence_weighted",
        "direction_only",
        "consensus",
    }


def test_agent_trace_persists_for_signals(temp_db):
    signal = Signal(
        ticker="GOOGL",
        source="news",
        direction=0.1,
        confidence=0.2,
        rationale="test",
        agent_trace={"candidate_reads": [{"equation": "weighted_score"}]},
    )
    temp_db.store_signal(signal, "news-run")
    got = temp_db.latest_signals("GOOGL", ("news",))["news"]
    assert got.agent_trace["candidate_reads"][0]["equation"] == "weighted_score"
