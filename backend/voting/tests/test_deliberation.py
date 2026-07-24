from voting.deliberation import Case, RebuttalMsg, Side, Stance, run_deliberation
from voting.judge import HeuristicJudge
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor


class ScriptedAgent:
    def __init__(self, name: str, case_text: str):
        self.name = name
        self._case = case_text

    def make_case(self, own, others):
        return self._case

    def rebut(self, own, opposing_case):
        return f"{self.name} disagrees with {opposing_case.agent}: risk is overstated."


def _agents():
    return {
        "sentiment": ScriptedAgent(
            "sentiment",
            "Headlines turned 78% negative in 24h; 3 outflow stories at 2.1x volume. SELL.",
        ),
        "realtime": ScriptedAgent("realtime", "Price is up. BUY."),
    }


def _stances():
    return [
        Stance(agent="sentiment", ticker="ETH/USD", side=Side.SELL),
        Stance(agent="realtime", ticker="ETH/USD", side=Side.BUY),
    ]


def test_deliberation_produces_binary_decision_and_transcript(tmp_path):
    record = TrackRecord(tmp_path / "tr.json")
    room = InMemoryFloor()

    verdict = run_deliberation(
        "d1", _stances(), _agents(), HeuristicJudge(), record, room
    )

    tv = verdict.verdicts[0]
    assert tv.ticker == "ETH/USD"
    assert tv.decision in (Side.BUY, Side.SELL)
    # Sentiment's specific, numbers-backed case outscores "Price is up." —
    # with equal credibility the desk sells.
    assert tv.decision == Side.SELL
    assert not tv.unanimous
    assert 0.5 <= tv.conviction <= 1.0
    assert abs(sum(tv.contributions.values()) - 1.0) < 0.01

    headers = [m.text.splitlines()[0] for m in room.history()]
    assert sum(h.startswith("🗳️ STANCE") for h in headers) == 2
    assert sum(h.startswith("📣 CASE") for h in headers) == 2
    assert sum(h.startswith("🛡️ REBUTTAL") for h in headers) == 2  # conflicting pair
    assert headers[-1].startswith("⚖️ VERDICT")


def test_poor_track_record_can_flip_the_vote(tmp_path):
    record = TrackRecord(tmp_path / "tr.json")
    # sentiment (the SELL voter) has been wrong repeatedly; realtime right.
    for _ in range(6):
        record.record_outcome("sentiment", -0.9)
        record.record_outcome("realtime", 0.9)
    assert record.credibility("sentiment") < record.credibility("realtime")

    # Identical (strong) cases so only credibility differs.
    agents = {
        "sentiment": ScriptedAgent("sentiment", "Volume 2x average, 3 catalysts, 78% skew."),
        "realtime": ScriptedAgent("realtime", "Volume 2x average, 3 catalysts, 78% skew."),
    }
    verdict = run_deliberation(
        "d2", _stances(), agents, HeuristicJudge(), record, InMemoryFloor()
    )
    tv = verdict.verdicts[0]
    # The credible agent's side wins the binary vote.
    assert tv.decision == Side.BUY
    assert tv.contributions["realtime"] > tv.contributions["sentiment"]


def test_unanimous_vote_flagged(tmp_path):
    stances = [
        Stance(agent="sentiment", ticker="BTC/USD", side=Side.BUY),
        Stance(agent="realtime", ticker="BTC/USD", side=Side.BUY),
    ]
    verdict = run_deliberation(
        "d3", stances, _agents(), HeuristicJudge(),
        TrackRecord(tmp_path / "tr.json"), InMemoryFloor(),
    )
    tv = verdict.verdicts[0]
    assert tv.decision == Side.BUY
    assert tv.unanimous
    assert tv.conviction == 1.0


def test_credibility_floor_never_silences(tmp_path):
    record = TrackRecord(tmp_path / "tr.json")
    for _ in range(50):
        record.record_outcome("doom", -1.0)
    assert record.credibility("doom") >= 0.1


def test_no_conflict_no_rebuttals(tmp_path):
    stances = [
        Stance(agent="sentiment", ticker="BTC/USD", side=Side.BUY),
        Stance(agent="realtime", ticker="BTC/USD", side=Side.BUY),
    ]
    room = InMemoryFloor()
    run_deliberation(
        "d4", stances, _agents(), HeuristicJudge(),
        TrackRecord(tmp_path / "tr.json"), room,
    )
    headers = [m.text.splitlines()[0] for m in room.history()]
    assert not any(h.startswith("🛡️") for h in headers)


def test_heuristic_judge_penalizes_unanswered_rebuttal():
    judge = HeuristicJudge()
    cases = [Case(agent="a", ticker="X", argument="Up 2.5% on 1.8x volume, 3 catalysts.")]
    base = judge.score([], cases, [])[0].score
    hit = judge.score(
        [], cases,
        [RebuttalMsg(agent="b", ticker="X", against="a", argument="Data is stale.")],
    )[0].score
    assert hit < base
