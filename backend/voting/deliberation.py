"""Deliberative voting: position change → case → rebuttal → judged verdict.

The entry point of the conversation for each agent is *the change in
position it wants to make* — that's it, nothing more. The debate follows:

  1. OPEN      each agent posts its PositionChange to the Band room
  2. CASES     each agent posts one case arguing for its change
  3. REBUTTALS agents whose changes conflict (opposite-sign deltas on the
               same ticker) each post one rebuttal
  4. VERDICT   the evaluation agent scores every case (LLM judge), combines
               argument quality with track-record credibility, and blends
               the proposed changes into the desk's final position:

                 weight_a = argument_score_a × credibility_a
                 final_target = Σ weight_a · target_a / Σ weight_a

Agents are arbitrary named identities (externally registered via the proxy,
see registry.py) — not a fixed enum — so the desk can grow analysts.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field, model_validator

from .transport import RoomTransport
from .track_record import TrackRecord

PM_NAME = "pm"
EVALUATOR_NAME = "evaluator"


class PositionChange(BaseModel):
    """The entry ticket to the floor. Positions are signed fractions of the
    agent's max allowed size: -1 (max short) .. 0 (flat) .. +1 (max long)."""

    agent: str
    ticker: str
    current: float = Field(ge=-1, le=1)
    target: float = Field(ge=-1, le=1)

    @property
    def delta(self) -> float:
        return self.target - self.current

    @model_validator(mode="after")
    def _must_move(self) -> "PositionChange":
        # target == current is a valid "stay put" stance; it still enters
        # the debate (a hold argument can win).
        return self


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
    final_target: float
    contributions: dict[str, float]  # agent -> weight share used in the blend
    scores: list[ArgumentScore]


class Verdict(BaseModel):
    session_id: str
    verdicts: list[TickerVerdict]
    credibility: dict[str, float]
    narrative: str


class Judge(Protocol):
    """Scores argument quality 0..1 per case. judge.py provides ClaudeJudge
    (LLM) and HeuristicJudge (deterministic fallback)."""

    def score(
        self,
        proposals: list[PositionChange],
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
    proposals: list[PositionChange] = []
    cases: list[Case] = []
    rebuttals: list[RebuttalMsg] = []
    verdict: Optional[Verdict] = None

    def conflicting_pairs(self) -> list[tuple[PositionChange, PositionChange]]:
        """Pairs of proposals pulling the same ticker in opposite directions."""
        pairs = []
        for i, a in enumerate(self.proposals):
            for b in self.proposals[i + 1:]:
                if a.ticker == b.ticker and a.delta * b.delta < 0:
                    pairs.append((a, b))
        return pairs


def _fence(header: str, body: str, payload: dict) -> str:
    return f"{header}\n{body}\n```json\n{json.dumps(payload)}\n```"


def post_position(room: RoomTransport, p: PositionChange) -> None:
    arrow = "→"
    header = (
        f"📍 POSITION {p.agent} · {p.ticker} · "
        f"{p.current:+.2f} {arrow} {p.target:+.2f} (Δ{p.delta:+.2f})"
    )
    room.post(p.agent, _fence(header, "", p.model_dump(mode="json")), mentions=[PM_NAME])


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
        lines.append(f"- {tv.ticker}: final position {tv.final_target:+.2f} ({shares})")
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
    """Judge the closed debate and blend proposals into final positions."""
    scores = judge.score(session.proposals, session.cases, session.rebuttals)
    score_by = {(s.agent, s.ticker): s for s in scores}

    verdicts = []
    for ticker in sorted({p.ticker for p in session.proposals}):
        proposals = [p for p in session.proposals if p.ticker == ticker]
        weights: dict[str, float] = {}
        for p in proposals:
            s = score_by.get((p.agent, ticker))
            arg_score = s.score if s else 0.5  # no case filed → mediocre default
            weights[p.agent] = arg_score * record.credibility(p.agent)

        total = sum(weights.values())
        if total <= 0:
            weights = {p.agent: 1 / len(proposals) for p in proposals}
            total = 1.0
        final = sum(weights[p.agent] * p.target for p in proposals) / total

        verdicts.append(
            TickerVerdict(
                ticker=ticker,
                final_target=round(final, 4),
                contributions={a: round(w / total, 4) for a, w in weights.items()},
                scores=[s for s in scores if s.ticker == ticker],
            )
        )

    verdict = Verdict(
        session_id=session.id,
        verdicts=verdicts,
        credibility={p.agent: record.credibility(p.agent) for p in session.proposals},
        narrative=_narrative(verdicts),
    )
    session.verdict = verdict
    session.phase = Phase.CLOSED
    post_verdict(room, verdict)
    return verdict


def _narrative(verdicts: list[TickerVerdict]) -> str:
    parts = []
    for tv in verdicts:
        lead = max(tv.contributions, key=lambda a: tv.contributions[a])
        parts.append(
            f"{tv.ticker} settles at {tv.final_target:+.2f}, led by {lead} "
            f"({tv.contributions[lead]:.0%} of the blend)"
        )
    return "Desk verdict: " + "; ".join(parts) + "."


class DeliberatingAgent(Protocol):
    """In-process agent interface (Guild analysts / scripted demo agents)."""

    name: str

    def make_case(self, own: PositionChange, others: list[PositionChange]) -> str: ...

    def rebut(self, own: PositionChange, opposing_case: Case) -> str: ...


def run_deliberation(
    session_id: str,
    proposals: list[PositionChange],
    agents: dict[str, DeliberatingAgent],
    judge: Judge,
    record: TrackRecord,
    room: RoomTransport,
) -> Verdict:
    """Full cycle with callback-driven agents (demo, tests, replay harness)."""
    session = DeliberationSession(id=session_id, proposals=proposals)

    for p in proposals:
        post_position(room, p)

    session.phase = Phase.CASES
    for p in proposals:
        agent = agents.get(p.agent)
        if agent is None:
            continue
        others = [q for q in proposals if q.agent != p.agent and q.ticker == p.ticker]
        case = Case(agent=p.agent, ticker=p.ticker, argument=agent.make_case(p, others))
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
