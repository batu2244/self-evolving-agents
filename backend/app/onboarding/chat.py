"""Conversational onboarding engine.

Deterministic slot extraction + reply composition. The concierge learns the
risk envelope (risk level, market, capital, target) from free text — including
inference from popular tickers the user says they'd buy. Works with no API
key; `llm.py` can override extraction with Claude when a key is present.
"""

import re
from dataclasses import dataclass, field

from app.onboarding.schemas import Market, RiskEnvelope, RiskLevel, UniverseProposal
from app.onboarding.universe import _CATALOG, _ELIGIBLE_BANDS, _with_history, propose_universe

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
    "XTB": ("X-Trade Brokers", "pl", "balanced"),
    "CDR": ("CD Projekt", "pl", "aggressive"),
    "ALE": ("Allegro", "pl", "balanced"),
    "PKO": ("PKO Bank Polski", "pl", "conservative"),
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
    "x-trade": "XTB", "cd projekt": "CDR", "allegro": "ALE",
}

_RISK_PATTERNS: list[tuple[RiskLevel, str]] = [
    ("conservative", r"conservativ|\bsafe(?:ty)?\b|cautious|careful|preserv|low[ -]risk|defensiv|don'?t lose"),
    ("aggressive", r"aggressiv|risky|high[ -]risk|\byolo\b|\bmoon\b|all[ -]in|\bbold\b|gambl|maximum risk|chase"),
    ("balanced", r"balanc|moderate|\bmedium\b|middle|somewhere in between"),
]

_MARKET_PATTERNS: list[tuple[Market, str]] = [
    ("pl", r"warsaw|poland|polish|\bgpw\b|\bwig\s?20\b|\bwig\b|\bpln\b|z[łl]oty"),
    ("crypto", r"crypto|bitcoin|\bbtc\b|ethereum|\beth\b|blockchain|\bdefi\b|stablecoin|\bcoins?\b|\btokens?\b"),
    ("eu", r"\beurope(?:an)?\b|\beu\b|stoxx|\bdax\b|xetra|euronext"),
    ("us", r"u\.s\.|\busa\b|\bamerican?\b|s&p|nasdaq|nyse|\bdow\b|united states|wall street|\bus (?:stocks|equities|market|tech)\b"),
]

_DEFAULTS_PATTERN = (
    r"pick for me|you (?:choose|decide|pick)|up to you|whatever|surprise me|"
    r"\bidk\b|don'?t know|\bdunno\b|no idea|just set|defaults?\b"
)

MARKET_LABEL: dict[Market, str] = {
    "us": "US equities",
    "eu": "EU equities",
    "pl": "Warsaw (GPW)",
    "crypto": "crypto",
}

# display label -> (regex, catalog sector names it covers)
SECTORS: dict[str, tuple[str, set[str]]] = {
    "Financials": (r"financ|bank|broker|insur|fintech", {"Financials", "Insurance"}),
    "Technology": (r"\btech|software|semiconductor|chip", {"Technology"}),
    "Energy": (r"energy|oil|gas|utilit", {"Energy", "Utilities"}),
    "Gaming": (r"gam(?:e|ing)|esports", {"Gaming"}),
    "Consumer": (r"consumer|retail|shop|staples|food", {"Consumer Staples", "Consumer Discretionary"}),
    "Healthcare": (r"health|pharma|medic", {"Healthcare"}),
    "Materials": (r"material|mining|metal|copper", {"Materials"}),
    "Communication": (r"telecom|communicat|media", {"Communication"}),
}

# symbol -> (name, market, vol band) across every floor, for picking stocks in chat
SYMBOL_INDEX: dict[str, tuple[str, Market, str]] = {
    a.symbol: (a.name, market, a.vol_band)
    for market, assets in _CATALOG.items()
    for a in assets
}

_BAND_TO_RISK: dict[str, RiskLevel] = {
    "low": "conservative",
    "medium": "balanced",
    "high": "aggressive",
}


