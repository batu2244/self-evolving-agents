from voting.tally import find_dissenter, normalize_weights, tally_ticker
from voting.types import (
    AnalystId,
    Direction,
    SizeClass,
    TallyConfig,
    Vote,
)

CFG = TallyConfig()
EQUAL = {AnalystId.SENTIMENT: 1 / 3, AnalystId.REALTIME: 1 / 3, AnalystId.HISTORICAL: 1 / 3}


def vote(analyst, direction, conf, ticker="BTC/USD", size=SizeClass.FULL):
    signal = {"buy": conf, "sell": -conf, "hold": 0.0}[direction]
    return Vote(
        analyst=analyst,
        ticker=ticker,
        direction=Direction(direction),
        signal=signal,
        confidence=conf,
        size_class=size,
        rationale="test",
    )


def test_unanimous_buy_gets_full_size():
    votes = [
        vote(AnalystId.SENTIMENT, "buy", 0.8),
        vote(AnalystId.REALTIME, "buy", 0.7),
        vote(AnalystId.HISTORICAL, "buy", 0.6),
    ]
    d = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    assert d.direction == Direction.BUY
    assert d.unanimous
    assert d.size_factor == 1.0


def test_split_vote_cuts_size():
    votes = [
        vote(AnalystId.SENTIMENT, "buy", 0.9),
        vote(AnalystId.REALTIME, "buy", 0.8),
        vote(AnalystId.HISTORICAL, "sell", 0.3),
    ]
    d = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    assert d.direction == Direction.BUY
    assert not d.unanimous
    assert d.size_factor == CFG.split_size_factor  # full-size voters, halved


def test_no_majority_forces_hold():
    votes = [
        vote(AnalystId.SENTIMENT, "buy", 0.5),
        vote(AnalystId.REALTIME, "sell", 0.5),
        vote(AnalystId.HISTORICAL, "hold", 0.6),
    ]
    d = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    assert d.direction == Direction.HOLD
    assert d.size_factor == 0.0


def test_weights_decide_contested_votes():
    votes = [
        vote(AnalystId.SENTIMENT, "buy", 0.8),
        vote(AnalystId.REALTIME, "sell", 0.8),
        vote(AnalystId.HISTORICAL, "sell", 0.8),
    ]
    # Sentiment has earned dominant weight (Loop 1) — its buy still loses
    # 0.6 vs 0.4 is not enough? mass(buy)=0.6*0.8, mass(sell)=0.4*0.8 → buy wins.
    heavy_sent = {AnalystId.SENTIMENT: 0.6, AnalystId.REALTIME: 0.2, AnalystId.HISTORICAL: 0.2}
    d = tally_ticker("BTC/USD", votes, heavy_sent, CFG)
    assert d.direction == Direction.BUY
    # With equal weights the same votes go the other way.
    d2 = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    assert d2.direction == Direction.SELL


def test_confident_dissenter_detected():
    votes = [
        vote(AnalystId.SENTIMENT, "buy", 0.9),
        vote(AnalystId.REALTIME, "buy", 0.8),
        vote(AnalystId.HISTORICAL, "sell", 0.75),
    ]
    d = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    dissent = find_dissenter(d, CFG)
    assert dissent is not None and dissent.analyst == AnalystId.HISTORICAL

    # Below threshold: no challenge.
    votes[2] = vote(AnalystId.HISTORICAL, "sell", 0.5)
    d = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    assert find_dissenter(d, CFG) is None


def test_size_class_caps_position():
    votes = [
        vote(AnalystId.SENTIMENT, "buy", 0.8, size=SizeClass.PROBE),
        vote(AnalystId.REALTIME, "buy", 0.8, size=SizeClass.PROBE),
        vote(AnalystId.HISTORICAL, "buy", 0.8, size=SizeClass.PROBE),
    ]
    d = tally_ticker("BTC/USD", votes, EQUAL, CFG)
    assert d.unanimous
    assert d.size_factor == 0.25  # unanimity can't override cautious sizing


def test_missing_voter_weights_renormalize():
    w = normalize_weights(EQUAL, {AnalystId.SENTIMENT, AnalystId.REALTIME})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert AnalystId.HISTORICAL not in w
