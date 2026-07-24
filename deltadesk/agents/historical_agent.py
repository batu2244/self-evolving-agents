"""Historical analyst: collect daily bars, store them, emit a trend signal."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import database as db  # noqa: E402
import derive  # noqa: E402
import marketdata  # noqa: E402
from contracts import Provenance, Signal, current_cycle  # noqa: E402

log = logging.getLogger("deltadesk.historical")

SOURCE = "historical"


async def collect(ticker: str, run_id: str, days: int | None = None) -> tuple[list[dict], str]:
    days = days or config.HISTORICAL_DAYS
    bars, source = await marketdata.fetch_bars(ticker, days)
    db.upsert_bars(run_id, ticker.upper(), bars, source)
    log.info("%s: stored %d bars from %s", ticker, len(bars), source)
    return bars, source


def build_signal(ticker: str, bars: list[dict], source: str, run_id: str,
                 cycle: str | None = None) -> Signal:
    closes = [b["close"] for b in bars if b.get("close") is not None]
    features = derive.derive_trend(closes)
    degraded = len(closes) < config.MA_LONG
    notes = ""
    if degraded:
        notes = (
            f"only {len(closes)} closes available; "
            f"MA{config.MA_LONG} cross-check unavailable"
        )
    return Signal(
        ticker=ticker,
        source=SOURCE,
        direction=features["direction"],
        confidence=features["confidence"],
        rationale=features["rationale"],
        cycle=cycle or current_cycle(),
        provenance=Provenance(
            source_run_id=run_id,
            inputs_used=[f"historical_bars[{ticker.upper()}]:{len(closes)} closes from {source}"],
            degraded=degraded,
            notes=notes,
        ),
    )


async def run(tickers: list[str] | None = None, cycle: str | None = None) -> list[Signal]:
    db.init_db()
    tickers = tickers or config.DEFAULT_SYMBOLS
    cycle = cycle or current_cycle()
    run_id = db.start_run(SOURCE, {"tickers": tickers, "cycle": cycle})
    signals: list[Signal] = []
    errors: list[str] = []

    for ticker in tickers:
        try:
            bars, source = await collect(ticker, run_id)
            signal = build_signal(ticker, bars, source, run_id, cycle)
            db.store_signal(signal, run_id)
            signals.append(signal)
        except Exception as exc:  # noqa: BLE001 - one ticker must not sink the rest
            log.warning("historical failed for %s: %s", ticker, exc)
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
