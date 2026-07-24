"""Band room message formats.

Every structured message is human-readable prose (humans see everything in a
Band room — that's the point) followed by a fenced JSON block the PM and
evaluator parse. One format, both audiences.
"""

from __future__ import annotations

import json
import re

from .types import Challenge, DecisionMemo, Rebuttal, Vote

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

VOTE_TAG = "📊 VOTE"
CHALLENGE_TAG = "⚔️ CHALLENGE"
REBUTTAL_TAG = "🛡️ REBUTTAL"
MEMO_TAG = "📋 DECISION"


def _with_fence(header: str, body: str, payload: dict) -> str:
    return f"{header}\n{body}\n```json\n{json.dumps(payload)}\n```"


def format_vote(vote: Vote) -> str:
    header = (
        f"{VOTE_TAG} {vote.analyst.value} · {vote.ticker} · "
        f"{vote.direction.value.upper()} · conf={vote.confidence:.2f} · "
        f"size={vote.size_class.value}"
    )
    return _with_fence(header, vote.rationale, vote.model_dump(mode="json"))


def format_challenge(challenge: Challenge, mention_names: dict[str, str]) -> str:
    mentions = " ".join(f"@{mention_names[a.value]}" for a in challenge.challenged)
    header = (
        f"{CHALLENGE_TAG} {challenge.dissenter.value} objects on "
        f"{challenge.ticker} — {mentions}"
    )
    return _with_fence(
        header, challenge.objection,
        challenge.model_dump(mode="json", exclude={"rebuttals"}),
    )


def format_rebuttal(rebuttal: Rebuttal) -> str:
    revised = (
        f" · revised conf={rebuttal.revised_confidence:.2f}"
        if rebuttal.revised_confidence is not None
        else " · stands pat"
    )
    header = f"{REBUTTAL_TAG} {rebuttal.analyst.value} · {rebuttal.ticker}{revised}"
    return _with_fence(header, rebuttal.text, rebuttal.model_dump(mode="json"))


def format_memo(memo: DecisionMemo) -> str:
    lines = [f"{MEMO_TAG} cycle {memo.cycle_id}"]
    for d in memo.decisions:
        challenge_note = " (after challenge)" if d.challenge else ""
        lines.append(
            f"- {d.ticker}: {d.direction.value.upper()} "
            f"size={d.size_factor:.2f} share={d.vote_share:.2f}"
            f"{' UNANIMOUS' if d.unanimous else ''}{challenge_note}"
        )
    lines.append(memo.narrative)
    return _with_fence(lines[0], "\n".join(lines[1:]), memo.model_dump(mode="json"))


def parse_payload(text: str) -> dict | None:
    """Extract the JSON payload from any floor message; None if unstructured."""
    m = _JSON_FENCE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_vote(text: str) -> Vote | None:
    if not text.startswith(VOTE_TAG):
        return None
    payload = parse_payload(text)
    return Vote.model_validate(payload) if payload else None


def parse_rebuttal(text: str) -> Rebuttal | None:
    if not text.startswith(REBUTTAL_TAG):
        return None
    payload = parse_payload(text)
    return Rebuttal.model_validate(payload) if payload else None
