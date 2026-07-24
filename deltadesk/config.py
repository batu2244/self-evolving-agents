"""Static configuration for the DeltaDesk analyst and forecaster agents.

Weights are STATIC by design this phase: nothing here is learned or tuned at
runtime. Changing desk behaviour means editing this file (or the matching env
var), which keeps every forecast reproducible from config + stored inputs.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent

# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

# Actian is the intended store; SQLite is the local development fallback.
DATABASE_URL = os.getenv("ACTIAN_DATABASE_URL") or f"sqlite:///{PROJECT_ROOT / 'deltadesk.db'}"

# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------

DEFAULT_SYMBOLS: list[str] = [
    s.strip().upper() for s in os.getenv("DEFAULT_SYMBOLS", "GOOGL").split(",") if s.strip()
]

# Deterministic, offline-safe mode for tests and demos.
MOCK_MODE = os.getenv("MOCK_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

# --------------------------------------------------------------------------
# Signal sources and weights
# --------------------------------------------------------------------------

SIGNAL_SOURCES: tuple[str, ...] = ("news", "historical", "realtime")

# Must cover exactly SIGNAL_SOURCES; the forecaster renormalizes over whichever
# sources actually reported, so these are relative, not absolute.
SIGNAL_WEIGHTS: dict[str, float] = {
    "news": 0.40,
    "historical": 0.35,
    "realtime": 0.25,
}

# |score| above this is UP/DOWN; anything inside the band is FLAT.
DIRECTION_THRESHOLD = float(os.getenv("DIRECTION_THRESHOLD", "0.15"))

# --------------------------------------------------------------------------
# Historical analyst
# --------------------------------------------------------------------------

HISTORICAL_DAYS = int(os.getenv("HISTORICAL_DAYS", "90"))
MA_SHORT = int(os.getenv("MA_SHORT", "10"))
MA_LONG = int(os.getenv("MA_LONG", "30"))
# Daily-return z-score beyond this flags a stretched move that may mean-revert.
MEAN_REVERSION_Z = float(os.getenv("MEAN_REVERSION_Z", "2.0"))
# Slope is normalized against this daily-drift percentage before clamping to -1..+1.
SLOPE_FULL_SCALE_PCT = float(os.getenv("SLOPE_FULL_SCALE_PCT", "0.4"))

# Local OHLCV fallback, used when the network is unavailable.
HISTORICAL_CSV = REPO_ROOT / "google_stock_1year.csv"

# --------------------------------------------------------------------------
# Realtime analyst
# --------------------------------------------------------------------------

# Intraday move (percent) treated as a full-strength momentum reading.
MOMENTUM_FULL_SCALE_PCT = float(os.getenv("MOMENTUM_FULL_SCALE_PCT", "2.0"))
# Volume multiple over the recent average that counts as a genuine anomaly.
VOLUME_ANOMALY_RATIO = float(os.getenv("VOLUME_ANOMALY_RATIO", "1.5"))

# --------------------------------------------------------------------------
# News analyst
# --------------------------------------------------------------------------

NEWS_AGENT_DIR = REPO_ROOT / "google-news-agent"
NEWS_AGENT_SCRIPT = NEWS_AGENT_DIR / "google_news_agent.py"
NEWS_AGENT_SAMPLE = NEWS_AGENT_DIR / "sample_output.json"
NEWS_AGENT_TIMEOUT = int(os.getenv("NEWS_AGENT_TIMEOUT", "420"))
# The news agent covers Alphabet only; it contributes nothing for other tickers.
NEWS_AGENT_TICKER = "GOOGL"

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "15"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))


def validate() -> None:
    """Fail fast on a config that would silently distort the tally."""
    missing = set(SIGNAL_SOURCES) - set(SIGNAL_WEIGHTS)
    if missing:
        raise ValueError(f"SIGNAL_WEIGHTS is missing sources: {sorted(missing)}")
    extra = set(SIGNAL_WEIGHTS) - set(SIGNAL_SOURCES)
    if extra:
        raise ValueError(f"SIGNAL_WEIGHTS has unknown sources: {sorted(extra)}")
    if any(w < 0 for w in SIGNAL_WEIGHTS.values()):
        raise ValueError("SIGNAL_WEIGHTS must be non-negative")
    if sum(SIGNAL_WEIGHTS.values()) <= 0:
        raise ValueError("SIGNAL_WEIGHTS must sum to a positive number")
    if not 0 <= DIRECTION_THRESHOLD < 1:
        raise ValueError("DIRECTION_THRESHOLD must be in [0, 1)")


validate()
