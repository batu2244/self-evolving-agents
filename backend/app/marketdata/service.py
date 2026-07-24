"""Symbol routing: slash-formatted symbols are crypto (Alpaca), everything
else is a stock on one of the desk's supported exchanges (Yahoo)."""

from app.marketdata import alpaca, yahoo
from app.marketdata.types import Quote


async def get_latest_prices(symbols: list[str]) -> dict[str, Quote]:
    crypto = [s for s in symbols if "/" in s]
    stocks = [s for s in symbols if "/" not in s]

    quotes: dict[str, Quote] = {}
    if stocks:
        quotes |= await yahoo.get_latest_prices(stocks)
    if crypto:
        quotes |= await alpaca.get_latest_prices(crypto)
    return quotes
