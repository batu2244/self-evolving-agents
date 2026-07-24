"""Market data API routes.

GET /api/marketdata/prices?symbols=AAPL,VOD.L,SIE.DE,PKO.WA — latest price per
symbol, keyless. Stocks on the major US / London / XETRA / Warsaw exchanges
(Yahoo suffix convention); slash-formatted crypto pairs for the live demo.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.marketdata import service
from app.marketdata.types import (
    MarketDataError,
    UnknownSymbol,
    UnsupportedExchange,
)

router = APIRouter(prefix="/api/marketdata", tags=["marketdata"])


class QuoteOut(BaseModel):
    symbol: str
    price: float
    currency: str
    ts: str
    exchange: str
    source: str


class PricesOut(BaseModel):
    prices: dict[str, QuoteOut]


def http_error(exc: MarketDataError) -> HTTPException:
    status = 422 if isinstance(exc, (UnknownSymbol, UnsupportedExchange)) else 502
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/prices", response_model=PricesOut)
async def prices(
    symbols: str = Query(min_length=1, description="comma-separated, e.g. AAPL,VOD.L,PKO.WA"),
) -> PricesOut:
    parsed = [s.strip() for s in symbols.split(",") if s.strip()]
    if not parsed:
        raise HTTPException(status_code=422, detail="no symbols given")
    try:
        quotes = await service.get_latest_prices(parsed)
    except MarketDataError as exc:
        raise http_error(exc) from exc
    return PricesOut(prices={s: QuoteOut(**vars(q)) for s, q in quotes.items()})
