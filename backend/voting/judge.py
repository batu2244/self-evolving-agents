"""Argument judges for the deliberation verdict.

The LLM evaluation agent reads the whole debate — every stance, case, and
rebuttal — and scores each case's argument quality 0..1. Two backends:

  OpenRouterJudge  foundation-model research endpoint (OPENROUTER_API_KEY,
                   OpenAI-compatible chat completions) — the desk's default
  ClaudeJudge      direct Anthropic API (ANTHROPIC_API_KEY)

HeuristicJudge is the deterministic fallback (no keys, tests, replay
harness), so the desk never blocks on an LLM.

Argument score is deliberately separate from credibility: the judge grades
*this debate's* reasoning; the track record (track_record.py) grades the
agent's history. The verdict multiplies the two.
"""

from __future__ import annotations

import json
import os
import re

import httpx
from pydantic import BaseModel, Field

from .deliberation import ArgumentScore, Case, RebuttalMsg, Stance

def _load_env_file() -> None:
    """Pick up backend/.env (gitignored) without a python-dotenv dependency."""
    from pathlib import Path

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env_file()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
ANTHROPIC_MODEL = "claude-opus-4-8"

JUDGE_SYSTEM = """\
You are the evaluation agent on an autonomous paper-trading desk. Analyst
agents have each taken a binary stance — BUY or SELL a ticker — and argued
for it. Score each case's ARGUMENT QUALITY from 0.0 to 1.0. Judge only the
reasoning in this debate — not the agent's history (the desk weighs that
separately) and not which side you would personally take.

Reward: specific, falsifiable evidence; correct use of data; arguments that
survive the rebuttals filed against them; acknowledging risk.
Punish: vague hype, circular reasoning, claims contradicted by a rebuttal
left unanswered, overconfidence without evidence.
Score every (agent, ticker) case exactly once.

Respond with ONLY a JSON object, no prose, in this exact shape:
{"scores": [{"agent": "...", "ticker": "...", "score": 0.0, "reasoning": "..."}]}"""


class _ScoreSheet(BaseModel):
    scores: list[ArgumentScore] = Field(min_length=1)


def _debate_transcript(
    stances: list[Stance], cases: list[Case], rebuttals: list[RebuttalMsg]
) -> str:
    lines = ["## Stances"]
    for s in stances:
        lines.append(f"- {s.agent}: {s.side.value.upper()} {s.ticker}")
    lines.append("\n## Cases")
    for c in cases:
        lines.append(f"### {c.agent} on {c.ticker}\n{c.argument}")
    if rebuttals:
        lines.append("\n## Rebuttals")
        for r in rebuttals:
            lines.append(f"### {r.agent} vs {r.against} on {r.ticker}\n{r.argument}")
    return "\n".join(lines)


class OpenRouterJudge:
    """LLM judge over OpenRouter's OpenAI-compatible endpoint."""

    def __init__(self, model: str = OPENROUTER_MODEL, api_key: str | None = None) -> None:
        self._model = model
        self._key = api_key or os.environ["OPENROUTER_API_KEY"]

    def score(
        self,
        stances: list[Stance],
        cases: list[Case],
        rebuttals: list[RebuttalMsg],
    ) -> list[ArgumentScore]:
        r = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self._model,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": _debate_transcript(stances, cases, rebuttals)},
                ],
            },
            timeout=60.0,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        # Tolerate a stray code fence around the JSON.
        m = re.search(r"\{.*\}", content, re.DOTALL)
        sheet = _ScoreSheet.model_validate(json.loads(m.group(0) if m else content))
        return sheet.scores


class ClaudeJudge:
    def __init__(self, model: str = ANTHROPIC_MODEL) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def score(
        self,
        stances: list[Stance],
        cases: list[Case],
        rebuttals: list[RebuttalMsg],
    ) -> list[ArgumentScore]:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=JUDGE_SYSTEM,
            messages=[
                {"role": "user", "content": _debate_transcript(stances, cases, rebuttals)}
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
        stances: list[Stance],
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
    """OpenRouter when configured, then direct Anthropic, else heuristic."""
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            return OpenRouterJudge()
        except Exception:
            pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeJudge()
        except Exception:
            pass
    return HeuristicJudge()
