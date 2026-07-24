"""Named equation strategies give agents a measurable improvement surface."""

import pytest

import config
import derive
from agents.forecaster_agent import tally
from contracts import Provenance, Signal


@pytest.fixture(autouse=True)
def restore_equations():
    before = config.equation_snapshot()
    yield
    config.apply_equation_overrides(before)


def sig(source, direction, confidence=1.0):
    return Signal(
        ticker="GOOGL",
        source=source,
        direction=direction,
        confidence=confidence,
        rationale=f"{source} signal",
        provenance=Provenance(source_run_id=f"{source}-run"),
    )


def test_realtime_equations_can_produce_different_reads():
    quote = {"price": 102.0, "open": 100.0, "previous_close": 104.0}
    balanced = derive.derive_momentum(quote, equation="balanced_momentum")
    open_weighted = derive.derive_momentum(quote, equation="open_weighted")
    prev_only = derive.derive_momentum(quote, equation="previous_close_only")
    assert balanced["direction"] == pytest.approx(0.0192, abs=1e-4)
    assert open_weighted["direction"] > balanced["direction"]
    assert prev_only["direction"] < 0


def test_news_equation_can_fade_thin_article_coverage():
    decision = {"action": "BUY", "conviction": 0.8}
    score = {"weighted_score": 0.6, "articles_scored": 1}
    base = derive.derive_news(decision, score, equation="weighted_score")
    faded = derive.derive_news(decision, score, equation="article_count_fade")
    assert faded["direction"] < base["direction"]
    assert faded["equation"] == "article_count_fade"


def test_forecaster_direction_only_ignores_signal_confidence_in_score():
    signals = {"historical": sig("historical", 1.0, confidence=0.25)}
    base = tally("GOOGL", signals, threshold=0.0)
    config.apply_equation_overrides({"forecaster": "direction_only"})
    direction_only = tally("GOOGL", signals, threshold=0.0)
    assert base.score == pytest.approx(0.25)
    assert direction_only.score == pytest.approx(1.0)
    assert direction_only.equation_snapshot["forecaster"] == "direction_only"


def test_equation_override_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        config.apply_equation_overrides({"historical": "moon_math"})


def test_signal_equation_snapshot_persists(temp_db):
    signal = sig("realtime", 0.4)
    signal.equation_snapshot = {"realtime": "open_weighted"}
    temp_db.store_signal(signal, "realtime-run")
    got = temp_db.latest_signals("GOOGL", ("realtime",))["realtime"]
    assert got.equation_snapshot == {"realtime": "open_weighted"}


def test_forecast_equation_snapshot_persists(temp_db):
    config.apply_equation_overrides({"forecaster": "consensus"})
    forecast = tally("GOOGL", {"historical": sig("historical", 0.8)}, cycle="2026-07-24T12Z")
    temp_db.store_forecast(forecast, "forecaster-run")
    with temp_db.session_scope() as s:
        row = s.execute(temp_db.select(temp_db.ForecastRow)).scalar_one()
    assert row.equation_snapshot["forecaster"] == "consensus"
