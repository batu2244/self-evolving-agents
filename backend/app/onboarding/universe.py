"""Universe selection — turns a risk envelope into a tracker + screened universe.

Static catalogs stand in for a live constituent screen (volatility band,
liquidity, sector spread). The screening *rules* are real: risk level gates
which volatility bands are eligible, and the pick is sector-spread across
the eligible pool.
"""

import hashlib
import random

from app.onboarding.schemas import (
    Market,
    RiskEnvelope,
    RiskLevel,
    RiskRules,
    UniverseAsset,
    UniverseProposal,
)

# indicative base prices per symbol (paper desk — synthetic history hangs off these)
_BASE_PRICES: dict[str, float] = {
    "JNJ": 155, "PG": 165, "KO": 62, "WMT": 88, "MCD": 295, "MRK": 105,
    "MSFT": 425, "AAPL": 225, "JPM": 240, "V": 290, "UNH": 520, "HD": 390,
    "NVDA": 135, "TSLA": 260, "AMD": 155, "META": 560,
    "NESN": 92, "SAN": 95, "IBE": 13, "OR": 415, "DTE": 28, "AI": 175,
    "SAP": 210, "SIE": 185, "MC": 690, "NOVO-B": 780, "ALV": 290,
    "ASML": 880, "ADYEN": 1350, "IFX": 33,
    "BTC/USD": 96_500, "ETH/USD": 3_450, "LTC/USD": 105, "SOL/USD": 190,
    "LINK/USD": 22, "DOT/USD": 8.5, "AVAX/USD": 38, "NEAR/USD": 6.8, "INJ/USD": 27,
}

_DAILY_VOL = {"low": 0.009, "medium": 0.018, "high": 0.034}
_HISTORY_DAYS = 30


