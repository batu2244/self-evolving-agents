from voting.transport import InMemoryFloor
from voting import messages as fmt
from voting.floor import run_vote_cycle
from voting.types import (
    AnalystId,
    Challenge,
    Direction,
    Rebuttal,
    SizeClass,
    Vote,
)

EQUAL = {AnalystId.SENTIMENT: 1 / 3, AnalystId.REALTIME: 1 / 3, AnalystId.HISTORICAL: 1 / 3}


def vote(analyst, direction, conf, ticker="ETH/USD"):
    signal = {"buy": conf, "sell": -conf, "hold": 0.0}[direction]
    return Vote(
        analyst=analyst, ticker=ticker, direction=Direction(direction),
        signal=signal, confidence=conf, size_class=SizeClass.FULL,
        rationale=f"{analyst.value} case for {direction}",
    )


class ConcedingAnalyst:
    """Scripted stand-in: concedes to any challenge by slashing confidence."""

    def __init__(self, analyst_id):
        self.id = analyst_id

    def rebut(self, challenge: Challenge, own_vote: Vote) -> Rebuttal:
        return Rebuttal(
            analyst=self.id, ticker=challenge.ticker,
            text="Fair point — cutting conviction.", revised_confidence=0.2,
        )


def test_challenge_round_can_flip_decision_to_hold():
    votes = {
        AnalystId.SENTIMENT: [vote(AnalystId.SENTIMENT, "buy", 0.85)],
        AnalystId.REALTIME: [vote(AnalystId.REALTIME, "buy", 0.8)],
        AnalystId.HISTORICAL: [vote(AnalystId.HISTORICAL, "sell", 0.9)],
    }
    analysts = {a: ConcedingAnalyst(a) for a in AnalystId}
    room = InMemoryFloor()

    memo = run_vote_cycle("t1", votes, analysts, EQUAL, room)

    d = memo.decisions[0]
    assert d.challenge is not None
    assert len(d.challenge.rebuttals) == 2  # both majority analysts answered
    # Concessions (conf 0.2 each) vs the 0.9 dissenter: sell mass now dominates.
    assert d.direction == Direction.SELL

    transcript = [m.text.splitlines()[0] for m in room.history()]
    assert sum(t.startswith(fmt.VOTE_TAG) for t in transcript) == 3
    assert sum(t.startswith(fmt.CHALLENGE_TAG) for t in transcript) == 1
    assert sum(t.startswith(fmt.REBUTTAL_TAG) for t in transcript) == 2
    assert transcript[-1].startswith(fmt.MEMO_TAG)


def test_votes_round_trip_through_room_messages():
    v = vote(AnalystId.SENTIMENT, "buy", 0.77)
    assert fmt.parse_vote(fmt.format_vote(v)) == v
    assert fmt.parse_vote("just chatting") is None
