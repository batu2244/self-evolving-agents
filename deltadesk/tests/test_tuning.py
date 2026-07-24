"""The improvability seam: every behavioural knob is externally tunable,
bounded, and recorded with the forecast it produced."""

import json

import pytest

import config
from agents.forecaster_agent import tally
from tests.test_forecaster import all_three


@pytest.fixture(autouse=True)
def restore_config():
    """Tunables are module globals; put them back after each test."""
    before = config.snapshot()
    yield
    config.apply_overrides(before)


def test_every_weight_and_threshold_is_declared_tunable():
    for source in config.SIGNAL_SOURCES:
        assert f"SIGNAL_WEIGHTS.{source}" in config.TUNABLES
    assert "DIRECTION_THRESHOLD" in config.TUNABLES


def test_snapshot_reports_current_values():
    snap = config.snapshot()
    assert snap["SIGNAL_WEIGHTS.news"] == config.SIGNAL_WEIGHTS["news"]
    assert snap["DIRECTION_THRESHOLD"] == config.DIRECTION_THRESHOLD
    assert set(snap) == set(config.TUNABLES)


def test_override_changes_behaviour():
    config.apply_overrides({
        "SIGNAL_WEIGHTS.news": 1.0,
        "SIGNAL_WEIGHTS.historical": 0.0,
        "SIGNAL_WEIGHTS.realtime": 0.0,
    })
    f = tally("GOOGL", all_three(news=1.0, historical=-1.0, realtime=-1.0))
    assert f.score == pytest.approx(1.0)  # only news counts now


def test_threshold_override_changes_direction():
    signals = all_three(0.3, 0.3, 0.3)
    config.apply_overrides({"DIRECTION_THRESHOLD": 0.9})
    assert tally("GOOGL", signals).direction == "FLAT"
    config.apply_overrides({"DIRECTION_THRESHOLD": 0.1})
    assert tally("GOOGL", signals).direction == "UP"


def test_unknown_key_is_rejected():
    with pytest.raises(KeyError):
        config.apply_overrides({"SIGNAL_WEIGHTS.astrology": 1.0})


def test_out_of_bounds_value_is_rejected_not_clamped():
    with pytest.raises(ValueError, match="outside its allowed range"):
        config.apply_overrides({"DIRECTION_THRESHOLD": 5.0})
    with pytest.raises(ValueError):
        config.apply_overrides({"SIGNAL_WEIGHTS.news": -0.5})


def test_all_zero_weights_is_rejected():
    """A tuner must not be able to silence every analyst at once."""
    with pytest.raises(ValueError):
        config.apply_overrides({
            "SIGNAL_WEIGHTS.news": 0.0,
            "SIGNAL_WEIGHTS.historical": 0.0,
            "SIGNAL_WEIGHTS.realtime": 0.0,
        })


def test_integer_tunables_stay_integers():
    config.apply_overrides({"MA_SHORT": 12.0, "HISTORICAL_DAYS": 45.0})
    assert isinstance(config.MA_SHORT, int) and config.MA_SHORT == 12
    assert isinstance(config.HISTORICAL_DAYS, int)


def test_forecast_records_the_config_that_produced_it():
    config.apply_overrides({"DIRECTION_THRESHOLD": 0.42})
    f = tally("GOOGL", all_three(0.5, 0.5, 0.5))
    assert f.config_snapshot["DIRECTION_THRESHOLD"] == pytest.approx(0.42)
    assert f.config_snapshot["SIGNAL_WEIGHTS.news"] == config.SIGNAL_WEIGHTS["news"]


def test_empty_forecast_also_records_config():
    f = tally("GOOGL", {})
    assert f.config_snapshot  # attributable even when nothing reported


def test_overrides_file_roundtrip(tmp_path):
    path = tmp_path / "tune.json"
    path.write_text(json.dumps({"DIRECTION_THRESHOLD": 0.33}))
    applied = config.apply_overrides(config.load_overrides_file(path))
    assert applied["DIRECTION_THRESHOLD"] == pytest.approx(0.33)
    assert config.DIRECTION_THRESHOLD == pytest.approx(0.33)


def test_config_snapshot_persists_to_the_database(temp_db):
    config.apply_overrides({"DIRECTION_THRESHOLD": 0.27})
    f = tally("GOOGL", all_three(0.6, 0.6, 0.6), cycle="2026-07-24T12Z")
    temp_db.store_forecast(f, "run-1")
    with temp_db.session_scope() as s:
        row = s.execute(temp_db.select(temp_db.ForecastRow)).scalar_one()
    assert row.config_snapshot["DIRECTION_THRESHOLD"] == pytest.approx(0.27)
