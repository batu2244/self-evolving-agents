"""Optional Claude-powered slot extraction for the onboarding chat.

If ANTHROPIC_API_KEY is set, the concierge extracts the risk envelope from
free conversation with Claude (structured output). On any failure — no key,
network error, malformed output — callers fall back to the deterministic
rules in `chat.py`, so the chat never breaks without a key.
"""

import json
import os

from app.onboarding.chat import (
    CAPITAL_MAX,
    CAPITAL_MIN,
    TARGET_MAX,
    TARGET_MIN,
    Extraction,
    Slots,
)

_MODEL = "claude-opus-4-8"

_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {
            "anyOf": [
                {"type": "string", "enum": ["conservative", "balanced", "aggressive"]},
                {"type": "null"},
            ]
        },
        "market": {
            "anyOf": [{"type": "string", "enum": ["us", "eu", "pl", "crypto"]}, {"type": "null"}]
        },
        "capital_usd": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "target_return_pct": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "defaults_requested": {"type": "boolean"},
    },
    "required": ["risk_level", "market", "capital_usd", "target_return_pct", "defaults_requested"],
    "additionalProperties": False,
}

_SYSTEM = """You extract a trading risk envelope from an onboarding conversation for a paper-trading desk.

Slots:
- risk_level: conservative | balanced | aggressive. Infer from stated appetite OR from what the user says they'd buy (NVDA/TSLA/SOL-style momentum names read aggressive; KO/JNJ/Nestlé-style names read conservative; broad large caps read balanced).
- market: us | eu | pl | crypto. Infer from named assets if not stated (pl = Warsaw Stock Exchange / GPW; XTB, CD Projekt, Allegro, PKO read pl).
- capital_usd: committed paper capital in USD.
- target_return_pct: how much they want to beat the benchmark tracker by, in % per QUARTER.
- defaults_requested: true only if the user asks you to decide for them.

Return the user's CURRENT intent for each slot across the whole conversation — later statements override earlier ones. Use null for anything not yet expressed. Never invent values."""


def _get_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    from anthropic import AsyncAnthropic

    return AsyncAnthropic()


async def llm_extract(history: list[dict[str, str]], slots: Slots) -> Extraction | None:
    """history: [{"role": "user"|"assistant", "content": str}, ...]"""
    client = _get_client()
    if client is None:
        return None
    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM
            + f"\n\nSlots already captured: {json.dumps(vars(slots), default=str)}",
            messages=[
                {"role": m["role"], "content": m["content"]} for m in history[-12:]
            ],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
    except Exception:
        return None

    ex = Extraction()
    if data.get("risk_level") in ("conservative", "balanced", "aggressive"):
        ex.risk_level = data["risk_level"]
    if data.get("market") in ("us", "eu", "crypto"):
        ex.market = data["market"]
    capital = data.get("capital_usd")
    if isinstance(capital, (int, float)) and CAPITAL_MIN <= capital <= CAPITAL_MAX:
        ex.capital_usd = float(capital)
    target = data.get("target_return_pct")
    if isinstance(target, (int, float)) and TARGET_MIN <= target <= TARGET_MAX:
        ex.target_return_pct = float(target)
    ex.defaults_requested = bool(data.get("defaults_requested"))
    return ex
