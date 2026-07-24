"""Forecaster: tally the analyst signals into one directional score per ticker.

Weights are static (config.SIGNAL_WEIGHTS) and renormalized over whichever sources
actually reported, so a missing analyst never silently drags the score toward zero
-- it narrows the base and is recorded as degraded provenance instead.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import analysis_modes  # noqa: E402
import database as db  # noqa: E402
import gemini_decision  # noqa: E402
import prompts  # noqa: E402
from contracts import (  # noqa: E402
    Contribution,
    Forecast,
    Provenance,
    Signal,
    action_for_direction,
    clamp,
    current_cycle,
)

log = logging.getLogger("deltadesk.forecaster")

SOURCE = "forecaster"


def tally(ticker: str, signals: dict[str, Signal], cycle: str | None = None,
          threshold: float | None = None, equation: str | None = None,
          trace_candidates: bool = True,
          selection_policy: str = "configured",
          thinking: gemini_decision.DecisionResult | None = None) -> Forecast:
    """Combine per-source signals into a forecast. Pure: no I/O."""
    threshold = config.DIRECTION_THRESHOLD if threshold is None else threshold
    prompt_snapshot = prompts.snapshot(SOURCE)
    if thinking is not None:
        selection = analysis_modes.ModeSelection(
            SOURCE,
            "gemini-thinking",
            thinking.decision.selected_equation,
            thinking.decision.rationale,
            {"evidence": thinking.decision.evidence},
        )
    elif equation is not None:
        selection = analysis_modes.ModeSelection(
            SOURCE,
            "configured",
            equation,
            "A named equation was supplied directly to the forecaster.",
            {"configured_equation": equation},
        )
    else:
        selection = analysis_modes.select_forecaster(signals, policy=selection_policy)
    equation = selection.equation
    expected = set(config.SIGNAL_SOURCES)
    present = {s: sig for s, sig in signals.items() if s in expected}
    missing = sorted(expected - set(present))

    if not present:
        return Forecast(
            ticker=ticker.upper(),
            action="HOLD",
            direction="FLAT",
            score=0.0,
            confidence=0.0,
            per_agent_contributions=[],
            rationale="No analyst signals available; no directional view.",
            cycle=cycle or current_cycle(),
            config_snapshot=config.snapshot(),
            prompt_snapshot=prompt_snapshot,
            equation_snapshot={SOURCE: equation},
            model_snapshot=(
                thinking.model_snapshot()
                if thinking is not None
                else {"provider": "deterministic", "model": None, "thinking_level": None}
            ),
            agent_trace={
                "workflow": ["read_signals", "evaluate_forecast_equations", "select_forecast"],
                "input_summary": {"ticker": ticker.upper(), "signals": 0},
                "candidate_forecasts": [],
                "selected_equation": equation,
                "selection_policy": selection.policy,
                "selection_reason": selection.reason,
                "selection_evidence": selection.evidence,
                "final_action": "HOLD",
                "thinking_decision": thinking.trace_snapshot() if thinking is not None else {},
            } if trace_candidates else {},
            provenance=Provenance(
                inputs_used=[],
                degraded=True,
                notes=f"all sources missing: {', '.join(sorted(expected))}",
            ),
        )

    # Renormalize the configured weights across the sources that reported.
    raw_weights = {s: config.SIGNAL_WEIGHTS.get(s, 0.0) for s in present}
    total_weight = sum(raw_weights.values())
    if total_weight <= 0:
        raw_weights = {s: 1.0 for s in present}
        total_weight = float(len(present))
    weights = {s: w / total_weight for s, w in raw_weights.items()}

    contributions: list[Contribution] = []
    score = 0.0
    for source in sorted(present, key=lambda s: -weights[s]):
        sig = present[source]
        if equation == "confidence_weighted":
            # Confidence scales the push: an unsure analyst moves the score less.
            contribution = weights[source] * sig.direction * sig.confidence
        elif equation == "direction_only":
            contribution = weights[source] * sig.direction
        elif equation == "consensus":
            contribution = weights[source] * sig.direction * sig.confidence
        else:
            raise ValueError(f"unknown forecaster equation {equation!r}")
        score += contribution
        contributions.append(
            Contribution(
                source=source,
                action=sig.action,
                direction=round(sig.direction, 4),
                confidence=round(sig.confidence, 4),
                weight=round(weights[source], 4),
                contribution=round(contribution, 4),
                rationale=sig.rationale,
            )
        )
    if equation == "consensus":
        signed = 0.0 if score == 0 else (1.0 if score > 0 else -1.0)
        agreeing_weight = sum(
            weights[s]
            for s in present
            if present[s].direction != 0 and signed and (present[s].direction > 0) == (signed > 0)
        )
        score = score * agreeing_weight
        for idx, c in enumerate(contributions):
            adjusted = c.contribution * agreeing_weight
            contributions[idx] = c.model_copy(update={"contribution": round(adjusted, 4)})
    score = clamp(score)

    direction = "UP" if score > threshold else ("DOWN" if score < -threshold else "FLAT")

    # Confidence = how much of the weighted, confidence-scaled mass agrees with the
    # sign of the score, tempered by how many of the expected sources reported.
    effective = {s: weights[s] * present[s].confidence for s in present}
    effective_total = sum(effective.values())
    if effective_total <= 0:
        agreement = 0.0
    elif score == 0:
        agreement = 0.0
    else:
        agreeing = sum(
            e for s, e in effective.items()
            if present[s].direction != 0 and (present[s].direction > 0) == (score > 0)
        )
        agreement = agreeing / effective_total

    coverage = len(present) / len(expected)
    mean_conf = sum(s.confidence for s in present.values()) / len(present)
    confidence = clamp(agreement * mean_conf * coverage, 0.0, 1.0)

    degraded = bool(missing) or any(s.provenance.degraded for s in present.values())
    notes: list[str] = []
    if missing:
        notes.append(f"missing sources: {', '.join(missing)} (weights renormalized over the rest)")
    upstream = sorted(s for s, sig in present.items() if sig.provenance.degraded)
    if upstream:
        notes.append(f"degraded upstream inputs: {', '.join(upstream)}")

    lead = f"{direction} at score {score:+.3f} using equation={equation}"
    detail = ", ".join(
        f"{c.source} {c.direction:+.2f}x{c.confidence:.2f}@w{c.weight:.2f} -> {c.contribution:+.3f}"
        for c in contributions
    )
    rationale = f"{lead} from {len(present)}/{len(expected)} analysts: {detail}."
    if missing:
        rationale += f" Partial view -- no signal from {', '.join(missing)}."

    action = (
        thinking.decision.action
        if thinking is not None
        else action_for_direction(score, threshold)
    )
    if thinking is not None:
        confidence = round((confidence + thinking.decision.confidence) / 2, 4)
        rationale = f"{thinking.decision.rationale} Quantitative tally: {rationale}"

    forecast = Forecast(
        ticker=ticker.upper(),
        action=action,
        direction=direction,
        score=round(score, 4),
        confidence=round(confidence, 4),
        per_agent_contributions=contributions,
        rationale=rationale,
        deterministic=thinking is None or thinking.provider == "mock",
        cycle=cycle or current_cycle(),
        config_snapshot=config.snapshot(),
        prompt_snapshot=prompt_snapshot,
        equation_snapshot={SOURCE: equation},
        model_snapshot=(
            thinking.model_snapshot()
            if thinking is not None
            else {"provider": "deterministic", "model": None, "thinking_level": None}
        ),
        provenance=Provenance(
            source_run_id=None,
            inputs_used=[
                f"signals[{source}]:{present[source].cycle}"
                f"{' (run ' + present[source].provenance.source_run_id + ')' if present[source].provenance.source_run_id else ''}"
                for source in sorted(present)
            ],
            degraded=degraded,
            notes="; ".join(notes),
        ),
    )
    if trace_candidates:
        forecast.agent_trace = {
            "workflow": [
                "read_signals",
                "evaluate_forecast_equations",
                *(["gemini_thinking_decision"] if thinking is not None else []),
                "select_forecast",
            ],
            "input_summary": {
                "ticker": ticker.upper(),
                "signals": len(present),
                "sources": sorted(present),
                "missing_sources": missing,
            },
            "candidate_forecasts": evaluate_forecast_candidates(
                ticker, signals, cycle=cycle, threshold=threshold
            ),
            "selected_equation": equation,
            "selection_policy": selection.policy,
            "selection_reason": selection.reason,
            "selection_evidence": selection.evidence,
            "selected_forecast": forecast_candidate_summary(forecast),
            "final_action": action,
            "thinking_decision": thinking.trace_snapshot() if thinking is not None else {},
        }
    return forecast


def forecast_candidate_summary(forecast: Forecast) -> dict:
    return {
        "equation": forecast.equation_snapshot.get(SOURCE, ""),
        "action": forecast.action,
        "direction": forecast.direction,
        "score": forecast.score,
        "confidence": forecast.confidence,
        "rationale": forecast.rationale,
    }


def evaluate_forecast_candidates(
    ticker: str,
    signals: dict[str, Signal],
    cycle: str | None = None,
    threshold: float | None = None,
) -> list[dict]:
    return [
        forecast_candidate_summary(
            tally(
                ticker,
                signals,
                cycle=cycle,
                threshold=threshold,
                equation=equation,
                trace_candidates=False,
            )
        )
        for equation in config.EQUATION_CHOICES[SOURCE]
    ]


async def run(tickers: list[str] | None = None, cycle: str | None = None) -> list[Forecast]:
    db.init_db()
    performance_memory = db.agent_performance_context(SOURCE)
    tickers = tickers or config.DEFAULT_SYMBOLS
    cycle = cycle or current_cycle()
    run_id = db.start_run(SOURCE, {
        "tickers": tickers,
        "cycle": cycle,
        "prompt": prompts.snapshot(SOURCE),
        "equation": config.equation_snapshot(SOURCE),
        "analysis_policy": config.analysis_policy_snapshot(SOURCE),
        "performance_memory": performance_memory,
        "gemini": {
            "model": config.GEMINI_THINKING_MODEL,
            "thinking_level": config.GEMINI_THINKING_LEVEL,
            "required_for_live": True,
        },
    })

    forecasts: list[Forecast] = []
    for ticker in tickers:
        signals = db.latest_signals(ticker)
        fallback = analysis_modes.select_forecaster(
            signals,
            policy=config.ANALYSIS_POLICY_BY_AGENT[SOURCE],
        )
        candidates = evaluate_forecast_candidates(ticker, signals, cycle=cycle)
        thinking = await gemini_decision.decide(
            agent=SOURCE,
            ticker=ticker,
            system_prompt=prompts.load(SOURCE).system_prompt,
            input_summary={
                "sources": sorted(signals),
                "signals": {
                    source: {
                        "action": signal.action,
                        "direction": signal.direction,
                        "confidence": signal.confidence,
                        "degraded": signal.provenance.degraded,
                    }
                    for source, signal in signals.items()
                },
                "performance_memory": performance_memory,
            },
            candidates=candidates,
            allowed_equations=config.EQUATION_CHOICES[SOURCE],
            fallback_equation=fallback.equation,
        )
        forecast = tally(
            ticker,
            signals,
            cycle,
            equation=thinking.decision.selected_equation,
            thinking=thinking,
        )
        forecast.learning_snapshot = performance_memory
        forecast.agent_trace["performance_memory"] = performance_memory
        db.store_forecast(forecast, run_id)
        forecasts.append(forecast)
        log.info(
            "%s: %s (%s) score=%+.3f confidence=%.3f from %d analysts%s",
            ticker, forecast.action, forecast.direction, forecast.score, forecast.confidence,
            len(forecast.per_agent_contributions),
            " (degraded)" if forecast.provenance.degraded else "",
        )

    db.finish_run(run_id, "success", details={"forecasts": len(forecasts)})
    return forecasts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()
    db.activate_learned_policy()
    out = asyncio.run(run())
    print(db.dumps([f.model_dump() for f in out]))
