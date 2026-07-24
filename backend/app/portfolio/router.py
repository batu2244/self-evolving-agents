"""Portfolio API routes — the desk's P&L scoreboard.

POST /api/portfolio        — allocate the budget (re-POST resets the book)
GET  /api/portfolio        — snapshot at last-known prices
POST /api/portfolio/fills  — record an executed buy/sell
GET  /api/portfolio/fills  — trade log
POST /api/portfolio/mark   — push fresh prices, get the re-graded snapshot
"""

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.marketdata import service as marketdata
from app.marketdata.router import http_error
from app.marketdata.types import MarketDataError
from app.portfolio.ledger import Ledger, LedgerError, Snapshot, UnknownSymbol
from app.portfolio.schemas import (
    FillOut,
    FillRequest,
    FillResponse,
    InitRequest,
    MarkRequest,
    SnapshotOut,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# In-memory single-desk ledger for the hackathon, same lifecycle as the
# onboarding envelope store.
_state: dict[str, Ledger] = {}


def _ledger() -> Ledger:
    if "ledger" not in _state:
        raise HTTPException(status_code=404, detail="Portfolio not initialized")
    return _state["ledger"]


def _snapshot_out(snap: Snapshot) -> SnapshotOut:
    return SnapshotOut.model_validate(asdict(snap))


@router.post("", status_code=201, response_model=SnapshotOut, response_model_by_alias=True)
async def init(body: InitRequest) -> SnapshotOut:
    _state["ledger"] = Ledger(
        initial_budget=body.budget_usd, tracker_symbol=body.tracker_symbol
    )
    return _snapshot_out(_state["ledger"].snapshot())


@router.get("", response_model=SnapshotOut, response_model_by_alias=True)
async def snapshot() -> SnapshotOut:
    try:
        return _snapshot_out(_ledger().snapshot())
    except UnknownSymbol as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/fills", status_code=201, response_model=FillResponse, response_model_by_alias=True
)
async def record_fill(body: FillRequest) -> FillResponse:
    price = body.price
    if price is None:  # market order — execute at the latest traded price
        try:
            quotes = await marketdata.get_latest_prices([body.symbol])
        except MarketDataError as exc:
            raise http_error(exc) from exc
        price = quotes[body.symbol].price
    try:
        fill = _ledger().execute(
            symbol=body.symbol,
            side=body.side,
            qty=body.qty,
            price=price,
            ts=datetime.now(timezone.utc).isoformat(),
        )
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FillResponse(
        fill=FillOut.model_validate(asdict(fill)),
        snapshot=_snapshot_out(_state["ledger"].snapshot()),
    )


@router.get("/fills", response_model=list[FillOut], response_model_by_alias=True)
async def fills() -> list[FillOut]:
    return [FillOut.model_validate(asdict(f)) for f in _ledger().fills]


@router.post("/mark", response_model=SnapshotOut, response_model_by_alias=True)
async def mark(body: MarkRequest) -> SnapshotOut:
    try:
        return _snapshot_out(_ledger().mark(body.prices))
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
