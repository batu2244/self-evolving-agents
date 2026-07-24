"""FastAPI router for the voting floor.

This module deliberately does NOT own the FastAPI app — whoever assembles
the main backend mounts it:

    from voting.api import router as voting_router
    app.include_router(voting_router)

For standalone dev/demo there's a factory:

    uvicorn "voting.api:create_app" --factory --reload

Two voting modes are exposed:
  - /deliberations/*  the deliberative flow (position change → case →
                      rebuttal → judged verdict) — the primary mode.
                      External agents drive it stage by stage over HTTP.
  - /cycle            the simpler weighted-tally vote (kept as fallback).

Plus the agent proxy (/agents/*): external services register a Band agent
(or have one created via BAND_ADMIN_KEY) and fetch its Band key.

Uses the in-memory floor by default; set BAND_CHAT_ID + per-registered-agent
keys to run through a live Band room.
"""

from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from . import deliberation as delib
from .band_client import BandAgentClient, BandFloor
from .deliberation import (
    Case,
    DeliberationSession,
    Phase,
    PositionChange,
    RebuttalMsg,
    Verdict,
)
from .floor import run_vote_cycle
from .judge import default_judge
from .registry import AgentRegistry, RegistryError
from .track_record import TrackRecord
from .transport import InMemoryFloor, RoomTransport
from .types import AnalystId, Challenge, DecisionMemo, Rebuttal, TallyConfig, Vote

router = APIRouter(prefix="/api/voting", tags=["voting"])

CORE_AGENTS = [a.value for a in AnalystId] + ["pm", "evaluator"]


class _State:
    registry = AgentRegistry()
    record = TrackRecord()
    judge = default_judge()
    room: RoomTransport | None = None
    sessions: dict[str, DeliberationSession] = {}
    last_memo: DecisionMemo | None = None
    weights: dict[AnalystId, float] = {a: 1 / 3 for a in AnalystId}

    @classmethod
    def get_room(cls) -> RoomTransport:
        if cls.room is None:
            chat_id = os.environ.get("BAND_CHAT_ID")
            names = {a["name"] for a in cls.registry.list_agents()} | set(CORE_AGENTS)
            try:
                clients = {n: BandAgentClient(n, cls.registry.key_for(n)) for n in names}
                if chat_id and "pm" in clients:
                    cls.room = BandFloor(chat_id, clients, reader="pm")
                else:
                    cls.room = InMemoryFloor()
            except RegistryError:
                cls.room = InMemoryFloor()
        return cls.room


# ---------------------------------------------------------------- agent proxy


class RegisterAgentRequest(BaseModel):
    name: str
    # External service brings its own Band identity — or omit and the proxy
    # creates one on the desk owner's Band account (needs BAND_ADMIN_KEY).
    band_key: str | None = None


@router.post("/agents")
def register_agent(req: RegisterAgentRequest) -> dict:
    try:
        return _State.registry.register(req.name, req.band_key)
    except RegistryError as e:
        raise HTTPException(400, str(e))


@router.get("/agents")
def list_agents() -> list[dict]:
    return _State.registry.list_agents()


@router.get("/agents/{name}/key")
def agent_key(name: str) -> dict:
    """Returns the agent's Band key. Localhost/team-network trust model."""
    try:
        return {"name": name, "band_key": _State.registry.key_for(name)}
    except RegistryError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------- deliberative vote flow


@router.post("/deliberations")
def open_deliberation() -> dict:
    session = DeliberationSession(id=uuid4().hex[:8])
    _State.sessions[session.id] = session
    return {"id": session.id, "phase": session.phase}


def _session(sid: str) -> DeliberationSession:
    s = _State.sessions.get(sid)
    if s is None:
        raise HTTPException(404, f"unknown deliberation: {sid}")
    return s


@router.post("/deliberations/{sid}/position")
def submit_position(sid: str, p: PositionChange) -> dict:
    """The entry point for each agent: the position change it wants. Nothing more."""
    s = _session(sid)
    if s.phase != Phase.OPEN:
        raise HTTPException(409, f"positions closed (phase={s.phase})")
    s.proposals.append(p)
    delib.post_position(_State.get_room(), p)
    return {"phase": s.phase, "proposals": len(s.proposals)}


