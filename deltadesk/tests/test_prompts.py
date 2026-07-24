"""Agent prompt surfaces are explicit, overrideable, and attributable."""

import prompts
from agents.forecaster_agent import tally
from contracts import Provenance, Signal


def test_default_prompts_have_stable_snapshots():
    snap = prompts.snapshot("historical")
    assert snap["agent"] == "historical"
    assert snap["source"] == "default"
    assert len(snap["prompt_hash"]) == 16
    assert "Historical Analyst" in snap["system_prompt"]


def test_prompt_file_override_changes_snapshot(tmp_path):
    path = tmp_path / "historical.prompt"
    path.write_text("You are a stricter historical analyst.", encoding="utf-8")
    try:
        prompts.set_prompt_override("historical", path)
        snap = prompts.snapshot("historical")
    finally:
        prompts.clear_prompt_overrides()
    assert snap["source"] == str(path)
    assert snap["system_prompt"] == "You are a stricter historical analyst."


def test_signal_prompt_snapshot_persists(temp_db):
    signal = Signal(
        ticker="GOOGL",
        source="historical",
        direction=0.2,
        confidence=0.7,
        rationale="test",
        prompt_snapshot=prompts.snapshot("historical"),
        provenance=Provenance(source_run_id="historical-run"),
    )
    temp_db.store_signal(signal, "historical-run")
    got = temp_db.latest_signals("GOOGL", ("historical",))["historical"]
    assert got.prompt_snapshot["agent"] == "historical"
    assert got.prompt_snapshot["prompt_hash"] == signal.prompt_snapshot["prompt_hash"]


def test_forecast_prompt_snapshot_persists(temp_db):
    signals = {
        "historical": Signal(
            ticker="GOOGL",
            source="historical",
            direction=1.0,
            confidence=1.0,
            rationale="test",
        )
    }
    forecast = tally("GOOGL", signals, cycle="2026-07-24T12Z")
    temp_db.store_forecast(forecast, "forecaster-run")
    with temp_db.session_scope() as s:
        row = s.execute(temp_db.select(temp_db.ForecastRow)).scalar_one()
    assert row.prompt_snapshot["agent"] == "forecaster"
    assert row.prompt_snapshot["prompt_hash"] == forecast.prompt_snapshot["prompt_hash"]
