import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.portfolio.ledger import (
    InsufficientCash,
    Ledger,
    LedgerError,
    ShortNotAllowed,
    Side,
)

TS = "2026-07-24T20:00:00+00:00"


def make_ledger(budget=10_000.0, **kw):
    return Ledger(initial_budget=budget, **kw)


def test_buy_moves_cash_into_position():
    led = make_ledger()
    led.execute("BTC/USD", Side.BUY, qty=0.1, price=60_000, ts=TS)
    snap = led.snapshot()
    assert snap.cash == pytest.approx(4_000)
    assert snap.equity == pytest.approx(10_000)  # marked at cost, nothing gained yet
    assert snap.total_pnl == pytest.approx(0)


def test_sell_realizes_pnl_at_avg_cost():
    led = make_ledger(budget=20_000)
    led.execute("ETH/USD", Side.BUY, qty=2, price=3_000, ts=TS)
    led.execute("ETH/USD", Side.BUY, qty=2, price=3_500, ts=TS)  # avg 3250
    fill = led.execute("ETH/USD", Side.SELL, qty=3, price=3_400, ts=TS)
    assert fill.realized_pnl == pytest.approx(3 * (3_400 - 3_250))
    snap = led.snapshot()
    assert snap.realized_pnl == pytest.approx(450)
    assert snap.positions[0].qty == pytest.approx(1)
    assert snap.positions[0].avg_cost == pytest.approx(3_250)


def test_closing_a_position_removes_it():
    led = make_ledger()
    led.execute("BTC/USD", Side.BUY, qty=0.1, price=50_000, ts=TS)
    led.execute("BTC/USD", Side.SELL, qty=0.1, price=55_000, ts=TS)
    snap = led.snapshot()
    assert snap.positions == []
    assert snap.cash == pytest.approx(10_500)
    assert snap.total_pnl == pytest.approx(500)
    assert snap.total_pnl == pytest.approx(snap.realized_pnl + snap.unrealized_pnl)


def test_mark_computes_unrealized_pnl():
    led = make_ledger()
    led.execute("BTC/USD", Side.BUY, qty=0.1, price=60_000, ts=TS)
    snap = led.mark({"BTC/USD": 66_000})
    assert snap.unrealized_pnl == pytest.approx(600)
    assert snap.equity == pytest.approx(10_600)
    assert snap.return_pct == pytest.approx(6)


def test_cannot_spend_more_than_cash():
    led = make_ledger(budget=1_000)
    with pytest.raises(InsufficientCash):
        led.execute("BTC/USD", Side.BUY, qty=1, price=60_000, ts=TS)


def test_cannot_sell_more_than_held_until_shorting_enabled():
    led = make_ledger()
    led.execute("BTC/USD", Side.BUY, qty=0.1, price=50_000, ts=TS)
    with pytest.raises(ShortNotAllowed):
        led.execute("BTC/USD", Side.SELL, qty=0.2, price=50_000, ts=TS)


def test_short_round_trip_when_enabled():
    # future mode, but the math must already be right
    led = make_ledger(allow_short=True)
    led.execute("BTC/USD", Side.SELL, qty=0.1, price=60_000, ts=TS)
    snap = led.mark({"BTC/USD": 54_000})
    assert snap.unrealized_pnl == pytest.approx(600)  # short profits on the way down
    fill = led.execute("BTC/USD", Side.BUY, qty=0.1, price=54_000, ts=TS)
    assert fill.realized_pnl == pytest.approx(600)
    assert led.snapshot().cash == pytest.approx(10_600)


def test_tracker_delta_vs_buy_and_hold():
    led = make_ledger(tracker_symbol="SPY")
    led.mark({"SPY": 500})  # baseline: budget buys 20 SPY
    led.execute("AAPL", Side.BUY, qty=10, price=200, ts=TS)
    snap = led.mark({"AAPL": 210, "SPY": 505})  # desk +100, tracker +1% = +100
    assert snap.tracker_equity == pytest.approx(10_100)
    assert snap.delta_usd == pytest.approx(0)


def test_rejects_nonpositive_inputs():
    led = make_ledger()
    with pytest.raises(LedgerError):
        led.execute("BTC/USD", Side.BUY, qty=0, price=100, ts=TS)
    with pytest.raises(LedgerError):
        led.execute("BTC/USD", Side.BUY, qty=1, price=-5, ts=TS)
    with pytest.raises(LedgerError):
        Ledger(initial_budget=0)


# --- API layer ---


@pytest.fixture
def client():
    from app.portfolio import router as portfolio_router

    portfolio_router._state.clear()
    return TestClient(app)


def test_api_full_cycle(client):
    assert client.get("/api/portfolio").status_code == 404

    r = client.post("/api/portfolio", json={"budgetUsd": 10_000, "trackerSymbol": "BTC/USD"})
    assert r.status_code == 201
    assert r.json()["cash"] == 10_000

    r = client.post(
        "/api/portfolio/fills",
        json={"symbol": "BTC/USD", "side": "buy", "qty": 0.1, "price": 60_000},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["fill"]["notional"] == pytest.approx(6_000)
    assert body["snapshot"]["cash"] == pytest.approx(4_000)

    r = client.post("/api/portfolio/mark", json={"prices": {"BTC/USD": 66_000}})
    snap = r.json()
    assert snap["unrealizedPnl"] == pytest.approx(600)
    assert snap["totalPnl"] == pytest.approx(600)

    r = client.get("/api/portfolio/fills")
    assert [f["side"] for f in r.json()] == ["buy"]


def test_api_market_order_fills_at_live_price(client, monkeypatch):
    from app.marketdata import service
    from app.marketdata.types import Quote

    async def fake(symbols):
        return {
            s: Quote(s, 64_000.0, "USD", "2026-07-24T20:00:00Z", "Alpaca Crypto", "alpaca-crypto")
            for s in symbols
        }

    monkeypatch.setattr(service, "get_latest_prices", fake)
    client.post("/api/portfolio", json={"budgetUsd": 10_000})
    r = client.post(
        "/api/portfolio/fills",
        json={"symbol": "BTC/USD", "side": "buy", "qty": 0.1},  # no price = market order
    )
    assert r.status_code == 201
    assert r.json()["fill"]["price"] == pytest.approx(64_000)
    assert r.json()["snapshot"]["cash"] == pytest.approx(3_600)


def test_api_rejects_bad_fills(client):
    client.post("/api/portfolio", json={"budgetUsd": 1_000})
    r = client.post(
        "/api/portfolio/fills",
        json={"symbol": "BTC/USD", "side": "sell", "qty": 1, "price": 100},
    )
    assert r.status_code == 422
    assert "short selling is not enabled" in r.json()["detail"]
