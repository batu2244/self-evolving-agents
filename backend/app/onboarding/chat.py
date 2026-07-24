"""Conversational onboarding engine.

Deterministic slot extraction + reply composition. The concierge learns the
risk envelope (risk level, market, capital, target) from free text — including
inference from popular tickers the user says they'd buy. Works with no API
key; `llm.py` can override extraction with Claude when a key is present.
"""

import re
from dataclasses import dataclass, field

from app.onboarding.schemas import Market, RiskEnvelope, RiskLevel, UniverseProposal
from app.onboarding.universe import propose_universe

CAPITAL_MIN = 1_000
CAPITAL_MAX = 10_000_000
TARGET_MIN = 0.25
TARGET_MAX = 25.0

DEFAULT_TARGET: dict[RiskLevel, float] = {
    "conservative": 1.0,
    "balanced": 2.0,
    "aggressive": 4.0,
}

# symbol -> (display name, market, risk tilt). Kept to unambiguous tokens.
POPULAR: dict[str, tuple[str, Market, RiskLevel]] = {
    "NVDA": ("NVIDIA", "us", "aggressive"),
    "TSLA": ("Tesla", "us", "aggressive"),
    "AMD": ("AMD", "us", "aggressive"),
    "META": ("Meta", "us", "aggressive"),
    "AAPL": ("Apple", "us", "balanced"),
    "MSFT": ("Microsoft", "us", "balanced"),
    "GOOGL": ("Alphabet", "us", "balanced"),
    "AMZN": ("Amazon", "us", "balanced"),
    "JPM": ("JPMorgan", "us", "balanced"),
    "KO": ("Coca-Cola", "us", "conservative"),
    "JNJ": ("Johnson & Johnson", "us", "conservative"),
    "PG": ("Procter & Gamble", "us", "conservative"),
    "WMT": ("Walmart", "us", "conservative"),
    "ASML": ("ASML", "eu", "aggressive"),
    "ADYEN": ("Adyen", "eu", "aggressive"),
    "SAP": ("SAP", "eu", "balanced"),
    "LVMH": ("LVMH", "eu", "balanced"),
    "NESN": ("Nestlé", "eu", "conservative"),
    "BTC": ("Bitcoin", "crypto", "balanced"),
    "ETH": ("Ethereum", "crypto", "balanced"),
    "SOL": ("Solana", "crypto", "aggressive"),
    "DOGE": ("Dogecoin", "crypto", "aggressive"),
}

_NAME_ALIASES: dict[str, str] = {
    "nvidia": "NVDA", "tesla": "TSLA", "meta": "META", "apple": "AAPL",
    "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "jpmorgan": "JPM", "coca-cola": "KO", "coca cola": "KO",
    "walmart": "WMT", "nestle": "NESN", "nestlé": "NESN",
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "dogecoin": "DOGE",
}

_RISK_PATTERNS: list[tuple[RiskLevel, str]] = [
    ("conservative", r"conservativ|\bsafe(?:ty)?\b|cautious|careful|preserv|low[ -]risk|defensiv|don'?t lose"),
    ("aggressive", r"aggressiv|risky|high[ -]risk|\byolo\b|\bmoon\b|all[ -]in|\bbold\b|gambl|maximum risk|chase"),
    ("balanced", r"balanc|moderate|\bmedium\b|middle|somewhere in between"),
]

_MARKET_PATTERNS: list[tuple[Market, str]] = [
    ("crypto", r"crypto|bitcoin|\bbtc\b|ethereum|\beth\b|blockchain|\bdefi\b|stablecoin|\bcoins?\b|\btokens?\b"),
    ("eu", r"\beurope(?:an)?\b|\beu\b|stoxx|\bdax\b|xetra|euronext"),
    ("us", r"u\.s\.|\busa\b|\bamerican?\b|s&p|nasdaq|nyse|\bdow\b|united states|wall street|\bus (?:stocks|equities|market|tech)\b"),
]

_DEFAULTS_PATTERN = (
    r"pick for me|you (?:choose|decide|pick)|up to you|whatever|surprise me|"
    r"\bidk\b|don'?t know|\bdunno\b|no idea|just set|defaults?\b"
)

