"""Signal derivation per source: trend, momentum, and the news mapping."""

import pytest

import config
import derive


# --------------------------------------------------------------------------
# Historical: trend
# --------------------------------------------------------------------------


def test_rising_series_is_bullish():
    closes = [100 + i for i in range(60)]
    out = derive.derive_trend(closes)
    assert out["direction"] > 0.5
    assert out["slope_pct_per_day"] > 0
    assert out["ma_relationship"] == "short_above_long"


def test_falling_series_is_bearish():
    closes = [200 - i for i in range(60)]
    out = derive.derive_trend(closes)
    assert out["direction"] < -0.5
    assert out["ma_relationship"] == "short_below_long"


def test_flat_series_is_neutral():
    out = derive.derive_trend([150.0] * 60)
    assert out["direction"] == pytest.approx(0.0, abs=1e-6)
    assert out["slope_pct_per_day"] == pytest.approx(0.0, abs=1e-9)


def test_direction_stays_in_range_on_violent_series():
    closes = [100 * (1.2**i) for i in range(40)]
    out = derive.derive_trend(closes)
    assert -1.0 <= out["direction"] <= 1.0
    assert 0.0 <= out["confidence"] <= 1.0


def test_short_history_is_degraded_and_less_confident():
    long_run = derive.derive_trend([100 + i for i in range(60)])
    short_run = derive.derive_trend([100 + i for i in range(8)])
    assert short_run["ma_relationship"] == "unknown"
    assert short_run["confidence"] < long_run["confidence"]


def test_insufficient_data_returns_neutral():
    out = derive.derive_trend([100.0])
    assert out["direction"] == 0.0
    assert out["confidence"] == 0.0


def test_mean_reversion_flag_fades_an_overextended_move():
    calm = [100 + i * 0.1 for i in range(40)]
    spiked = calm + [calm[-1] * 1.25]  # a large final jump
    out = derive.derive_trend(spiked)
    assert out["mean_reversion_flag"] is True
    assert abs(out["mean_reversion_z"]) >= config.MEAN_REVERSION_Z


def test_mean_reversion_fades_rather_than_flips():
    """An extended uptrend is still an uptrend; the flag tempers, it does not invert."""
    closes = [100 + i * 0.5 for i in range(40)] + [100 + 39 * 0.5 + 12]
    out = derive.derive_trend(closes)
    assert out["mean_reversion_flag"] is True
    assert out["direction"] > 0


# --------------------------------------------------------------------------
# Realtime: momentum
# --------------------------------------------------------------------------


def test_price_above_open_and_prev_close_is_bullish():
    out = derive.derive_momentum(
        {"price": 102.0, "open": 100.0, "previous_close": 100.0,
         "volume": 1e6, "average_volume": 1e6}
    )
    assert out["direction"] > 0
    assert out["vs_open_pct"] == pytest.approx(2.0)
    assert out["vs_prev_close_pct"] == pytest.approx(2.0)


def test_price_below_open_is_bearish():
    out = derive.derive_momentum({"price": 98.0, "open": 100.0, "previous_close": 100.0})
    assert out["direction"] < 0


def test_volume_anomaly_raises_confidence():
    quiet = derive.derive_momentum(
        {"price": 101.0, "open": 100.0, "previous_close": 100.0,
         "volume": 1e6, "average_volume": 1e6}
    )
    heavy = derive.derive_momentum(
        {"price": 101.0, "open": 100.0, "previous_close": 100.0,
         "volume": 3e6, "average_volume": 1e6}
    )
    assert heavy["volume_anomaly"] is True
    assert quiet["volume_anomaly"] is False
    assert heavy["confidence"] > quiet["confidence"]
    assert heavy["direction"] == quiet["direction"]  # volume affects trust, not direction


def test_missing_price_is_neutral():
    out = derive.derive_momentum({"price": None})
    assert out["direction"] == 0.0 and out["confidence"] == 0.0


def test_missing_references_yields_no_reading():
    out = derive.derive_momentum({"price": 100.0, "open": None, "previous_close": None})
    assert out["direction"] == 0.0 and out["confidence"] == 0.0


def test_momentum_clamps_on_extreme_move():
    out = derive.derive_momentum({"price": 200.0, "open": 100.0, "previous_close": 100.0})
    assert out["direction"] == 1.0


# --------------------------------------------------------------------------
# News mapping
# --------------------------------------------------------------------------


def test_news_maps_weighted_score_to_direction():
    out = derive.derive_news(
        {"action": "BUY", "conviction": 0.8},
        {"weighted_score": 0.42, "articles_scored": 10},
    )
    assert out["direction"] == pytest.approx(0.42)
    assert out["confidence"] > 0.5


def test_news_with_no_articles_is_neutral():
    out = derive.derive_news({"action": "HOLD", "conviction": 0.0},
                             {"weighted_score": 0.0, "articles_scored": 0})
    assert out["direction"] == 0.0 and out["confidence"] == 0.0


def test_news_falls_back_to_action_when_score_absent():
    out = derive.derive_news({"action": "SELL", "conviction": 0.6},
                             {"weighted_score": 0.0, "articles_scored": 5})
    assert out["direction"] < 0


def test_news_confidence_grows_with_coverage():
    thin = derive.derive_news({"action": "BUY", "conviction": 0.7},
                              {"weighted_score": 0.3, "articles_scored": 2})
    broad = derive.derive_news({"action": "BUY", "conviction": 0.7},
                               {"weighted_score": 0.3, "articles_scored": 10})
    assert broad["confidence"] > thin["confidence"]
