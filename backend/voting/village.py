"""The village: one committee instance with one agent of each type.

The desk's agent types live in deltadesk/agents (news, realtime,
historical — each emitting the uniform Signal contract). Initializing a
village instantiates exactly one voter of every registered type; each
heartbeat they all collect, convert their Signal into a binary stance
(direction >= 0 → BUY, else SELL), and deliberate:

    grade previous heartbeat → stances → cases → rebuttals → judged verdict

New agent types register with `register_agent_type(name, factory)` and are
automatically present in every village initialized afterwards.

    python -m voting.village XTB.WA            # one heartbeat
    python -m voting.village XTB.WA --verbose  # with floor transcript

Village state (previous decision, track record) persists per village name
in voting/data/, so a cron/loop of heartbeats is self-grading, same as
voting.cycle.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

# deltadesk/ is a flat app at the repo root; its modules resolve top-level
# names (config, database, …) by self-inserting their dir on sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from .deliberation import Case, Side, Stance, run_deliberation
from .judge import default_judge
from .learnings import LearningStore
from .track_record import TrackRecord
from .transport import InMemoryFloor

DATA_DIR = Path(__file__).parent / "data"
FULL_MOVE = 0.004  # a 0.4% move between heartbeats = full-magnitude outcome


class VillageAgent(Protocol):
    """One voter. Wraps an agent type's collection into the floor interface."""

    name: str

    async def collect(self, tickers: list[str], cycle: str) -> None: ...

    def stance(self, ticker: str) -> Stance | None: ...

    def make_case(self, own: Stance, others: list[Stance]) -> str: ...

    def rebut(self, own: Stance, opposing_case: Case) -> str: ...


class SignalVoter:
    """Adapter for any deltadesk analyst module (async run(tickers, cycle)
    -> list[Signal]). One instance = one voter in the village."""

    def __init__(self, name: str, module) -> None:
        self.name = name
        self._module = module
        self._signals: dict[str, object] = {}

    async def collect(self, tickers: list[str], cycle: str) -> None:
        try:
            signals = await self._module.run(tickers, cycle)
        except Exception as exc:
            print(f"[village] {self.name} collection failed: {exc}", file=sys.stderr)
            signals = []
        self._signals = {s.ticker.upper(): s for s in signals}

    def stance(self, ticker: str) -> Stance | None:
        s = self._signals.get(ticker.upper())
        if s is None:
            return None  # nothing collected → this agent sits the vote out
        return Stance(
            agent=self.name,
            ticker=ticker.upper(),
            side=Side.BUY if s.direction >= 0 else Side.SELL,
        )

    def make_case(self, own: Stance, others: list[Stance]) -> str:
        s = self._signals[own.ticker]
        degraded = " (inputs degraded)" if s.provenance.degraded else ""
        return (
            f"Signal {s.direction:+.2f} at {s.confidence:.0%} confidence{degraded}: "
            f"{s.rationale}"
        )

    def rebut(self, own: Stance, opposing_case: Case) -> str:
        s = self._signals[own.ticker]
        inputs = ", ".join(s.provenance.inputs_used[:2]) or "my collected inputs"
        return (
            f"My read stands on {inputs} at {s.confidence:.0%} confidence; "
            f"{opposing_case.agent}'s case doesn't contradict that evidence."
        )


# ------------------------------------------------------ agent type registry


def _news_factory() -> VillageAgent:
    from deltadesk.agents import news_agent

    return SignalVoter("news", news_agent)


def _realtime_factory() -> VillageAgent:
    from deltadesk.agents import realtime_agent

    return SignalVoter("realtime", realtime_agent)


def _historical_factory() -> VillageAgent:
    from deltadesk.agents import historical_agent

    return SignalVoter("historical", historical_agent)


AGENT_TYPES: dict[str, Callable[[], VillageAgent]] = {
    "news": _news_factory,
    "realtime": _realtime_factory,
    "historical": _historical_factory,
}


def register_agent_type(name: str, factory: Callable[[], VillageAgent]) -> None:
    """Incoming agent types hook in here; every village initialized after
    registration gets one instance of the new type."""
    AGENT_TYPES[name] = factory


# ----------------------------------------------------------------- village


