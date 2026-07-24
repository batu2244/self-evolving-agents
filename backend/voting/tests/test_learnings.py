from voting.learnings import LearningStore


def _store(tmp_path) -> LearningStore:
    return LearningStore(f"sqlite:///{tmp_path}/learnings.db")


def _grading(agent, side, score, ticker="XTB.WA", cred=0.5):
    return {"ticker": ticker, "agent": agent, "side": side, "score": score,
            "credibility": cred}


def test_wrong_streak_produces_learning(tmp_path):
    store = _store(tmp_path)
    for cycle in ("c1", "c2", "c3"):
        store.record_gradings("v", cycle, [_grading("trend", "buy", -0.5)])
    out = store.derive(
        village="v", cycle="c3",
        graded=[_grading("trend", "buy", -0.5, cred=0.41)],
        decisions_now={"XTB.WA": "buy"}, decisions_prev={"XTB.WA": "buy"},
        credibility={"trend": 0.41}, prev_leader="trend",
    )
    kinds = {l.kind for l in out}
    assert "streak" in kinds
    streak = next(l for l in out if l.kind == "streak")
    assert "wrong side" in streak.text and streak.agent == "trend"


def test_flip_and_leader_change(tmp_path):
    store = _store(tmp_path)
    store.record_gradings("v", "c1", [_grading("tape", "sell", 0.4)])
    out = store.derive(
        village="v", cycle="c1",
        graded=[_grading("tape", "sell", 0.4)],
        decisions_now={"XTB.WA": "sell"}, decisions_prev={"XTB.WA": "buy"},
        credibility={"tape": 0.62, "trend": 0.48}, prev_leader="trend",
    )
    kinds = {l.kind for l in out}
    assert "flip" in kinds and "leader" in kinds


def test_vindicated_dissenter(tmp_path):
    store = _store(tmp_path)
    graded = [
        _grading("tape", "buy", -0.6),
        _grading("trend", "buy", -0.6),
        _grading("newsflow", "sell", 0.6),
        {"ticker": "XTB.WA", "agent": "DESK", "side": "buy", "score": -0.6,
         "credibility": None},
    ]
    store.record_gradings("v", "c1", graded)
    out = store.derive(
        village="v", cycle="c1", graded=graded,
        decisions_now={"XTB.WA": "buy"}, decisions_prev={},
        credibility={"tape": 0.5, "trend": 0.5, "newsflow": 0.5}, prev_leader=None,
    )
    dissent = [l for l in out if l.kind == "dissent"]
    assert len(dissent) == 1 and dissent[0].agent == "newsflow"


def test_latest_surfaces_learnings(tmp_path):
    store = _store(tmp_path)
    store.derive(
        village="v", cycle="c1", graded=[],
        decisions_now={"XTB.WA": "sell"}, decisions_prev={"XTB.WA": "buy"},
        credibility={}, prev_leader=None,
    )
    latest = store.latest(village="v")
    assert len(latest) == 1
    assert latest[0]["kind"] == "flip"
    assert "flipped" in latest[0]["text"]
