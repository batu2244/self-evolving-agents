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
import prompts
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
    p.add_argument(
        "--prompt-file", action="append", metavar="AGENT=PATH", default=[],
        help="Override an agent system prompt for this run. AGENT is one of "
             "news, historical, realtime, forecaster. Repeatable.",
    )
    p.add_argument("--list-prompts", action="store_true",
                   help="Print agent prompt hashes, sources, and text, then exit")
    p.add_argument(
        "--equation", action="append", metavar="AGENT=NAME", default=[],
        help="Select a named equation strategy for this run. AGENT is one of "
             "news, historical, realtime, forecaster. Repeatable.",
    )
    p.add_argument("--list-equations", action="store_true",
                   help="Print equation strategies and current selections, then exit")
    p.add_argument(
        "--analysis-policy", action="append", metavar="AGENT=POLICY", default=[],
        help="Choose equation selection policy: auto or configured. Repeatable. "
             "Passing --equation locks that agent to configured unless overridden here.",
    )
    p.add_argument("--list-analysis-policies", action="store_true",
                   help="Print each agent's equation selection policy, then exit")
    p.add_argument(
        "--gemini-thinking",
        action="store_true",
        help="Force real Gemini thinking decisions even when MOCK_MODE=1. "
             "Useful for reproducible demos with deterministic market inputs.",
    )
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


def apply_prompt_files(items: list[str]) -> None:
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--prompt-file expects AGENT=PATH, got {item!r}")
        agent, _, path = item.partition("=")
        prompts.set_prompt_override(agent.strip(), path.strip())


def print_prompts() -> None:
    for agent, snap in prompts.list_prompts().items():
        print(f"{agent:10} {snap['prompt_hash']} {snap['source']}")
        print(snap["system_prompt"])
        print()


def collect_equations(items: list[str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--equation expects AGENT=NAME, got {item!r}")
        agent, _, name = item.partition("=")
        selected[agent.strip()] = name.strip()
    return selected


def print_equations() -> None:
    current = config.equation_snapshot()
    for agent, choices in sorted(config.EQUATION_CHOICES.items()):
        print(f"{agent:10} current={current[agent]}")
        print(f"{'':10} choices={', '.join(choices)}")


def collect_analysis_policies(items: list[str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--analysis-policy expects AGENT=POLICY, got {item!r}")
        agent, _, policy = item.partition("=")
        selected[agent.strip()] = policy.strip()
    return selected


def print_analysis_policies() -> None:
    current = config.analysis_policy_snapshot()
    for agent in sorted(current):
        print(
            f"{agent:10} current={current[agent]} "
            f"choices={', '.join(config.ANALYSIS_POLICIES)}"
        )


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

    if args.gemini_thinking:
        config.GEMINI_REASONING_IN_MOCK_MODE = True

    apply_prompt_files(args.prompt_file)
    if args.list_prompts:
        print_prompts()
        return 0

    equation_overrides = collect_equations(args.equation)
    if equation_overrides:
        applied = config.apply_equation_overrides(equation_overrides)
        config.apply_analysis_policy_overrides({
            agent: "configured" for agent in equation_overrides
        })
        log.info("equation strategies overridden for this run: %s", applied)
    policy_overrides = collect_analysis_policies(args.analysis_policy)
    if policy_overrides:
        applied = config.apply_analysis_policy_overrides(policy_overrides)
        log.info("analysis policies overridden for this run: %s", applied)
    if args.list_equations or args.list_analysis_policies:
        if args.list_equations:
            print_equations()
        if args.list_equations and args.list_analysis_policies:
            print()
        if args.list_analysis_policies:
            print_analysis_policies()
        return 0

    db.init_db()
    learned = db.activate_learned_policy()
    if learned:
        log.info("activated daily learned policy: %s", learned)

    # Explicit run flags are applied after learned defaults, so a Guild/manual
    # experiment can override policy without changing the stored active version.
    overrides = collect_overrides(args)
    if overrides:
        applied = config.apply_overrides(overrides)
        log.info("tunables overridden for this run: %s", applied)

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
