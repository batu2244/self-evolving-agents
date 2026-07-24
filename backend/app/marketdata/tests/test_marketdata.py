import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.marketdata import alpaca, service, yahoo
from app.marketdata.types import (
    Quote,
    UnknownSymbol,
    UnsupportedExchange,
    UpstreamError,
)

TS = "2026-07-24T20:00:00Z"
TS_UNIX = 1784905200


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def yahoo_response(price, currency, exchange="TestEx"):
    meta = {
        "regularMarketPrice": price,
        "currency": currency,
        "regularMarketTime": TS_UNIX,
        "fullExchangeName": exchange,
    }
    return httpx.Response(200, json={"chart": {"result": [{"meta": meta}]}})


# --- yahoo provider ---


@pytest.mark.anyio
async def test_supported_exchanges_us_uk_de_pl():
    prices = {"AAPL": 333.0, "VOD.L": 11_460.0, "SIE.DE": 272.25, "PKO.WA": 106.58}

    def handler(request):
        sym = request.url.path.rsplit("/", 1)[1]
        return yahoo_response(prices[sym], "GBp" if sym == "VOD.L" else "USD")

    quotes = await yahoo.get_latest_prices(list(prices), client=mock_client(handler))
    assert set(quotes) == set(prices)
    assert quotes["PKO.WA"].price == 106.58


@pytest.mark.anyio
async def test_lse_pence_normalized_to_gbp():
    quotes = await yahoo.get_latest_prices(
        ["VOD.L"], client=mock_client(lambda r: yahoo_response(114.6, "GBp", "LSE"))
    )
    assert quotes["VOD.L"].price == pytest.approx(1.146)
    assert quotes["VOD.L"].currency == "GBP"


@pytest.mark.anyio
async def test_unsupported_exchange_rejected_up_front():
    with pytest.raises(UnsupportedExchange, match="7203.T"):
        await yahoo.get_latest_prices(["AAPL", "7203.T"], client=mock_client(None))


@pytest.mark.anyio
async def test_unknown_symbol_raises():
    with pytest.raises(UnknownSymbol, match="NOPE"):
        await yahoo.get_latest_prices(
            ["NOPE"], client=mock_client(lambda r: httpx.Response(404, json={}))
        )


@pytest.mark.anyio
async def test_upstream_error_is_wrapped():
    with pytest.raises(UpstreamError, match="429"):
        await yahoo.get_latest_prices(
            ["AAPL"], client=mock_client(lambda r: httpx.Response(429, text="rate limit"))
        )


# --- alpaca crypto provider ---


@pytest.mark.anyio
async def test_crypto_prices_need_no_credentials():
    def handler(request):
        assert "APCA-API-KEY-ID" not in request.headers
        return httpx.Response(
            200, json={"trades": {"BTC/USD": {"p": 64_000.5, "t": TS}}}
        )

    quotes = await alpaca.get_latest_prices(["BTC/USD"], client=mock_client(handler))
    assert quotes["BTC/USD"].price == 64_000.5
    assert quotes["BTC/USD"].currency == "USD"


# --- routing service ---


@pytest.mark.anyio
async def test_service_routes_stocks_and_crypto(monkeypatch):
    async def fake_yahoo(symbols, client=None):
        return {s: Quote(s, 1.0, "PLN", TS, "WSE", "yahoo") for s in symbols}

    async def fake_alpaca(symbols, client=None):
        return {s: Quote(s, 2.0, "USD", TS, "Alpaca Crypto", "alpaca-crypto") for s in symbols}

    monkeypatch.setattr(yahoo, "get_latest_prices", fake_yahoo)
    monkeypatch.setattr(alpaca, "get_latest_prices", fake_alpaca)
    quotes = await service.get_latest_prices(["PKO.WA", "BTC/USD"])
    assert quotes["PKO.WA"].source == "yahoo"
    assert quotes["BTC/USD"].source == "alpaca-crypto"


# --- API layer ---


def test_prices_endpoint(monkeypatch):
    async def fake(symbols):
        return {s: Quote(s, 100.0, "USD", TS, "TestEx", "yahoo") for s in symbols}

    monkeypatch.setattr(service, "get_latest_prices", fake)
    r = TestClient(app).get("/api/marketdata/prices", params={"symbols": "AAPL, PKO.WA"})
    assert r.status_code == 200
    assert set(r.json()["prices"]) == {"AAPL", "PKO.WA"}
    assert r.json()["prices"]["AAPL"]["currency"] == "USD"


def test_prices_endpoint_maps_errors(monkeypatch):
    async def fake(symbols):
        raise UnsupportedExchange("7203.T: unsupported exchange")

    monkeypatch.setattr(service, "get_latest_prices", fake)
    r = TestClient(app).get("/api/marketdata/prices", params={"symbols": "7203.T"})
    assert r.status_code == 422
