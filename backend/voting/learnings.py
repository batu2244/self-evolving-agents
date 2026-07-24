"""Village notes in Actian, surfaced as learnings.

Every heartbeat's gradings are written as rows (the desk's raw notes), and
from them the store derives *learnings* — durable, human-readable lessons:

  streak     an agent has been wrong (or right) N straight heartbeats
  flip       the desk reversed its previous decision on a ticker
  leader     trust changed hands — a different agent now holds top credibility
  dissent    the desk was wrong while a lone dissenter was right

Storage follows the same contract as deltadesk/database.py: SQLAlchemy
against ACTIAN_DATABASE_URL, falling back to the shared local SQLite file —
so the notes land in the same Actian database as the agents' signals and
forecasts, and moving to a live Actian instance is just setting the env var.

Surfacing: Village.heartbeat() attaches the new learnings to its output,
the CLI prints them, and the FastAPI router (mounted by voting.api) serves
GET /api/voting/learnings for the dashboard.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import Session, declarative_base

STREAK_THRESHOLD = 3

Base = declarative_base()


def _database_url() -> str:
    url = os.getenv("ACTIAN_DATABASE_URL")
    if url:
        return url
    # Same fallback file as deltadesk/database.py so all desk data
    # (signals, forecasts, notes) lives in one database.
    repo_root = Path(__file__).resolve().parent.parent.parent
    return f"sqlite:///{repo_root / 'deltadesk' / 'deltadesk.db'}"


class GradingNote(Base):
    """One agent's graded stance on one heartbeat — the raw note."""

    __tablename__ = "village_gradings"

    id = Column(String(36), primary_key=True)
    village = Column(String(64), nullable=False, index=True)
    cycle = Column(String(32), nullable=False)
    ticker = Column(String(16), nullable=False, index=True)
    agent = Column(String(64), nullable=False, index=True)
    side = Column(String(8), nullable=False)
    score = Column(Float, nullable=False)  # signed hit in [-1, 1]
    credibility = Column(Float)
    created_at = Column(DateTime, nullable=False)


class Learning(Base):
    """A derived lesson the village surfaces."""

    __tablename__ = "village_learnings"

    id = Column(String(36), primary_key=True)
    village = Column(String(64), nullable=False, index=True)
    cycle = Column(String(32), nullable=False)
    ticker = Column(String(16), nullable=False)
    kind = Column(String(16), nullable=False)  # streak | flip | leader | dissent
    agent = Column(String(64))
    text = Column(Text, nullable=False)
    weight = Column(Integer, default=1)  # streak length / emphasis
    created_at = Column(DateTime, nullable=False, index=True)


