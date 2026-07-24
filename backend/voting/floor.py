"""The trading floor (solution-design §3).

Orchestrates one vote cycle in a Band room:

  1. every analyst posts its VOTE messages (@mentioning the PM)
  2. PM tallies the weighted vote
  3. if a confident dissenter lost, a CHALLENGE round runs: the dissenter
     @mentions the majority, each challenged analyst posts one rebuttal
     (optionally revising its confidence), and the PM re-tallies
  4. PM posts the DECISION memo — the prediction tomorrow's delta will grade

The floor is transport-agnostic (RoomTransport): Band live, in-memory for
tests/replay. It reads weights, never writes them — Loop 1 belongs to the
evaluator.
"""

from __future__ import annotations

from typing import Callable, Protocol

from . import messages as fmt
from .transport import RoomTransport
from .tally import Weights, apply_rebuttal_revisions, find_dissenter, tally_ticker
from .types import (
    AnalystId,
    Challenge,
    DecisionMemo,
    Direction,
    Rebuttal,
    TallyConfig,
    TickerDecision,
    Vote,
)

PM_NAME = "pm"


class Analyst(Protocol):
    """What the floor needs from an analyst agent. The Guild llmAgents
    implement this; tests and the replay harness use scripted stand-ins."""

    id: AnalystId

    def rebut(self, challenge: Challenge, own_vote: Vote) -> Rebuttal:
        """One rebuttal to a challenge naming this analyst. May revise confidence."""
        ...


def run_vote_cycle(
    cycle_id: str,
    votes_by_analyst: dict[AnalystId, list[Vote]],
    analysts: dict[AnalystId, Analyst],
    weights: Weights,
    room: RoomTransport,
    config: TallyConfig | None = None,
    narrator: Callable[[list[TickerDecision]], str] | None = None,
) -> DecisionMemo:
    config = config or TallyConfig()

    # 1. Votes hit the floor — on the record, @routed to the PM.
    all_votes: list[Vote] = []
    for analyst_id, votes in votes_by_analyst.items():
        for vote in votes:
            room.post(analyst_id.value, fmt.format_vote(vote), mentions=[PM_NAME])
            all_votes.append(vote)

    # 2–3. Tally per ticker, with at most one challenge round each.
    decisions: list[TickerDecision] = []
    for ticker in sorted({v.ticker for v in all_votes}):
        decision = tally_ticker(ticker, all_votes, weights, config)
        dissent = find_dissenter(decision, config)
        if dissent and analysts:
            decision = _run_challenge_round(
                decision, dissent, all_votes, analysts, weights, room, config
            )
        decisions.append(decision)

    # 4. The decision memo — the falsifiable claim the evaluator grades.
    memo = DecisionMemo(
        cycle_id=cycle_id,
        decisions=decisions,
        weights=weights,
        narrative=(narrator or _default_narrative)(decisions),
    )
    room.post(PM_NAME, fmt.format_memo(memo), mentions=["evaluator"])
    return memo


def _run_challenge_round(
    decision: TickerDecision,
    dissent: Vote,
    all_votes: list[Vote],
    analysts: dict[AnalystId, Analyst],
    weights: Weights,
    room: RoomTransport,
    config: TallyConfig,
) -> TickerDecision:
    ticker = decision.ticker
    challenged = [
        v.analyst
        for v in decision.votes
        if v.analyst != dissent.analyst and v.direction == decision.direction
    ]
    challenge = Challenge(
        dissenter=dissent.analyst,
        ticker=ticker,
        objection=dissent.rationale,
        challenged=challenged,
    )
    room.post(
        dissent.analyst.value,
        fmt.format_challenge(challenge, {a.value: a.value for a in challenged}),
        mentions=[a.value for a in challenged] + [PM_NAME],
    )

    revisions: dict[AnalystId, float] = {}
    for analyst_id in challenged:
        analyst = analysts.get(analyst_id)
        own_vote = next(
            v for v in decision.votes if v.analyst == analyst_id and v.ticker == ticker
        )
        if analyst is None:
            continue
        rebuttal = analyst.rebut(challenge, own_vote)
        challenge.rebuttals.append(rebuttal)
        room.post(analyst_id.value, fmt.format_rebuttal(rebuttal), mentions=[PM_NAME])
        if rebuttal.revised_confidence is not None:
            revisions[analyst_id] = rebuttal.revised_confidence

    revised_votes = apply_rebuttal_revisions(all_votes, revisions, ticker)
    retallied = tally_ticker(ticker, revised_votes, weights, config)
    retallied.challenge = challenge
    return retallied


def _default_narrative(decisions: list[TickerDecision]) -> str:
    traded = [d for d in decisions if d.direction != Direction.HOLD]
    if not traded:
        return "No weighted majority formed — the desk holds and keeps its powder dry."
    parts = []
    for d in traded:
        how = "unanimously" if d.unanimous else f"on a {d.vote_share:.0%} majority"
        contested = " after a contested challenge round" if d.challenge else ""
        parts.append(
            f"{d.direction.value.upper()} {d.ticker} at {d.size_factor:.0%} size, "
            f"{how}{contested}"
        )
    return "Desk decision: " + "; ".join(parts) + "."
