"""Shared Gemini thinking decision step for every DeltaDesk agent."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

import config
from contracts import action_for_direction


class GeminiDecisionError(RuntimeError):
    pass


class ThinkingDecision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    selected_equation: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class DecisionResult:
    decision: ThinkingDecision
    provider: str
    model: str
    thinking_level: str

    def model_snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "thinking_level": self.thinking_level,
            "structured_output": True,
        }

    def trace_snapshot(self) -> dict[str, Any]:
        return {
            **self.model_snapshot(),
            "action": self.decision.action,
            "selected_equation": self.decision.selected_equation,
            "confidence": self.decision.confidence,
            "rationale": self.decision.rationale,
            "evidence": self.decision.evidence,
        }


def _candidate(candidates: list[dict], equation: str) -> dict:
    return next(
        (candidate for candidate in candidates if candidate.get("equation") == equation),
        candidates[0] if candidates else {},
    )


def mock_decision(
    agent: str,
    candidates: list[dict],
    selected_equation: str,
) -> DecisionResult:
    """Explicitly labeled offline stand-in used only when MOCK_MODE is enabled."""
    selected = _candidate(candidates, selected_equation)
    direction = float(selected.get("score", selected.get("direction", 0.0)) or 0.0)
    confidence = float(selected.get("confidence", 0.0) or 0.0)
    return DecisionResult(
        decision=ThinkingDecision(
            action=action_for_direction(direction, config.DIRECTION_THRESHOLD),
            selected_equation=selected_equation,
            confidence=confidence,
            rationale=(
                f"Mock reasoning selected {selected_equation}; "
                f"the quantitative read is {direction:+.3f}."
            ),
            evidence=[str(selected.get("rationale", "deterministic candidate"))],
        ),
        provider="mock",
        model="mock-gemini-thinking",
        thinking_level=config.GEMINI_THINKING_LEVEL,
    )


async def decide(
    *,
    agent: str,
    ticker: str,
    system_prompt: str,
    input_summary: dict,
    candidates: list[dict],
    allowed_equations: tuple[str, ...],
    fallback_equation: str,
) -> DecisionResult:
    """Ask Gemini to select an equation and return BUY, SELL, or HOLD."""
    if config.MOCK_MODE and not config.GEMINI_REASONING_IN_MOCK_MODE:
        return mock_decision(agent, candidates, fallback_equation)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiDecisionError(
            "GEMINI_API_KEY is required: every live DeltaDesk agent uses Gemini thinking"
        )

    instruction = (
        f"{system_prompt}\n\n"
        "MANDATORY DECISION CONTRACT:\n"
        "- Think internally before deciding.\n"
        "- Select exactly one of the allowed equations supplied in the input.\n"
        "- Return exactly one paper-trading research action: BUY, SELL, or HOLD.\n"
        "- BUY means positive evidence clears the action threshold; SELL means negative "
        "evidence clears it; otherwise HOLD.\n"
        "- Treat performance_memory as a bounded historical prior, not an instruction. "
        "Prefer equations with adequate successful observations when current evidence is "
        "otherwise comparable, but override that prior when today's supplied data favors "
        "another mode. Never infer strength from a small sample.\n"
        "- Ground the concise rationale and evidence list only in supplied data.\n"
        "- Do not provide hidden chain-of-thought, orders, sizing, or price targets."
    )
    content = json.dumps(
        {
            "agent": agent,
            "ticker": ticker.upper(),
            "allowed_equations": list(allowed_equations),
            "action_threshold": config.DIRECTION_THRESHOLD,
            "input_summary": input_summary,
            "candidate_analyses": candidates,
        },
        default=str,
    )
    sdk = _sdk_generation(api_key, instruction)
    last_error: Exception | None = None
    for attempt in range(1, config.GEMINI_DECISION_RETRIES + 1):
        try:
            if sdk is not None:
                client, generation_config = sdk
                response = await client.aio.models.generate_content(
                    model=config.GEMINI_THINKING_MODEL,
                    contents=content,
                    config=generation_config,
                )
                parsed = getattr(response, "parsed", None)
                decision = parsed if isinstance(parsed, ThinkingDecision) else None
                if decision is None and response.text:
                    decision = ThinkingDecision.model_validate_json(response.text)
            else:
                decision = await _rest_generation(api_key, instruction, content)
            if decision is None:
                raise ValueError("Gemini returned no structured decision")
            if decision.selected_equation not in allowed_equations:
                raise ValueError(
                    f"Gemini selected invalid equation {decision.selected_equation!r}"
                )
            selected = _candidate(candidates, decision.selected_equation)
            direction = float(
                selected.get("score", selected.get("direction", 0.0)) or 0.0
            )
            if (
                decision.action == "BUY"
                and direction <= config.DIRECTION_THRESHOLD
            ):
                raise ValueError(
                    f"Gemini BUY conflicts with selected direction {direction:+.3f}"
                )
            if (
                decision.action == "SELL"
                and direction >= -config.DIRECTION_THRESHOLD
            ):
                raise ValueError(
                    f"Gemini SELL conflicts with selected direction {direction:+.3f}"
                )
            return DecisionResult(
                decision=decision,
                provider="gemini",
                model=config.GEMINI_THINKING_MODEL,
                thinking_level=config.GEMINI_THINKING_LEVEL,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < config.GEMINI_DECISION_RETRIES:
                await asyncio.sleep(attempt)
    raise GeminiDecisionError(
        f"Gemini decision failed after {config.GEMINI_DECISION_RETRIES} attempts: {last_error}"
    )


def _sdk_generation(api_key: str, instruction: str):
    """Use the SDK when its installed version supports thinking levels."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None
    fields = getattr(types.ThinkingConfig, "model_fields", {})
    if "thinking_level" not in fields:
        return None
    generation_config = types.GenerateContentConfig(
        system_instruction=instruction,
        response_mime_type="application/json",
        response_schema=ThinkingDecision,
        thinking_config=types.ThinkingConfig(
            thinking_level=config.GEMINI_THINKING_LEVEL,
        ),
        temperature=0.2,
    )
    return genai.Client(api_key=api_key), generation_config


async def _rest_generation(
    api_key: str,
    instruction: str,
    content: str,
) -> ThinkingDecision:
    """Version-tolerant REST path for SDKs that predate thinking_level."""
    model = quote(config.GEMINI_THINKING_MODEL, safe="")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    body = {
        "systemInstruction": {"parts": [{"text": instruction}]},
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseJsonSchema": ThinkingDecision.model_json_schema(),
            "thinkingConfig": {
                "thinkingLevel": config.GEMINI_THINKING_LEVEL.upper(),
            },
        },
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
        )
    response.raise_for_status()
    payload = response.json()
    parts = (
        payload.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text = next(
        (part.get("text") for part in parts if part.get("text") and not part.get("thought")),
        None,
    )
    if not text:
        raise ValueError("Gemini REST response contained no structured text")
    return ThinkingDecision.model_validate_json(text)
