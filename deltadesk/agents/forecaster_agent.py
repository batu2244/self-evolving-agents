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
import database as db  # noqa: E402
from contracts import Contribution, Forecast, Provenance, Signal, clamp, current_cycle  # noqa: E402

log = logging.getLogger("deltadesk.forecaster")

SOURCE = "forecaster"


def tally(ticker: str, signals: dict[str, Signal], cycle: str | None = None,
          threshold: float | None = None) -> Forecast:
    """Combine per-source signals into a forecast. Pure: no I/O."""
    threshold = config.DIRECTION_THRESHOLD if threshold is None else threshold
    expected = set(config.SIGNAL_SOURCES)
    present = {s: sig for s, sig in signals.items() if s in expected}
    missing = sorted(expected - set(present))

    if not present:
        return Forecast(
            ticker=ticker.upper(),
            direction="FLAT",
            score=0.0,
            confidence=0.0,
            per_agent_contributions=[],
            rationale="No analyst signals available; no directional view.",
            cycle=cycle or current_cycle(),
            config_snapshot=config.snapshot(),
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
        # Confidence scales the push: an unsure analyst moves the score less.
        contribution = weights[source] * sig.direction * sig.confidence
        score += contribution
        contributions.append(
            Contribution(
                source=source,
                direction=round(sig.direction, 4),
                confidence=round(sig.confidence, 4),
                weight=round(weights[source], 4),
                contribution=round(contribution, 4),
                rationale=sig.rationale,
            )
        )
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

    lead = f"{direction} at score {score:+.3f}"
    detail = ", ".join(
        f"{c.source} {c.direction:+.2f}x{c.confidence:.2f}@w{c.weight:.2f} -> {c.contribution:+.3f}"
        for c in contributions
    )
    rationale = f"{lead} from {len(present)}/{len(expected)} analysts: {detail}."
    if missing:
        rationale += f" Partial view -- no signal from {', '.join(missing)}."

    return Forecast(
        ticker=ticker.upper(),
        direction=direction,
        score=round(score, 4),
        confidence=round(confidence, 4),
        per_agent_contributions=contributions,
        rationale=rationale,
        cycle=cycle or current_cycle(),
        config_snapshot=config.snapshot(),
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


async def run(tickers: list[str] | None = None, cycle: str | None = None) -> list[Forecast]:
    db.init_db()
    tickers = tickers or config.DEFAULT_SYMBOLS
    cycle = cycle or current_cycle()
    run_id = db.start_run(SOURCE, {"tickers": tickers, "cycle": cycle})

    forecasts: list[Forecast] = []
    for ticker in tickers:
        signals = db.latest_signals(ticker)
        forecast = tally(ticker, signals, cycle)
        db.store_forecast(forecast, run_id)
        forecasts.append(forecast)
        log.info(
            "%s: %s score=%+.3f confidence=%.3f from %d analysts%s",
            ticker, forecast.direction, forecast.score, forecast.confidence,
            len(forecast.per_agent_contributions),
            " (degraded)" if forecast.provenance.degraded else "",
        )

    db.finish_run(run_id, "success", details={"forecasts": len(forecasts)})
    return forecasts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = asyncio.run(run())
    print(db.dumps([f.model_dump() for f in out]))