@dataclass
class Slots:
    risk_level: RiskLevel | None = None
    target_return_pct: float | None = None
    capital_usd: float | None = None
    market: Market | None = None
    sector: str | None = None  # display label from SECTORS
    picks: list[str] = field(default_factory=list)  # symbols chosen in chat

    def complete(self) -> bool:
        # risk/target default at proposal time; sector and picks are optional
        return None not in (self.capital_usd, self.market)

    def resolved_risk(self) -> RiskLevel:
        if self.risk_level:
            return self.risk_level
        bands = [SYMBOL_INDEX[p][2] for p in self.picks if p in SYMBOL_INDEX]
        if bands:
            return _BAND_TO_RISK[max(set(bands), key=bands.count)]
        return "balanced"

    def to_envelope(self) -> RiskEnvelope:
        assert self.capital_usd and self.market
        risk = self.resolved_risk()
        target = self.target_return_pct or DEFAULT_TARGET[risk]
        return RiskEnvelope(
            risk_level=risk,
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
    sector: str | None = None
    tickers: list[str] = field(default_factory=list)  # catalog symbols mentioned
    inferred_risk: RiskLevel | None = None
    inferred_market: Market | None = None
    defaults_requested: bool = False
    capital_multiplier: float | None = None  # "double/halve the capital"
    notes: list[str] = field(default_factory=list)  # bounds violations etc.


def rule_extract(text: str) -> Extraction:
    ex = Extraction()
    lowered = text.lower()

    # tickers first — they power the "learn from what you'd buy" inference.
    # Any symbol or name from the catalogs counts as a pick.
    for symbol, (name, _mkt, _band) in SYMBOL_INDEX.items():
        token = re.escape(symbol.split("/")[0])
        if re.search(rf"\b{token}\b", text, re.IGNORECASE):
            ex.tickers.append(symbol)
        elif len(name) > 3 and name.lower() in lowered:
            ex.tickers.append(symbol)
    for alias, symbol in _NAME_ALIASES.items():
        resolved = symbol if symbol in SYMBOL_INDEX else f"{symbol}/USD"
        if resolved in SYMBOL_INDEX and resolved not in ex.tickers and alias in lowered:
            ex.tickers.append(resolved)
    for symbol in POPULAR:
        if symbol in SYMBOL_INDEX:
            continue
        if re.search(rf"\b{re.escape(symbol)}\b", text, re.IGNORECASE):
            ex.tickers.append(symbol)
    if ex.tickers:
        markets = [
            SYMBOL_INDEX[s][1] if s in SYMBOL_INDEX else POPULAR[s][1] for s in ex.tickers
        ]
        ex.inferred_market = max(set(markets), key=markets.count)
        tilts = [
            _BAND_TO_RISK[SYMBOL_INDEX[s][2]] if s in SYMBOL_INDEX else POPULAR[s][2]
            for s in ex.tickers
        ]
        ex.inferred_risk = max(set(tilts), key=tilts.count)

    for label, (pattern, _names) in SECTORS.items():
        if re.search(pattern, lowered):
            ex.sector = label
            break

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

    if ex.capital_usd is None:
        if re.search(r"\bdouble\b", lowered):
            ex.capital_multiplier = 2.0
        elif re.search(r"\b(halve|half)\b", lowered):
            ex.capital_multiplier = 0.5

    ex.defaults_requested = bool(re.search(_DEFAULTS_PATTERN, lowered))
    return ex


_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "thousand": 1_000, "million": 1_000_000, "grand": 1_000}


