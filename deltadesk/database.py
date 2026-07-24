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
    inspect,
    select,
    text,
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
    action = Column(String(8), nullable=False, default="HOLD")
    cycle = Column(String(32), nullable=False, index=True)
    direction = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    rationale = Column(Text)
    deterministic = Column(Boolean, default=True)
    source_run_id = Column(String(64))
    inputs_used = Column(JSON)
    degraded = Column(Boolean, default=False)
    provenance_notes = Column(Text)
    prompt_snapshot = Column(JSON)
    equation_snapshot = Column(JSON)
    agent_trace = Column(JSON)
    model_snapshot = Column(JSON)
    learning_snapshot = Column(JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ForecastRow(Base):
    __tablename__ = "forecasts"
    __table_args__ = (UniqueConstraint("ticker", "cycle", name="uq_forecast_ticker_cycle"),)

    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), index=True)
    ticker = Column(String(32), nullable=False, index=True)
    cycle = Column(String(32), nullable=False, index=True)
    action = Column(String(8), nullable=False, default="HOLD")
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
    config_snapshot = Column(JSON)
    prompt_snapshot = Column(JSON)
    equation_snapshot = Column(JSON)
    agent_trace = Column(JSON)
    model_snapshot = Column(JSON)
    learning_snapshot = Column(JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class PerformanceOutcome(Base):
    """One immutable prediction evaluated over the next stored trading close."""

    __tablename__ = "performance_outcomes"
    __table_args__ = (
        UniqueConstraint("subject_type", "subject_id", name="uq_outcome_subject"),
    )

    id = Column(Integer, primary_key=True)
    subject_type = Column(String(16), nullable=False)
    subject_id = Column(Integer, nullable=False)
    agent = Column(String(32), nullable=False, index=True)
    ticker = Column(String(32), nullable=False, index=True)
    action = Column(String(8), nullable=False)
    equation = Column(String(64), nullable=False)
    signal_date = Column(String(10), nullable=False, index=True)
    entry_date = Column(String(10), nullable=False)
    exit_date = Column(String(10), nullable=False)
    entry_close = Column(Float, nullable=False)
    exit_close = Column(Float, nullable=False)
    return_pct = Column(Float, nullable=False)
    performance_score = Column(Float, nullable=False)
    correct = Column(Boolean, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentPolicy(Base):
    """Latest bounded policy learned for one agent."""

    __tablename__ = "agent_policies"

    id = Column(Integer, primary_key=True)
    agent = Column(String(32), unique=True, nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    learning_date = Column(String(10), nullable=False)
    recommended_equation = Column(String(64))
    equation_stats = Column(JSON)
    reliability = Column(Float, nullable=False, default=0.5)
    observations = Column(Integer, nullable=False, default=0)
    config_overrides = Column(JSON)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class DailyLearningRun(Base):
    """Idempotency and audit ledger for the once-per-day learner."""

    __tablename__ = "daily_learning_runs"

    id = Column(Integer, primary_key=True)
    learning_date = Column(String(10), unique=True, nullable=False, index=True)
    status = Column(String(16), nullable=False, default="running")
    outcomes_added = Column(Integer, nullable=False, default=0)
    before_snapshot = Column(JSON)
    after_snapshot = Column(JSON)
    details = Column(JSON)
    error = Column(Text)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True))


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
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    _ensure_compat_columns(engine)


def _ensure_compat_columns(engine) -> None:
    """Add newly introduced columns to older local SQLite databases."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    wanted = {
        "signals": {
            "action": "VARCHAR(8) NOT NULL DEFAULT 'HOLD'",
            "prompt_snapshot": "JSON",
            "equation_snapshot": "JSON",
            "agent_trace": "JSON",
            "model_snapshot": "JSON",
            "learning_snapshot": "JSON",
        },
        "forecasts": {
            "action": "VARCHAR(8) NOT NULL DEFAULT 'HOLD'",
            "prompt_snapshot": "JSON",
            "equation_snapshot": "JSON",
            "agent_trace": "JSON",
            "model_snapshot": "JSON",
            "learning_snapshot": "JSON",
        },
    }
    with engine.begin() as conn:
        for table, columns in wanted.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl_type in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


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
        row.action = signal.action
        row.direction = signal.direction
        row.confidence = signal.confidence
        row.rationale = signal.rationale
        row.deterministic = signal.deterministic
        row.source_run_id = signal.provenance.source_run_id
        row.inputs_used = signal.provenance.inputs_used
        row.degraded = signal.provenance.degraded
        row.provenance_notes = signal.provenance.notes
        row.prompt_snapshot = signal.prompt_snapshot
        row.equation_snapshot = signal.equation_snapshot
        row.agent_trace = signal.agent_trace
        row.model_snapshot = signal.model_snapshot
        row.learning_snapshot = signal.learning_snapshot
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
                action=row.action or "HOLD",
                direction=row.direction,
                confidence=row.confidence,
                rationale=row.rationale or "",
                deterministic=bool(row.deterministic),
                cycle=row.cycle,
                created_at=_as_utc(row.created_at),
                prompt_snapshot=dict(row.prompt_snapshot or {}),
                equation_snapshot=dict(row.equation_snapshot or {}),
                agent_trace=dict(row.agent_trace or {}),
                model_snapshot=dict(row.model_snapshot or {}),
                learning_snapshot=dict(row.learning_snapshot or {}),
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
        row.action = forecast.action
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
        row.config_snapshot = forecast.config_snapshot
        row.prompt_snapshot = forecast.prompt_snapshot
        row.equation_snapshot = forecast.equation_snapshot
        row.agent_trace = forecast.agent_trace
        row.model_snapshot = forecast.model_snapshot
        row.learning_snapshot = forecast.learning_snapshot
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


# --------------------------------------------------------------------------
# Daily performance learning
# --------------------------------------------------------------------------


def evaluation_subjects() -> list[dict]:
    """Latest prediction per agent/ticker/day that has not been evaluated."""
    with session_scope() as s:
        evaluated = {
            (row.subject_type, row.subject_id)
            for row in s.execute(select(PerformanceOutcome)).scalars()
        }
        candidates: list[dict] = []
        for row in s.execute(select(SignalRow)).scalars():
            candidates.append({
                "subject_type": "signal",
                "subject_id": row.id,
                "agent": row.source,
                "ticker": row.ticker,
                "action": row.action or "HOLD",
                "equation": (row.equation_snapshot or {}).get(row.source, "unknown"),
                "signal_date": row.cycle[:10],
                "cycle": row.cycle,
                "created_at": row.created_at,
            })
        for row in s.execute(select(ForecastRow)).scalars():
            candidates.append({
                "subject_type": "forecast",
                "subject_id": row.id,
                "agent": "forecaster",
                "ticker": row.ticker,
                "action": row.action or "HOLD",
                "equation": (row.equation_snapshot or {}).get("forecaster", "unknown"),
                "signal_date": row.cycle[:10],
                "cycle": row.cycle,
                "created_at": row.created_at,
            })

    # Intraday cycles can produce several predictions. One daily observation per
    # agent and ticker prevents high-frequency runs from dominating policy updates.
    latest: dict[tuple[str, str, str, str], dict] = {}
    for item in candidates:
        key = (
            item["subject_type"],
            item["agent"],
            item["ticker"],
            item["signal_date"],
        )
        current = latest.get(key)
        order = (
            (item["created_at"].isoformat() if item["created_at"] else ""),
            item["subject_id"],
        )
        current_order = (
            (
                current["created_at"].isoformat() if current["created_at"] else "",
                current["subject_id"],
            )
            if current else None
        )
        if current is None or order > current_order:
            latest[key] = item
    return [
        item
        for item in latest.values()
        if (item["subject_type"], item["subject_id"]) not in evaluated
    ]


def bars_for_evaluation(ticker: str, signal_date: str) -> list[dict]:
    """Return the entry close and later closes available for one prediction."""
    with session_scope() as s:
        rows = list(
            s.execute(
                select(HistoricalBar)
                .where(
                    HistoricalBar.ticker == ticker.upper(),
                    HistoricalBar.bar_date >= signal_date,
                    HistoricalBar.close.is_not(None),
                )
                .order_by(HistoricalBar.bar_date.asc())
                .limit(3)
            ).scalars()
        )
    return [{"bar_date": row.bar_date, "close": row.close} for row in rows]


def store_performance_outcomes(outcomes: list[dict]) -> int:
    """Insert outcome labels once. Returns the number of new rows."""
    inserted = 0
    with session_scope() as s:
        existing = {
            (row.subject_type, row.subject_id)
            for row in s.execute(select(PerformanceOutcome)).scalars()
        }
        for data in outcomes:
            key = (data["subject_type"], data["subject_id"])
            if key in existing:
                continue
            s.add(PerformanceOutcome(**data))
            existing.add(key)
            inserted += 1
    return inserted


def performance_outcomes(since_date: str | None = None) -> list[dict]:
    stmt = select(PerformanceOutcome)
    if since_date:
        stmt = stmt.where(PerformanceOutcome.signal_date >= since_date)
    stmt = stmt.order_by(PerformanceOutcome.signal_date.asc(), PerformanceOutcome.id.asc())
    with session_scope() as s:
        rows = list(s.execute(stmt).scalars())
    return [
        {
            "agent": row.agent,
            "ticker": row.ticker,
            "action": row.action,
            "equation": row.equation,
            "signal_date": row.signal_date,
            "return_pct": row.return_pct,
            "performance_score": row.performance_score,
            "correct": bool(row.correct),
        }
        for row in rows
    ]


def _policy_dict(row: AgentPolicy) -> dict:
    return {
        "agent": row.agent,
        "version": row.version,
        "learning_date": row.learning_date,
        "recommended_equation": row.recommended_equation,
        "equation_stats": dict(row.equation_stats or {}),
        "reliability": row.reliability,
        "observations": row.observations,
        "config_overrides": dict(row.config_overrides or {}),
        "updated_at": _as_utc(row.updated_at).isoformat(),
    }


def agent_performance_context(agent: str) -> dict:
    """Compact learned prior supplied to one agent's next decision."""
    with session_scope() as s:
        row = s.execute(
            select(AgentPolicy).where(AgentPolicy.agent == agent)
        ).scalar_one_or_none()
        if row is None:
            return {
                "learned": False,
                "agent": agent,
                "observations": 0,
                "note": "No completed performance policy yet; use current data only.",
            }
        return {"learned": True, **_policy_dict(row)}


def all_policy_snapshots() -> dict[str, dict]:
    with session_scope() as s:
        rows = list(s.execute(select(AgentPolicy).order_by(AgentPolicy.agent)).scalars())
        return {row.agent: _policy_dict(row) for row in rows}


def upsert_agent_policy(
    *,
    agent: str,
    learning_date: str,
    recommended_equation: str,
    equation_stats: dict,
    reliability: float,
    observations: int,
    config_overrides: dict | None = None,
) -> dict:
    with session_scope() as s:
        row = s.execute(
            select(AgentPolicy).where(AgentPolicy.agent == agent)
        ).scalar_one_or_none()
        if row is None:
            row = AgentPolicy(agent=agent, version=1)
            s.add(row)
        elif row.learning_date != learning_date:
            row.version += 1
        row.learning_date = learning_date
        row.recommended_equation = recommended_equation
        row.equation_stats = equation_stats
        row.reliability = reliability
        row.observations = observations
        row.config_overrides = config_overrides or {}
        row.updated_at = utcnow()
        s.flush()
        return _policy_dict(row)


def activate_learned_policy() -> dict[str, float]:
    """Apply only allow-listed learned overrides for the next complete run."""
    if not config.LEARNING_ENABLED:
        return {}
    context = agent_performance_context("forecaster")
    requested = context.get("config_overrides") or {}
    allowed = {
        key: value
        for key, value in requested.items()
        if key.startswith("SIGNAL_WEIGHTS.")
    }
    return config.apply_overrides(allowed) if allowed else {}


def start_daily_learning(learning_date: str) -> bool:
    """Claim a date. Successful or currently running dates are idempotent."""
    with session_scope() as s:
        row = s.execute(
            select(DailyLearningRun).where(
                DailyLearningRun.learning_date == learning_date
            )
        ).scalar_one_or_none()
        if row is not None and row.status in {"running", "success"}:
            return False
        before = all_policy_snapshots()
        if row is None:
            row = DailyLearningRun(learning_date=learning_date)
            s.add(row)
            row.before_snapshot = before
        row.status = "running"
        row.outcomes_added = 0
        row.after_snapshot = None
        row.details = {}
        row.error = None
        row.started_at = utcnow()
        row.finished_at = None
    return True


def finish_daily_learning(
    learning_date: str,
    *,
    status: str,
    outcomes_added: int = 0,
    details: dict | None = None,
    error: str | None = None,
) -> None:
    with session_scope() as s:
        row = s.execute(
            select(DailyLearningRun).where(
                DailyLearningRun.learning_date == learning_date
            )
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = status
        row.outcomes_added = outcomes_added
        row.after_snapshot = all_policy_snapshots()
        row.details = details or {}
        row.error = error[:2000] if error else None
        row.finished_at = utcnow()


def daily_learning_run(learning_date: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(
            select(DailyLearningRun).where(
                DailyLearningRun.learning_date == learning_date
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "learning_date": row.learning_date,
            "status": row.status,
            "outcomes_added": row.outcomes_added,
            "before_snapshot": dict(row.before_snapshot or {}),
            "after_snapshot": dict(row.after_snapshot or {}),
            "details": dict(row.details or {}),
            "error": row.error,
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
