"""Testing set for the app: XTB (Warsaw) with a twice-daily heartbeat.

The desk trades XTB.WA every session at two decision points:
  open+1h   10:00 Europe/Warsaw (WSE opens 09:00)
  close-1h  16:00 Europe/Warsaw (WSE closes 17:00)

`build` turns the last ~2 months of real GPW hourly bars into a fixture:
one record per decision point with (a) the price features visible AT that
moment (no lookahead), (b) the XTB news headlines published in the days
leading up to it (Google News archive, Polish locale), and (c) the
realized forward return to the next decision point — the grading label.

`replay` runs the grade-then-vote committee loop over the fixture.
Committee reality check: the desk currently has ONE real agent type — the
news agent (newsflow votes from the fixture's stored headlines, the same
signal shape as google-news-agent). The three price archetypes (tape /
trend / open-range) are STAND-INS for the incoming price agents and can be
dropped with --news-only.

    python -m voting.testset build            # writes voting/testdata/xtb_wa_60d.json
    python -m voting.testset replay           # news agent + price stand-ins
    python -m voting.testset replay --news-only
    python -m voting.testset replay --llm     # OpenRouter judge

Honesty notes: headline history is day-granular (an afternoon decision's
same-day window may include stories published after 16:00 — documented
limit); build filters out XTB.com's own market commentary, keeping only
stories about the company.
"""

from __future__ import annotations

import json
import re
import sys
import time
import zoneinfo
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from voting.deliberation import Case, Side, Stance, run_deliberation
from voting.judge import HeuristicJudge, default_judge
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor

SYMBOL = "XTB.WA"
TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
HEADERS = {"User-Agent": "Mozilla/5.0 (DeltaDesk hackathon)"}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
NEWS_URL = "https://news.google.com/rss/search"
NEWS_QUERY = "XTB akcje kurs"
WINDOW_DAYS = 62  # ~2 months of sessions
NEWS_LOOKBACK_DAYS = 3

TESTDATA = Path(__file__).parent / "testdata"
FIXTURE = TESTDATA / "xtb_wa_60d.json"

# Decision prices come from the close of the bar ENDING at the decision time:
# 10:00 decision = close of the 09:00-10:00 bar; 16:00 = the 15:00-16:00 bar.
DECISIONS = [("open+1h", 9), ("close-1h", 15)]

# Bilingual headline lexicon (GPW coverage is mostly Polish).
POSITIVE = {
    "beat", "beats", "surge", "rally", "record", "soar", "jump", "upgrade",
    "buy", "bullish", "growth", "strong", "gain", "gains", "raise", "high",
    "rekord", "rekordowe", "zysk", "zyski", "wzrost", "rośnie", "rosną",
    "kupuj", "mocny", "mocne", "poprawa", "dywidenda", "skup", "przebija",
    "zwyżka", "drożeją", "hossa",
}
NEGATIVE = {
    "miss", "fall", "falls", "drop", "drops", "plunge", "cut", "downgrade",
    "sell", "bearish", "weak", "lawsuit", "probe", "fear", "risk", "slump",
    "strata", "straty", "spadek", "spada", "spadają", "sprzedaj", "kara",
    "pozew", "obniża", "obniżka", "słaby", "słabe", "przecena", "tąpnięcie",
    "bessa", "tanieją", "ryzyko",
}


def _fetch(symbol: str, interval: str, range_: str) -> dict:
    r = httpx.get(
        CHART_URL.format(symbol=symbol),
        params={"interval": interval, "range": range_},
        headers=HEADERS,
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()["chart"]["result"][0]


def _fetch_day_headlines(day: str, limit: int = 8) -> list[str]:
    """Headlines about XTB published on `day` (Google News archive query).
    XTB.com's own market commentary is dropped unless the story is about
    XTB itself."""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    try:
        r = httpx.get(
            NEWS_URL,
            params={"q": f"{NEWS_QUERY} after:{day} before:{nxt}",
                    "hl": "pl", "gl": "PL", "ceid": "PL:pl"},
            headers=HEADERS, timeout=15.0, follow_redirects=True,
        )
        r.raise_for_status()
    except Exception:
        return []
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text)
    out = []
    for t in titles:
        if not t or t.endswith("Google News"):
            continue
        body = t.rsplit(" - ", 1)[0].lower()  # strip the source suffix
        if "xtb" in body:  # about the company, not broker commentary on other markets
            out.append(t)
    return out[:limit]


