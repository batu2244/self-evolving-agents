"""The uniform signal contract every analyst emits.

One shape for every source means the forecaster never needs to know how a signal
was derived — only how strongly it points, how much to trust it, and what it read
to get there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def current_cycle(now: datetime | None = None) -> str:
    """Cycle bucket used for dedup: one slot per ticker per source per hour."""
    return (now or utcnow()).strftime("%Y-%m-%dT%HZ")


class Provenance(BaseModel):
    """What a signal was computed from, so any number can be traced back."""

    source_run_id: str | None = Field(
        default=None, description="agent_runs.run_id of the collection run that produced the inputs"
    )
    inputs_used: list[str] = Field(
        default_factory=list, description="Human-readable identifiers of the records read"
    )
    degraded: bool = Field(
        default=False, description="True when the signal rests on incomplete inputs"
    )
    notes: str = ""


class Signal(BaseModel):
    """A single analyst's directional read on one ticker."""

    ticker: str
    source: str
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    direction: float = Field(ge=-1.0, le=1.0, description="-1 fully bearish .. +1 fully bullish")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    provenance: Provenance = Field(default_factory=Provenance)
    deterministic: bool = True
    cycle: str = Field(default_factory=current_cycle)
    created_at: datetime = Field(default_factory=utcnow)
    prompt_snapshot: dict[str, str] = Field(
        default_factory=dict,
        description="Agent prompt identity and text in force for this signal",
    )
    equation_snapshot: dict[str, str] = Field(
        default_factory=dict,
        description="Named equation strategy in force for this signal",
    )
    agent_trace: dict = Field(
        default_factory=dict,
        description="Structured trace of candidate reads and selection rationale",
    )
    model_snapshot: dict = Field(
        default_factory=dict,
        description="Decision provider, Gemini model, and thinking level used",
    )
    learning_snapshot: dict = Field(
        default_factory=dict,
        description="Versioned performance policy available when this signal was produced",
    )

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class Contribution(BaseModel):
    """One analyst's share of a forecast, in the forecast's own units."""

    source: str
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    direction: float
    confidence: float
    weight: float = Field(description="Configured weight, renormalized over reporting sources")
    contribution: float = Field(description="Signed push on the final score")
    rationale: str


class Forecast(BaseModel):
    """The forecaster's tally across all reporting analysts."""

    ticker: str
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    direction: str = Field(description="UP, DOWN, or FLAT")
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    per_agent_contributions: list[Contribution] = Field(default_factory=list)
    rationale: str
    provenance: Provenance = Field(default_factory=Provenance)
    deterministic: bool = True
    cycle: str = Field(default_factory=current_cycle)
    created_at: datetime = Field(default_factory=utcnow)
    mode: str = "paper-trading-research"
    config_snapshot: dict[str, float] = Field(
        default_factory=dict,
        description="Tunable values in force for this forecast, so an outcome can later "
                    "be attributed to the exact settings that produced it",
    )
    prompt_snapshot: dict[str, str] = Field(
        default_factory=dict,
        description="Forecaster prompt identity and text in force for this forecast",
    )
    equation_snapshot: dict[str, str] = Field(
        default_factory=dict,
        description="Named equation strategy in force for this forecast",
    )
    agent_trace: dict = Field(
        default_factory=dict,
        description="Structured trace of candidate forecasts and selection rationale",
    )
    model_snapshot: dict = Field(
        default_factory=dict,
        description="Decision provider, Gemini model, and thinking level used",
    )
    learning_snapshot: dict = Field(
        default_factory=dict,
        description="Versioned performance policy available when this forecast was produced",
    )


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def action_for_direction(direction: float, threshold: float) -> str:
    if direction > threshold:
        return "BUY"
    if direction < -threshold:
        return "SELL"
    return "HOLD"
