"""Onboarding data contracts — the risk envelope and the proposed universe.

These mirror `frontend/src/modules/onboarding/types.ts`; change both together.
"""

from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["conservative", "balanced", "aggressive"]
Market = Literal["us", "eu", "pl", "crypto"]
VolBand = Literal["low", "medium", "high"]


class RiskEnvelope(BaseModel):
    risk_level: RiskLevel = Field(alias="riskLevel")
    # % per quarter vs the tracker, e.g. 2.0 = "beat the tracker by 2%/quarter"
    target_return_pct: float = Field(alias="targetReturnPct", ge=0.25, le=25)
    capital_usd: float = Field(alias="capitalUsd", ge=1_000, le=10_000_000)
    market: Market

    model_config = {"populate_by_name": True}


class UniverseAsset(BaseModel):
    symbol: str
    name: str
    sector: str
    vol_band: VolBand = Field(alias="volBand")
    # indicative paper-desk pricing — synthetic but deterministic per symbol
    last_price: float = Field(default=0.0, alias="lastPrice")
    change_30d_pct: float = Field(default=0.0, alias="change30dPct")
    history: list[float] = Field(default_factory=list)  # 30 daily closes, oldest first

    model_config = {"populate_by_name": True}


class RiskRules(BaseModel):
    max_position_pct: float = Field(alias="maxPositionPct")
    max_daily_drawdown_pct: float = Field(alias="maxDailyDrawdownPct")
    stop_rule: str = Field(alias="stopRule")

    model_config = {"populate_by_name": True}


class UniverseProposal(BaseModel):
    tracker_symbol: str = Field(alias="trackerSymbol")
    tracker_name: str = Field(alias="trackerName")
    currency: Literal["USD", "EUR", "PLN"]
    trading_window: str = Field(alias="tradingWindow")
    universe: list[UniverseAsset]
    rules: RiskRules

    model_config = {"populate_by_name": True}
