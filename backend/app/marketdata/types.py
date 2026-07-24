"""Shared market-data contracts and errors."""

from dataclasses import dataclass


class MarketDataError(Exception):
    """Message is safe to surface to the API caller."""


class UnknownSymbol(MarketDataError):
    pass


class UnsupportedExchange(MarketDataError):
    pass


class UpstreamError(MarketDataError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    currency: str  # ISO code; LSE pence (GBp) is already normalized to GBP
    ts: str  # exchange trade timestamp, ISO-8601
    exchange: str  # e.g. "NasdaqGS", "LSE", "XETRA", "WSE"
    source: str  # "yahoo" | "alpaca-crypto"
