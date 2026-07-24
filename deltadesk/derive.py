"""Pure signal derivation: data in, direction/confidence out.

Deliberately free of I/O and database access so every rule here is directly
testable, and so a stored signal can be recomputed from its stored inputs.
"""

from __future__ import annotations

import statistics
from typing import Sequence

import config
from contracts import clamp


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def trend_slope(closes: Sequence[float]) -> float:
    """Least-squares slope of closes, expressed as percent-of-price per day."""
    n = len(closes)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean, y_mean = _mean(xs), _mean(closes)
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, closes)) / denom
    return (slope / y_mean) * 100 if y_mean else 0.0


def moving_average(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window or window <= 0:
        return None
    return _mean(closes[-window:])


def daily_returns(closes: Sequence[float]) -> list[float]:
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1]
    ]


def mean_reversion_z(closes: Sequence[float]) -> float:
    """Z-score of the most recent daily return against the recent return distribution."""
    rets = daily_returns(closes)
    if len(rets) < 5:
        return 0.0
    body, last = rets[:-1], rets[-1]
    sigma = statistics.pstdev(body)
    if sigma == 0:
        return 0.0
    return (last - _mean(body)) / sigma


def derive_trend(closes: Sequence[float]) -> dict:
    """Historical analyst: trend slope + MA relationship + mean-reversion flag."""
    if len(closes) < 2:
        return {
            "direction": 0.0,
            "confidence": 0.0,
            "slope_pct_per_day": 0.0,
            "ma_short": None,
            "ma_long": None,
            "ma_relationship": "unknown",
            "mean_reversion_z": 0.0,
            "mean_reversion_flag": False,
            "rationale": "Not enough history to derive a trend.",
        }

    slope = trend_slope(closes)
    slope_component = clamp(slope / config.SLOPE_FULL_SCALE_PCT)

    ma_short = moving_average(closes, config.MA_SHORT)
    ma_long = moving_average(closes, config.MA_LONG)
    if ma_short is None or ma_long is None or not ma_long:
        ma_component, relationship = 0.0, "unknown"
    else:
        gap = (ma_short - ma_long) / ma_long
        # A 2% separation between the averages is treated as a full-strength reading.
        ma_component = clamp(gap / 0.02)
        relationship = "short_above_long" if ma_short > ma_long else (
            "short_below_long" if ma_short < ma_long else "equal"
        )

    z = mean_reversion_z(closes)
    stretched = abs(z) >= config.MEAN_REVERSION_Z

    # Trend is the core read; the MA relationship confirms or tempers it.
    direction = clamp(0.6 * slope_component + 0.4 * ma_component)

    # A stretched last move argues the next one gives some back, so fade the
    # signal rather than flipping it -- an extended trend is still a trend.
    if stretched:
        direction = clamp(direction - 0.35 * clamp(z / 4.0))

    agreement = 1.0 - min(1.0, abs(slope_component - ma_component))
    confidence = clamp(
        0.25 + 0.45 * agreement + 0.30 * min(1.0, abs(direction)), 0.0, 1.0
    )
    if relationship == "unknown":
        confidence *= 0.6  # not enough history for the MA cross-check
    if stretched:
        confidence *= 0.85

    bits = [f"slope {slope:+.3f}%/day over {len(closes)} closes"]
    if relationship != "unknown":
        bits.append(f"MA{config.MA_SHORT} {'>' if ma_component > 0 else '<'} MA{config.MA_LONG}")
    if stretched:
        bits.append(f"last move stretched (z={z:+.2f}), signal faded")
    return {
        "direction": round(direction, 4),
        "confidence": round(clamp(confidence, 0.0, 1.0), 4),
        "slope_pct_per_day": round(slope, 4),
        "ma_short": ma_short,
        "ma_long": ma_long,
        "ma_relationship": relationship,
        "mean_reversion_z": round(z, 4),
        "mean_reversion_flag": stretched,
        "rationale": "; ".join(bits) + ".",
    }


