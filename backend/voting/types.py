"""Uniform vote schema (solution-design §3).

Every analyst emits the same shape — this uniformity is what makes
attribution (§5) and weight learning (§6, Loop 1) possible.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AnalystId(str, Enum):
    SENTIMENT = "sentiment"
    REALTIME = "realtime"
    HISTORICAL = "historical"


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class SizeClass(str, Enum):
    FULL = "full"
    HALF = "half"
    PROBE = "probe"


SIZE_CLASS_FACTOR = {SizeClass.FULL: 1.0, SizeClass.HALF: 0.5, SizeClass.PROBE: 0.25}


class Vote(BaseModel):
    analyst: AnalystId
    ticker: str
    direction: Direction
    signal: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    size_class: SizeClass = SizeClass.HALF
    rationale: str

    @model_validator(mode="after")
    def _sign_matches_direction(self) -> "Vote":
        if self.direction == Direction.BUY and self.signal <= 0:
            raise ValueError("buy vote requires positive signal")
        if self.direction == Direction.SELL and self.signal >= 0:
            raise ValueError("sell vote requires negative signal")
        if self.direction == Direction.HOLD and self.signal != 0:
            raise ValueError("hold vote requires signal == 0")
        return self


class Rebuttal(BaseModel):
    analyst: AnalystId
    ticker: str
    text: str
    # A challenged analyst may concede ground by revising confidence down
    # (or dig in and revise up); None means it stands pat.
    revised_confidence: Optional[float] = Field(default=None, ge=0, le=1)


class Challenge(BaseModel):
    dissenter: AnalystId
    ticker: str
    objection: str
    challenged: list[AnalystId]
    rebuttals: list[Rebuttal] = []


class TallyConfig(BaseModel):
    # Weighted conviction share the winning direction needs to trade at all.
    majority_threshold: float = 0.5
    # Dissenter confidence at/above this triggers a challenge round.
    challenge_threshold: float = 0.7
    # Size haircut when the vote is a majority but not unanimous.
    split_size_factor: float = 0.5


class TickerDecision(BaseModel):
    ticker: str
    direction: Direction
    # 0..1 — fraction of the max position size the risk envelope allows.
    size_factor: float
    # Weighted conviction share behind the winning direction.
    vote_share: float
    unanimous: bool
    votes: list[Vote]
    challenge: Optional[Challenge] = None


class DecisionMemo(BaseModel):
    cycle_id: str
    decisions: list[TickerDecision]
    weights: dict[AnalystId, float]
    narrative: str