MARKET_LABEL: dict[Market, str] = {"us": "US equities", "eu": "EU equities", "crypto": "crypto"}


@dataclass
class Slots:
    risk_level: RiskLevel | None = None
    target_return_pct: float | None = None
    capital_usd: float | None = None
    market: Market | None = None

    def complete(self) -> bool:
        return None not in (self.risk_level, self.capital_usd, self.market)

    def to_envelope(self) -> RiskEnvelope:
        assert self.risk_level and self.capital_usd and self.market
        target = self.target_return_pct or DEFAULT_TARGET[self.risk_level]
        return RiskEnvelope(
            risk_level=self.risk_level,
            target_return_pct=target,
            capital_usd=self.capital_usd,
            market=self.market,
        )


@dataclass
class Extraction:
    risk_level: RiskLevel | None = None
    target_return_pct: float | None = None
    capital_usd: float | None = None
    market: Market | None = None
    tickers: list[str] = field(default_factory=list)  # POPULAR symbols mentioned
    inferred_risk: RiskLevel | None = None
    inferred_market: Market | None = None
    defaults_requested: bool = False
    notes: list[str] = field(default_factory=list)  # bounds violations etc.


def rule_extract(text: str) -> Extraction:
    ex = Extraction()
    lowered = text.lower()

    # tickers first — they power the "learn from what you'd buy" inference
    for symbol in POPULAR:
        if re.search(rf"\b{symbol}\b", text, re.IGNORECASE):
            ex.tickers.append(symbol)
    for alias, symbol in _NAME_ALIASES.items():
        if symbol not in ex.tickers and alias in lowered:
            ex.tickers.append(symbol)
    if ex.tickers:
        markets = [POPULAR[s][1] for s in ex.tickers]
        tilts = [POPULAR[s][2] for s in ex.tickers]
        ex.inferred_market = max(set(markets), key=markets.count)
        ex.inferred_risk = max(set(tilts), key=tilts.count)

    for level, pattern in _RISK_PATTERNS:
        if re.search(pattern, lowered):
            ex.risk_level = level
            break

    for market, pattern in _MARKET_PATTERNS:
        if re.search(pattern, lowered):
            ex.market = market
            break

    pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", lowered)
    if pct:
        value = float(pct.group(1))
        if TARGET_MIN <= value <= TARGET_MAX:
            ex.target_return_pct = value
        else:
            ex.notes.append(
                f"A target of {value:g}% per quarter is outside the desk's bounds "
                f"({TARGET_MIN:g}–{TARGET_MAX:g}%) — I left it unset."
            )

    capital = _extract_capital(lowered)
    if capital is not None:
        if CAPITAL_MIN <= capital <= CAPITAL_MAX:
            ex.capital_usd = capital
        else:
            bound = "minimum is $1,000" if capital < CAPITAL_MIN else "maximum is $10,000,000"
            ex.notes.append(f"${capital:,.0f} won't work — the {bound}. This is a paper desk, not a fund.")

    ex.defaults_requested = bool(re.search(_DEFAULTS_PATTERN, lowered))
    return ex


_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "thousand": 1_000, "million": 1_000_000, "grand": 1_000}


def _extract_capital(lowered: str) -> float | None:
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:%|percent)", " ", lowered)  # don't read targets as capital
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k|m|thousand|million|grand)?\b", t)
    if not m:
        m = re.search(r"\b([\d,]+(?:\.\d+)?)\s*(k|m|thousand|million|grand)\b", t)
    if not m:
        m = re.search(r"\b(\d{4,}|\d{1,3}(?:,\d{3})+)\b", t)
        if m:
            return float(m.group(1).replace(",", ""))
        return None
    value = float(m.group(1).replace(",", ""))
    suffix = m.group(2)
    return value * _MULTIPLIERS.get(suffix or "", 1)


