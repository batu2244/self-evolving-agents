"""Portfolio data contracts.

These mirror `frontend/src/modules/portfolio/types.ts`; change both together.
"""

from pydantic import BaseModel, Field

from app.portfolio.ledger import Side


class InitRequest(BaseModel):
    budget_usd: float = Field(alias="budgetUsd", gt=0)
    tracker_symbol: str | None = Field(alias="trackerSymbol", default=None)

    model_config = {"populate_by_name": True}


class FillRequest(BaseModel):
    symbol: str = Field(min_length=1)
    side: Side
    qty: float = Field(gt=0)
    # omit for a market order — the fill executes at the latest traded price
    price: float | None = Field(gt=0, default=None)

    model_config = {"populate_by_name": True}


class MarkRequest(BaseModel):
    # symbol -> latest price; include the tracker symbol to grade the delta
    prices: dict[str, float]


class FillOut(BaseModel):
    seq: int
    ts: str
    symbol: str
    side: Side
    qty: float
    price: float
    notional: float
    realized_pnl: float = Field(serialization_alias="realizedPnl")
    cash_after: float = Field(serialization_alias="cashAfter")


class PositionOut(BaseModel):
    symbol: str
    qty: float
    avg_cost: float = Field(serialization_alias="avgCost")
    last_price: float = Field(serialization_alias="lastPrice")
    market_value: float = Field(serialization_alias="marketValue")
    unrealized_pnl: float = Field(serialization_alias="unrealizedPnl")


class SnapshotOut(BaseModel):
    initial_budget: float = Field(serialization_alias="initialBudget")
    cash: float
    equity: float
    realized_pnl: float = Field(serialization_alias="realizedPnl")
    unrealized_pnl: float = Field(serialization_alias="unrealizedPnl")
    total_pnl: float = Field(serialization_alias="totalPnl")
    return_pct: float = Field(serialization_alias="returnPct")
    positions: list[PositionOut]
    tracker_symbol: str | None = Field(serialization_alias="trackerSymbol")
    tracker_equity: float | None = Field(serialization_alias="trackerEquity")
    delta_usd: float | None = Field(serialization_alias="deltaUsd")
    delta_pct: float | None = Field(serialization_alias="deltaPct")


class FillResponse(BaseModel):
    fill: FillOut
    snapshot: SnapshotOut
