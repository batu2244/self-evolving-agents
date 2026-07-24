"""Persistence for collected data, analyst signals, and forecasts.

SQLAlchemy against ACTIAN_DATABASE_URL, falling back to local SQLite. Every write
goes through an upsert keyed on the natural dedup key, so re-running an agent in
the same cycle corrects the row instead of duplicating it.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

import config
from contracts import Contribution, Forecast, Provenance, Signal, utcnow

Base = declarative_base()

_engine = None
_SessionFactory = None


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    agent = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True))
    error = Column(Text)
    details = Column(JSON)


class MarketSnapshot(Base):
    """A realtime quote, as collected."""

    __tablename__ = "market_snapshots"
    __table_args__ = (UniqueConstraint("ticker", "cycle", name="uq_snapshot_ticker_cycle"),)

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), index=True)
    ticker = Column(String(32), nullable=False, index=True)
    cycle = Column(String(32), nullable=False)
    price = Column(Float)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    previous_close = Column(Float)
    volume = Column(Float)
    average_volume = Column(Float)
    source = Column(String(32))
    observed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    raw = Column(JSON)


class HistoricalBar(Base):
    """One daily OHLCV bar."""

    __tablename__ = "historical_bars"
    __table_args__ = (UniqueConstraint("ticker", "bar_date", name="uq_bar_ticker_date"),)

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), index=True)
    ticker = Column(String(32), nullable=False, index=True)
    bar_date = Column(String(10), nullable=False)  # YYYY-MM-DD, UTC
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    source = Column(String(32))
    ingested_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class SignalRow(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("ticker", "source", "cycle", name="uq_signal_ticker_source_cycle"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), index=True)
    ticker = Column(String(32), nullable=False, index=True)
    source = Column(String(32), nullable=False, index=True)
    cycle = Column(String(32), nullable=False, index=True)
    direction = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    rationale = Column(Text)
    deterministic = Column(Boolean, default=True)
    source_run_id = Column(String(64))
    inputs_used = Column(JSON)
    degraded = Column(Boolean, default=False)
    provenance_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ForecastRow(Base):
    __tablename__ = "forecasts"
    __table_args__ = (UniqueConstraint("ticker", "cycle", name="uq_forecast_ticker_cycle"),)

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), index=True)
    ticker = Column(String(32), nullable=False, index=True)
    cycle = Column(String(32), nullable=False, index=True)
    direction = Column(String(8), nullable=False)
    score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    per_agent_contributions = Column(JSON)
    rationale = Column(Text)
    deterministic = Column(Boolean, default=True)
    inputs_used = Column(JSON)
    degraded = Column(Boolean, default=False)
    provenance_notes = Column(Text)
    mode = Column(String(32), default="paper-trading-research")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


# --------------------------------------------------------------------------
# Engine / session
# --------------------------------------------------------------------------


def get_engine(url: str | None = None):
    global _engine, _SessionFactory
    if _engine is None or url is not None:
        target = url or config.DATABASE_URL
        kwargs: dict[str, Any] = {"future": True}
        if target.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(target, **kwargs)
        _SessionFactory = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    get_engine(url)
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine so a later call can bind a different URL (tests)."""
    global _engine, _SessionFactory
    _engine = None
    _SessionFactory = None


# --------------------------------------------------------------------------
# Run logging
# --------------------------------------------------------------------------


def start_run(agent: str, details: dict | None = None) -> str:
    run_id = f"{agent}-{uuid.uuid4().hex[:12]}"
    with session_scope() as s:
        s.add(AgentRun(run_id=run_id, agent=agent, status="running", details=details or {}))
    return run_id


def finish_run(run_id: str, status: str, error: str | None = None, details: dict | None = None) -> None:
    with session_scope() as s:
        row = s.execute(select(AgentRun).where(AgentRun.run_id == run_id)).scalar_one_or_none()
        if row is None:
            return
        row.status = status
        row.finished_at = utcnow()
        if error:
            row.error = error[:2000]
        if details:
            merged = dict(row.details or {})
            merged.update(details)
            row.details = merged


# --------------------------------------------------------------------------
# Upserts
# --------------------------------------------------------------------------


def upsert_snapshot(run_id: str, ticker: str, cycle: str, data: dict) -> None:
    with session_scope() as s:
        row = s.execute(
            select(MarketSnapshot).where(
                MarketSnapshot.ticker == ticker, MarketSnapshot.cycle == cycle
            )
        ).scalar_one_or_none()
        if row is None:
            row = MarketSnapshot(ticker=ticker, cycle=cycle)
            s.add(row)
        row.run_id = run_id
        row.observed_at = utcnow()
        for field in ("price", "open", "high", "low", "previous_close", "volume",
                      "average_volume", "source"):
            if field in data:
                setattr(row, field, data[field])
        row.raw = data.get("raw")


