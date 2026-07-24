"""Configuration for the DeltaDesk analyst, forecaster, and learning loop.

Values begin from reproducible defaults. The daily learner may activate a
versioned, bounded policy from the database before a run; every resulting
forecast still records the exact values that produced it.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent
# Reuse credentials already configured for the original Google news agent.
load_dotenv(REPO_ROOT / "google-news-agent" / ".env", override=False)

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
# Equation strategies
# --------------------------------------------------------------------------
#
# These are named deterministic formulas each agent is allowed to use. Defaults
# preserve the original behaviour; overrides make equation experiments
# attributable without editing code.

EQUATION_CHOICES: dict[str, tuple[str, ...]] = {
    "news": ("weighted_score", "action_conviction_blend", "article_count_fade"),
    "historical": ("trend_blend", "slope_only", "ma_cross"),
    "realtime": ("balanced_momentum", "previous_close_only", "open_weighted"),
    "forecaster": ("confidence_weighted", "direction_only", "consensus"),
}

NEWS_EQUATION = os.getenv("NEWS_EQUATION", "weighted_score")
HISTORICAL_EQUATION = os.getenv("HISTORICAL_EQUATION", "trend_blend")
REALTIME_EQUATION = os.getenv("REALTIME_EQUATION", "balanced_momentum")
FORECAST_EQUATION = os.getenv("FORECAST_EQUATION", "confidence_weighted")

EQUATION_BY_AGENT: dict[str, str] = {
    "news": NEWS_EQUATION,
    "historical": HISTORICAL_EQUATION,
    "realtime": REALTIME_EQUATION,
    "forecaster": FORECAST_EQUATION,
}

# Regime boundaries used by automatic equation selection. These live on the
# tuning surface below so outcome evaluation can improve when modes switch.
NEWS_THIN_COVERAGE_MAX = int(os.getenv("NEWS_THIN_COVERAGE_MAX", "2"))
NEWS_ACTION_CONVICTION_MIN = float(os.getenv("NEWS_ACTION_CONVICTION_MIN", "0.65"))
NEWS_WEAK_SCORE_ABS = float(os.getenv("NEWS_WEAK_SCORE_ABS", "0.15"))
FORECAST_LOW_CONFIDENCE = float(os.getenv("FORECAST_LOW_CONFIDENCE", "0.35"))

# An agent can either select an equation from the current data ("auto") or use
# the configured equation exactly ("configured"). Auto is the production
# default; configured is the reproducible experiment/replay mode.
ANALYSIS_POLICIES: tuple[str, ...] = ("auto", "configured")
ANALYSIS_POLICY_BY_AGENT: dict[str, str] = {
    agent: os.getenv(f"{agent.upper()}_ANALYSIS_POLICY", "auto").strip().lower()
    for agent in EQUATION_CHOICES
}

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

# --------------------------------------------------------------------------
# Gemini reasoning
# --------------------------------------------------------------------------

GEMINI_THINKING_MODEL = os.getenv("GEMINI_THINKING_MODEL", "gemini-3.6-flash").strip()
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "high").strip().lower()
GEMINI_DECISION_RETRIES = int(os.getenv("GEMINI_DECISION_RETRIES", "3"))
GEMINI_REASONING_IN_MOCK_MODE = (
    os.getenv("GEMINI_REASONING_IN_MOCK_MODE", "").strip().lower()
    in {"1", "true", "yes", "on"}
)

# --------------------------------------------------------------------------
# Daily performance learning
# --------------------------------------------------------------------------

LEARNING_ENABLED = (
    os.getenv("LEARNING_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
LEARNING_LOOKBACK_DAYS = int(os.getenv("LEARNING_LOOKBACK_DAYS", "60"))
LEARNING_MARKET_DATA_DAYS = int(os.getenv("LEARNING_MARKET_DATA_DAYS", "120"))
LEARNING_MIN_OBSERVATIONS = int(os.getenv("LEARNING_MIN_OBSERVATIONS", "3"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.15"))
LEARNING_MAX_WEIGHT_STEP = float(os.getenv("LEARNING_MAX_WEIGHT_STEP", "0.05"))
LEARNING_MIN_SOURCE_WEIGHT = float(os.getenv("LEARNING_MIN_SOURCE_WEIGHT", "0.10"))
PERFORMANCE_FULL_SCALE_PCT = float(os.getenv("PERFORMANCE_FULL_SCALE_PCT", "2.0"))
HOLD_BAND_PCT = float(os.getenv("HOLD_BAND_PCT", "0.5"))


# --------------------------------------------------------------------------
# Tuning surface
# --------------------------------------------------------------------------
#
# Every knob that changes desk behaviour is declared here with its bounds, so a
# future tuning agent can adjust behaviour without editing code -- and cannot
# push a value somewhere nonsensical. Weights stay STATIC within a run: overrides
# are applied once, before any agent executes, and the resulting values are
# stamped onto each forecast so an outcome can be attributed to the exact
# settings that produced it.
#
# This is the seam for self-improvement, not self-improvement itself: nothing
# here changes a value on its own.

TUNABLES: dict[str, tuple[float, float]] = {
    "SIGNAL_WEIGHTS.news": (0.0, 1.0),
    "SIGNAL_WEIGHTS.historical": (0.0, 1.0),
    "SIGNAL_WEIGHTS.realtime": (0.0, 1.0),
    "DIRECTION_THRESHOLD": (0.0, 0.99),
    "MA_SHORT": (2, 100),
    "MA_LONG": (3, 400),
    "MEAN_REVERSION_Z": (0.5, 6.0),
    "SLOPE_FULL_SCALE_PCT": (0.01, 10.0),
    "MOMENTUM_FULL_SCALE_PCT": (0.1, 20.0),
    "VOLUME_ANOMALY_RATIO": (1.0, 10.0),
    "HISTORICAL_DAYS": (5, 3650),
    "NEWS_THIN_COVERAGE_MAX": (0, 20),
    "NEWS_ACTION_CONVICTION_MIN": (0.0, 1.0),
    "NEWS_WEAK_SCORE_ABS": (0.0, 1.0),
    "FORECAST_LOW_CONFIDENCE": (0.0, 1.0),
}


def snapshot() -> dict[str, float]:
    """Current value of every tunable -- recorded alongside each forecast."""
    out: dict[str, float] = {}
    for key in TUNABLES:
        if "." in key:
            container, field = key.split(".", 1)
            out[key] = globals()[container][field]
        else:
            out[key] = globals()[key]
    return out


def apply_overrides(overrides: dict[str, float]) -> dict[str, float]:
    """Set tunables before any agent runs. Returns the values actually applied.

    Raises on an unknown key or an out-of-bounds value rather than silently
    clamping -- a tuner that asks for something impossible has a bug worth seeing.
    """
    applied: dict[str, float] = {}
    for key, raw in overrides.items():
        if key not in TUNABLES:
            raise KeyError(f"{key!r} is not tunable; known keys: {sorted(TUNABLES)}")
        low, high = TUNABLES[key]
        value = float(raw)
        if not low <= value <= high:
            raise ValueError(f"{key}={value} is outside its allowed range [{low}, {high}]")
        if key.startswith(("MA_", "HISTORICAL_DAYS")) or key == "NEWS_THIN_COVERAGE_MAX":
            value = int(value)
        if "." in key:
            container, field = key.split(".", 1)
            globals()[container][field] = value
        else:
            globals()[key] = value
        applied[key] = value
    validate()
    return applied


def load_overrides_file(path) -> dict[str, float]:
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("tuning file must contain a JSON object of key -> number")
    return data


def equation_snapshot(agent: str | None = None) -> dict[str, str]:
    """Equation strategy names currently selected, optionally for one agent."""
    if agent is not None:
        return {agent: EQUATION_BY_AGENT[agent]}
    return dict(EQUATION_BY_AGENT)


def apply_equation_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Select named equation strategies before agents run."""
    applied: dict[str, str] = {}
    for raw_agent, raw_name in overrides.items():
        agent = raw_agent.strip().lower()
        name = raw_name.strip()
        if agent not in EQUATION_CHOICES:
            raise KeyError(f"unknown equation agent {agent!r}; known agents: {sorted(EQUATION_CHOICES)}")
        if name not in EQUATION_CHOICES[agent]:
            raise ValueError(
                f"{name!r} is not a valid {agent} equation; "
                f"known choices: {', '.join(EQUATION_CHOICES[agent])}"
            )
        EQUATION_BY_AGENT[agent] = name
        if agent == "news":
            globals()["NEWS_EQUATION"] = name
        elif agent == "historical":
            globals()["HISTORICAL_EQUATION"] = name
        elif agent == "realtime":
            globals()["REALTIME_EQUATION"] = name
        elif agent == "forecaster":
            globals()["FORECAST_EQUATION"] = name
        applied[agent] = name
    validate()
    return applied


