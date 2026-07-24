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
import database as db  # noqa: E402
import derive  # noqa: E402
from contracts import Provenance, Signal, current_cycle  # noqa: E402

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
                 cycle: str | None = None) -> Signal:
    decision = payload.get("decision") or {}
    score = payload.get("signal_score") or {}
    features = derive.derive_news(decision, score)

    notes: list[str] = []
    if not is_live:
        notes.append("read from cached sample output, not a live run")
    if payload.get("pioneer_status", "ok") != "ok":
        notes.append(f"upstream classifier degraded: {str(payload['pioneer_status'])[:120]}")
    as_of = payload.get("as_of", "unknown")

    return Signal(
        ticker=ticker,
        source=SOURCE,
        direction=features["direction"],
        confidence=features["confidence"],
        rationale=features["rationale"],
        cycle=cycle or current_cycle(),
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
    tickers = tickers or config.DEFAULT_SYMBOLS
    cycle = cycle or current_cycle()
    run_id = db.start_run(SOURCE, {"tickers": tickers, "cycle": cycle})

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
            signal = build_signal(ticker, payload, run_id, is_live, cycle)
            db.store_signal(signal, run_id)
            signals.append(signal)

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