class LearningStore:
    def __init__(self, url: str | None = None) -> None:
        self._engine = create_engine(url or _database_url(), future=True)
        Base.metadata.create_all(self._engine)

    # -- raw notes ---------------------------------------------------------

    def record_gradings(self, village: str, cycle: str, graded: list[dict]) -> None:
        now = datetime.now(timezone.utc)
        with Session(self._engine) as s:
            for g in graded:
                if g["agent"] == "DESK":
                    continue
                s.add(GradingNote(
                    id=str(uuid.uuid4()), village=village, cycle=cycle,
                    ticker=g["ticker"], agent=g["agent"], side=g["side"],
                    score=g["score"], credibility=g.get("credibility"),
                    created_at=now,
                ))
            s.commit()

    def _recent_scores(self, village: str, ticker: str, agent: str, n: int) -> list[float]:
        with Session(self._engine) as s:
            rows = s.execute(
                select(GradingNote.score)
                .where(GradingNote.village == village,
                       GradingNote.ticker == ticker,
                       GradingNote.agent == agent)
                .order_by(GradingNote.created_at.desc())
                .limit(n)
            ).scalars().all()
        return list(rows)

    # -- derivation --------------------------------------------------------

    def derive(
        self,
        village: str,
        cycle: str,
        graded: list[dict],
        decisions_now: dict[str, str],
        decisions_prev: dict[str, str],
        credibility: dict[str, float],
        prev_leader: str | None,
    ) -> list[Learning]:
        now = datetime.now(timezone.utc)
        out: list[Learning] = []

        def note(kind: str, ticker: str, text: str, agent: str | None = None, weight: int = 1):
            out.append(Learning(
                id=str(uuid.uuid4()), village=village, cycle=cycle, ticker=ticker,
                kind=kind, agent=agent, text=text, weight=weight, created_at=now,
            ))

        desk_by_ticker = {g["ticker"]: g for g in graded if g["agent"] == "DESK"}
        agents_graded = [g for g in graded if g["agent"] != "DESK"]

        # Streaks: N consecutive wrong (or right) calls on the same ticker.
        for g in agents_graded:
            scores = self._recent_scores(village, g["ticker"], g["agent"], STREAK_THRESHOLD)
            if len(scores) < STREAK_THRESHOLD:
                continue
            if all(x < 0 for x in scores):
                note("streak", g["ticker"],
                     f"{g['agent']} has been on the wrong side of {g['ticker']} "
                     f"{len(scores)} heartbeats running; credibility down to "
                     f"{g.get('credibility'):.2f}. Discount its side until it lands one.",
                     agent=g["agent"], weight=len(scores))
            elif all(x > 0 for x in scores):
                note("streak", g["ticker"],
                     f"{g['agent']} has read {g['ticker']} right {len(scores)} "
                     f"heartbeats running (credibility {g.get('credibility'):.2f}). "
                     f"Its side deserves the benefit of the doubt.",
                     agent=g["agent"], weight=len(scores))

        # Flips: the desk reversed itself.
        for ticker, side in decisions_now.items():
            prev = decisions_prev.get(ticker)
            if prev and prev != side:
                note("flip", ticker,
                     f"Desk flipped {ticker} from {prev.upper()} to {side.upper()} — "
                     f"the committee's read changed inside one heartbeat window.")

        # Leadership: top-credibility agent changed.
        if credibility:
            leader = max(credibility, key=lambda a: credibility[a])
            if prev_leader and leader != prev_leader:
                note("leader", "*",
                     f"Trust changed hands: {leader} (credibility "
                     f"{credibility[leader]:.2f}) overtook {prev_leader}. "
                     f"The regime the desk is trading has likely changed.",
                     agent=leader)

        # Dissent vindicated: desk wrong, exactly one agent right.
        for ticker, desk in desk_by_ticker.items():
            if desk["score"] >= 0:
                continue
            right = [g for g in agents_graded if g["ticker"] == ticker and g["score"] > 0]
            if len(right) == 1:
                note("dissent", ticker,
                     f"The desk got {ticker} wrong while {right[0]['agent']} alone "
                     f"was right ({right[0]['side'].upper()}). A confident dissent "
                     f"from it should weigh more next time.",
                     agent=right[0]["agent"])

        if out:
            # expire_on_commit=False: callers read the returned rows after
            # the session closes (village output, CLI print).
            with Session(self._engine, expire_on_commit=False) as s:
                s.add_all(out)
                s.commit()
        return out

    # -- surfacing ---------------------------------------------------------

    def latest(self, village: str | None = None, limit: int = 20) -> list[dict]:
        with Session(self._engine) as s:
            q = select(Learning).order_by(Learning.created_at.desc()).limit(limit)
            if village:
                q = q.where(Learning.village == village)
            rows = s.execute(q).scalars().all()
        return [{
            "village": r.village, "cycle": r.cycle, "ticker": r.ticker,
            "kind": r.kind, "agent": r.agent, "text": r.text,
            "weight": r.weight, "created_at": r.created_at.isoformat(),
        } for r in rows]


# ------------------------------------------------------------------- router

router = APIRouter(tags=["learnings"])  # mounted under /api/voting by voting.api
_store: LearningStore | None = None


def get_store() -> LearningStore:
    global _store
    if _store is None:
        _store = LearningStore()
    return _store


@router.get("/learnings")
def learnings(village: str | None = None, limit: int = 20) -> list[dict]:
    """Learnings from the village, newest first — for the dashboard."""
    return get_store().latest(village=village, limit=limit)


def main() -> None:
    village = sys.argv[1] if len(sys.argv) > 1 else None
    for l in get_store().latest(village=village):
        print(f"[{l['created_at'][:16]}] ({l['kind']}) {l['text']}")


if __name__ == "__main__":
    main()
