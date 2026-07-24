import asyncio

import pytest

import config
import daily_learning
from contracts import Provenance, Signal


def signal(source, action, equation, cycle, direction=0.5):
    return Signal(
        ticker="GOOGL",
        source=source,
        action=action,
        direction=direction,
        confidence=0.8,
        rationale="test",
        cycle=cycle,
        equation_snapshot={source: equation},
        provenance=Provenance(inputs_used=["fixture"]),
    )


def add_bars(db, dates_and_closes):
    db.upsert_bars(
        "bars",
        "GOOGL",
        [
            {
                "bar_date": bar_date,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100,
            }
            for bar_date, close in dates_and_closes
        ],
        "test",
    )


def test_action_scoring_rewards_correct_direction_and_quiet_hold():
    buy_score, buy_correct = daily_learning.score_action("BUY", 2.0)
    sell_score, sell_correct = daily_learning.score_action("SELL", -2.0)
    hold_score, hold_correct = daily_learning.score_action("HOLD", 0.0)
    wrong_score, wrong_correct = daily_learning.score_action("BUY", -2.0)

    assert buy_score == sell_score == hold_score == pytest.approx(1.0)
    assert buy_correct and sell_correct and hold_correct
    assert wrong_score == pytest.approx(-1.0)
    assert not wrong_correct


def test_daily_learning_labels_next_close_and_is_idempotent(
    temp_db, monkeypatch
):
    monkeypatch.setattr(config, "LEARNING_MIN_OBSERVATIONS", 1)
    temp_db.store_signal(
        signal("news", "BUY", "weighted_score", "2026-07-20T12Z"),
        "news-run",
    )
    add_bars(
        temp_db,
        [("2026-07-20", 100.0), ("2026-07-21", 102.0)],
    )

    first = asyncio.run(
        daily_learning.run_daily("2026-07-21", refresh_market_data=False)
    )
    second = asyncio.run(
        daily_learning.run_daily("2026-07-21", refresh_market_data=False)
    )

    outcomes = temp_db.performance_outcomes()
    assert first["status"] == "success"
    assert first["outcomes_added"] == 1
    assert second["idempotent"] is True
    assert len(outcomes) == 1
    assert outcomes[0]["performance_score"] == pytest.approx(1.0)
    assert temp_db.agent_performance_context("news")["recommended_equation"] == (
        "weighted_score"
    )


def test_daily_learning_does_not_use_future_exit_bar(temp_db):
    temp_db.store_signal(
        signal("historical", "BUY", "trend_blend", "2026-07-20T12Z"),
        "historical-run",
    )
    add_bars(
        temp_db,
        [("2026-07-20", 100.0), ("2026-07-22", 103.0)],
    )
    result = asyncio.run(
        daily_learning.run_daily("2026-07-21", refresh_market_data=False)
    )
    assert result["outcomes_added"] == 0
    assert temp_db.performance_outcomes() == []


def test_learned_weights_are_normalized_bounded_and_reward_performance(
    monkeypatch,
):
    monkeypatch.setattr(config, "LEARNING_MIN_OBSERVATIONS", 1)
    monkeypatch.setattr(config, "LEARNING_RATE", 1.0)
    monkeypatch.setattr(config, "LEARNING_MAX_WEIGHT_STEP", 0.05)
    previous = {
        "forecaster": {
            "config_overrides": {
                "SIGNAL_WEIGHTS.news": 0.40,
                "SIGNAL_WEIGHTS.historical": 0.35,
                "SIGNAL_WEIGHTS.realtime": 0.25,
            }
        }
    }
    outcomes = [
        {"agent": "news", "performance_score": 1.0},
        {"agent": "historical", "performance_score": 0.0},
        {"agent": "realtime", "performance_score": -1.0},
    ]
    learned = daily_learning.learned_weights(outcomes, previous)

    assert sum(learned.values()) == pytest.approx(1.0)
    assert learned["news"] > 0.40
    assert learned["realtime"] < 0.25
    assert all(
        abs(learned[source] - previous["forecaster"]["config_overrides"][
            f"SIGNAL_WEIGHTS.{source}"
        ]) <= 0.050001
        for source in config.SIGNAL_SOURCES
    )


def test_activate_policy_applies_only_learned_source_weights(
    temp_db, monkeypatch
):
    original = dict(config.SIGNAL_WEIGHTS)
    monkeypatch.setattr(config, "SIGNAL_WEIGHTS", dict(original))
    temp_db.upsert_agent_policy(
        agent="forecaster",
        learning_date="2026-07-21",
        recommended_equation="confidence_weighted",
        equation_stats={},
        reliability=0.7,
        observations=3,
        config_overrides={
            "SIGNAL_WEIGHTS.news": 0.45,
            "SIGNAL_WEIGHTS.historical": 0.35,
            "SIGNAL_WEIGHTS.realtime": 0.20,
        },
    )
    applied = temp_db.activate_learned_policy()
    assert applied["SIGNAL_WEIGHTS.news"] == pytest.approx(0.45)
    assert config.SIGNAL_WEIGHTS["realtime"] == pytest.approx(0.20)