def apply_extraction(slots: Slots, ex: Extraction) -> Slots:
    new = Slots(**vars(slots))
    if ex.risk_level:
        new.risk_level = ex.risk_level
    if ex.market:
        new.market = ex.market
    if ex.capital_usd is not None:
        new.capital_usd = ex.capital_usd
    if ex.target_return_pct is not None:
        new.target_return_pct = ex.target_return_pct
    # inference from tickers only fills gaps, never overrides a stated answer
    if new.market is None and ex.inferred_market:
        new.market = ex.inferred_market
    if new.risk_level is None and ex.inferred_risk:
        new.risk_level = ex.inferred_risk
    if ex.defaults_requested:
        new.risk_level = new.risk_level or "balanced"
        new.market = new.market or "us"
        new.capital_usd = new.capital_usd or 10_000
    return new


@dataclass
class Turn:
    reply: str
    slots: Slots
    suggestions: list[str]
    proposal: UniverseProposal | None
    done: bool


def respond(user_text: str, slots: Slots, ex: Extraction | None = None) -> Turn:
    ex = ex or rule_extract(user_text)
    new = apply_extraction(slots, ex)
    parts: list[str] = []

    if ex.tickers:
        names = ", ".join(f"{s} ({POPULAR[s][0]})" for s in ex.tickers[:4])
        flavor = ""
        if slots.risk_level is None and ex.inferred_risk and not ex.risk_level:
            flavor = f" — reads {ex.inferred_risk} to me"
        parts.append(f"{names}{flavor}.")

    if new.risk_level and new.risk_level != slots.risk_level:
        parts.append(f"I'll run the desk {new.risk_level}.")
    if new.market and new.market != slots.market:
        extra = " — trades 24/7, the desk never sleeps" if new.market == "crypto" else ""
        parts.append(f"Trading {MARKET_LABEL[new.market]}{extra}.")
    if new.capital_usd and new.capital_usd != slots.capital_usd:
        parts.append(f"${new.capital_usd:,.0f} committed — paper only, nothing real moves.")
    if new.target_return_pct and new.target_return_pct != slots.target_return_pct:
        parts.append(f"The bar: beat the tracker by {new.target_return_pct:g}% per quarter.")
    parts.extend(ex.notes)

    if new.complete():
        envelope = new.to_envelope()
        proposal = propose_universe(envelope)
        if new.target_return_pct is None:
            new.target_return_pct = envelope.target_return_pct
            article = "an" if envelope.risk_level == "aggressive" else "a"
            parts.append(
                f"I set the bar at tracker +{envelope.target_return_pct:g}%/quarter "
                f"(standard for {article} {envelope.risk_level} desk) — give me a number to change it."
            )
        parts.append(
            f"Here's the desk I'd staff: {len(proposal.universe)} names screened against "
            f"{proposal.tracker_symbol} ({proposal.tracker_name}), max position "
            f"{proposal.rules.max_position_pct:g}%, daily drawdown capped at "
            f"{proposal.rules.max_daily_drawdown_pct:g}%. Pick the stocks you want — each one "
            f"goes to the committee to argue over daily — then ratify. Or tell me what to change."
        )
        return Turn(
            reply=" ".join(parts),
            slots=new,
            suggestions=["Make it more aggressive", "Double the capital", "Switch to crypto"],
            proposal=proposal,
            done=True,
        )

    if not parts:
        parts.append("I didn't catch anything I can trade on there.")
    question, suggestions = _next_question(new)
    parts.append(question)
    return Turn(reply=" ".join(parts), slots=new, suggestions=suggestions, proposal=None, done=False)


def _next_question(slots: Slots) -> tuple[str, list[str]]:
    if slots.risk_level is None:
        return (
            "How much risk can you stomach — keep it safe, balanced, or chase the delta?",
            ["Keep it safe", "Balanced", "Chase the delta"],
        )
    if slots.market is None:
        return (
            "Where should the desk trade — US equities, EU equities, or crypto (24/7)?",
            ["US equities", "EU equities", "Crypto"],
        )
    return (
        "How much paper capital should the desk run? Anywhere from $1,000 to $10,000,000.",
        ["$10,000", "$25,000", "$100,000"],
    )
