"""Scripted binary-vote deliberation on the in-memory floor.

Sentiment votes SELL ETH while realtime and historical vote BUY; the
conflicting pairs trade rebuttals and the evaluation agent settles it with
argument scores × track-record credibility. Realtime enters with a losing
streak on record, so its thin "price is up" case carries little weight.

    python -m voting.demo
"""

from voting.deliberation import Case, Side, Stance, run_deliberation
from voting.judge import HeuristicJudge
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor

STANCES = [
    Stance(agent="sentiment", ticker="ETH/USD", side=Side.SELL),
    Stance(agent="realtime", ticker="ETH/USD", side=Side.BUY),
    Stance(agent="historical", ticker="ETH/USD", side=Side.BUY),
    Stance(agent="sentiment", ticker="BTC/USD", side=Side.BUY),
    Stance(agent="realtime", ticker="BTC/USD", side=Side.BUY),
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
        "time in the following two sessions.",
    ("sentiment", "BTC/USD"):
        "ETF inflow coverage net-positive, 24h news flow clean; BUY is justified.",
    ("realtime", "BTC/USD"):
        "Up 1.2% vs open on 1.8x average volume with tracker momentum confirming.",
}


class ScriptedAgent:
    def __init__(self, name: str):
        self.name = name

    def make_case(self, own: Stance, others) -> str:
        return CASES[(self.name, own.ticker)]

    def rebut(self, own: Stance, opposing_case: Case) -> str:
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
    verdict = run_deliberation("demo-001", STANCES, agents, HeuristicJudge(), record, room)

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
        flag = " UNANIMOUS" if tv.unanimous else ""
        print(f"{tv.ticker}: {tv.decision.value.upper()} at {tv.conviction:.0%} conviction{flag}  ({shares})")
    print(verdict.narrative)


if __name__ == "__main__":
    main()