def build() -> dict:
    hourly = _fetch(SYMBOL, "60m", "3mo")
    daily = _fetch(SYMBOL, "1d", "6mo")
    hq = hourly["indicators"]["quote"][0]

    cutoff = (datetime.now(TZ) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    # Group hourly bars by Warsaw session date.
    days: dict[str, list[dict]] = {}
    for ts, o, c, v in zip(hourly["timestamp"], hq["open"], hq["close"], hq["volume"]):
        if c is None or o is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=TZ)
        key = dt.strftime("%Y-%m-%d")
        if key < cutoff:
            continue
        days.setdefault(key, []).append(
            {"hour": dt.hour, "open": o, "close": c, "volume": v or 0}
        )

    dq = daily["indicators"]["quote"][0]
    daily_closes: dict[str, float] = {}
    for ts, c in zip(daily["timestamp"], dq["close"]):
        if c is not None:
            daily_closes[datetime.fromtimestamp(ts, tz=TZ).strftime("%Y-%m-%d")] = c
    daily_dates = sorted(daily_closes)

    all_volumes = [b["volume"] for bars in days.values() for b in bars if b["volume"]]
    avg_bar_volume = sum(all_volumes) / max(1, len(all_volumes))

    points: list[dict] = []
    for d in sorted(days):
        bars = {b["hour"]: b for b in days[d]}
        prior = [x for x in daily_dates if x < d]
        if len(prior) < 21 or 9 not in bars:
            continue
        prev_close = daily_closes[prior[-1]]
        day_open = bars[9]["open"]

        for label, end_hour in DECISIONS:
            if end_hour not in bars:
                continue
            price = bars[end_hour]["close"]
            prev_bar = bars.get(end_hour - 1)
            lookback_start = (date.fromisoformat(d) - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat()
            # Morning sees news up to yesterday; afternoon adds today's
            # (day-granular — may include post-16:00 items, see module note).
            news_end = prior[-1] if label == "open+1h" else d
            points.append({
                "id": f"{d}-{label}",
                "date": d,
                "label": label,
                "time_local": f"{end_hour + 1:02d}:00",
                "price": round(price, 4),
                "features": {
                    "ret_last_hour": round(price / prev_bar["close"] - 1, 6) if prev_bar else round(price / day_open - 1, 6),
                    "ret_since_open": round(price / day_open - 1, 6),
                    "gap_open": round(day_open / prev_close - 1, 6),
                    "prev_day_ret": round(prev_close / daily_closes[prior[-2]] - 1, 6),
                    "ret_5d": round(prev_close / daily_closes[prior[-6]] - 1, 6),
                    "ret_20d": round(prev_close / daily_closes[prior[-21]] - 1, 6),
                    "volume_ratio": round(bars[end_hour]["volume"] / avg_bar_volume, 3) if avg_bar_volume else 1.0,
                },
                "news_window": {"start": lookback_start, "end": news_end},
                "outcome": None,  # filled below
            })

    for i in range(len(points) - 1):
        nxt = points[i + 1]
        points[i]["outcome"] = {
            "forward_ret": round(nxt["price"] / points[i]["price"] - 1, 6),
            "next_point": nxt["id"],
            "overnight": nxt["date"] != points[i]["date"],
        }

    # Headline archive: every calendar day the news windows can reference.
    first = min(p["news_window"]["start"] for p in points)
    last = max(p["news_window"]["end"] for p in points)
    headlines: dict[str, list[str]] = {}
    day = date.fromisoformat(first)
    while day <= date.fromisoformat(last):
        key = day.isoformat()
        headlines[key] = _fetch_day_headlines(key)
        time.sleep(0.15)
        day += timedelta(days=1)
    with_news = sum(1 for v in headlines.values() if v)
    print(f"news archive: {with_news}/{len(headlines)} days have XTB coverage",
          file=sys.stderr)

    fixture = {
        "symbol": SYMBOL,
        "exchange": "Warsaw Stock Exchange (GPW)",
        "currency": "PLN",
        "heartbeat": "twice daily: 10:00 (open+1h) and 16:00 (close-1h) Europe/Warsaw",
        "window_days": WINDOW_DAYS,
        "generated_at": datetime.now(TZ).isoformat(),
        "buy_and_hold_ret": round(points[-1]["price"] / points[0]["price"] - 1, 6),
        "decision_points": points,
        "headlines": headlines,
    }
    TESTDATA.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=2, ensure_ascii=False))
    return fixture


# ------------------------------------------------------------------ replay


def _score_headlines(titles: list[str]) -> tuple[float, int, int]:
    pos = neg = 0
    for t in titles:
        words = set(re.findall(r"[a-ząćęłńóśźż']+", t.lower()))
        pos += len(words & POSITIVE) > 0
        neg += len(words & NEGATIVE) > 0
    return (pos - neg) / max(1, len(titles)), pos, neg


