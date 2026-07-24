#!/usr/bin/env python3
"""Download OHLCV bars for a ticker from Yahoo Finance and write a clean CSV.

Yahoo caps intraday history: 1h bars go back about 730 days, 1m about 7. The
script asks for the widest window the interval allows and reports what it got.

    python fetch_stock_history.py                          # GOOGL, 1h, max window
    python fetch_stock_history.py --interval 1d --period 5y
    python fetch_stock_history.py --ticker MSFT --out msft_1h.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# Yahoo's documented ceiling per intraday interval.
MAX_PERIOD = {
    "1m": "7d",
    "2m": "60d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
    "1h": "730d",
    "90m": "60d",
}

MARKET_TZ = "America/New_York"


def fetch(ticker: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if df.empty:
        raise SystemExit(f"No data returned for {ticker} at interval={interval} period={period}")

    df = df[~df.index.duplicated(keep="last")].sort_index()

    out = pd.DataFrame(index=df.index)
    # UTC is the project's storage convention; ET is kept so market-hours
    # filtering does not require a timezone conversion downstream.
    out["timestamp_utc"] = df.index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    out["timestamp_et"] = df.index.tz_convert(MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S")
    out["ticker"] = ticker
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            out[col.lower()] = df[col]
    if "Adj Close" in df.columns:
        out["adj_close"] = df["Adj Close"]
    return out.reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Download OHLCV bars to CSV")
    p.add_argument("--ticker", default="GOOGL")
    p.add_argument("--interval", default="1h", help="1m, 5m, 15m, 30m, 1h, 1d, 1wk (default: 1h)")
    p.add_argument("--period", help="Look-back window (default: the max the interval allows)")
    p.add_argument("--out", help="Output CSV path (default: <ticker>_<interval>.csv in the project root)")
    args = p.parse_args(argv)

    period = args.period or MAX_PERIOD.get(args.interval, "max")
    df = fetch(args.ticker, args.interval, period)

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent / f"{args.ticker.lower()}_{args.interval}.csv"
    )
    df.to_csv(out, index=False)

    print(f"ticker   : {args.ticker}", file=sys.stderr)
    print(f"interval : {args.interval}  (period requested: {period})", file=sys.stderr)
    print(f"rows     : {len(df):,}", file=sys.stderr)
    print(f"range    : {df['timestamp_utc'].iloc[0]} -> {df['timestamp_utc'].iloc[-1]}", file=sys.stderr)
    print(f"written  : {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