def _extract_capital(lowered: str) -> float | None:
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:%|percent)", " ", lowered)  # don't read targets as capital
    # negative amounts are noise, not a commitment — drop them before parsing
    t = re.sub(r"-\s*\$?\s*[\d,]+(?:\.\d+)?\s*(?:k|m|thousand|million|grand|dollars|bucks|usd)?\b", " ", t)
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k|m|thousand|million|grand)?\b", t)
    if not m:
        m = re.search(r"\b([\d,]+(?:\.\d+)?)\s*(k|m|thousand|million|grand)\b", t)
    if not m:
        m = re.search(r"\b([\d,]+(?:\.\d+)?)\s*(?:dollars|bucks|usd)\b", t)
        if m:
            return float(m.group(1).replace(",", ""))
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
    new.picks = list(slots.picks)
    if ex.risk_level:
        new.risk_level = ex.risk_level
    if ex.market:
        new.market = ex.market
    if ex.sector:
        new.sector = ex.sector
    if ex.capital_usd is not None:
        new.capital_usd = ex.capital_usd
    elif ex.capital_multiplier is not None and slots.capital_usd is not None:
        new.capital_usd = min(max(slots.capital_usd * ex.capital_multiplier, CAPITAL_MIN), CAPITAL_MAX)
    if ex.target_return_pct is not None:
        new.target_return_pct = ex.target_return_pct
    # mentioned stocks become picks; inference fills gaps, never overrides answers
    for s in ex.tickers:
        if s in SYMBOL_INDEX and s not in new.picks:
            new.picks.append(s)
    if new.market is None and ex.inferred_market:
        new.market = ex.inferred_market
    if new.risk_level is None and ex.inferred_risk:
        new.risk_level = ex.inferred_risk
    # picks from another floor than a previously assumed market move the desk there
    if new.picks:
        pick_market = SYMBOL_INDEX[new.picks[-1]][1]
        if ex.market is None and slots.market is None:
            new.market = pick_market
    if ex.defaults_requested:
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
    candidates: list[dict[str, str]] = field(default_factory=list)
    preselect: list[str] = field(default_factory=list)  # picks to pre-check in the proposal


# The concierge asks at most this many questions before proposing with defaults.
MAX_QUESTIONS = 4


def radar(slots: Slots) -> list[dict[str, str]]:
    """Stocks on the radar for the current partial envelope — shown at every
    stage so the user is always looking at concrete names. Narrows with each
    answer: market → sector → risk."""
    if slots.market:
        pool = _CATALOG[slots.market]
        if slots.sector and slots.sector in SECTORS:
            names = SECTORS[slots.sector][1]
            filtered = [a for a in pool if a.sector in names]
            pool = filtered or pool
        if slots.risk_level:
            bands = _ELIGIBLE_BANDS[slots.risk_level]
            filtered = [a for a in pool if a.vol_band in bands]
            pool = filtered or pool
        return [{"symbol": a.symbol, "name": a.name} for a in pool[:5]]
    # market unknown — one flavour of each floor
    picks = ["NVDA", "ASML", "XTB", "KO"]
    out = [{"symbol": s, "name": SYMBOL_INDEX[s][0]} for s in picks if s in SYMBOL_INDEX]
    out.append({"symbol": "BTC/USD", "name": "Bitcoin"})
    return out


def _sector_chips(market: Market) -> list[str]:
    """Sector labels for the market, most-represented first."""
    counts: dict[str, int] = {}
    for a in _CATALOG[market]:
        for label, (_p, names) in SECTORS.items():
            if a.sector in names:
                counts[label] = counts.get(label, 0) + 1
    ordered = sorted(counts, key=counts.get, reverse=True)  # type: ignore[arg-type]
    return ordered[:4]


def _sector_aware_proposal(envelope: RiskEnvelope, slots: Slots) -> UniverseProposal:
    """Standard screened universe, reordered so the chosen sector leads and
    the user's picks are always present."""
    proposal = propose_universe(envelope)
    universe = list(proposal.universe)
    if slots.sector and slots.sector in SECTORS:
        names = SECTORS[slots.sector][1]
        universe.sort(key=lambda a: a.sector not in names)  # stable: sector first
    present = {a.symbol for a in universe}
    missing = [p for p in slots.picks if p in SYMBOL_INDEX and p not in present]
    for symbol in reversed(missing):
        market = SYMBOL_INDEX[symbol][1]
        asset = next(a for a in _CATALOG[market] if a.symbol == symbol)
        universe.insert(0, _with_history(asset))
    return proposal.model_copy(update={"universe": universe[:8]})