def derive_momentum(quote: dict) -> dict:
    """Realtime analyst: price vs open / previous close, plus a volume anomaly check."""
    price = quote.get("price")
    if not price:
        return {
            "direction": 0.0,
            "confidence": 0.0,
            "vs_open_pct": 0.0,
            "vs_prev_close_pct": 0.0,
            "volume_ratio": None,
            "volume_anomaly": False,
            "rationale": "No price available.",
        }

    open_price = quote.get("open") or 0.0
    prev_close = quote.get("previous_close") or 0.0
    vs_open = ((price - open_price) / open_price * 100) if open_price else 0.0
    vs_prev = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

    parts = [p for p in ((vs_open, bool(open_price)), (vs_prev, bool(prev_close))) if p[1]]
    if not parts:
        return {
            "direction": 0.0,
            "confidence": 0.0,
            "vs_open_pct": 0.0,
            "vs_prev_close_pct": 0.0,
            "volume_ratio": None,
            "volume_anomaly": False,
            "rationale": "Neither open nor previous close available; no momentum reading.",
        }

    move = _mean([p[0] for p in parts])
    direction = clamp(move / config.MOMENTUM_FULL_SCALE_PCT)

    volume, avg_volume = quote.get("volume"), quote.get("average_volume")
    ratio = (volume / avg_volume) if (volume and avg_volume) else None
    anomaly = bool(ratio and ratio >= config.VOLUME_ANOMALY_RATIO)

    confidence = clamp(0.2 + 0.5 * min(1.0, abs(direction)), 0.0, 1.0)
    if anomaly:
        # Heavy volume behind a move makes it likelier to be real, not noise.
        confidence = clamp(confidence + 0.2, 0.0, 1.0)
    if len(parts) == 1:
        confidence *= 0.8  # only one reference point

    bits = []
    if open_price:
        bits.append(f"{vs_open:+.2f}% vs open")
    if prev_close:
        bits.append(f"{vs_prev:+.2f}% vs previous close")
    if ratio is not None:
        bits.append(f"volume {ratio:.2f}x average" + (" (anomaly)" if anomaly else ""))
    return {
        "direction": round(direction, 4),
        "confidence": round(confidence, 4),
        "vs_open_pct": round(vs_open, 4),
        "vs_prev_close_pct": round(vs_prev, 4),
        "volume_ratio": round(ratio, 4) if ratio is not None else None,
        "volume_anomaly": anomaly,
        "rationale": "; ".join(bits) + ".",
    }


def derive_news(decision: dict, signal_score: dict) -> dict:
    """News analyst: map the existing news agent's own output onto the contract.

    The news agent already produces a weighted score in -1..+1 and an explicit
    action, so this maps rather than recomputes.
    """
    action = str(decision.get("action", "HOLD")).upper()
    weighted = signal_score.get("weighted_score")
    direction = float(weighted) if weighted is not None else 0.0
    if direction == 0.0 and action in ("BUY", "SELL"):
        # Narrative call with no mechanical score behind it; take a soft version.
        direction = 0.3 if action == "BUY" else -0.3
    direction = clamp(direction)

    conviction = float(decision.get("conviction", 0.0) or 0.0)
    scored = int(signal_score.get("articles_scored", 0) or 0)
    if scored == 0:
        return {
            "direction": 0.0,
            "confidence": 0.0,
            "articles_scored": 0,
            "rationale": "No significant articles in the window.",
        }
    # More scored articles means a steadier read; saturates around ten.
    coverage = min(1.0, scored / 10.0)
    confidence = clamp(0.5 * conviction + 0.5 * coverage, 0.0, 1.0)
    return {
        "direction": round(direction, 4),
        "confidence": round(confidence, 4),
        "articles_scored": scored,
        "rationale": (
            f"News desk called {action} at {conviction:.0%} conviction across "
            f"{scored} scored articles (weighted score {direction:+.3f})."
        ),
    }
