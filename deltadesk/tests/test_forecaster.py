"""Weighted 3-way tally, direction thresholds, and degraded provenance."""

import pytest

import config
from agents.forecaster_agent import tally
from contracts import Provenance, Signal


def sig(source, direction, confidence=1.0, degraded=False, ticker="GOOGL"):
    return Signal(
        ticker=ticker,
        source=source,
        direction=direction,
        confidence=confidence,
        rationale=f"{source} test signal",
        provenance=Provenance(source_run_id=f"{source}-run", degraded=degraded),
    )


def all_three(news=0.0, historical=0.0, realtime=0.0, confidence=1.0):
    return {
        "news": sig("news", news, confidence),
        "historical": sig("historical", historical, confidence),
        "realtime": sig("realtime", realtime, confidence),
    }


# --------------------------------------------------------------------------
# Weighting
# --------------------------------------------------------------------------


def test_unanimous_bullish_signals_give_full_score():
    f = tally("GOOGL", all_three(1.0, 1.0, 1.0))
    assert f.direction == "UP"
    assert f.score == pytest.approx(1.0)
    assert f.confidence == pytest.approx(1.0)


def test_unanimous_bearish_signals_give_full_negative_score():
    f = tally("GOOGL", all_three(-1.0, -1.0, -1.0))
    assert f.direction == "DOWN"
    assert f.score == pytest.approx(-1.0)


def test_score_is_the_configured_weighted_average():
    f = tally("GOOGL", all_three(1.0, -1.0, 1.0))
    expected = (
        config.SIGNAL_WEIGHTS["news"]
        - config.SIGNAL_WEIGHTS["historical"]
        + config.SIGNAL_WEIGHTS["realtime"]
    )
    assert f.score == pytest.approx(expected, abs=1e-4)


def test_confidence_scales_each_contribution():
    full = tally("GOOGL", all_three(1.0, 1.0, 1.0, confidence=1.0))
    half = tally("GOOGL", all_three(1.0, 1.0, 1.0, confidence=0.5))
    assert half.score == pytest.approx(full.score * 0.5, abs=1e-4)


def test_contributions_sum_to_score():
    f = tally("GOOGL", all_three(0.8, -0.3, 0.5, confidence=0.9))
    assert sum(c.contribution for c in f.per_agent_contributions) == pytest.approx(f.score, abs=1e-3)


def test_every_reporting_source_is_attributed():
    f = tally("GOOGL", all_three(0.5, 0.5, 0.5))
    assert {c.source for c in f.per_agent_contributions} == set(config.SIGNAL_SOURCES)
    assert sum(c.weight for c in f.per_agent_contributions) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------


def test_score_inside_the_band_is_flat():
    f = tally("GOOGL", all_three(0.1, 0.1, 0.1), threshold=0.5)
    assert f.direction == "FLAT"


def test_score_just_above_threshold_is_up():
    f = tally("GOOGL", all_three(1.0, 1.0, 1.0), threshold=0.99)
    assert f.direction == "UP"


def test_threshold_boundary_is_exclusive():
    """Exactly at the threshold is FLAT; the band is inclusive of its edges."""
    f = tally("GOOGL", all_three(0.5, 0.5, 0.5), threshold=0.5)
    assert f.score == pytest.approx(0.5)
    assert f.direction == "FLAT"


def test_opposing_signals_cancel_to_flat():
    signals = {
        "news": sig("news", 1.0),
        "historical": sig("historical", -1.0),
        "realtime": sig("realtime", 0.0),
    }
    f = tally("GOOGL", signals, threshold=0.2)
    assert f.direction == "FLAT"


# --------------------------------------------------------------------------
# Degraded provenance
# --------------------------------------------------------------------------


def test_missing_source_marks_provenance_degraded():
    signals = {"news": sig("news", 1.0), "historical": sig("historical", 1.0)}
    f = tally("GOOGL", signals)
    assert f.provenance.degraded is True
    assert "realtime" in f.provenance.notes
    assert len(f.per_agent_contributions) == 2


def test_weights_renormalize_over_reporting_sources():
    signals = {"news": sig("news", 1.0), "historical": sig("historical", 1.0)}
    f = tally("GOOGL", signals)
    # Two full-strength agreeing signals still produce a full-strength score.
    assert f.score == pytest.approx(1.0)
    assert sum(c.weight for c in f.per_agent_contributions) == pytest.approx(1.0)


def test_partial_coverage_lowers_confidence():
    full = tally("GOOGL", all_three(1.0, 1.0, 1.0))
    partial = tally("GOOGL", {"news": sig("news", 1.0), "historical": sig("historical", 1.0)})
    assert partial.confidence < full.confidence
    assert partial.score == pytest.approx(full.score)  # score unaffected, trust reduced


def test_upstream_degraded_signal_propagates():
    signals = all_three(1.0, 1.0, 1.0)
    signals["news"] = sig("news", 1.0, degraded=True)
    f = tally("GOOGL", signals)
    assert f.provenance.degraded is True
    assert "news" in f.provenance.notes


def test_no_signals_at_all_is_flat_and_degraded():
    f = tally("GOOGL", {})
    assert f.direction == "FLAT"
    assert f.score == 0.0 and f.confidence == 0.0
    assert f.provenance.degraded is True
    assert f.per_agent_contributions == []


def test_unknown_source_is_ignored():
    signals = all_three(1.0, 1.0, 1.0)
    signals["astrology"] = sig("astrology", -1.0)
    f = tally("GOOGL", signals)
    assert {c.source for c in f.per_agent_contributions} == set(config.SIGNAL_SOURCES)
    assert f.score == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_disagreement_lowers_confidence_below_agreement():
    agree = tally("GOOGL", all_three(0.6, 0.6, 0.6))
    disagree = tally("GOOGL", all_three(0.6, -0.6, 0.6))
    assert disagree.confidence < agree.confidence


def test_forecast_is_labelled_research_and_deterministic():
    f = tally("GOOGL", all_three(0.5, 0.5, 0.5))
    assert f.deterministic is True
    assert f.mode == "paper-trading-research"
    assert f.ticker == "GOOGL"


def test_tally_is_deterministic():
    a = tally("GOOGL", all_three(0.4, -0.2, 0.7, confidence=0.8), cycle="2026-07-24T14Z")
    b = tally("GOOGL", all_three(0.4, -0.2, 0.7, confidence=0.8), cycle="2026-07-24T14Z")
    assert a.score == b.score and a.confidence == b.confidence
    assert a.rationale == b.rationale
