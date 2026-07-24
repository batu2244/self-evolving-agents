"""Storage: dedup per (ticker, source, cycle), run logging, and end-to-end flow."""

import asyncio

import pytest

import config
from agents import forecaster_agent, historical_agent, realtime_agent
from contracts import Provenance, Signal, current_cycle


def make_signal(source="news", direction=0.5, cycle="2026-07-24T12Z", ticker="GOOGL"):
    return Signal(
        ticker=ticker,
        source=source,
        action="BUY",
        direction=direction,
        confidence=0.8,
        rationale=f"{source} rationale",
        cycle=cycle,
        provenance=Provenance(source_run_id="run-1", inputs_used=["x"]),
        model_snapshot={"provider": "gemini", "model": "gemini-test"},
    )


def test_signal_roundtrip(temp_db):
    temp_db.store_signal(make_signal(), "run-1")
    got = temp_db.latest_signals("GOOGL")
    assert set(got) == {"news"}
    assert got["news"].direction == pytest.approx(0.5)
    assert got["news"].action == "BUY"
    assert got["news"].model_snapshot["provider"] == "gemini"
    assert got["news"].provenance.inputs_used == ["x"]


def test_same_cycle_updates_instead_of_duplicating(temp_db):
    temp_db.store_signal(make_signal(direction=0.5), "run-1")
    temp_db.store_signal(make_signal(direction=-0.9), "run-2")
    with temp_db.session_scope() as s:
        rows = list(s.execute(temp_db.select(temp_db.SignalRow)).scalars())
    assert len(rows) == 1
    assert rows[0].direction == pytest.approx(-0.9)
    assert rows[0].run_id == "run-2"


def test_different_cycles_are_separate_rows(temp_db):
    temp_db.store_signal(make_signal(cycle="2026-07-24T12Z"), "run-1")
    temp_db.store_signal(make_signal(cycle="2026-07-24T13Z"), "run-2")
    with temp_db.session_scope() as s:
        rows = list(s.execute(temp_db.select(temp_db.SignalRow)).scalars())
    assert len(rows) == 2


def test_latest_signals_returns_most_recent_cycle(temp_db):
    old = make_signal(direction=0.1, cycle="2026-07-24T10Z")
    new = make_signal(direction=0.9, cycle="2026-07-24T11Z")
    temp_db.store_signal(old, "run-1")
    temp_db.store_signal(new, "run-2")
    assert temp_db.latest_signals("GOOGL")["news"].direction == pytest.approx(0.9)


def test_bars_dedup_on_ticker_and_date(temp_db):
    bars = [{"bar_date": "2026-07-20", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    temp_db.upsert_bars("run-1", "GOOGL", bars, "test")
    temp_db.upsert_bars("run-2", "GOOGL", [{**bars[0], "close": 9.9}], "test")
    stored = temp_db.recent_bars("GOOGL", 10)
    assert len(stored) == 1
    assert stored[0]["close"] == pytest.approx(9.9)


def test_snapshot_dedup_per_cycle(temp_db):
    temp_db.upsert_snapshot("run-1", "GOOGL", "2026-07-24T12Z", {"price": 100.0, "source": "t"})
    temp_db.upsert_snapshot("run-2", "GOOGL", "2026-07-24T12Z", {"price": 111.0, "source": "t"})
    snap = temp_db.latest_snapshot("GOOGL")
    assert snap["price"] == pytest.approx(111.0)


def test_forecast_dedup_per_ticker_cycle(temp_db):
    signals = {s: make_signal(source=s, direction=0.5) for s in config.SIGNAL_SOURCES}
    f1 = forecaster_agent.tally("GOOGL", signals, cycle="2026-07-24T12Z")
    temp_db.store_forecast(f1, "run-1")
    temp_db.store_forecast(f1, "run-2")
    with temp_db.session_scope() as s:
        rows = list(s.execute(temp_db.select(temp_db.ForecastRow)).scalars())
    assert len(rows) == 1
    assert rows[0].run_id == "run-2"
    assert rows[0].per_agent_contributions  # contributions persisted as JSON


def test_run_logging_records_success(temp_db):
    run_id = temp_db.start_run("historical", {"tickers": ["GOOGL"]})
    temp_db.finish_run(run_id, "success", details={"signals": 1})
    with temp_db.session_scope() as s:
        row = s.execute(
            temp_db.select(temp_db.AgentRun).where(temp_db.AgentRun.run_id == run_id)
        ).scalar_one()
    assert row.status == "success"
    assert row.finished_at is not None
    assert row.details["signals"] == 1


def test_run_logging_records_failure(temp_db):
    run_id = temp_db.start_run("realtime")
    temp_db.finish_run(run_id, "failed", error="upstream down")
    with temp_db.session_scope() as s:
        row = s.execute(
            temp_db.select(temp_db.AgentRun).where(temp_db.AgentRun.run_id == run_id)
        ).scalar_one()
    assert row.status == "failed" and "upstream down" in row.error


# --------------------------------------------------------------------------
# Agents end to end, in deterministic mock mode
# --------------------------------------------------------------------------


@pytest.fixture()
def mock_mode(monkeypatch):
    monkeypatch.setattr(config, "MOCK_MODE", True)
    import marketdata
    monkeypatch.setattr(marketdata.config, "MOCK_MODE", True)


def test_historical_agent_stores_bars_and_signal(temp_db, mock_mode):
    signals = asyncio.run(historical_agent.run(["GOOGL"], cycle="2026-07-24T12Z"))
    assert len(signals) == 1
    assert signals[0].source == "historical"
    assert -1.0 <= signals[0].direction <= 1.0
    assert temp_db.recent_bars("GOOGL", 200)
    assert temp_db.latest_signals("GOOGL", ("historical",))


def test_realtime_agent_stores_snapshot_and_signal(temp_db, mock_mode):
    signals = asyncio.run(realtime_agent.run(["GOOGL"], cycle="2026-07-24T12Z"))
    assert len(signals) == 1
    assert signals[0].source == "realtime"
    assert temp_db.latest_snapshot("GOOGL")["price"] > 0


def test_mock_mode_is_deterministic(temp_db, mock_mode):
    a = asyncio.run(historical_agent.run(["GOOGL"], cycle="2026-07-24T12Z"))
    b = asyncio.run(historical_agent.run(["GOOGL"], cycle="2026-07-24T13Z"))
    assert a[0].direction == b[0].direction
    assert a[0].confidence == b[0].confidence


def test_forecast_over_two_live_analysts_is_degraded(temp_db, mock_mode):
    cycle = "2026-07-24T12Z"
    asyncio.run(historical_agent.run(["GOOGL"], cycle=cycle))
    asyncio.run(realtime_agent.run(["GOOGL"], cycle=cycle))
    forecasts = asyncio.run(forecaster_agent.run(["GOOGL"], cycle=cycle))
    assert len(forecasts) == 1
    f = forecasts[0]
    # news never ran, so the tally must not present itself as the full view
    assert f.provenance.degraded is True
    assert "news" in f.provenance.notes
    assert {c.source for c in f.per_agent_contributions} == {"historical", "realtime"}


def test_current_cycle_is_hourly_bucket():
    from datetime import datetime, timezone
    stamp = datetime(2026, 7, 24, 14, 37, tzinfo=timezone.utc)
    assert current_cycle(stamp) == "2026-07-24T14Z"
