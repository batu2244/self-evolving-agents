"""Realtime analyst: collect a quote, store the snapshot, emit a momentum signal."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import analysis_modes  # noqa: E402
import database as db  # noqa: E402
import derive  # noqa: E402
import gemini_decision  # noqa: E402
import marketdata  # noqa: E402
import prompts  # noqa: E402
from contracts import Provenance, Signal, action_for_direction, current_cycle  # noqa: E402

log = logging.getLogger("deltadesk.realtime")

SOURCE = "realtime"


async def collect(ticker: str, run_id: str, cycle: str) -> dict:
    quote = await marketdata.fetch_quote(ticker)
    db.upsert_snapshot(run_id, ticker.upper(), cycle, {**quote, "raw": quote})
    log.info("%s: quote %.4f from %s", ticker, quote.get("price") or 0.0, quote.get("source"))
    return quote


def build_signal(
    ticker: str,
    quote: dict,
    run_id: str,
    cycle: str | None = None,
    thinking: gemini_decision.DecisionResult | None = None,
) -> Signal:
    prompt_snapshot = prompts.snapshot(SOURCE)
    selection = analysis_modes.select_realtime(quote)
    if thinking is not None:
        selection = analysis_modes.ModeSelection(
            SOURCE,
            "gemini-thinking",
            thinking.decision.selected_equation,
            thinking.decision.rationale,
            {"evidence": thinking.decision.evidence},
        )
    features = derive.derive_momentum(quote, equation=selection.equation)
    candidates = derive.evaluate_momentum_candidates(quote)
    missing = [f for f in ("open", "previous_close") if not quote.get(f)]
    degraded = bool(missing) or not quote.get("price")
    notes = f"missing quote fields: {', '.join(missing)}" if missing else ""
    action = (
        thinking.decision.action
        if thinking is not None
        else action_for_direction(features["direction"], config.DIRECTION_THRESHOLD)
    )
    confidence = (
        round((features["confidence"] + thinking.decision.confidence) / 2, 4)
        if thinking is not None
        else features["confidence"]
    )
    rationale = (
        f"{thinking.decision.rationale} Quantitative read: {features['rationale']}"
        if thinking is not None
        else features["rationale"]
    )
    return Signal(
        ticker=ticker,
        source=SOURCE,
        action=action,
        direction=features["direction"],
        confidence=confidence,
        rationale=rationale,
        deterministic=thinking is None or thinking.provider == "mock",
        cycle=cycle or current_cycle(),
        prompt_snapshot=prompt_snapshot,
        equation_snapshot={SOURCE: selection.equation},
        agent_trace={
            "workflow": [
                "collect_quote",
                "evaluate_equations",
                *(["gemini_thinking_decision"] if thinking is not None else []),
                "select_signal",
            ],
            "input_summary": {
                "ticker": ticker.upper(),
                "price": quote.get("price"),
                "open": quote.get("open"),
                "previous_close": quote.get("previous_close"),
                "source": quote.get("source"),
            },
            "candidate_reads": candidates,
            "selected_equation": features["equation"],
            "selection_policy": selection.policy,
            "selection_reason": selection.reason,
            "selection_evidence": selection.evidence,
            "selected_read": derive.candidate_summary(features),
            "final_action": action,
            "thinking_decision": thinking.trace_snapshot() if thinking is not None else {},
        },
        model_snapshot=(
            thinking.model_snapshot()
            if thinking is not None
            else {"provider": "deterministic", "model": None, "thinking_level": None}
        ),
        provenance=Provenance(
            source_run_id=run_id,
            inputs_used=[f"market_snapshots[{ticker.upper()}] from {quote.get('source')}"],
            degraded=degraded,
            notes=notes,
        ),
    )


async def run(tickers: list[str] | None = None, cycle: str | None = None) -> list[Signal]:
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
    signals: list[Signal] = []
    errors: list[str] = []

    for ticker in tickers:
        try:
            quote = await collect(ticker, run_id, cycle)
            fallback = analysis_modes.select_realtime(quote)
            thinking = await gemini_decision.decide(
                agent=SOURCE,
                ticker=ticker,
                system_prompt=prompts.load(SOURCE).system_prompt,
                input_summary={
                    "price": quote.get("price"),
                    "open": quote.get("open"),
                    "previous_close": quote.get("previous_close"),
                    "volume": quote.get("volume"),
                    "average_volume": quote.get("average_volume"),
                    "source": quote.get("source"),
                    "performance_memory": performance_memory,
                },
                candidates=derive.evaluate_momentum_candidates(quote),
                allowed_equations=config.EQUATION_CHOICES[SOURCE],
                fallback_equation=fallback.equation,
            )
            signal = build_signal(ticker, quote, run_id, cycle, thinking)
            signal.learning_snapshot = performance_memory
            signal.agent_trace["performance_memory"] = performance_memory
            db.store_signal(signal, run_id)
            signals.append(signal)
            log.info(
                "%s: %s direction=%+.3f confidence=%.3f via %s",
                ticker,
                signal.action,
                signal.direction,
                signal.confidence,
                signal.model_snapshot.get("model"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("realtime failed for %s: %s", ticker, exc)
            errors.append(f"{ticker}: {exc}")

    db.finish_run(
        run_id,
        "failed" if errors and not signals else "success",
        error="; ".join(errors) or None,
        details={"signals": len(signals)},
    )
    return signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = asyncio.run(run())
    print(db.dumps([s.model_dump() for s in out]))
