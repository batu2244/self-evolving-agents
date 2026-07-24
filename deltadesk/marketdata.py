"""Price collection for the realtime and historical analysts.

Yahoo's chart endpoint is keyless and covers the equities this desk follows, so
no credentials are needed. MOCK_MODE and the bundled CSV keep the pipeline
runnable with no network at all.
"""

from __future__ import annotations

import csv
import logging
import math
from datetime import date, datetime, timedelta, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import config

log = logging.getLogger("deltadesk.marketdata")

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (DeltaDesk research agent)"}


class MarketDataUnavailable(RuntimeError):
    """Raised when no source could supply usable data for a ticker."""


# --------------------------------------------------------------------------
# Deterministic mock series
# --------------------------------------------------------------------------


def _seed(ticker: str) -> int:
    return sum(ord(c) for c in ticker.upper())


def mock_bars(ticker: str, days: int) -> list[dict]:
    """A smooth, reproducible series: same ticker and length always yields the same bars."""
    seed = _seed(ticker)
    base = 100.0 + (seed % 50)
    drift = 0.0009 * (1 if seed % 2 == 0 else -1)
    bars: list[dict] = []
    start = date(2026, 1, 1)
    for i in range(days):
        # Deterministic wobble; no RNG so runs are byte-identical.
        wave = math.sin((seed + i) / 7.0) * 0.9
        close = base * (1 + drift) ** i + wave
        openp = close - wave * 0.4
        bars.append(
            {
                "bar_date": (start + timedelta(days=i)).isoformat(),
                "open": round(openp, 4),
                "high": round(max(openp, close) + 0.6, 4),
                "low": round(min(openp, close) - 0.6, 4),
                "close": round(close, 4),
                "volume": float(1_000_000 + ((seed * (i + 1)) % 400_000)),
            }
        )
    return bars


def mock_quote(ticker: str) -> dict:
    bars = mock_bars(ticker, 30)
    last, prev = bars[-1], bars[-2]
    avg_volume = sum(b["volume"] for b in bars[-20:]) / len(bars[-20:])
    return {
        "ticker": ticker.upper(),
        "price": last["close"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "previous_close": prev["close"],
        "volume": last["volume"],
        "average_volume": round(avg_volume, 2),
        "source": "mock",
    }


# --------------------------------------------------------------------------
# CSV fallback
# --------------------------------------------------------------------------


def load_csv_bars(path, ticker: str, days: int) -> list[dict]:
    """Read the bundled yfinance export.

    That file carries three header rows (`Price`/`Ticker`/`Date`) before the data,
    so rows are accepted only when the first field parses as a date.
    """
    bars: list[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 7:
                continue
            try:
                bar_date = datetime.strptime(row[0].strip(), "%Y-%m-%d").date().isoformat()
            except ValueError:
                continue  # header or blank row
            try:
                # Columns: Date, Adj Close, Close, High, Low, Open, Volume
                bars.append(
                    {
                        "bar_date": bar_date,
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "open": float(row[5]),
                        "volume": float(row[6]),
                    }
                )
            except (ValueError, IndexError):
                continue
    bars.sort(key=lambda b: b["bar_date"])
    return bars[-days:]


# --------------------------------------------------------------------------
# Yahoo chart API
# --------------------------------------------------------------------------


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(config.MAX_RETRIES),
    wait=wait_exponential(multiplier=0.6, max=6),
    reraise=True,
)
async def _chart(client: httpx.AsyncClient, ticker: str, rng: str, interval: str) -> dict:
    resp = await client.get(
        CHART_URL.format(symbol=ticker),
        params={"interval": interval, "range": rng},
        headers=HEADERS,
    )
    resp.raise_for_status()
    return resp.json()


def _chart_result(payload: dict, ticker: str) -> dict:
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        raise MarketDataUnavailable(f"{ticker}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise MarketDataUnavailable(f"{ticker}: empty chart result")
    return results[0]


async def fetch_bars(ticker: str, days: int) -> tuple[list[dict], str]:
    """Daily OHLCV. Returns (bars, source). Falls back to CSV, then raises."""
    if config.MOCK_MODE:
        return mock_bars(ticker, days), "mock"

    rng = f"{max(days + 15, 30)}d"
    try:
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS) as client:
            result = _chart_result(await _chart(client, ticker, rng, "1d"), ticker)
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        bars = []
        for i, ts in enumerate(stamps):
            close = _at(quote.get("close"), i)
            if close is None:
                continue  # holiday / halted session
            bars.append(
                {
                    "bar_date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                    "open": _at(quote.get("open"), i),
                    "high": _at(quote.get("high"), i),
                    "low": _at(quote.get("low"), i),
                    "close": close,
                    "volume": _at(quote.get("volume"), i) or 0.0,
                }
            )
        if bars:
            return bars[-days:], "yahoo"
        raise MarketDataUnavailable(f"{ticker}: no usable bars")
    except Exception as exc:  # noqa: BLE001 - fall through to the local file
        log.warning("Yahoo bars failed for %s (%s); trying local CSV", ticker, exc)

    if ticker.upper() == "GOOGL" and config.HISTORICAL_CSV.exists():
        bars = load_csv_bars(config.HISTORICAL_CSV, ticker, days)
        if bars:
            return bars, "csv"
    raise MarketDataUnavailable(f"{ticker}: no bar source available")


async def fetch_quote(ticker: str) -> dict:
    """Current quote with the fields the momentum signal needs."""
    if config.MOCK_MODE:
        return mock_quote(ticker)

    try:
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS) as client:
            result = _chart_result(await _chart(client, ticker, "1mo", "1d"), ticker)
        meta = result.get("meta") or {}
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        volumes = [v for v in (quote.get("volume") or []) if v]
        price = meta.get("regularMarketPrice")
        if price is None:
            closes = [c for c in (quote.get("close") or []) if c is not None]
            price = closes[-1] if closes else None
        if price is None:
            raise MarketDataUnavailable(f"{ticker}: no price in chart response")
        idx = len(stamps) - 1
        return {
            "ticker": ticker.upper(),
            "price": float(price),
            "open": meta.get("regularMarketDayOpen") or _at(quote.get("open"), idx),
            "high": meta.get("regularMarketDayHigh") or _at(quote.get("high"), idx),
            "low": meta.get("regularMarketDayLow") or _at(quote.get("low"), idx),
            "previous_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "volume": meta.get("regularMarketVolume") or (volumes[-1] if volumes else None),
            "average_volume": round(sum(volumes[-20:]) / len(volumes[-20:]), 2) if volumes else None,
            "source": "yahoo",
        }
    except MarketDataUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MarketDataUnavailable(f"{ticker}: quote fetch failed ({exc})") from exc


def _at(series, index: int):
    if not series or index >= len(series):
        return None
    value = series[index]
    return float(value) if value is not None else None
