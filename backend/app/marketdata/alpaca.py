"""Crypto quotes from Alpaca's public crypto feed — no credentials needed.

Kept alongside the stock providers because the live on-stage demo runs on
crypto (solution-design.md §4); the desk's stock universe goes through
`yahoo.py`. Crypto symbols are slash-formatted, e.g. "BTC/USD".
"""

import httpx

from app.marketdata.types import Quote, UnknownSymbol, UpstreamError

CRYPTO_URL = "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades"
TIMEOUT = httpx.Timeout(5.0)


async def get_latest_prices(
    symbols: list[str], client: httpx.AsyncClient | None = None
) -> dict[str, Quote]:
    """One quote per symbol; raises UnknownSymbol if any symbol has no trade."""
    owned = client is None
    client = client or httpx.AsyncClient()
    try:
        try:
            resp = await client.get(
                CRYPTO_URL, params={"symbols": ",".join(symbols)}, timeout=TIMEOUT
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise UpstreamError(
                f"Alpaca returned {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Alpaca unreachable: {exc}") from exc
    finally:
        if owned:
            await client.aclose()

    trades = resp.json().get("trades", {})
    quotes = {
        sym: Quote(
            symbol=sym,
            price=trades[sym]["p"],
            currency=sym.split("/")[1],
            ts=trades[sym]["t"],
            exchange="Alpaca Crypto",
            source="alpaca-crypto",
        )
        for sym in symbols
        if sym in trades
    }
    missing = [s for s in symbols if s not in quotes]
    if missing:
        raise UnknownSymbol(f"no trades found for: {', '.join(missing)}")
    return quotes
