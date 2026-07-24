import asyncio

import pytest

from voting.deliberation import Side
from voting.judge import HeuristicJudge
from voting.village import SignalVoter, Village


class FakeSignal:
    def __init__(self, ticker, direction, confidence, rationale):
        self.ticker = ticker
        self.direction = direction
        self.confidence = confidence
        self.rationale = rationale
        self.provenance = type("P", (), {"degraded": False, "inputs_used": ["fixture"]})()


class FakeModule:
    def __init__(self, direction, confidence=0.7, rationale="because the data says so, 3 points confirm"):
        self.direction = direction
        self.confidence = confidence
        self.rationale = rationale

    async def run(self, tickers, cycle):
        return [FakeSignal(t, self.direction, self.confidence, self.rationale) for t in tickers]


class BrokenModule:
    async def run(self, tickers, cycle):
        raise RuntimeError("collection exploded")


def _types(**modules):
    return {
        name: (lambda n=name, m=mod: SignalVoter(n, m))
        for name, mod in modules.items()
    }


async def _fake_price(_ticker):
    return 100.0


def _village(tmp_path, **modules) -> Village:
    return Village(
        name="test", tickers=["XTB.WA"], judge=HeuristicJudge(),
        agent_types=_types(**modules), price_fn=_fake_price, data_dir=tmp_path,
    )


def test_village_initializes_one_of_each_type(tmp_path):
    v = _village(tmp_path, news=FakeModule(0.5), realtime=FakeModule(-0.4), historical=FakeModule(0.2))
    assert sorted(v.agents) == ["historical", "news", "realtime"]


def test_village_heartbeat_votes_binary(tmp_path):
    v = _village(tmp_path, news=FakeModule(0.5), realtime=FakeModule(-0.4), historical=FakeModule(0.2))
    out = asyncio.run(v.heartbeat(cycle="c1"))
    r = out["results"]["XTB.WA"]
    assert r["decision"] in ("buy", "sell")
    assert r["decision"] == "buy"  # 2 buy signals vs 1 sell, equal credibility
    assert not r["unanimous"]
    headers = [m["text"].splitlines()[0] for m in r["transcript"]]
    assert sum(h.startswith("🗳️ STANCE") for h in headers) == 3
    assert headers[-1].startswith("⚖️ VERDICT")


def test_second_heartbeat_grades_the_first(tmp_path):
    v = _village(tmp_path, news=FakeModule(0.5), realtime=FakeModule(-0.4))
    asyncio.run(v.heartbeat(cycle="c1"))
    out2 = asyncio.run(v.heartbeat(cycle="c2"))
    agents_graded = {g["agent"] for g in out2["grading"]}
    assert {"news", "realtime", "DESK"} <= agents_graded


def test_broken_agent_sits_out_without_killing_the_vote(tmp_path):
    v = _village(tmp_path, news=FakeModule(0.5), broken=BrokenModule())
    out = asyncio.run(v.heartbeat(cycle="c1"))
    r = out["results"]["XTB.WA"]
    assert r["decision"] == "buy"
    assert list(r["contributions"]) == ["news"]  # broken agent absent, vote proceeds
