"""Scripted deliberation cycle on the in-memory floor — the demo's 30 seconds.

Each agent enters with just the position change it wants on ETH/USD:
sentiment wants to flip short while realtime and historical want to add.
They argue, the conflicting pair trade rebuttals, and the evaluation agent
blends the proposals weighted by argument quality × track-record
credibility. Realtime enters with a losing streak on record, so its thin
"price is up" case moves the desk far less than it wants.

    python -m voting.demo
"""

from voting.deliberation import Case, PositionChange, run_deliberation
from voting.judge import default_judge
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor

PROPOSALS = [
    PositionChange(agent="sentiment", ticker="ETH/USD", current=0.5, target=-0.25),
    PositionChange(agent="realtime", ticker="ETH/USD", current=0.5, target=1.0),
    PositionChange(agent="historical", ticker="ETH/USD", current=0.5, target=0.75),
    PositionChange(agent="sentiment", ticker="BTC/USD", current=0.25, target=0.5),
    PositionChange(agent="realtime", ticker="BTC/USD", current=0.25, target=0.5),
]

CASES = {
    ("sentiment", "ETH/USD"):
        "Headline sentiment flipped to 78% negative over 24h: three exchange-outflow "
        "stories trending on 2.1x normal social volume, no positive catalysts queued. "
        "The last two times skew crossed 75% negative, ETH closed down 2%+.",
    ("realtime", "ETH/USD"):
        "Price is up 1.4% since open and holding above VWAP. Momentum is momentum.",
    ("historical", "ETH/USD"):
        "Mean-reversion setup: 3 consecutive red days historically bounce 62% of the "
        "time in the following two sessions; sizing up modestly fits the pattern.",
    ("sentiment", "BTC/USD"):
        "ETF inflow coverage net-positive, 24h news flow clean; modest add is justified.",
    ("realtime", "BTC/USD"):
        "Up 1.2% vs open on 1.8x average volume with tracker momentum confirming.",
}


class ScriptedAgent:
    def __init__(self, name: str):
        self.name = name

    def make_case(self, own: PositionChange, others) -> str:
        return CASES[(self.name, own.ticker)]

    def rebut(self, own: PositionChange, opposing_case: Case) -> str:
        if self.name == "sentiment":
            return (
                "Momentum without volume-confirmed news support faded 4 of the last "
                "5 times; the outflow stories broke after your VWAP read."
            )
        return "Sentiment skew has been noisy this week; price action is primary."


def main() -> None:
    record = TrackRecord(path="/tmp/deltadesk-demo-track-record.json")
    # Seed the desk's history: realtime has been on a losing streak,
    # sentiment has been mostly right, historical is mid-pack.
    for _ in range(4):
        record.record_outcome("realtime", -0.7)
        record.record_outcome("sentiment", 0.6)
        record.record_outcome("historical", 0.1)

    room = InMemoryFloor()
    agents = {n: ScriptedAgent(n) for n in ("sentiment", "realtime", "historical")}
    verdict = run_deliberation("demo-001", PROPOSALS, agents, default_judge(), record, room)

    print("=" * 72)
    print("TRADING FLOOR TRANSCRIPT")
    print("=" * 72)
    for m in room.history():
        mentions = f"  → {', '.join('@' + x for x in m.mentions)}" if m.mentions else ""
        print(f"\n[{m.sender}]{mentions}")
        print(m.text.split("```json")[0].rstrip())

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print("credibility going in:", {a: record.credibility(a) for a in agents})
    for tv in verdict.verdicts:
        shares = ", ".join(f"{a}={w:.0%}" for a, w in sorted(tv.contributions.items()))
        print(f"{tv.ticker}: final position {tv.final_target:+.2f}  ({shares})")
    print(verdict.narrative)


if __name__ == "__main__":
    main()