class Village:
    """One committee: one agent of each registered type, voting together."""

    def __init__(
        self,
        name: str,
        tickers: list[str],
        judge=None,
        agent_types: dict[str, Callable[[], VillageAgent]] | None = None,
        price_fn: Callable[[str], "asyncio.Future | object"] | None = None,
        data_dir: Path | None = None,
        learning_store: LearningStore | None = None,
    ) -> None:
        self.name = name
        self.tickers = [t.upper() for t in tickers]
        self.judge = judge or default_judge()
        types = agent_types if agent_types is not None else AGENT_TYPES
        self.agents: dict[str, VillageAgent] = {n: f() for n, f in types.items()}
        self._data_dir = data_dir or DATA_DIR
        self.record = TrackRecord(
            path=self._data_dir / f"village_{name}_track_record.json", alpha=0.15
        )
        self._price_fn = price_fn or _default_price
        self.learnings = learning_store or LearningStore()

    # -- state -------------------------------------------------------------

    @property
    def _state_path(self) -> Path:
        return self._data_dir / f"village_{self.name}_state.json"

    def _load_state(self) -> dict | None:
        if self._state_path.exists():
            return json.loads(self._state_path.read_text())
        return None

    def _save_state(self, state: dict) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(state, indent=2))

    # -- the heartbeat -----------------------------------------------------

    async def heartbeat(self, cycle: str | None = None) -> dict:
        now = datetime.now(timezone.utc)
        cycle = cycle or now.strftime("%Y%m%d-%H%M")
        prices = {t: await self._price_fn(t) for t in self.tickers}

        # 1. Grade the previous heartbeat before voting again.
        grading = []
        prev = self._load_state()
        if prev:
            for t, entry in prev.get("tickers", {}).items():
                price_now = prices.get(t)
                if not price_now or not entry.get("price"):
                    continue
                move = price_now / entry["price"] - 1
                for st in entry["stances"]:
                    direction = 1 if st["side"] == "buy" else -1
                    score = max(-1.0, min(1.0, direction * move / FULL_MOVE))
                    cred = self.record.record_outcome(st["agent"], score)
                    grading.append({"ticker": t, "agent": st["agent"], "side": st["side"],
                                    "score": round(score, 3), "credibility": cred})
                desk_dir = 1 if entry["decision"] == "buy" else -1
                grading.append({"ticker": t, "agent": "DESK", "side": entry["decision"],
                                "score": round(desk_dir * move / FULL_MOVE, 3),
                                "credibility": None})

        # 2. Every agent type collects concurrently, then the vote runs.
        await asyncio.gather(*(a.collect(self.tickers, cycle) for a in self.agents.values()))

        results = {}
        state: dict = {"cycle": cycle, "ts": now.isoformat(), "tickers": {}}
        for t in self.tickers:
            stances = [s for s in (a.stance(t) for a in self.agents.values()) if s]
            if not stances:
                continue
            voters = {s.agent: self.agents[s.agent] for s in stances}
            room = InMemoryFloor()
            verdict = run_deliberation(
                f"{self.name}-{t}-{cycle}", stances, voters, self.judge, self.record, room
            )
            tv = verdict.verdicts[0]
            results[t] = {
                "decision": tv.decision.value,
                "conviction": tv.conviction,
                "unanimous": tv.unanimous,
                "contributions": tv.contributions,
                "scores": [s.model_dump() for s in tv.scores],
                "transcript": [
                    {"sender": m.sender, "text": m.text, "mentions": m.mentions}
                    for m in room.history()
                ],
            }
            state["tickers"][t] = {
                "price": prices.get(t),
                "decision": tv.decision.value,
                "stances": [{"agent": s.agent, "side": s.side.value} for s in stances],
            }

        # 3. Notes to Actian, learnings surfaced back out of them.
        derived = []
        if grading:
            self.learnings.record_gradings(self.name, cycle, grading)
            credibility = {a: self.record.credibility(a) for a in self.agents}
            derived = self.learnings.derive(
                village=self.name,
                cycle=cycle,
                graded=grading,
                decisions_now={t: r["decision"] for t, r in results.items()},
                decisions_prev={t: e["decision"] for t, e in (prev or {}).get("tickers", {}).items()},
                credibility=credibility,
                prev_leader=(prev or {}).get("leader"),
            )
        cred_now = {a: self.record.credibility(a) for a in self.agents}
        state["leader"] = max(cred_now, key=lambda a: cred_now[a]) if cred_now else None

        self._save_state(state)
        return {"village": self.name, "cycle": cycle, "grading": grading,
                "prices": prices, "results": results,
                "learnings": [{"kind": l.kind, "ticker": l.ticker, "agent": l.agent,
                               "text": l.text} for l in derived]}


async def _default_price(ticker: str) -> float | None:
    from deltadesk import marketdata

    try:
        quote = await marketdata.fetch_quote(ticker)
        return quote.get("price")
    except Exception as exc:
        print(f"[village] price fetch failed for {ticker}: {exc}", file=sys.stderr)
        return None


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tickers = args or ["XTB.WA"]
    verbose = "--verbose" in sys.argv

    village = Village(name="default", tickers=tickers)
    print(f"village 'default' initialized with one of each agent type: "
          f"{', '.join(village.agents)}")
    out = asyncio.run(village.heartbeat())

    for g in out["grading"]:
        who = f"{g['agent']:<11}"
        cred = f" → credibility {g['credibility']:.2f}" if g["credibility"] else ""
        print(f"graded {g['ticker']}: {who} {g['side']:<4} scored {g['score']:+.2f}{cred}")
    if not out["grading"]:
        print("first heartbeat — nothing to grade yet")

    for t, r in out["results"].items():
        flag = " UNANIMOUS" if r["unanimous"] else ""
        print(f"{t}: {r['decision'].upper()} at {r['conviction']:.0%} conviction{flag}")
        print("  " + ", ".join(f"{a} {w:.0%}" for a, w in sorted(r["contributions"].items())))

    for l in out["learnings"]:
        print(f"📝 learning ({l['kind']}): {l['text']}")
        if verbose:
            for m in r["transcript"]:
                print(f"\n[{m['sender']}]")
                print(m["text"].split("```json")[0].rstrip())


if __name__ == "__main__":
    main()