def respond(
    user_text: str,
    slots: Slots,
    ex: Extraction | None = None,
    force_complete: bool = False,
) -> Turn:
    ex = ex or rule_extract(user_text)
    new = apply_extraction(slots, ex)
    parts: list[str] = []

    if force_complete and not new.complete():
        new.market = new.market or "us"
        new.capital_usd = new.capital_usd or 10_000
        parts.append("Four questions is my cap — I filled the rest with desk defaults.")

    new_picks = [p for p in new.picks if p not in slots.picks]
    if new_picks:
        names = ", ".join(f"{s} ({SYMBOL_INDEX[s][0]})" for s in new_picks[:4])
        parts.append(f"{names} — noted, the committee will focus there.")

    if new.market and new.market != slots.market:
        extra = " — trades 24/7, the desk never sleeps" if new.market == "crypto" else ""
        parts.append(f"Trading {MARKET_LABEL[new.market]}{extra}.")
    if new.sector and new.sector != slots.sector:
        parts.append(f"Scanning {new.sector} names.")
    if new.risk_level and new.risk_level != slots.risk_level and ex.risk_level:
        parts.append(f"I'll run the desk {new.risk_level}.")
    if new.capital_usd and new.capital_usd != slots.capital_usd:
        parts.append(f"${new.capital_usd:,.0f} committed — paper only, nothing real moves.")
    if new.target_return_pct and new.target_return_pct != slots.target_return_pct:
        parts.append(f"The bar: beat the tracker by {new.target_return_pct:g}% per quarter.")
    parts.extend(ex.notes)

    if new.complete():
        if new.risk_level is None:
            risk = new.resolved_risk()
            if new.picks:
                parts.append(f"Your picks read {risk} — position rules follow.")
            new.risk_level = risk
        envelope = new.to_envelope()
        proposal = _sector_aware_proposal(envelope, new)
        if new.target_return_pct is None:
            new.target_return_pct = envelope.target_return_pct
            article = "an" if envelope.risk_level == "aggressive" else "a"
            parts.append(
                f"I set the bar at tracker +{envelope.target_return_pct:g}%/quarter "
                f"(standard for {article} {envelope.risk_level} desk) — give me a number to change it."
            )
        preselect = [p for p in new.picks if p in {a.symbol for a in proposal.universe}]
        confirm = (
            f"{', '.join(preselect)} is pre-checked — confirm or adjust, then ratify."
            if preselect
            else "Pick the stock you want to trade — each pick goes to the committee to argue over daily — then ratify."
        )
        parts.append(
            f"Here's the desk: {len(proposal.universe)} names screened against "
            f"{proposal.tracker_symbol} ({proposal.tracker_name}), max position "
            f"{proposal.rules.max_position_pct:g}%, daily drawdown capped at "
            f"{proposal.rules.max_daily_drawdown_pct:g}%. {confirm}"
        )
        return Turn(
            reply=" ".join(parts),
            slots=new,
            suggestions=["Make it more aggressive", "Double the capital", "Switch to Warsaw (GPW)"],
            proposal=proposal,
            done=True,
            preselect=preselect,
        )

    if not parts:
        parts.append("I didn't catch anything I can trade on there.")
    question, suggestions = _next_question(new)
    parts.append(question)
    return Turn(
        reply=" ".join(parts),
        slots=new,
        suggestions=suggestions,
        proposal=None,
        done=False,
        candidates=radar(new),
    )


def _next_question(slots: Slots) -> tuple[str, list[str]]:
    """The funnel: market → sector → stock → capital. At most 4 questions."""
    if slots.market is None:
        return (
            "Which floor do you want to trade — US equities, EU equities, Warsaw (GPW), or crypto (24/7)?",
            ["US equities", "EU equities", "Warsaw (GPW)", "Crypto"],
        )
    if not slots.picks:
        if slots.sector is None:
            chips = _sector_chips(slots.market)
            return (
                f"Which corner of the {MARKET_LABEL[slots.market]} floor — "
                f"{', '.join(chips)}, or something else?",
                chips,
            )
        names = radar(slots)
        listed = ", ".join(f"{c['symbol']} ({c['name']})" for c in names[:4])
        return (
            f"These catch the floor's eye: {listed}. Which one should the committee focus on?",
            [c["symbol"] for c in names[:4]],
        )
    return (
        "How much money should the desk put in? Anywhere from $1,000 to $10,000,000 — paper only.",
        ["$10,000", "$25,000", "$100,000"],
    )