def analysis_policy_snapshot(agent: str | None = None) -> dict[str, str]:
    """Analysis-mode selection policies, optionally for one agent."""
    if agent is not None:
        return {agent: ANALYSIS_POLICY_BY_AGENT[agent]}
    return dict(ANALYSIS_POLICY_BY_AGENT)


def apply_analysis_policy_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Select automatic or configured equation selection for each agent."""
    applied: dict[str, str] = {}
    for raw_agent, raw_policy in overrides.items():
        agent = raw_agent.strip().lower()
        policy = raw_policy.strip().lower()
        if agent not in EQUATION_CHOICES:
            raise KeyError(f"unknown analysis agent {agent!r}; known agents: {sorted(EQUATION_CHOICES)}")
        if policy not in ANALYSIS_POLICIES:
            raise ValueError(
                f"{policy!r} is not a valid analysis policy; "
                f"known policies: {', '.join(ANALYSIS_POLICIES)}"
            )
        ANALYSIS_POLICY_BY_AGENT[agent] = policy
        applied[agent] = policy
    validate()
    return applied


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
    if GEMINI_THINKING_LEVEL not in {"low", "medium", "high"}:
        raise ValueError("GEMINI_THINKING_LEVEL must be low, medium, or high")
    if LEARNING_LOOKBACK_DAYS < 1:
        raise ValueError("LEARNING_LOOKBACK_DAYS must be positive")
    if LEARNING_MIN_OBSERVATIONS < 1:
        raise ValueError("LEARNING_MIN_OBSERVATIONS must be positive")
    if not 0 < LEARNING_RATE <= 1:
        raise ValueError("LEARNING_RATE must be in (0, 1]")
    if not 0 < LEARNING_MAX_WEIGHT_STEP <= 1:
        raise ValueError("LEARNING_MAX_WEIGHT_STEP must be in (0, 1]")
    if not 0 <= LEARNING_MIN_SOURCE_WEIGHT < 1:
        raise ValueError("LEARNING_MIN_SOURCE_WEIGHT must be in [0, 1)")
    if PERFORMANCE_FULL_SCALE_PCT <= 0 or HOLD_BAND_PCT <= 0:
        raise ValueError("performance scoring percentages must be positive")
    for agent, name in EQUATION_BY_AGENT.items():
        if name not in EQUATION_CHOICES[agent]:
            raise ValueError(
                f"{agent} equation {name!r} is invalid; "
                f"known choices: {', '.join(EQUATION_CHOICES[agent])}"
            )
    for agent, policy in ANALYSIS_POLICY_BY_AGENT.items():
        if policy not in ANALYSIS_POLICIES:
            raise ValueError(
                f"{agent} analysis policy {policy!r} is invalid; "
                f"known policies: {', '.join(ANALYSIS_POLICIES)}"
            )


validate()
