"""Realtime analyst: collect a quote, store the snapshot, emit a momentum signal."""

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

log = logging.getLogger("deltadesk.realtime")

SOURCE = "realtime"


async def collect(ticker: str, run_id: str, cycle: str) -> dict:
    quote = await marketdata.fetch_quote(ticker)
    db.upsert_snapshot(run_id, ticker.upper(), cycle, {**quote, "raw": quote})
    log.info("%s: quote %.4f from %s", ticker, quote.get("price") or 0.0, quote.get("source"))
    return quote


def build_signal(ticker: str, quote: dict, run_id: str, cycle: str | None = None) -> Signal:
    features = derive.derive_momentum(quote)
    missing = [f for f in ("open", "previous_close") if not quote.get(f)]
    degraded = bool(missing) or not quote.get("price")
    notes = f"missing quote fields: {', '.join(missing)}" if missing else ""
    return Signal(
        ticker=ticker,
        source=SOURCE,
        direction=features["direction"],
        confidence=features["confidence"],
        rationale=features["rationale"],
        cycle=cycle or current_cycle(),
        provenance=Provenance(
            source_run_id=run_id,
            inputs_used=[f"market_snapshots[{ticker.upper()}] from {quote.get('source')}"],
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
            quote = await collect(ticker, run_id, cycle)
            signal = build_signal(ticker, quote, run_id, cycle)
            db.store_signal(signal, run_id)
            signals.append(signal)
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
