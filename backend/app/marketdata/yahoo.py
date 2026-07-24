"""Stock quotes via Yahoo Finance's chart API — keyless, covers our exchanges.

The desk trades stocks on the major US, British, German, and Polish exchanges,
addressed with Yahoo suffixes: plain ticker = US (NYSE/Nasdaq), ".L" = London,
".DE" = XETRA, ".WA" = Warsaw. Anything else is rejected up front.

LSE quotes arrive in pence (currency "GBp"); we normalize to GBP so a pence
price can never enter the ledger at 100x its value.
"""

import asyncio
from datetime import datetime, timezone

import httpx

from app.marketdata.types import Quote, UnknownSymbol, UnsupportedExchange, UpstreamError

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (DeltaDesk hackathon)"}
TIMEOUT = httpx.Timeout(5.0)

SUPPORTED_SUFFIXES = {
    "": "US (NYSE/Nasdaq)",
    "L": "London Stock Exchange",
    "DE": "Deutsche Börse XETRA",
    "WA": "Warsaw Stock Exchange",
}


def _check_supported(symbol: str) -> None:
    suffix = symbol.rsplit(".", 1)[1] if "." in symbol else ""
    if suffix.upper() not in SUPPORTED_SUFFIXES:
        supported = ", ".join(f"'.{s}'" if s else "none (US)" for s in SUPPORTED_SUFFIXES)
        raise UnsupportedExchange(
            f"{symbol}: unsupported exchange suffix '.{suffix}' — supported: {supported}"
        )


async def _fetch_quote(client: httpx.AsyncClient, symbol: str) -> Quote | None:
    try:
        resp = await client.get(
            CHART_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "1d"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Yahoo unreachable: {exc}") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise UpstreamError(f"Yahoo returned {resp.status_code}: {resp.text[:200]}")

    meta = (resp.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
    price, currency = meta.get("regularMarketPrice"), meta.get("currency")
    if price is None or not currency:
        return None
    if currency == "GBp":  # LSE quotes in pence
        price, currency = price / 100, "GBP"
    ts = datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc).isoformat()
    return Quote(
        symbol=symbol,
        price=price,
        currency=currency,
        ts=ts,
        exchange=meta.get("fullExchangeName", ""),
        source="yahoo",
    )


async def get_latest_prices(
    symbols: list[str], client: httpx.AsyncClient | None = None
) -> dict[str, Quote]:
    """One quote per symbol; raises UnknownSymbol if any symbol has no quote."""
    for symbol in symbols:
        _check_supported(symbol)

    owned = client is None
    client = client or httpx.AsyncClient()
    try:
        results = await asyncio.gather(*(_fetch_quote(client, s) for s in symbols))
    finally:
        if owned:
            await client.aclose()

    quotes = {s: q for s, q in zip(symbols, results) if q is not None}
    missing = [s for s in symbols if s not in quotes]
    if missing:
        raise UnknownSymbol(f"no quotes found for: {', '.join(missing)}")
    return quotes
