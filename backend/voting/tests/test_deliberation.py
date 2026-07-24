from voting.deliberation import Case, PositionChange, run_deliberation
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
            "Headlines turned 78% negative in 24h; 3 outflow stories at 2.1x volume. Cutting exposure.",
        ),
        "realtime": ScriptedAgent("realtime", "Price is up. Buying more."),
    }


def _proposals():
    return [
        PositionChange(agent="sentiment", ticker="ETH/USD", current=0.5, target=-0.25),
        PositionChange(agent="realtime", ticker="ETH/USD", current=0.5, target=1.0),
    ]


def test_deliberation_blends_positions_and_posts_transcript(tmp_path):
    record = TrackRecord(tmp_path / "tr.json")
    room = InMemoryFloor()

    verdict = run_deliberation(
        "d1", _proposals(), _agents(), HeuristicJudge(), record, room
    )

    tv = verdict.verdicts[0]
    assert tv.ticker == "ETH/USD"
    # Blend must land strictly between the two proposals.
    assert -0.25 < tv.final_target < 1.0
    # Sentiment's specific, numbers-backed case outscores "Price is up."
    assert tv.contributions["sentiment"] > tv.contributions["realtime"]

    headers = [m.text.splitlines()[0] for m in room.history()]
    assert sum(h.startswith("📍 POSITION") for h in headers) == 2
    assert sum(h.startswith("📣 CASE") for h in headers) == 2
    assert sum(h.startswith("🛡️ REBUTTAL") for h in headers) == 2  # conflicting pair
    assert headers[-1].startswith("⚖️ VERDICT")


def test_poor_track_record_lowers_influence(tmp_path):
    record = TrackRecord(tmp_path / "tr.json")
    # realtime has been wrong repeatedly; sentiment right.
    for _ in range(5):
        record.record_outcome("realtime", -0.9)
        record.record_outcome("sentiment", 0.9)
    assert record.credibility("realtime") < record.credibility("sentiment")

    # Give both identical (strong) cases so only credibility differs.
    agents = {
        "sentiment": ScriptedAgent("sentiment", "Volume 2x average, 3 catalysts, 78% skew."),
        "realtime": ScriptedAgent("realtime", "Volume 2x average, 3 catalysts, 78% skew."),
    }
    verdict = run_deliberation(
        "d2", _proposals(), agents, HeuristicJudge(), record, InMemoryFloor()
    )
    tv = verdict.verdicts[0]
    assert tv.contributions["sentiment"] > tv.contributions["realtime"]
    # Blend pulled toward the credible agent's target (-0.25).
    assert tv.final_target < (1.0 + -0.25) / 2


def test_credibility_floor_never_silences(tmp_path):
    record = TrackRecord(tmp_path / "tr.json")
    for _ in range(50):
        record.record_outcome("doom", -1.0)
    assert record.credibility("doom") >= 0.1


def test_no_conflict_no_rebuttals(tmp_path):
    proposals = [
        PositionChange(agent="sentiment", ticker="BTC/USD", current=0.0, target=0.5),
        PositionChange(agent="realtime", ticker="BTC/USD", current=0.2, target=0.8),
    ]
    room = InMemoryFloor()
    run_deliberation(
        "d3", proposals, _agents(), HeuristicJudge(),
        TrackRecord(tmp_path / "tr.json"), room,
    )
    headers = [m.text.splitlines()[0] for m in room.history()]
    assert not any(h.startswith("🛡️") for h in headers)


def test_heuristic_judge_penalizes_unanswered_rebuttal():
    judge = HeuristicJudge()
    cases = [Case(agent="a", ticker="X", argument="Up 2.5% on 1.8x volume, 3 catalysts.")]
    base = judge.score([], cases, [])[0].score
    from voting.deliberation import RebuttalMsg

    hit = judge.score(
        [], cases,
        [RebuttalMsg(agent="b", ticker="X", against="a", argument="Data is stale.")],
    )[0].score
    assert hit < base
