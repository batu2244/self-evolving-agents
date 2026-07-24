"""News analyst: wrap the existing google-news-agent in the signal contract.

The news agent is left exactly as it is. This runs it, reads its JSON on stdout,
and maps its consolidated decision onto a Signal -- no re-analysis, no second
opinion. In MOCK_MODE (or when the live run fails) it reads the checked-in
sample output instead, and marks the provenance degraded so a cached read is
never mistaken for a fresh one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import analysis_modes  # noqa: E402
import database as db  # noqa: E402
import derive  # noqa: E402
import gemini_decision  # noqa: E402
import prompts  # noqa: E402
from contracts import Provenance, Signal, action_for_direction, current_cycle  # noqa: E402

log = logging.getLogger("deltadesk.news")

SOURCE = "news"


class NewsUnavailable(RuntimeError):
    pass


def load_sample() -> dict:
    if not config.NEWS_AGENT_SAMPLE.exists():
        raise NewsUnavailable(f"no sample output at {config.NEWS_AGENT_SAMPLE}")
    return json.loads(config.NEWS_AGENT_SAMPLE.read_text(encoding="utf-8"))


async def run_news_agent(hours: int = 24, limit: int = 10) -> tuple[dict, bool]:
    """Run the news agent. Returns (payload, is_live)."""
    if config.MOCK_MODE:
        return load_sample(), False
    if not config.NEWS_AGENT_SCRIPT.exists():
        raise NewsUnavailable(f"news agent not found at {config.NEWS_AGENT_SCRIPT}")

    # Prefer the news agent's own virtualenv; it has deps this project does not.
    venv_python = config.NEWS_AGENT_DIR / ".venv" / "bin" / "python"
    executable = str(venv_python) if venv_python.exists() else sys.executable

    proc = await asyncio.create_subprocess_exec(
        executable, str(config.NEWS_AGENT_SCRIPT), "--hours", str(hours), "--limit", str(limit),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config.NEWS_AGENT_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=config.NEWS_AGENT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise NewsUnavailable(f"news agent timed out after {config.NEWS_AGENT_TIMEOUT}s") from None

    if proc.returncode != 0 or not stdout.strip():
        tail = (stderr or b"").decode(errors="replace").strip().splitlines()[-1:] or ["no stderr"]
        raise NewsUnavailable(f"news agent exited {proc.returncode}: {tail[0][:200]}")
    return json.loads(stdout), True


def build_signal(ticker: str, payload: dict, run_id: str, is_live: bool,
                 cycle: str | None = None,
                 thinking: gemini_decision.DecisionResult | None = None) -> Signal:
    prompt_snapshot = prompts.snapshot(SOURCE)
    decision = payload.get("decision") or {}
    score = payload.get("signal_score") or {}
    selection = analysis_modes.select_news(decision, score)
    if thinking is not None:
        selection = analysis_modes.ModeSelection(
            SOURCE,
            "gemini-thinking",
            thinking.decision.selected_equation,
            thinking.decision.rationale,
            {"evidence": thinking.decision.evidence},
        )
    features = derive.derive_news(decision, score, equation=selection.equation)
    candidates = derive.evaluate_news_candidates(decision, score)

    notes: list[str] = []
    if not is_live:
        notes.append("read from cached sample output, not a live run")
    if payload.get("pioneer_status", "ok") != "ok":
        notes.append(f"upstream classifier degraded: {str(payload['pioneer_status'])[:120]}")
    as_of = payload.get("as_of", "unknown")

    action = (
        thinking.decision.action
        if thinking is not None
        else (
            str(decision.get("action", "")).upper()
            if str(decision.get("action", "")).upper() in {"BUY", "SELL", "HOLD"}
            else action_for_direction(features["direction"], config.DIRECTION_THRESHOLD)
        )
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
                "run_google_news_agent",
                "read_consolidated_decision",
                "evaluate_equations",
                *(["gemini_thinking_decision"] if thinking is not None else []),
                "select_signal",
            ],
            "input_summary": {
                "ticker": ticker.upper(),
                "as_of": payload.get("as_of"),
                "articles_scored": score.get("articles_scored", 0),
                "upstream_action": decision.get("action"),
                "upstream_weighted_score": score.get("weighted_score"),
                "is_live": is_live,
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
            else {"provider": "upstream/deterministic", "model": None, "thinking_level": None}
        ),
        provenance=Provenance(
            source_run_id=run_id,
            inputs_used=[
                f"google-news-agent decision as_of {as_of}",
                f"{features['articles_scored']} scored articles",
            ],
            degraded=bool(notes),
            notes="; ".join(notes),
        ),
    )


async def run(tickers: list[str] | None = None, cycle: str | None = None,
              hours: int = 24, limit: int = 10) -> list[Signal]:
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

    covered = [t for t in tickers if t.upper() == config.NEWS_AGENT_TICKER]
    skipped = [t for t in tickers if t.upper() != config.NEWS_AGENT_TICKER]
    if skipped:
        # Silently emitting a neutral signal would let an uncovered ticker dilute
        # the tally as though news had been checked and found nothing.
        log.warning("news agent covers %s only; no signal for %s",
                    config.NEWS_AGENT_TICKER, ", ".join(skipped))

    signals: list[Signal] = []
    error: str | None = None
    if covered:
        try:
            payload, is_live = await run_news_agent(hours=hours, limit=limit)
        except Exception as exc:  # noqa: BLE001 - fall back to the cached read
            log.warning("live news run failed (%s); using sample output", exc)
            error = str(exc)
            try:
                payload, is_live = load_sample(), False
            except Exception as inner:  # noqa: BLE001
                db.finish_run(run_id, "failed", error=f"{exc}; {inner}")
                return []
        for ticker in covered:
            decision = payload.get("decision") or {}
            score = payload.get("signal_score") or {}
            fallback = analysis_modes.select_news(decision, score)
            thinking = await gemini_decision.decide(
                agent=SOURCE,
                ticker=ticker,
                system_prompt=prompts.load(SOURCE).system_prompt,
                input_summary={
                    "as_of": payload.get("as_of"),
                    "articles_scored": score.get("articles_scored", 0),
                    "upstream_action": decision.get("action"),
                    "upstream_conviction": decision.get("conviction"),
                    "upstream_weighted_score": score.get("weighted_score"),
                    "is_live": is_live,
                    "performance_memory": performance_memory,
                },
                candidates=derive.evaluate_news_candidates(decision, score),
                allowed_equations=config.EQUATION_CHOICES[SOURCE],
                fallback_equation=fallback.equation,
            )
            signal = build_signal(ticker, payload, run_id, is_live, cycle, thinking)
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

    db.finish_run(
        run_id,
        "success" if signals else "failed",
        error=error,
        details={"signals": len(signals), "skipped": skipped},
    )
    return signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = asyncio.run(run())
    print(db.dumps([s.model_dump() for s in out]))
