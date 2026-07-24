"""Onboarding API routes.

POST /api/onboarding/chat      — concierge chat turn: message in, reply + learned envelope out
POST /api/onboarding/universe  — envelope in, proposed tracker + universe out (direct)
POST /api/onboarding/envelope  — ratify the envelope + the stocks selected for the committee
GET  /api/onboarding/envelope  — current desk constitution, 404 if not configured

On ratify, each selected symbol becomes a mandate for the trading committee —
the desk (analysts, PM, evaluator) reads them from `get_committee_mandates()`.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.onboarding.chat import Slots, respond, rule_extract
from app.onboarding.llm import llm_extract
from app.onboarding.schemas import (
    Market,
    RiskEnvelope,
    RiskLevel,
    UniverseProposal,
)
from app.onboarding.universe import propose_universe

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# In-memory store for the hackathon; the desk reads this as its constitution.
_state: dict[str, object] = {}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class PartialEnvelope(BaseModel):
    risk_level: RiskLevel | None = Field(default=None, alias="riskLevel")
    target_return_pct: float | None = Field(default=None, alias="targetReturnPct")
    capital_usd: float | None = Field(default=None, alias="capitalUsd")
    market: Market | None = None

    model_config = {"populate_by_name": True}


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    slots: PartialEnvelope = PartialEnvelope()


class ChatResponse(BaseModel):
    reply: str
    slots: PartialEnvelope
    suggestions: list[str]
    proposal: UniverseProposal | None = None
    done: bool


class RatifyRequest(BaseModel):
    envelope: RiskEnvelope
    proposal: UniverseProposal
    # symbols from the proposal the user picked — each one is forwarded to the
    # trading committee as a mandate
    selected: list[str] = Field(min_length=1)


@router.post("/chat", response_model=ChatResponse, response_model_by_alias=True)
async def chat(body: ChatRequest) -> ChatResponse:
    last_user = next((m for m in reversed(body.messages) if m.role == "user"), None)
    if last_user is None:
        raise HTTPException(status_code=422, detail="No user message in conversation")

    slots = Slots(
        risk_level=body.slots.risk_level,
        target_return_pct=body.slots.target_return_pct,
        capital_usd=body.slots.capital_usd,
        market=body.slots.market,
    )
    # Claude extraction when a key is configured; deterministic rules otherwise.
    extraction = await llm_extract([m.model_dump() for m in body.messages], slots)
    if extraction is not None:
        # rules still contribute ticker mentions and relative adjustments
        rules = rule_extract(last_user.content)
        extraction.tickers = rules.tickers
        extraction.inferred_risk = extraction.risk_level or rules.inferred_risk
        extraction.inferred_market = extraction.market or rules.inferred_market
        if extraction.capital_usd is None:
            extraction.capital_multiplier = rules.capital_multiplier

    turn = respond(last_user.content, slots, extraction)
    return ChatResponse(
        reply=turn.reply,
        slots=PartialEnvelope(
            risk_level=turn.slots.risk_level,
            target_return_pct=turn.slots.target_return_pct,
            capital_usd=turn.slots.capital_usd,
            market=turn.slots.market,
        ),
        suggestions=turn.suggestions,
        proposal=turn.proposal,
        done=turn.done,
    )


@router.post("/universe", response_model=UniverseProposal, response_model_by_alias=True)
async def universe(envelope: RiskEnvelope) -> UniverseProposal:
    # Simulate the universe-selector agent's screening latency so the
    # frontend's loading state is a real, observable state.
    await asyncio.sleep(0.7)
    return propose_universe(envelope)


@router.post("/envelope", status_code=201)
async def ratify(body: RatifyRequest) -> dict[str, object]:
    known = {a.symbol for a in body.proposal.universe}
    unknown = [s for s in body.selected if s not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Selected symbols not in the screened universe: {', '.join(unknown)}",
        )
    _state["envelope"] = body.envelope
    _state["proposal"] = body.proposal
    _state["selected"] = body.selected
    return {"status": "ratified", "committee_mandates": body.selected}


@router.get("/envelope", response_model_by_alias=True)
async def current() -> RatifyRequest:
    if "envelope" not in _state:
        raise HTTPException(status_code=404, detail="Desk not configured")
    return RatifyRequest(
        envelope=_state["envelope"],  # type: ignore[arg-type]
        proposal=_state["proposal"],  # type: ignore[arg-type]
        selected=_state["selected"],  # type: ignore[arg-type]
    )


def get_committee_mandates() -> list[str]:
    """Public hook for the trading module: the stocks the user staffed the
    committee with. Empty until the desk is ratified."""
    return list(_state.get("selected", []))  # type: ignore[arg-type]
