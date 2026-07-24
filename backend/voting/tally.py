"""Weighted vote tally (solution-design §3).

Rules, verbatim from the design doc:
  - a weighted majority is required to trade at all
  - unanimity earns full position size
  - a split vote cuts size (or forces a hold when no majority forms)

Pure functions only — the Band room and challenge round live in floor.py.
"""

from __future__ import annotations

from collections import defaultdict

from .types import (
    SIZE_CLASS_FACTOR,
    AnalystId,
    Direction,
    TallyConfig,
    TickerDecision,
    Vote,
)

Weights = dict[AnalystId, float]


def normalize_weights(weights: Weights, voters: set[AnalystId]) -> Weights:
    """Restrict to analysts who actually voted and renormalize to sum 1."""
    present = {a: w for a, w in weights.items() if a in voters}
    total = sum(present.values())
    if total <= 0:
        # Degenerate weights — fall back to equal say rather than crash the desk.
        return {a: 1 / len(present) for a in present} if present else {}
    return {a: w / total for a, w in present.items()}


def tally_ticker(
    ticker: str,
    votes: list[Vote],
    weights: Weights,
    config: TallyConfig,
) -> TickerDecision:
    """Tally one ticker's votes into a decision.

    Conviction mass for a direction = sum of (weight x confidence) of its
    voters. The winning direction must clear ``majority_threshold`` of the
    total conviction mass (hold votes count toward the total, so a
    low-conviction majority against a strong hold camp still holds).
    """
    votes = [v for v in votes if v.ticker == ticker]
    if not votes:
        return TickerDecision(
            ticker=ticker, direction=Direction.HOLD, size_factor=0.0,
            vote_share=1.0, unanimous=True, votes=[],
        )

    w = normalize_weights(weights, {v.analyst for v in votes})
    mass: dict[Direction, float] = defaultdict(float)
    for v in votes:
        mass[v.direction] += w.get(v.analyst, 0.0) * v.confidence
    total = sum(mass.values())

    if total <= 0:
        return TickerDecision(
            ticker=ticker, direction=Direction.HOLD, size_factor=0.0,
            vote_share=1.0, unanimous=False, votes=votes,
        )

    # Only buy/sell can win; hold is the default that must be beaten.
    actionable = {d: m for d, m in mass.items() if d != Direction.HOLD}
    winner = max(actionable, key=lambda d: actionable[d]) if actionable else Direction.HOLD
    share = mass[winner] / total if winner != Direction.HOLD else 1.0

    if winner == Direction.HOLD or share < config.majority_threshold:
        return TickerDecision(
            ticker=ticker, direction=Direction.HOLD, size_factor=0.0,
            vote_share=share, unanimous=False, votes=votes,
        )

    unanimous = all(v.direction == winner for v in votes)
    winning_votes = [v for v in votes if v.direction == winner]
    # Winners' own size-classes cap the position even on a sweep.
    size_cap = sum(
        w.get(v.analyst, 0.0) * SIZE_CLASS_FACTOR[v.size_class] for v in winning_votes
    ) / sum(w.get(v.analyst, 0.0) for v in winning_votes)

    size_factor = size_cap * (1.0 if unanimous else config.split_size_factor)
    return TickerDecision(
        ticker=ticker, direction=winner, size_factor=round(size_factor, 4),
        vote_share=round(share, 4), unanimous=unanimous, votes=votes,
    )


def tally_all(
    votes: list[Vote], weights: Weights, config: TallyConfig
) -> list[TickerDecision]:
    tickers = sorted({v.ticker for v in votes})
    return [tally_ticker(t, votes, weights, config) for t in tickers]


def find_dissenter(decision: TickerDecision, config: TallyConfig) -> Vote | None:
    """A confident analyst on the losing side of an actionable decision
    (or arguing for action against a hold) triggers a challenge round."""
    for v in decision.votes:
        if v.direction != decision.direction and v.confidence >= config.challenge_threshold:
            return v
    return None


def apply_rebuttal_revisions(
    votes: list[Vote], revisions: dict[AnalystId, float], ticker: str
) -> list[Vote]:
    """Return votes with challenged analysts' revised confidences applied."""
    out = []
    for v in votes:
        if v.ticker == ticker and v.analyst in revisions:
            out.append(v.model_copy(update={"confidence": revisions[v.analyst]}))
        else:
            out.append(v)
    return out