@router.post("/deliberations/{sid}/case")
def submit_case(sid: str, c: Case) -> dict:
    s = _session(sid)
    if s.phase == Phase.OPEN:
        s.phase = Phase.CASES  # first case closes the position window
    if s.phase != Phase.CASES:
        raise HTTPException(409, f"cases closed (phase={s.phase})")
    if not any(p.agent == c.agent and p.ticker == c.ticker for p in s.proposals):
        raise HTTPException(400, "file a position before arguing it")
    s.cases.append(c)
    delib.post_case(_State.get_room(), c)
    return {"phase": s.phase, "cases": len(s.cases)}


@router.post("/deliberations/{sid}/rebuttal")
def submit_rebuttal(sid: str, r: RebuttalMsg) -> dict:
    s = _session(sid)
    if s.phase == Phase.CASES:
        s.phase = Phase.REBUTTALS
    if s.phase != Phase.REBUTTALS:
        raise HTTPException(409, f"rebuttals closed (phase={s.phase})")
    s.rebuttals.append(r)
    delib.post_rebuttal(_State.get_room(), r)
    return {"phase": s.phase, "rebuttals": len(s.rebuttals)}


@router.post("/deliberations/{sid}/verdict", response_model=Verdict)
def close_and_judge(sid: str) -> Verdict:
    """PM calls this to end the debate: judge scores the cases, credibility
    weighs the agents, proposals blend into the desk's final positions."""
    s = _session(sid)
    if s.phase == Phase.CLOSED:
        return s.verdict  # idempotent
    if not s.proposals:
        raise HTTPException(400, "no positions were filed")
    return delib.decide(s, _State.judge, _State.record, _State.get_room())


@router.get("/deliberations/{sid}", response_model=DeliberationSession)
def get_deliberation(sid: str) -> DeliberationSession:
    return _session(sid)


# ------------------------------------------------------------- track record


class OutcomeReport(BaseModel):
    agent: str
    # Signed hit in [-1, 1]: proposed direction · realized move (§5 attribution).
    score: float


@router.post("/outcomes")
def report_outcome(req: OutcomeReport) -> dict:
    """The evaluator reports each agent's realized outcome after the window;
    poor performance lowers the credibility used in future verdicts."""
    cred = _State.record.record_outcome(req.agent, req.score)
    return {"agent": req.agent, "credibility": cred}


@router.get("/credibility")
def credibility() -> dict:
    return _State.record.snapshot()


# ------------------------------------------------------------ floor & memo


@router.get("/floor")
def floor_transcript() -> list[dict]:
    return [
        {"sender": m.sender, "text": m.text, "mentions": m.mentions}
        for m in _State.get_room().history()
    ]


# ----------------------------------------------- simple weighted-tally mode


class StandPatAnalyst:
    def __init__(self, analyst_id: AnalystId, canned: Rebuttal | None) -> None:
        self.id = analyst_id
        self._canned = canned

    def rebut(self, challenge: Challenge, own_vote: Vote) -> Rebuttal:
        return self._canned or Rebuttal(
            analyst=self.id, ticker=challenge.ticker, text="Standing by my read."
        )


class CycleRequest(BaseModel):
    votes: list[Vote]
    weights: dict[AnalystId, float] | None = None
    rebuttals: list[Rebuttal] = []
    config: TallyConfig | None = None


@router.post("/cycle", response_model=DecisionMemo)
def run_cycle(req: CycleRequest) -> DecisionMemo:
    if not req.votes:
        raise HTTPException(400, "no votes submitted")
    weights = req.weights or _State.weights
    by_analyst: dict[AnalystId, list[Vote]] = {}
    for v in req.votes:
        by_analyst.setdefault(v.analyst, []).append(v)
    canned = {r.analyst: r for r in req.rebuttals}
    analysts = {a: StandPatAnalyst(a, canned.get(a)) for a in AnalystId}

    memo = run_vote_cycle(
        cycle_id=uuid4().hex[:8],
        votes_by_analyst=by_analyst,
        analysts=analysts,
        weights=weights,
        room=_State.get_room(),
        config=req.config,
    )
    _State.last_memo = memo
    return memo


@router.get("/memo", response_model=DecisionMemo)
def latest_memo() -> DecisionMemo:
    if _State.last_memo is None:
        raise HTTPException(404, "no cycle has run yet")
    return _State.last_memo


def create_app() -> FastAPI:
    """Standalone dev app (CORS wide open for the local React dashboard)."""
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="DeltaDesk voting floor (standalone)")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(router)
    return app