class NewsFlowAgent:
    """The desk's real agent type today: news sentiment over the decision
    point's headline window. With no fresh coverage it keeps its last read
    (a desk doesn't forget yesterday's story at 10:01)."""

    name = "newsflow"

    def __init__(self, archive: dict[str, list[str]]):
        self.archive = archive
        self.side = Side.BUY  # neutral start
        self.titles: list[str] = []
        self.score = 0.0
        self.pos = self.neg = 0
        self.stale = True

    def load(self, window: dict) -> None:
        d = date.fromisoformat(window["start"])
        end = date.fromisoformat(window["end"])
        titles = []
        while d <= end:
            titles += self.archive.get(d.isoformat(), [])
            d += timedelta(days=1)
        self.titles = titles
        if titles:
            self.score, self.pos, self.neg = _score_headlines(titles)
            self.side = Side.BUY if self.score >= 0 else Side.SELL
            self.stale = False
        else:
            self.stale = True  # hold previous side

    def stance(self, ticker: str) -> Stance:
        return Stance(agent=self.name, ticker=ticker, side=self.side)

    def make_case(self, own, others) -> str:
        if self.stale:
            return (f"No fresh XTB coverage in the window — I keep my prior read: "
                    f"{self.side.value.upper()}.")
        sample = "; ".join(f'"{t}"' for t in self.titles[:2])
        return (
            f"{len(self.titles)} XTB stories in the window: {self.pos} positive, "
            f"{self.neg} negative (net {self.score:+.2f}). E.g. {sample}. "
            f"Coverage says {self.side.value.upper()}."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (f"Price follows the story on this name: coverage is running "
                f"{self.pos}-{self.neg} {'positive' if self.score >= 0 else 'negative'}.")


class TapeAgent:
    """STAND-IN for the incoming realtime price agent."""

    name = "tape"

    def load(self, f: dict) -> None:
        self.f = f
        signal = f["ret_last_hour"] * (1 if f["volume_ratio"] >= 0.8 else -0.5)
        self.side = Side.BUY if signal >= 0 else Side.SELL

    def stance(self, ticker: str) -> Stance:
        return Stance(agent=self.name, ticker=ticker, side=self.side)

    def make_case(self, own, others) -> str:
        f = self.f
        confirmed = f["volume_ratio"] >= 0.8
        return (
            f"Last hour {f['ret_last_hour']:+.2%} on {f['volume_ratio']:.1f}x average "
            f"bar volume. " + ("Flow confirms — go with the tape: " if confirmed
                               else "Thin flow, unconvincing move — fade it: ")
            + self.side.value.upper() + "."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (f"The freshest hour ({self.f['ret_last_hour']:+.2%}) is what we trade "
                f"into the next heartbeat; longer windows lag.")


class TrendAgent:
    """STAND-IN for the incoming historical price agent."""

    name = "trend"

    def load(self, f: dict) -> None:
        self.f = f
        self.side = Side.BUY if (f["ret_20d"] * 2 + f["ret_5d"]) >= 0 else Side.SELL

    def stance(self, ticker: str) -> Stance:
        return Stance(agent=self.name, ticker=ticker, side=self.side)

    def make_case(self, own, others) -> str:
        f = self.f
        return (
            f"5-day {f['ret_5d']:+.2%}, 20-day {f['ret_20d']:+.2%}, previous session "
            f"{f['prev_day_ret']:+.2%}. The prevailing trend says {self.side.value.upper()}."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (f"Hours are noise at this horizon; the {self.f['ret_20d']:+.2%} 20-day "
                f"trend is the tradeable signal. I stand on it.")


class OpenRangeAgent:
    """STAND-IN for the incoming intraday-structure price agent."""

    name = "openrange"

    def load(self, f: dict, label: str) -> None:
        self.f, self.label = f, label
        if label == "open+1h":
            self.side = Side.BUY if f["gap_open"] >= 0 else Side.SELL
        else:
            self.side = Side.SELL if f["ret_since_open"] >= 0 else Side.BUY

    def stance(self, ticker: str) -> Stance:
        return Stance(agent=self.name, ticker=ticker, side=self.side)

    def make_case(self, own, others) -> str:
        f = self.f
        if self.label == "open+1h":
            return (f"Opened {f['gap_open']:+.2%} vs yesterday's close; first hour "
                    f"{f['ret_since_open']:+.2%}. Gaps tend to run through the "
                    f"morning — {self.side.value.upper()}.")
        return (f"Day move {f['ret_since_open']:+.2%} since open with one hour left; "
                f"into the close I fade the stretch — {self.side.value.upper()}.")

    def rebut(self, own, opposing_case: Case) -> str:
        return (f"Session structure beats raw direction: {self.label} behavior is about "
                f"where the day's move exhausts, not where it points.")


REPLAY_DUMP = TESTDATA / "xtb_wa_60d_replay.json"


def replay(use_llm: bool = False, news_only: bool = False, dump: bool = False) -> None:
    fixture = json.loads(FIXTURE.read_text())
    points = [p for p in fixture["decision_points"] if p["outcome"]]
    judge = default_judge() if use_llm else HeuristicJudge()
    Path("/tmp/deltadesk-xtb-replay.json").unlink(missing_ok=True)
    record = TrackRecord(path="/tmp/deltadesk-xtb-replay.json", alpha=0.15)

    news = NewsFlowAgent(fixture["headlines"])
    if news_only:
        agents: dict = {news.name: news}
    else:
        tape, trend, orange = TapeAgent(), TrendAgent(), OpenRangeAgent()
        agents = {a.name: a for a in (news, tape, trend, orange)}

    desk_ret = 0.0
    hits = 0
    rows = []
    dumped = []
    for p in points:
        f = p["features"]
        news.load(p["news_window"])
        if not news_only:
            agents["tape"].load(f)
            agents["trend"].load(f)
            agents["openrange"].load(f, p["label"])
        stances = [a.stance(fixture["symbol"]) for a in agents.values()]

        room = InMemoryFloor()
        verdict = run_deliberation(p["id"], stances, agents, judge, record, room)
        tv = verdict.verdicts[0]
        fwd = p["outcome"]["forward_ret"]
        direction = 1 if tv.decision == Side.BUY else -1
        desk_ret += direction * fwd
        right = direction * fwd > 0
        hits += right

        credibility_before = {a: record.credibility(a) for a in agents}
        for s in stances:
            d = 1 if s.side == Side.BUY else -1
            record.record_outcome(s.agent, max(-1.0, min(1.0, d * fwd / 0.004)))

        rows.append((p["id"], tv.decision.value.upper(), tv.conviction, fwd, right))
        if dump:
            # Front-end embeddable record: full conversation + structured verdict.
            dumped.append({
                "id": p["id"],
                "date": p["date"],
                "label": p["label"],
                "price": p["price"],
                "transcript": [
                    {"sender": m.sender, "text": m.text, "mentions": m.mentions}
                    for m in room.history()
                ],
                "verdict": tv.model_dump(mode="json"),
                "credibility_before": credibility_before,
                "credibility_after": {a: record.credibility(a) for a in agents},
                "grading": {"forward_ret": fwd, "desk_was_right": right},
            })

    if dump:
        REPLAY_DUMP.write_text(json.dumps({
            "symbol": fixture["symbol"],
            "heartbeat": fixture["heartbeat"],
            "committee": sorted(agents),
            "buy_and_hold_ret": fixture["buy_and_hold_ret"],
            "decisions": dumped,
        }, indent=2, ensure_ascii=False))
        print(f"wrote {REPLAY_DUMP} ({len(dumped)} decisions with full transcripts)",
              file=sys.stderr)

    committee = "news agent only" if news_only else "news agent + 3 price STAND-INS (real price agents incoming)"
    print(f"REPLAY · {fixture['symbol']} · {len(points)} decisions over "
          f"{len({p['date'] for p in points})} sessions · committee: {committee} · "
          f"judge: {type(judge).__name__}")
    print("-" * 78)
    for pid, decision, conv, fwd, right in rows:
        print(f"{pid:<24} {decision:<4} conv {conv:.0%}  fwd {fwd:+.2%}  {'✓' if right else '✗'}")
    print("-" * 78)
    n = len(rows)
    print(f"hit rate: {hits}/{n} ({hits / n:.0%})")
    print(f"desk cumulative (sum of signed heartbeat returns): {desk_ret:+.2%}")
    print(f"buy-and-hold over the window:                      {fixture['buy_and_hold_ret']:+.2%}")
    print("final credibility:", {a: record.credibility(a) for a in agents})


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        fx = build()
        pts = fx["decision_points"]
        print(f"wrote {FIXTURE} — {len(pts)} decision points over "
              f"{len({p['date'] for p in pts})} sessions, "
              f"buy-and-hold {fx['buy_and_hold_ret']:+.2%}")
    elif cmd == "replay":
        if not FIXTURE.exists():
            build()
        replay(
            use_llm="--llm" in sys.argv,
            news_only="--news-only" in sys.argv,
            dump="--dump" in sys.argv,
        )
    else:
        sys.exit(f"unknown command: {cmd} (use build | replay)")


if __name__ == "__main__":
    main()
