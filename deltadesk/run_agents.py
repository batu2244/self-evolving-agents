#!/usr/bin/env python3
"""Direct runner for the DeltaDesk agents -- no orchestrator required.

    python run_agents.py news
    python run_agents.py historical
    python run_agents.py realtime
    python run_agents.py forecast
    python run_agents.py all       # news + historical + realtime -> forecast
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import config
import database as db
from agents import forecaster_agent, historical_agent, news_agent, realtime_agent
from contracts import current_cycle

log = logging.getLogger("deltadesk.run")

ANALYSTS = {
    "news": news_agent,
    "historical": historical_agent,
    "realtime": realtime_agent,
}


async def run_all(tickers: list[str], cycle: str) -> dict:
    """Analysts first (concurrently), then the forecaster over what they stored."""
    results = await asyncio.gather(
        *(mod.run(tickers, cycle) for mod in ANALYSTS.values()),
        return_exceptions=True,
    )

    signals = []
    for name, result in zip(ANALYSTS, results):
        if isinstance(result, BaseException):
            log.error("%s analyst failed: %s", name, result)
            continue
        signals.extend(result)

    forecasts = await forecaster_agent.run(tickers, cycle)
    return {
        "cycle": cycle,
        "signals": [s.model_dump() for s in signals],
        "forecasts": [f.model_dump() for f in forecasts],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run DeltaDesk agents")
    p.add_argument("command", choices=[*ANALYSTS, "forecast", "all"])
    p.add_argument("--tickers", help="Comma-separated override for DEFAULT_SYMBOLS")
    p.add_argument("--cycle", help="Dedup bucket (default: current UTC hour)")
    p.add_argument(
        "--tune", action="append", metavar="KEY=VALUE", default=[],
        help="Override a tunable for this run, e.g. --tune SIGNAL_WEIGHTS.news=0.5. "
             "Repeatable. See --list-tunables.",
    )
    p.add_argument("--tune-file", help="JSON file of tunable overrides")
    p.add_argument("--list-tunables", action="store_true",
                   help="Print tunable knobs, bounds, and current values, then exit")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def collect_overrides(args: argparse.Namespace) -> dict[str, float]:
    overrides: dict[str, float] = {}
    if args.tune_file:
        overrides.update(config.load_overrides_file(args.tune_file))
    for item in args.tune:
        if "=" not in item:
            raise SystemExit(f"--tune expects KEY=VALUE, got {item!r}")
        key, _, value = item.partition("=")
        overrides[key.strip()] = float(value)
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.list_tunables:
        current = config.snapshot()
        for key, (low, high) in sorted(config.TUNABLES.items()):
            print(f"{key:34} = {current[key]:<10} allowed [{low}, {high}]")
        return 0

    # Tuning is applied once, before any agent runs, so weights stay static
    # for the whole cycle and the stamped snapshot describes every forecast in it.
    overrides = collect_overrides(args)
    if overrides:
        applied = config.apply_overrides(overrides)
        log.info("tunables overridden for this run: %s", applied)

    db.init_db()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else config.DEFAULT_SYMBOLS
    )
    cycle = args.cycle or current_cycle()

    if args.command == "all":
        payload = asyncio.run(run_all(tickers, cycle))
    elif args.command == "forecast":
        payload = [f.model_dump() for f in asyncio.run(forecaster_agent.run(tickers, cycle))]
    else:
        payload = [s.model_dump() for s in asyncio.run(ANALYSTS[args.command].run(tickers, cycle))]

    print(db.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
