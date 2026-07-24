"""Guild experiment wrapper exposes reproducible flags and numeric metrics."""

import pytest

import config
from guild_experiment import configure, experiment_metrics, parse_args


@pytest.fixture(autouse=True)
def restore_config():
    equations = config.equation_snapshot()
    policies = config.analysis_policy_snapshot()
    tunables = config.snapshot()
    mock_mode = config.MOCK_MODE
    gemini_reasoning = config.GEMINI_REASONING_IN_MOCK_MODE
    gemini_model = config.GEMINI_THINKING_MODEL
    thinking_level = config.GEMINI_THINKING_LEVEL
    yield
    config.apply_equation_overrides(equations)
    config.apply_analysis_policy_overrides(policies)
    config.apply_overrides(tunables)
    config.MOCK_MODE = mock_mode
    config.GEMINI_REASONING_IN_MOCK_MODE = gemini_reasoning
    config.GEMINI_THINKING_MODEL = gemini_model
    config.GEMINI_THINKING_LEVEL = thinking_level


def test_guild_flags_configure_equation_and_policy():
    args = parse_args([
        "--historical_policy", "configured",
        "--historical_equation", "slope_only",
        "--mock_mode", "true",
    ])
    configure(args)
    assert config.ANALYSIS_POLICY_BY_AGENT["historical"] == "configured"
    assert config.EQUATION_BY_AGENT["historical"] == "slope_only"
    assert config.MOCK_MODE is True


def test_experiment_metrics_are_guild_scalar_friendly():
    payload = {
        "signals": [
            {
                "source": "news",
                "action": "BUY",
                "direction": 0.4,
                "confidence": 0.7,
                "provenance": {"degraded": False},
            },
            {
                "source": "historical",
                "action": "SELL",
                "direction": -0.2,
                "confidence": 0.5,
                "provenance": {"degraded": True},
            },
        ],
        "forecasts": [
            {"action": "BUY", "direction": "UP", "score": 0.25, "confidence": 0.6}
        ],
    }
    metrics = experiment_metrics(payload)
    assert metrics["signals_count"] == 2.0
    assert metrics["degraded_sources"] == 1.0
    assert metrics["forecast_direction"] == 1.0
    assert metrics["forecast_action"] == 1.0
    assert metrics["historical_action"] == -1.0
    assert metrics["forecast_score"] == 0.25
    assert metrics["realtime_direction"] == 0.0
