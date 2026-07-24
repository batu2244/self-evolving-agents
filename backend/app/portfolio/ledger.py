"""Paper-trading P&L ledger — the desk's own answer to "how much money did we make?"

Pure unit: no FastAPI, no I/O, no clock. The PM agent reports each executed
fill here; anyone with fresh prices can mark the book. Everything is graded
against the initial budget, and (optionally) against buy-and-holding the
tracker with that same budget — the decision delta of solution-design.md §5.

Positions are signed floats (crypto trades fractionally). The realize/average
math already handles negative quantities, so enabling short selling later is
just `allow_short=True` — until then any fill that would take a position below
zero is rejected.
"""

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class LedgerError(Exception):
    """Rejected fill; message is safe to surface to the API caller."""


class InsufficientCash(LedgerError):
    pass


class ShortNotAllowed(LedgerError):
    pass


class UnknownSymbol(LedgerError):
    pass


@dataclass(frozen=True)
class Fill:
    seq: int
    ts: str  # ISO-8601, supplied by the caller (live clock or replay harness)
    symbol: str
    side: Side
    qty: float
    price: float
    notional: float
    realized_pnl: float  # realized by this fill alone
    cash_after: float


@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: float
    avg_cost: float
    last_price: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True)
class Snapshot:
    initial_budget: float
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float  # equity - initial_budget
    return_pct: float  # total_pnl / initial_budget * 100
    positions: list[PositionView]
    # vs buy-and-holding the tracker with the same budget; None until the
    # tracker has been marked at least once (that first mark is the baseline).
    tracker_symbol: str | None
    tracker_equity: float | None
    delta_usd: float | None
    delta_pct: float | None


@dataclass
class Ledger:
    initial_budget: float
    tracker_symbol: str | None = None
    allow_short: bool = False

    cash: float = field(init=False)
    realized_pnl: float = field(init=False, default=0.0)
    positions: dict[str, Position] = field(init=False, default_factory=dict)
    last_prices: dict[str, float] = field(init=False, default_factory=dict)
    fills: list[Fill] = field(init=False, default_factory=list)
    _tracker_baseline: float | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.initial_budget <= 0:
            raise LedgerError("initial budget must be positive")
        self.cash = self.initial_budget

    def execute(self, symbol: str, side: Side, qty: float, price: float, ts: str) -> Fill:
        if qty <= 0:
            raise LedgerError("qty must be positive")
        if price <= 0:
            raise LedgerError("price must be positive")

        pos = self.positions.setdefault(symbol, Position())
        signed = qty if side is Side.BUY else -qty
        new_qty = pos.qty + signed

        if new_qty < 0 and not self.allow_short:
            raise ShortNotAllowed(
                f"sell of {qty} {symbol} exceeds held {pos.qty}; short selling is not enabled"
            )
        if side is Side.BUY and qty * price > self.cash + 1e-9:
            raise InsufficientCash(
                f"buy needs ${qty * price:,.2f} but only ${self.cash:,.2f} cash available"
            )

        realized = 0.0
        if pos.qty * signed < 0:  # fill reduces (or flips) existing exposure
            closed = min(abs(signed), abs(pos.qty))
            # long: profit when price > avg_cost; short: profit when price < avg_cost
            realized = closed * (price - pos.avg_cost) * (1 if pos.qty > 0 else -1)
            self.realized_pnl += realized
        if new_qty != 0 and pos.qty * new_qty <= 0:
            # opened fresh or flipped through zero — cost basis restarts here
            pos.avg_cost = price
        elif abs(new_qty) > abs(pos.qty):  # added to existing exposure — re-average
            pos.avg_cost = (abs(pos.qty) * pos.avg_cost + qty * price) / abs(new_qty)

        pos.qty = new_qty
        if new_qty == 0:
            del self.positions[symbol]
        self.cash -= signed * price
        self.last_prices[symbol] = price

        fill = Fill(
            seq=len(self.fills) + 1,
            ts=ts,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            notional=qty * price,
            realized_pnl=realized,
            cash_after=self.cash,
        )
        self.fills.append(fill)
        return fill

    def mark(self, prices: dict[str, float]) -> Snapshot:
        """Update last-known prices and return the book graded at them."""
        for symbol, price in prices.items():
            if price <= 0:
                raise LedgerError(f"mark price for {symbol} must be positive")
            self.last_prices[symbol] = price
        if (
            self.tracker_symbol is not None
            and self._tracker_baseline is None
            and self.tracker_symbol in self.last_prices
        ):
            self._tracker_baseline = self.last_prices[self.tracker_symbol]
        return self.snapshot()

    def snapshot(self) -> Snapshot:
        views: list[PositionView] = []
        for symbol, pos in sorted(self.positions.items()):
            if symbol not in self.last_prices:
                raise UnknownSymbol(f"no known price for {symbol}")
            last = self.last_prices[symbol]
            views.append(
                PositionView(
                    symbol=symbol,
                    qty=pos.qty,
                    avg_cost=pos.avg_cost,
                    last_price=last,
                    market_value=pos.qty * last,
                    unrealized_pnl=pos.qty * (last - pos.avg_cost),
                )
            )

        unrealized = sum(v.unrealized_pnl for v in views)
        equity = self.cash + sum(v.market_value for v in views)
        total = equity - self.initial_budget

        tracker_equity = delta_usd = delta_pct = None
        if self._tracker_baseline is not None and self.tracker_symbol is not None:
            tracker_last = self.last_prices[self.tracker_symbol]
            tracker_equity = self.initial_budget * tracker_last / self._tracker_baseline
            delta_usd = equity - tracker_equity
            delta_pct = delta_usd / self.initial_budget * 100

        return Snapshot(
            initial_budget=self.initial_budget,
            cash=self.cash,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            total_pnl=total,
            return_pct=total / self.initial_budget * 100,
            positions=views,
            tracker_symbol=self.tracker_symbol,
            tracker_equity=tracker_equity,
            delta_usd=delta_usd,
            delta_pct=delta_pct,
        )
