#!/usr/bin/env python3
"""Evaluate prior calls and publish one bounded DeltaDesk policy per day."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import config
import database as db
import marketdata
from contracts import clamp

log = logging.getLogger("deltadesk.learning")

AGENTS = (*config.SIGNAL_SOURCES, "forecaster")


def score_action(action: str, return_pct: float) -> tuple[float, bool]:
    """Score BUY/SELL/HOLD on a common -1..+1 utility scale."""
    normalized = action.upper()
    if normalized == "BUY":
        return clamp(return_pct / config.PERFORMANCE_FULL_SCALE_PCT), (
            return_pct > config.HOLD_BAND_PCT
        )
    if normalized == "SELL":
        return clamp(-return_pct / config.PERFORMANCE_FULL_SCALE_PCT), (
            return_pct < -config.HOLD_BAND_PCT
        )
    score = clamp(
        (config.HOLD_BAND_PCT - abs(return_pct)) / config.HOLD_BAND_PCT
    )
    return score, abs(return_pct) <= config.HOLD_BAND_PCT


def evaluate_subject(subject: dict, learning_date: str) -> dict | None:
    bars = db.bars_for_evaluation(subject["ticker"], subject["signal_date"])
    if len(bars) < 2 or bars[1]["bar_date"] > learning_date:
        return None
    entry, exit_bar = bars[0], bars[1]
    if not entry["close"]:
        return None
    return_pct = (exit_bar["close"] - entry["close"]) / entry["close"] * 100.0
    score, correct = score_action(subject["action"], return_pct)
    return {
        "subject_type": subject["subject_type"],
        "subject_id": subject["subject_id"],
        "agent": subject["agent"],
        "ticker": subject["ticker"],
        "action": subject["action"],
        "equation": subject["equation"],
        "signal_date": subject["signal_date"],
        "entry_date": entry["bar_date"],
        "exit_date": exit_bar["bar_date"],
        "entry_close": entry["close"],
        "exit_close": exit_bar["close"],
        "return_pct": round(return_pct, 6),
        "performance_score": round(score, 6),
        "correct": correct,
    }


def equation_summary(agent: str, outcomes: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for outcome in outcomes:
        if outcome["agent"] == agent:
            grouped[outcome["equation"]].append(outcome)

    summary: dict[str, dict] = {}
    for equation in config.EQUATION_CHOICES[agent]:
        rows = grouped.get(equation, [])
        count = len(rows)
        summary[equation] = {
            "observations": count,
            "mean_score": round(
                sum(row["performance_score"] for row in rows) / count, 6
            ) if count else 0.0,
            "hit_rate": round(
                sum(bool(row["correct"]) for row in rows) / count, 6
            ) if count else 0.0,
        }
    return summary


def recommended_equation(agent: str, stats: dict, previous: dict | None) -> str:
    eligible = [
        (name, values)
        for name, values in stats.items()
        if values["observations"] >= config.LEARNING_MIN_OBSERVATIONS
    ]
    if eligible:
        return max(
            eligible,
            key=lambda item: (
                item[1]["mean_score"],
                item[1]["hit_rate"],
                item[1]["observations"],
            ),
        )[0]
    if previous and previous.get("recommended_equation") in config.EQUATION_CHOICES[agent]:
        return previous["recommended_equation"]
    return config.EQUATION_BY_AGENT[agent]


def _project_bounded_weights(
    current: dict[str, float],
    target: dict[str, float],
) -> dict[str, float]:
    """Move toward target while preserving a simplex and per-day step bounds."""
    sources = config.SIGNAL_SOURCES
    current_total = sum(max(0.0, current.get(source, 0.0)) for source in sources)
    if current_total <= 0:
        current = {source: 1.0 / len(sources) for source in sources}
    else:
        current = {
            source: max(0.0, current.get(source, 0.0)) / current_total
            for source in sources
        }
    target_total = sum(max(0.0, target.get(source, 0.0)) for source in sources)
    target = {
        source: (
            max(0.0, target.get(source, 0.0)) / target_total
            if target_total else current[source]
        )
        for source in sources
    }
    lower = {
        source: max(
            config.LEARNING_MIN_SOURCE_WEIGHT,
            current[source] - config.LEARNING_MAX_WEIGHT_STEP,
        )
        for source in sources
    }
    upper = {
        source: min(1.0, current[source] + config.LEARNING_MAX_WEIGHT_STEP)
        for source in sources
    }
    result = {
        source: min(
            upper[source],
            max(
                lower[source],
                current[source]
                + config.LEARNING_RATE * (target[source] - current[source]),
            ),
        )
        for source in sources
    }

    for _ in range(10):
        residual = 1.0 - sum(result.values())
        if abs(residual) < 1e-10:
            break
        if residual > 0:
            capacity = {
                source: upper[source] - result[source]
                for source in sources
                if result[source] < upper[source]
            }
        else:
            capacity = {
                source: result[source] - lower[source]
                for source in sources
                if result[source] > lower[source]
            }
        total_capacity = sum(capacity.values())
        if total_capacity <= 0:
            break
        for source, room in capacity.items():
            result[source] += residual * (room / total_capacity)

    rounded = {source: round(result[source], 6) for source in sources}
    correction = round(1.0 - sum(rounded.values()), 6)
    rounded[max(sources, key=lambda source: rounded[source])] += correction
    return rounded


def learned_weights(outcomes: list[dict], previous: dict[str, dict]) -> dict[str, float]:
    old_overrides = (previous.get("forecaster") or {}).get("config_overrides") or {}
    current = {
        source: float(
            old_overrides.get(
                f"SIGNAL_WEIGHTS.{source}",
                config.SIGNAL_WEIGHTS[source],
            )
        )
        for source in config.SIGNAL_SOURCES
    }
    rows_by_agent = {
        source: [row for row in outcomes if row["agent"] == source]
        for source in config.SIGNAL_SOURCES
    }
    quality: dict[str, float] = {}
    for source, rows in rows_by_agent.items():
        if len(rows) < config.LEARNING_MIN_OBSERVATIONS:
            quality[source] = current[source]
            continue
        mean_score = sum(row["performance_score"] for row in rows) / len(rows)
        quality[source] = max(0.01, (mean_score + 1.0) / 2.0)
    return _project_bounded_weights(current, quality)


def publish_policies(
    learning_date: str,
    outcomes: list[dict],
    previous: dict[str, dict] | None = None,
) -> dict[str, dict]:
    previous = previous if previous is not None else db.all_policy_snapshots()
    weights = learned_weights(outcomes, previous)
    published: dict[str, dict] = {}

    for agent in AGENTS:
        agent_rows = [row for row in outcomes if row["agent"] == agent]
        # Analyst outcomes can still update ensemble weights even before there is
        # a forecaster outcome, so a forecaster policy is always published.
        if not agent_rows and agent != "forecaster":
            continue
        stats = equation_summary(agent, outcomes)
        observations = len(agent_rows)
        mean_score = (
            sum(row["performance_score"] for row in agent_rows) / observations
            if observations else 0.0
        )
        overrides = (
            {
                f"SIGNAL_WEIGHTS.{source}": weights[source]
                for source in config.SIGNAL_SOURCES
            }
            if agent == "forecaster" else {}
        )
        published[agent] = db.upsert_agent_policy(
            agent=agent,
            learning_date=learning_date,
            recommended_equation=recommended_equation(
                agent, stats, previous.get(agent)
            ),
            equation_stats=stats,
            reliability=round((mean_score + 1.0) / 2.0, 6),
            observations=observations,
            config_overrides=overrides,
        )
    return published


async def _refresh_bars(subjects: list[dict]) -> tuple[list[str], list[str]]:
    tickers = sorted({subject["ticker"] for subject in subjects})
    refreshed: list[str] = []
    errors: list[str] = []
    run_id = db.start_run("performance-learner", {"tickers": tickers})
    for ticker in tickers:
        try:
            bars, source = await marketdata.fetch_bars(
                ticker, config.LEARNING_MARKET_DATA_DAYS
            )
            db.upsert_bars(run_id, ticker, bars, source)
            refreshed.append(ticker)
        except Exception as exc:  # noqa: BLE001 - stored bars may still suffice
            errors.append(f"{ticker}: {exc}")
            log.warning("could not refresh %s for learning: %s", ticker, exc)
    db.finish_run(
        run_id,
        "success" if refreshed or not tickers else "failed",
        error="; ".join(errors) or None,
        details={"refreshed": refreshed},
    )
    return refreshed, errors


async def run_daily(
    learning_date: str | None = None,
    *,
    refresh_market_data: bool = True,
) -> dict:
    """Run at most once for a date; later calls return the stored result."""
    db.init_db()
    learning_date = learning_date or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(learning_date)
    if not config.LEARNING_ENABLED:
        return {"learning_date": learning_date, "status": "disabled"}
    prior_run = db.daily_learning_run(learning_date)
    if not db.start_daily_learning(learning_date):
        stored = db.daily_learning_run(learning_date) or {}
        return {**stored, "idempotent": True}

    try:
        subjects = db.evaluation_subjects()
        refreshed: list[str] = []
        refresh_errors: list[str] = []
        if refresh_market_data and subjects:
            refreshed, refresh_errors = await _refresh_bars(subjects)

        evaluated = [
            outcome
            for subject in subjects
            if (outcome := evaluate_subject(subject, learning_date)) is not None
        ]
        inserted = db.store_performance_outcomes(evaluated)
        cutoff = (
            date.fromisoformat(learning_date)
            - timedelta(days=config.LEARNING_LOOKBACK_DAYS)
        ).isoformat()
        outcomes = db.performance_outcomes(cutoff)
        recovering = bool(prior_run and prior_run.get("status") == "failed")
        policy_base = (
            prior_run.get("before_snapshot")
            if recovering and prior_run else None
        )
        policies = (
            publish_policies(learning_date, outcomes, previous=policy_base)
            if inserted or recovering else {}
        )
        details = {
            "subjects_considered": len(subjects),
            "subjects_ready": len(evaluated),
            "market_data_refreshed": refreshed,
            "market_data_errors": refresh_errors,
            "lookback_start": cutoff,
            "policies_published": sorted(policies),
        }
        db.finish_daily_learning(
            learning_date,
            status="success",
            outcomes_added=inserted,
            details=details,
        )
        return db.daily_learning_run(learning_date) or {}
    except Exception as exc:
        db.finish_daily_learning(
            learning_date,
            status="failed",
            error=str(exc),
        )
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate DeltaDesk performance and publish today's learned policy"
    )
    parser.add_argument("--date", help="Learning date in YYYY-MM-DD (default: today UTC)")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Use only historical bars already stored in the database",
    )
    parser.add_argument("--show-policy", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    if args.show_policy:
        print(db.dumps(db.all_policy_snapshots()))
        return 0
    result = asyncio.run(
        run_daily(args.date, refresh_market_data=not args.no_refresh)
    )
    print(db.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