def _with_history(asset: UniverseAsset) -> UniverseAsset:
    """Attach a deterministic 30-day synthetic price walk ending at the base
    price. Seeded by symbol so every request (and every QA run) sees the
    same chart."""
    base = _BASE_PRICES.get(asset.symbol, 100.0)
    seed = int(hashlib.md5(asset.symbol.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    vol = _DAILY_VOL[asset.vol_band]
    # walk backwards from today's price so the series ends exactly at `base`
    closes = [base]
    for _ in range(_HISTORY_DAYS - 1):
        closes.append(closes[-1] / (1 + rng.gauss(0.0008, vol)))
    closes.reverse()
    closes = [round(c, 2 if base >= 1 else 4) for c in closes]
    change = (closes[-1] / closes[0] - 1) * 100
    return asset.model_copy(
        update={
            "last_price": closes[-1],
            "change_30d_pct": round(change, 2),
            "history": closes,
        }
    )

_TRACKERS: dict[Market, dict[RiskLevel, tuple[str, str]]] = {
    "us": {
        "conservative": ("SCHD", "Schwab US Dividend Equity ETF"),
        "balanced": ("SPY", "SPDR S&P 500 ETF"),
        "aggressive": ("QQQ", "Invesco NASDAQ-100 ETF"),
    },
    "eu": {
        "conservative": ("EXSA", "iShares STOXX Europe 600"),
        "balanced": ("EXW1", "iShares EURO STOXX 50"),
        "aggressive": ("EXV3", "iShares STOXX Europe 600 Technology"),
    },
    "crypto": {
        "conservative": ("BTC/USD", "Bitcoin"),
        "balanced": ("BTC/USD", "Bitcoin"),
        "aggressive": ("ETH/USD", "Ethereum"),
    },
}

_CATALOG: dict[Market, list[UniverseAsset]] = {
    "us": [
        UniverseAsset(symbol="JNJ", name="Johnson & Johnson", sector="Healthcare", vol_band="low"),
        UniverseAsset(symbol="PG", name="Procter & Gamble", sector="Consumer Staples", vol_band="low"),
        UniverseAsset(symbol="KO", name="Coca-Cola", sector="Consumer Staples", vol_band="low"),
        UniverseAsset(symbol="WMT", name="Walmart", sector="Consumer Staples", vol_band="low"),
        UniverseAsset(symbol="MCD", name="McDonald's", sector="Consumer Discretionary", vol_band="low"),
        UniverseAsset(symbol="MRK", name="Merck & Co.", sector="Healthcare", vol_band="low"),
        UniverseAsset(symbol="MSFT", name="Microsoft", sector="Technology", vol_band="medium"),
        UniverseAsset(symbol="AAPL", name="Apple", sector="Technology", vol_band="medium"),
        UniverseAsset(symbol="JPM", name="JPMorgan Chase", sector="Financials", vol_band="medium"),
        UniverseAsset(symbol="V", name="Visa", sector="Financials", vol_band="medium"),
        UniverseAsset(symbol="UNH", name="UnitedHealth", sector="Healthcare", vol_band="medium"),
        UniverseAsset(symbol="HD", name="Home Depot", sector="Consumer Discretionary", vol_band="medium"),
        UniverseAsset(symbol="NVDA", name="NVIDIA", sector="Technology", vol_band="high"),
        UniverseAsset(symbol="TSLA", name="Tesla", sector="Consumer Discretionary", vol_band="high"),
        UniverseAsset(symbol="AMD", name="Advanced Micro Devices", sector="Technology", vol_band="high"),
        UniverseAsset(symbol="META", name="Meta Platforms", sector="Communication", vol_band="high"),
    ],
    "eu": [
        UniverseAsset(symbol="NESN", name="Nestlé", sector="Consumer Staples", vol_band="low"),
        UniverseAsset(symbol="SAN", name="Sanofi", sector="Healthcare", vol_band="low"),
        UniverseAsset(symbol="IBE", name="Iberdrola", sector="Utilities", vol_band="low"),
        UniverseAsset(symbol="OR", name="L'Oréal", sector="Consumer Staples", vol_band="low"),
        UniverseAsset(symbol="DTE", name="Deutsche Telekom", sector="Communication", vol_band="low"),
        UniverseAsset(symbol="AI", name="Air Liquide", sector="Materials", vol_band="low"),
        UniverseAsset(symbol="SAP", name="SAP", sector="Technology", vol_band="medium"),
        UniverseAsset(symbol="SIE", name="Siemens", sector="Industrials", vol_band="medium"),
        UniverseAsset(symbol="MC", name="LVMH", sector="Consumer Discretionary", vol_band="medium"),
        UniverseAsset(symbol="NOVO-B", name="Novo Nordisk", sector="Healthcare", vol_band="medium"),
        UniverseAsset(symbol="ALV", name="Allianz", sector="Financials", vol_band="medium"),
        UniverseAsset(symbol="ASML", name="ASML", sector="Technology", vol_band="high"),
        UniverseAsset(symbol="ADYEN", name="Adyen", sector="Financials", vol_band="high"),
        UniverseAsset(symbol="IFX", name="Infineon", sector="Technology", vol_band="high"),
    ],
    "crypto": [
        UniverseAsset(symbol="BTC/USD", name="Bitcoin", sector="Layer 1", vol_band="low"),
        UniverseAsset(symbol="ETH/USD", name="Ethereum", sector="Layer 1", vol_band="low"),
        UniverseAsset(symbol="LTC/USD", name="Litecoin", sector="Payments", vol_band="medium"),
        UniverseAsset(symbol="SOL/USD", name="Solana", sector="Layer 1", vol_band="medium"),
        UniverseAsset(symbol="LINK/USD", name="Chainlink", sector="Oracles", vol_band="medium"),
        UniverseAsset(symbol="DOT/USD", name="Polkadot", sector="Layer 0", vol_band="medium"),
        UniverseAsset(symbol="AVAX/USD", name="Avalanche", sector="Layer 1", vol_band="high"),
        UniverseAsset(symbol="NEAR/USD", name="NEAR Protocol", sector="Layer 1", vol_band="high"),
        UniverseAsset(symbol="INJ/USD", name="Injective", sector="DeFi", vol_band="high"),
    ],
}

# crypto vol bands are relative to the asset class, not to equities
_ELIGIBLE_BANDS: dict[RiskLevel, set[str]] = {
    "conservative": {"low"},
    "balanced": {"low", "medium"},
    "aggressive": {"medium", "high"},
}

_RULES: dict[RiskLevel, RiskRules] = {
    "conservative": RiskRules(
        max_position_pct=10, max_daily_drawdown_pct=1.5, stop_rule="Hard stop 3% below entry"
    ),
    "balanced": RiskRules(
        max_position_pct=20, max_daily_drawdown_pct=3.0, stop_rule="Hard stop 5% below entry"
    ),
    "aggressive": RiskRules(
        max_position_pct=30, max_daily_drawdown_pct=5.0, stop_rule="Trailing stop 8%"
    ),
}

_MARKET_META: dict[Market, tuple[str, str]] = {
    "us": ("USD", "NYSE 09:30–16:00 ET, daily cycle at 12:00 ET"),
    "eu": ("EUR", "XETRA 09:00–17:30 CET, daily cycle at 12:00 CET"),
    "crypto": ("USD", "24/7, daily cycle at 18:00 PT"),
}

_MAX_UNIVERSE = 8


def _sector_spread(pool: list[UniverseAsset], limit: int) -> list[UniverseAsset]:
    """Pick round-robin across sectors so no single sector dominates."""
    by_sector: dict[str, list[UniverseAsset]] = {}
    for asset in pool:
        by_sector.setdefault(asset.sector, []).append(asset)
    picked: list[UniverseAsset] = []
    while len(picked) < limit and any(by_sector.values()):
        for sector in list(by_sector):
            if by_sector[sector]:
                picked.append(by_sector[sector].pop(0))
                if len(picked) == limit:
                    break
    return picked


def propose_universe(envelope: RiskEnvelope) -> UniverseProposal:
    bands = _ELIGIBLE_BANDS[envelope.risk_level]
    pool = [a for a in _CATALOG[envelope.market] if a.vol_band in bands]
    tracker_symbol, tracker_name = _TRACKERS[envelope.market][envelope.risk_level]
    currency, window = _MARKET_META[envelope.market]
    return UniverseProposal(
        tracker_symbol=tracker_symbol,
        tracker_name=tracker_name,
        currency=currency,  # type: ignore[arg-type]
        trading_window=window,
        universe=[_with_history(a) for a in _sector_spread(pool, _MAX_UNIVERSE)],
        rules=_RULES[envelope.risk_level],
    )
