#!/usr/bin/env python3
"""Run one DeltaDesk experiment and expose its results to Guild.ai."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

import config
from contracts import current_cycle
from run_agents import run_all


def _bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Guild-tracked DeltaDesk experiment")
    parser.add_argument("--ticker", default="GOOGL")
    parser.add_argument("--cycle")
    parser.add_argument("--mock_mode", default="true")
    parser.add_argument("--gemini_model", default=config.GEMINI_THINKING_MODEL)
    parser.add_argument("--gemini_reasoning", default="false")
    parser.add_argument(
        "--thinking_level",
        choices=("low", "medium", "high"),
        default=config.GEMINI_THINKING_LEVEL,
    )

    for agent in config.EQUATION_CHOICES:
        parser.add_argument(
            f"--{agent}_policy",
            choices=config.ANALYSIS_POLICIES,
            default="auto",
        )
        parser.add_argument(
            f"--{agent}_equation",
            choices=config.EQUATION_CHOICES[agent],
            default=config.EQUATION_BY_AGENT[agent],
        )

    parser.add_argument("--news_weight", type=float, default=config.SIGNAL_WEIGHTS["news"])
    parser.add_argument(
        "--historical_weight", type=float, default=config.SIGNAL_WEIGHTS["historical"]
    )
    parser.add_argument(
        "--realtime_weight", type=float, default=config.SIGNAL_WEIGHTS["realtime"]
    )
    parser.add_argument("--direction_threshold", type=float, default=config.DIRECTION_THRESHOLD)
    parser.add_argument(
        "--news_thin_coverage_max", type=int, default=config.NEWS_THIN_COVERAGE_MAX
    )
    parser.add_argument(
        "--news_action_conviction_min",
        type=float,
        default=config.NEWS_ACTION_CONVICTION_MIN,
    )
    parser.add_argument(
        "--news_weak_score_abs", type=float, default=config.NEWS_WEAK_SCORE_ABS
    )
    parser.add_argument(
        "--forecast_low_confidence", type=float, default=config.FORECAST_LOW_CONFIDENCE
    )
    return parser.parse_args(argv)


def configure(args: argparse.Namespace) -> None:
    config.MOCK_MODE = _bool(args.mock_mode)
    config.GEMINI_THINKING_MODEL = args.gemini_model
    config.GEMINI_THINKING_LEVEL = args.thinking_level
    config.GEMINI_REASONING_IN_MOCK_MODE = _bool(args.gemini_reasoning)
    if config.GEMINI_REASONING_IN_MOCK_MODE:
        project_dir = Path(os.getenv("PROJECT_DIR", config.PROJECT_ROOT))
        shared_env = project_dir.parent / "google-news-agent" / ".env"
        if shared_env.exists():
            load_dotenv(shared_env, override=False)
    guild_sample = Path("sample_output.json")
    if guild_sample.exists():
        config.NEWS_AGENT_SAMPLE = guild_sample.resolve()
    config.apply_equation_overrides({
        agent: getattr(args, f"{agent}_equation")
        for agent in config.EQUATION_CHOICES
    })
    config.apply_analysis_policy_overrides({
        agent: getattr(args, f"{agent}_policy")
        for agent in config.EQUATION_CHOICES
    })
    config.apply_overrides({
        "SIGNAL_WEIGHTS.news": args.news_weight,
        "SIGNAL_WEIGHTS.historical": args.historical_weight,
        "SIGNAL_WEIGHTS.realtime": args.realtime_weight,
        "DIRECTION_THRESHOLD": args.direction_threshold,
        "NEWS_THIN_COVERAGE_MAX": args.news_thin_coverage_max,
        "NEWS_ACTION_CONVICTION_MIN": args.news_action_conviction_min,
        "NEWS_WEAK_SCORE_ABS": args.news_weak_score_abs,
        "FORECAST_LOW_CONFIDENCE": args.forecast_low_confidence,
    })


def experiment_metrics(payload: dict) -> dict[str, float]:
    signals = payload["signals"]
    forecasts = payload["forecasts"]
    forecast = forecasts[0] if forecasts else None
    direction_code = {"DOWN": -1.0, "FLAT": 0.0, "UP": 1.0}
    action_code = {"SELL": -1.0, "HOLD": 0.0, "BUY": 1.0}
    metrics = {
        "signals_count": float(len(signals)),
        "degraded_sources": float(sum(s["provenance"]["degraded"] for s in signals)),
        "forecast_score": float(forecast["score"]) if forecast else 0.0,
        "forecast_confidence": float(forecast["confidence"]) if forecast else 0.0,
        "forecast_direction": direction_code.get(
            forecast["direction"] if forecast else "FLAT", 0.0
        ),
        "forecast_action": action_code.get(
            forecast.get("action", "HOLD") if forecast else "HOLD", 0.0
        ),
        "learning_policy_version": float(
            (forecast.get("learning_snapshot") or {}).get("version", 0)
        ) if forecast else 0.0,
        "learning_observations": float(
            (forecast.get("learning_snapshot") or {}).get("observations", 0)
        ) if forecast else 0.0,
        "learning_reliability": float(
            (forecast.get("learning_snapshot") or {}).get("reliability", 0.0)
        ) if forecast else 0.0,
    }
    by_source = {signal["source"]: signal for signal in signals}
    for source in config.SIGNAL_SOURCES:
        signal = by_source.get(source)
        metrics[f"{source}_direction"] = float(signal["direction"]) if signal else 0.0
        metrics[f"{source}_confidence"] = float(signal["confidence"]) if signal else 0.0
        metrics[f"{source}_action"] = action_code.get(
            signal.get("action", "HOLD") if signal else "HOLD", 0.0
        )
    return metrics


def print_summary(payload: dict) -> None:
    for key, value in experiment_metrics(payload).items():
        print(f"{key}: {value}")

    for signal in payload["signals"]:
        equation = signal.get("equation_snapshot", {}).get(signal["source"], "unknown")
        policy = signal.get("agent_trace", {}).get("selection_policy", "unknown")
        print(
            f"selected_{signal['source']}_mode: {equation} ({policy}) "
            f"action={signal['action']}"
        )
    for forecast in payload["forecasts"]:
        equation = forecast.get("equation_snapshot", {}).get("forecaster", "unknown")
        policy = forecast.get("agent_trace", {}).get("selection_policy", "unknown")
        print(
            f"selected_forecaster_mode: {equation} ({policy}) "
            f"action={forecast['action']}"
        )
        learned = forecast.get("learning_snapshot") or {}
        print(
            f"active_learning_policy: version={learned.get('version', 0)} "
            f"observations={learned.get('observations', 0)}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure(args)
    cycle = args.cycle or current_cycle()
    payload = asyncio.run(run_all([args.ticker.upper()], cycle))
    Path("guild_result.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
