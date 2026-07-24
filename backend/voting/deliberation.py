"""Deliberative voting: stance → case → rebuttal → judged binary verdict.

The entry point of the conversation for each agent is *the trade it wants to
make* — BUY or SELL the ticker. That's it, nothing more. No sizing, no
regression: the vote is binary. The debate follows:

  1. OPEN      each agent posts its stance (buy/sell) to the Band room
  2. CASES     each agent posts one case arguing its side
  3. REBUTTALS agents on opposite sides of the same ticker each post one
  4. VERDICT   the evaluation agent scores every case (LLM judge), then the
               decision is a weighted vote:

                 weight_a = argument_score_a × credibility_a
                 decision = side with the larger total weight

               Conviction (the winning side's weight share) is reported so
               the PM can size, but the vote itself is strictly buy-or-sell.

Agents are arbitrary named identities (externally registered via the proxy,
see registry.py) — not a fixed enum — so the desk can grow analysts.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from .track_record import TrackRecord
from .transport import RoomTransport

PM_NAME = "pm"
EVALUATOR_NAME = "evaluator"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Stance(BaseModel):
    """The entry ticket to the floor: the trade the agent wants. Nothing more."""

    agent: str
    ticker: str
    side: Side


class Case(BaseModel):
    agent: str
    ticker: str
    argument: str


class RebuttalMsg(BaseModel):
    agent: str
    ticker: str
    against: str  # the agent being rebutted
    argument: str


class ArgumentScore(BaseModel):
    agent: str
    ticker: str
    score: float = Field(ge=0, le=1)
    reasoning: str = ""


class TickerVerdict(BaseModel):
    ticker: str
    decision: Side
    # Winning side's share of total vote weight — 0.5 (knife-edge) to 1.0.
    conviction: float
    unanimous: bool
    # agent -> its share of total vote weight (all agents, both sides).
    contributions: dict[str, float]
    scores: list[ArgumentScore]


class Verdict(BaseModel):
    session_id: str
    verdicts: list[TickerVerdict]
    credibility: dict[str, float]
    narrative: str


class Judge(Protocol):
    """Scores argument quality 0..1 per case. judge.py provides LLM judges
    (OpenRouter/Anthropic) and HeuristicJudge (deterministic fallback)."""

    def score(
        self,
        stances: list[Stance],
        cases: list[Case],
        rebuttals: list[RebuttalMsg],
    ) -> list[ArgumentScore]: ...


class Phase(str, Enum):
    OPEN = "open"
    CASES = "cases"
    REBUTTALS = "rebuttals"
    CLOSED = "closed"


class DeliberationSession(BaseModel):
    """State of one deliberation. Driven either in-process (run_deliberation)
    or over HTTP by external agents (api.py posts into it stage by stage)."""

    id: str
    phase: Phase = Phase.OPEN
    stances: list[Stance] = []
    cases: list[Case] = []
    rebuttals: list[RebuttalMsg] = []
    verdict: Optional[Verdict] = None

    def conflicting_pairs(self) -> list[tuple[Stance, Stance]]:
        """Pairs of stances on opposite sides of the same ticker."""
        pairs = []
        for i, a in enumerate(self.stances):
            for b in self.stances[i + 1:]:
                if a.ticker == b.ticker and a.side != b.side:
                    pairs.append((a, b))
        return pairs


def _fence(header: str, body: str, payload: dict) -> str:
    return f"{header}\n{body}\n```json\n{json.dumps(payload)}\n```"


def post_stance(room: RoomTransport, s: Stance) -> None:
    header = f"🗳️ STANCE {s.agent} · {s.ticker} · {s.side.value.upper()}"
    room.post(s.agent, _fence(header, "", s.model_dump(mode="json")), mentions=[PM_NAME])


def post_case(room: RoomTransport, c: Case) -> None:
    header = f"📣 CASE {c.agent} · {c.ticker}"
    room.post(c.agent, _fence(header, c.argument, c.model_dump(mode="json")), mentions=[PM_NAME])


def post_rebuttal(room: RoomTransport, r: RebuttalMsg) -> None:
    header = f"🛡️ REBUTTAL {r.agent} vs {r.against} · {r.ticker}"
    room.post(
        r.agent,
        _fence(header, r.argument, r.model_dump(mode="json")),
        mentions=[r.against, PM_NAME],
    )


def post_verdict(room: RoomTransport, v: Verdict) -> None:
    lines = []
    for tv in v.verdicts:
        shares = ", ".join(f"{a} {w:.0%}" for a, w in sorted(tv.contributions.items()))
        lines.append(
            f"- {tv.ticker}: {tv.decision.value.upper()} at {tv.conviction:.0%} conviction"
            f"{' — UNANIMOUS' if tv.unanimous else ''} ({shares})"
        )
    header = f"⚖️ VERDICT session {v.session_id}"
    room.post(
        EVALUATOR_NAME,
        _fence(header, "\n".join(lines) + "\n" + v.narrative, v.model_dump(mode="json")),
        mentions=[PM_NAME],
    )


def decide(
    session: DeliberationSession,
    judge: Judge,
    record: TrackRecord,
    room: RoomTransport,
) -> Verdict:
    """Judge the closed debate; the decision per ticker is the side with the
    larger sum of (argument score × credibility)."""
    scores = judge.score(session.stances, session.cases, session.rebuttals)
    score_by = {(s.agent, s.ticker): s for s in scores}

    verdicts = []
    for ticker in sorted({s.ticker for s in session.stances}):
        stances = [s for s in session.stances if s.ticker == ticker]
        weights: dict[str, float] = {}
        mass = {Side.BUY: 0.0, Side.SELL: 0.0}
        for s in stances:
            sc = score_by.get((s.agent, ticker))
            arg_score = sc.score if sc else 0.5  # no case filed → mediocre default
            w = arg_score * record.credibility(s.agent)
            weights[s.agent] = w
            mass[s.side] += w

        total = sum(weights.values()) or 1.0
        decision = Side.BUY if mass[Side.BUY] >= mass[Side.SELL] else Side.SELL
        verdicts.append(
            TickerVerdict(
                ticker=ticker,
                decision=decision,
                conviction=round(mass[decision] / total, 4),
                unanimous=all(s.side == decision for s in stances),
                contributions={a: round(w / total, 4) for a, w in weights.items()},
                scores=[s for s in scores if s.ticker == ticker],
            )
        )

    verdict = Verdict(
        session_id=session.id,
        verdicts=verdicts,
        credibility={s.agent: record.credibility(s.agent) for s in session.stances},
        narrative=_narrative(verdicts),
    )
    session.verdict = verdict
    session.phase = Phase.CLOSED
    post_verdict(room, verdict)
    return verdict


def _narrative(verdicts: list[TickerVerdict]) -> str:
    parts = []
    for tv in verdicts:
        how = "unanimously" if tv.unanimous else f"on {tv.conviction:.0%} of the vote weight"
        parts.append(f"{tv.decision.value.upper()} {tv.ticker} {how}")
    return "Desk decision: " + "; ".join(parts) + "."


class DeliberatingAgent(Protocol):
    """In-process agent interface (Guild analysts / scripted demo agents)."""

    name: str

    def make_case(self, own: Stance, others: list[Stance]) -> str: ...

    def rebut(self, own: Stance, opposing_case: Case) -> str: ...


def run_deliberation(
    session_id: str,
    stances: list[Stance],
    agents: dict[str, DeliberatingAgent],
    judge: Judge,
    record: TrackRecord,
    room: RoomTransport,
) -> Verdict:
    """Full cycle with callback-driven agents (demo, tests, replay harness)."""
    session = DeliberationSession(id=session_id, stances=stances)

    for s in stances:
        post_stance(room, s)

    session.phase = Phase.CASES
    for s in stances:
        agent = agents.get(s.agent)
        if agent is None:
            continue
        others = [q for q in stances if q.agent != s.agent and q.ticker == s.ticker]
        case = Case(agent=s.agent, ticker=s.ticker, argument=agent.make_case(s, others))
        session.cases.append(case)
        post_case(room, case)

    session.phase = Phase.REBUTTALS
    case_by = {(c.agent, c.ticker): c for c in session.cases}
    for a, b in session.conflicting_pairs():
        for mine, theirs in ((a, b), (b, a)):
            agent = agents.get(mine.agent)
            their_case = case_by.get((theirs.agent, theirs.ticker))
            if agent is None or their_case is None:
                continue
            reb = RebuttalMsg(
                agent=mine.agent,
                ticker=mine.ticker,
                against=theirs.agent,
                argument=agent.rebut(mine, their_case),
            )
            session.rebuttals.append(reb)
            post_rebuttal(room, reb)

    return decide(session, judge, record, room)