def upsert_bars(run_id: str, ticker: str, bars: list[dict], source: str) -> int:
    """Insert bars that are new; refresh ones already stored. Returns rows touched."""
    touched = 0
    with session_scope() as s:
        existing = {
            r.bar_date: r
            for r in s.execute(
                select(HistoricalBar).where(HistoricalBar.ticker == ticker)
            ).scalars()
        }
        for bar in bars:
            row = existing.get(bar["bar_date"])
            if row is None:
                row = HistoricalBar(ticker=ticker, bar_date=bar["bar_date"])
                s.add(row)
            row.run_id = run_id
            row.source = source
            row.ingested_at = utcnow()
            for field in ("open", "high", "low", "close", "volume"):
                if field in bar:
                    setattr(row, field, bar[field])
            touched += 1
    return touched


def store_signal(signal: Signal, run_id: str) -> None:
    with session_scope() as s:
        row = s.execute(
            select(SignalRow).where(
                SignalRow.ticker == signal.ticker,
                SignalRow.source == signal.source,
                SignalRow.cycle == signal.cycle,
            )
        ).scalar_one_or_none()
        if row is None:
            row = SignalRow(ticker=signal.ticker, source=signal.source, cycle=signal.cycle)
            s.add(row)
        row.run_id = run_id
        row.direction = signal.direction
        row.confidence = signal.confidence
        row.rationale = signal.rationale
        row.deterministic = signal.deterministic
        row.source_run_id = signal.provenance.source_run_id
        row.inputs_used = signal.provenance.inputs_used
        row.degraded = signal.provenance.degraded
        row.provenance_notes = signal.provenance.notes
        row.created_at = signal.created_at


def latest_signals(ticker: str, sources: tuple[str, ...] | None = None) -> dict[str, Signal]:
    """Most recent signal per source for a ticker, keyed by source."""
    wanted = sources or config.SIGNAL_SOURCES
    out: dict[str, Signal] = {}
    with session_scope() as s:
        for source in wanted:
            row = s.execute(
                select(SignalRow)
                .where(SignalRow.ticker == ticker.upper(), SignalRow.source == source)
                .order_by(SignalRow.created_at.desc(), SignalRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                continue
            out[source] = Signal(
                ticker=row.ticker,
                source=row.source,
                direction=row.direction,
                confidence=row.confidence,
                rationale=row.rationale or "",
                deterministic=bool(row.deterministic),
                cycle=row.cycle,
                created_at=_as_utc(row.created_at),
                provenance=Provenance(
                    source_run_id=row.source_run_id,
                    inputs_used=list(row.inputs_used or []),
                    degraded=bool(row.degraded),
                    notes=row.provenance_notes or "",
                ),
            )
    return out


def store_forecast(forecast: Forecast, run_id: str) -> None:
    with session_scope() as s:
        row = s.execute(
            select(ForecastRow).where(
                ForecastRow.ticker == forecast.ticker, ForecastRow.cycle == forecast.cycle
            )
        ).scalar_one_or_none()
        if row is None:
            row = ForecastRow(ticker=forecast.ticker, cycle=forecast.cycle)
            s.add(row)
        row.run_id = run_id
        row.direction = forecast.direction
        row.score = forecast.score
        row.confidence = forecast.confidence
        row.per_agent_contributions = [c.model_dump() for c in forecast.per_agent_contributions]
        row.rationale = forecast.rationale
        row.deterministic = forecast.deterministic
        row.inputs_used = forecast.provenance.inputs_used
        row.degraded = forecast.provenance.degraded
        row.provenance_notes = forecast.provenance.notes
        row.mode = forecast.mode
        row.created_at = forecast.created_at


def recent_bars(ticker: str, limit: int) -> list[dict]:
    """Most recent `limit` bars for a ticker, oldest first."""
    with session_scope() as s:
        rows = list(
            s.execute(
                select(HistoricalBar)
                .where(HistoricalBar.ticker == ticker.upper())
                .order_by(HistoricalBar.bar_date.desc())
                .limit(limit)
            ).scalars()
        )
    rows.reverse()
    return [
        {
            "bar_date": r.bar_date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "run_id": r.run_id,
        }
        for r in rows
    ]


def latest_snapshot(ticker: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(
            select(MarketSnapshot)
            .where(MarketSnapshot.ticker == ticker.upper())
            .order_by(MarketSnapshot.observed_at.desc(), MarketSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "run_id": row.run_id,
            "ticker": row.ticker,
            "cycle": row.cycle,
            "price": row.price,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "previous_close": row.previous_close,
            "volume": row.volume,
            "average_volume": row.average_volume,
            "source": row.source,
        }


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise TypeError(f"not JSON serializable: {type(value)}")


def dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=json_default, ensure_ascii=False)
