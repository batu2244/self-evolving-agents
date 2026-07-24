"""Argument judges for the deliberation verdict.

ClaudeJudge: an LLM evaluation agent reads the whole debate — every
position, case, and rebuttal — and scores each case's argument quality
0..1 via structured output. HeuristicJudge is the deterministic fallback
(no API key, tests, replay harness), so the desk never blocks on an LLM.

Argument score is deliberately separate from credibility: the judge grades
*this debate's* reasoning; the track record (track_record.py) grades the
agent's history. The verdict multiplies the two.
"""

from __future__ import annotations

import os
import re

from pydantic import BaseModel, Field

from .deliberation import ArgumentScore, Case, PositionChange, RebuttalMsg

JUDGE_MODEL = "claude-opus-4-8"

JUDGE_SYSTEM = """\
You are the evaluation agent on an autonomous paper-trading desk. Analyst
agents have proposed position changes and argued for them. Score each case's
ARGUMENT QUALITY from 0.0 to 1.0. Judge only the reasoning in this debate —
not the agent's history (the desk weighs that separately) and not whether
you personally agree with the trade.

Reward: specific, falsifiable evidence; correct use of data; arguments that
survive the rebuttals filed against them; acknowledging risk.
Punish: vague hype, circular reasoning, claims contradicted by a rebuttal
left unanswered, overconfidence without evidence.
Score every (agent, ticker) case exactly once."""


class _ScoreSheet(BaseModel):
    scores: list[ArgumentScore] = Field(min_length=1)


def _debate_transcript(
    proposals: list[PositionChange], cases: list[Case], rebuttals: list[RebuttalMsg]
) -> str:
    lines = ["## Proposed position changes"]
    for p in proposals:
        lines.append(f"- {p.agent} on {p.ticker}: {p.current:+.2f} -> {p.target:+.2f}")
    lines.append("\n## Cases")
    for c in cases:
        lines.append(f"### {c.agent} on {c.ticker}\n{c.argument}")
    if rebuttals:
        lines.append("\n## Rebuttals")
        for r in rebuttals:
            lines.append(f"### {r.agent} vs {r.against} on {r.ticker}\n{r.argument}")
    return "\n".join(lines)


class ClaudeJudge:
    def __init__(self, model: str = JUDGE_MODEL) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def score(
        self,
        proposals: list[PositionChange],
        cases: list[Case],
        rebuttals: list[RebuttalMsg],
    ) -> list[ArgumentScore]:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=JUDGE_SYSTEM,
            messages=[
                {"role": "user", "content": _debate_transcript(proposals, cases, rebuttals)}
            ],
            output_format=_ScoreSheet,
        )
        return response.parsed_output.scores


class HeuristicJudge:
    """Deterministic scorer: rewards specificity (numbers, percentages,
    ticker references) and substance; flat-scores everything else. Crude by
    design — it exists so the pipeline runs without an LLM."""

    def score(
        self,
        proposals: list[PositionChange],
        cases: list[Case],
        rebuttals: list[RebuttalMsg],
    ) -> list[ArgumentScore]:
        rebutted = {(r.against, r.ticker) for r in rebuttals}
        answered = {(r.agent, r.ticker) for r in rebuttals}
        out = []
        for c in cases:
            s = 0.4
            numbers = len(re.findall(r"\d+(?:\.\d+)?%?", c.argument))
            s += min(0.3, 0.06 * numbers)  # concrete figures
            s += min(0.1, len(c.argument) / 2000)  # substance, capped
            if (c.agent, c.ticker) in rebutted and (c.agent, c.ticker) not in answered:
                s -= 0.15  # took a hit and never answered
            out.append(
                ArgumentScore(
                    agent=c.agent,
                    ticker=c.ticker,
                    score=round(max(0.05, min(1.0, s)), 3),
                    reasoning="heuristic: specificity/substance/rebuttal-response",
                )
            )
        return out


def default_judge():
    """ClaudeJudge when a key is configured, heuristic otherwise."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeJudge()
        except Exception:
            return HeuristicJudge()
    return HeuristicJudge()
