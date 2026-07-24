"""Simulation: the committee votes BUY or SELL on a real ticker's last day.

Pulls live data (keyless): daily bars from Yahoo's chart API and headlines
from Google News RSS. Each analyst derives its binary stance from its own
slice of reality —

  realtime    last session: close-to-close move, gap, volume vs 30d average
  historical  trend + mean-reversion stats over the last ~60 sessions
  sentiment   keyword score over the last 48h of headlines

— then the full deliberation runs: stances → cases → rebuttals → judged
binary verdict (LLM judge via OpenRouter, heuristic fallback).

    python -m voting.simulate            # NVDA
    python -m voting.simulate AAPL
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

import httpx

from voting.deliberation import Case, Side, Stance, run_deliberation
from voting.judge import default_judge
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor

HEADERS = {"User-Agent": "Mozilla/5.0 (DeltaDesk hackathon)"}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
RSS_URL = "https://news.google.com/rss/search"

POSITIVE = {
    "beat", "beats", "surge", "surges", "rally", "record", "soar", "soars",
    "jump", "jumps", "upgrade", "upgraded", "buy", "bullish", "growth",
    "strong", "top", "tops", "gain", "gains", "raise", "raises", "high",
}
NEGATIVE = {
    "miss", "misses", "fall", "falls", "drop", "drops", "plunge", "plunges",
    "cut", "cuts", "downgrade", "downgraded", "sell", "bearish", "weak",
    "lawsuit", "probe", "ban", "fear", "fears", "risk", "risks", "slump", "low",
}


@dataclass
class MarketView:
    symbol: str
    dates: list[str]
    closes: list[float]
    opens: list[float]
    volumes: list[float]

    @property
    def last_date(self) -> str:
        return self.dates[-1]

    @property
    def last_close(self) -> float:
        return self.closes[-1]

    @property
    def day_return(self) -> float:
        return self.closes[-1] / self.closes[-2] - 1

    @property
    def gap(self) -> float:
        return self.opens[-1] / self.closes[-2] - 1

    @property
    def volume_ratio(self) -> float:
        avg = sum(self.volumes[-31:-1]) / max(1, len(self.volumes[-31:-1]))
        return self.volumes[-1] / avg if avg else 1.0

    def ret(self, days: int) -> float:
        return self.closes[-1] / self.closes[-1 - days] - 1

    def bounce_rate_after_down_streak(self, streak: int = 2) -> tuple[float, int]:
        """How often a `streak`-day losing run was followed by an up day."""
        hits = total = 0
        rets = [self.closes[i] / self.closes[i - 1] - 1 for i in range(1, len(self.closes))]
        for i in range(streak, len(rets)):
            if all(r < 0 for r in rets[i - streak:i]):
                total += 1
                hits += rets[i] > 0
        return (hits / total if total else 0.5), total


def fetch_bars(symbol: str) -> MarketView:
    r = httpx.get(
        CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": "3mo"},
        headers=HEADERS,
        timeout=10.0,
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    rows = [
        (ts, o, c, v)
        for ts, o, c, v in zip(
            result["timestamp"], quote["open"], quote["close"], quote["volume"]
        )
        if c is not None and o is not None
    ]
    from datetime import datetime, timezone

    return MarketView(
        symbol=symbol,
        dates=[datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") for ts, *_ in rows],
        opens=[o for _, o, _, _ in rows],
        closes=[c for _, _, c, _ in rows],
        volumes=[v or 0 for _, _, _, v in rows],
    )


def fetch_headlines(symbol: str, limit: int = 12) -> list[str]:
    try:
        r = httpx.get(
            RSS_URL,
            params={"q": f"{symbol} stock when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=HEADERS, timeout=10.0, follow_redirects=True,
        )
        r.raise_for_status()
        titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text)
        return [t for t in titles if t and not t.endswith("Google News")][:limit]
    except Exception:
        return []


def headline_score(headlines: list[str]) -> tuple[float, int, int]:
    pos = neg = 0
    for h in headlines:
        words = set(re.findall(r"[a-z']+", h.lower()))
        pos += len(words & POSITIVE) > 0
        neg += len(words & NEGATIVE) > 0
    n = max(1, len(headlines))
    return (pos - neg) / n, pos, neg


class RealtimeAgent:
    name = "realtime"

    def __init__(self, mv: MarketView):
        self.mv = mv
        # Momentum with volume confirmation; a red day on thin volume is a
        # fade (weak selling), so unconfirmed moves flip the read.
        signal = mv.day_return * (1 if mv.volume_ratio >= 0.8 else -0.5)
        self.side = Side.BUY if signal >= 0 else Side.SELL

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.mv.symbol, side=self.side)

    def make_case(self, own, others) -> str:
        mv = self.mv
        confirmed = mv.volume_ratio >= 0.8
        return (
            f"Last session ({mv.last_date}): {mv.symbol} closed {mv.day_return:+.2%} "
            f"at {mv.last_close:.2f}, gap at open {mv.gap:+.2%}, volume at "
            f"{mv.volume_ratio:.1f}x the 30-day average. "
            + (f"Volume confirms the move — I vote {self.side.value.upper()} with the tape."
               if confirmed else
               f"The move came on thin volume — unconvincing selling, so I fade it "
               f"and vote {self.side.value.upper()}.")
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (
            f"Yesterday's tape is the freshest fact we have: {self.mv.day_return:+.2%} "
            f"on {self.mv.volume_ratio:.1f}x volume. Models and headlines lag the price."
        )


class HistoricalAgent:
    name = "historical"

    def __init__(self, mv: MarketView):
        self.mv = mv
        self.bounce, self.samples = mv.bounce_rate_after_down_streak(2)
        # Trend follower: the 20-day direction decides the vote.
        self.side = Side.BUY if mv.ret(20) >= 0 else Side.SELL

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.mv.symbol, side=self.side)

    def make_case(self, own, others) -> str:
        mv = self.mv
        return (
            f"Over the window ending {mv.last_date}: 5-day return {mv.ret(5):+.2%}, "
            f"20-day {mv.ret(20):+.2%}. After two consecutive red days this quarter, "
            f"{mv.symbol} bounced the next session {self.bounce:.0%} of the time "
            f"({self.samples} occurrences). The prevailing trend says "
            f"{self.side.value.upper()}."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (
            f"One session is noise; the {self.mv.ret(20):+.2%} 20-day trend across "
            f"{len(self.mv.closes)} sessions is the signal. I stand on the base rates."
        )


class SentimentAgent:
    name = "sentiment"

    def __init__(self, mv: MarketView, headlines: list[str]):
        self.mv, self.headlines = mv, headlines
        self.score, self.pos, self.neg = headline_score(headlines)
        self.side = Side.BUY if self.score >= 0 else Side.SELL

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.mv.symbol, side=self.side)

    def make_case(self, own, others) -> str:
        sample = "; ".join(f'"{h}"' for h in self.headlines[:3]) or "(no headlines fetched)"
        return (
            f"Across {len(self.headlines)} headlines from the last 48h: {self.pos} lean "
            f"positive, {self.neg} negative (net score {self.score:+.2f}). "
            f"Examples: {sample}. News flow says {self.side.value.upper()}."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (
            f"Narrative moves this name as much as fundamentals: {self.pos} positive vs "
            f"{self.neg} negative stories right now. Price follows coverage here."
        )


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    live_news = "--live-news" in sys.argv

    mv = fetch_bars(symbol)
    headlines = fetch_headlines(symbol)
    agents = {
        "realtime": RealtimeAgent(mv),
        "historical": HistoricalAgent(mv),
        "sentiment": SentimentAgent(mv, headlines),
    }
    # The GOOGL news analyst (google-news-agent) joins the floor when we're
    # voting its ticker — live run with --live-news, else its sample output.
    if symbol.upper() in ("GOOGL", "GOOG"):
        from voting.adapters import NewsDeskAgent

        newsdesk = NewsDeskAgent.run() if live_news else NewsDeskAgent.from_sample()
        newsdesk.ticker = symbol  # vote under the symbol on the floor
        agents["newsdesk"] = newsdesk

    record = TrackRecord(path="/tmp/deltadesk-sim-track-record.json")
    if record.executions("sentiment") == 0:  # first run: seed desk history
        for _ in range(4):
            record.record_outcome("sentiment", 0.5)
            record.record_outcome("realtime", -0.4)
            record.record_outcome("historical", 0.2)

    stances = [a.stance() for a in agents.values()]
    judge = default_judge()
    room = InMemoryFloor()
    verdict = run_deliberation(
        f"sim-{symbol.lower()}-{mv.last_date}", stances, agents, judge, record, room
    )

    print("=" * 74)
    print(f"SIMULATION · {symbol} · last session {mv.last_date} "
          f"(close {mv.last_close:.2f}, {mv.day_return:+.2%}, vol {mv.volume_ratio:.1f}x)")
    print(f"judge: {type(judge).__name__}")
    print("=" * 74)
    for m in room.history():
        mentions = f"  → {', '.join('@' + x for x in m.mentions)}" if m.mentions else ""
        print(f"\n[{m.sender}]{mentions}")
        print(m.text.split("```json")[0].rstrip())

    print("\n" + "=" * 74)
    print("RESULT")
    print("=" * 74)
    print("credibility:", {a: record.credibility(a) for a in agents})
    for tv in verdict.verdicts:
        shares = ", ".join(f"{a}={w:.0%}" for a, w in sorted(tv.contributions.items()))
        flag = " UNANIMOUS" if tv.unanimous else ""
        print(f"{tv.ticker}: {tv.decision.value.upper()} at {tv.conviction:.0%} conviction{flag}  ({shares})")
        for s in tv.scores:
            print(f"  · {s.agent} argument scored {s.score:.2f}"
                  + (f" — {s.reasoning}" if s.reasoning else ""))
    print(verdict.narrative)


if __name__ == "__main__":
    main()
