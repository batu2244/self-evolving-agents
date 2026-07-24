"""Managed US examples: GOOGL and NVDA at the same twice-daily heartbeat.

Same cadence as the XTB set (voting/testset.py): trade one hour after the
open and one hour before the close — for Nasdaq that's 10:30 and 15:00
America/New_York. Fixtures use the exact same schema as the XTB one
(feature keys included; `ret_last_hour` means "last intraday bar" — here a
30-minute bar, Yahoo's finest granularity that still reaches back ~59
days, the practical limit of the requested 60-day window).

    python -m voting.markets build all         # GOOGL + NVDA fixtures
    python -m voting.markets build NVDA
    python -m voting.markets replay GOOGL [--news-only] [--llm] [--dump]

Replays reuse the committee from voting/testset.py: newsflow (the real
news agent type, voting from each fixture's stored English-locale headline
archive) plus the three price STAND-INS for the incoming price agents.
"""

from __future__ import annotations

import json
import re
import sys
import time
import zoneinfo
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from voting.deliberation import Side, run_deliberation
from voting.judge import HeuristicJudge, default_judge
from voting.testset import (
    HEADERS,
    CHART_URL,
    NEWS_URL,
    NEWS_LOOKBACK_DAYS,
    TESTDATA,
    NewsFlowAgent,
    OpenRangeAgent,
    TapeAgent,
    TrendAgent,
)
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor


@dataclass
class Market:
    symbol: str
    exchange: str
    currency: str
    tz: str
    open_time: str
    interval_min: int
    lookback_days: int
    decisions: list[tuple[str, str]]  # (label, decision time "HH:MM" local)
    news_query: str
    news_locale: dict[str, str]
    news_keywords: list[str]
    fixture: str

    @property
    def zone(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.tz)


# Yahoo serves 30m bars ~59 days back — the practical floor of "last 60 days".
MARKETS: dict[str, Market] = {
    "GOOGL": Market(
        symbol="GOOGL", exchange="Nasdaq", currency="USD",
        tz="America/New_York", open_time="09:30", interval_min=30, lookback_days=59,
        decisions=[("open+1h", "10:30"), ("close-1h", "15:00")],
        news_query="Alphabet OR GOOGL stock",
        news_locale={"hl": "en-US", "gl": "US", "ceid": "US:en"},
        news_keywords=["googl", "google", "alphabet"],
        fixture="googl_60d.json",
    ),
    "NVDA": Market(
        symbol="NVDA", exchange="Nasdaq", currency="USD",
        tz="America/New_York", open_time="09:30", interval_min=30, lookback_days=59,
        decisions=[("open+1h", "10:30"), ("close-1h", "15:00")],
        news_query="Nvidia OR NVDA stock",
        news_locale={"hl": "en-US", "gl": "US", "ceid": "US:en"},
        news_keywords=["nvda", "nvidia"],
        fixture="nvda_60d.json",
    ),
}


def _fetch_day_headlines(m: Market, day: str, limit: int = 8) -> list[str]:
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    try:
        r = httpx.get(
            NEWS_URL,
            params={"q": f"{m.news_query} after:{day} before:{nxt}", **m.news_locale},
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
        body = t.rsplit(" - ", 1)[0].lower()
        if any(k in body for k in m.news_keywords):
            out.append(t)
    return out[:limit]


def build(symbol: str) -> dict:
    m = MARKETS[symbol]
    now = int(time.time())
    r = httpx.get(
        CHART_URL.format(symbol=m.symbol),
        params={"interval": f"{m.interval_min}m",
                "period1": now - m.lookback_days * 86400, "period2": now},
        headers=HEADERS, timeout=20.0,
    )
    r.raise_for_status()
    intraday = r.json()["chart"]["result"][0]
    r = httpx.get(
        CHART_URL.format(symbol=m.symbol),
        params={"interval": "1d", "range": "6mo"},
        headers=HEADERS, timeout=20.0,
    )
    r.raise_for_status()
    daily = r.json()["chart"]["result"][0]
    hq = intraday["indicators"]["quote"][0]

    # Bars grouped by session date, keyed by bar START "HH:MM" local.
    days: dict[str, dict[str, dict]] = {}
    day_opens: dict[str, float] = {}
    for ts, o, c, v in zip(intraday["timestamp"], hq["open"], hq["close"], hq["volume"]):
        if c is None or o is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=m.zone)
        key = dt.strftime("%Y-%m-%d")
        start = dt.strftime("%H:%M")
        days.setdefault(key, {})[start] = {"open": o, "close": c, "volume": v or 0}
        if start == m.open_time:
            day_opens[key] = o

    dq = daily["indicators"]["quote"][0]
    daily_closes: dict[str, float] = {}
    for ts, c in zip(daily["timestamp"], dq["close"]):
        if c is not None:
            daily_closes[datetime.fromtimestamp(ts, tz=m.zone).strftime("%Y-%m-%d")] = c
    daily_dates = sorted(daily_closes)

    all_volumes = [b["volume"] for bars in days.values() for b in bars.values() if b["volume"]]
    avg_bar_volume = sum(all_volumes) / max(1, len(all_volumes))

    def bar_start_for(decision_time: str) -> str:
        h, mm = map(int, decision_time.split(":"))
        return (datetime(2000, 1, 1, h, mm) - timedelta(minutes=m.interval_min)).strftime("%H:%M")

    points: list[dict] = []
    for d in sorted(days):
        bars = days[d]
        prior = [x for x in daily_dates if x < d]
        if len(prior) < 21 or d not in day_opens:
            continue
        prev_close = daily_closes[prior[-1]]
        day_open = day_opens[d]

        for label, dtime in m.decisions:
            bar = bars.get(bar_start_for(dtime))
            if bar is None:
                continue  # holiday / short session
            price = bar["close"]
            prev_bar = bars.get(bar_start_for(bar_start_for(dtime)))
            lookback_start = (date.fromisoformat(d) - timedelta(days=NEWS_LOOKBACK_DAYS)).isoformat()
            news_end = prior[-1] if label == "open+1h" else d
            points.append({
                "id": f"{d}-{label}",
                "date": d,
                "label": label,
                "time_local": dtime,
                "price": round(price, 4),
                "features": {
                    # Same schema as the XTB fixture; "hour" = last bar here.
                    "ret_last_hour": round(price / prev_bar["close"] - 1, 6) if prev_bar else round(price / day_open - 1, 6),
                    "ret_since_open": round(price / day_open - 1, 6),
                    "gap_open": round(day_open / prev_close - 1, 6),
                    "prev_day_ret": round(prev_close / daily_closes[prior[-2]] - 1, 6),
                    "ret_5d": round(prev_close / daily_closes[prior[-6]] - 1, 6),
                    "ret_20d": round(prev_close / daily_closes[prior[-21]] - 1, 6),
                    "volume_ratio": round(bar["volume"] / avg_bar_volume, 3) if avg_bar_volume else 1.0,
                },
                "news_window": {"start": lookback_start, "end": news_end},
                "outcome": None,
            })

    for i in range(len(points) - 1):
        nxt = points[i + 1]
        points[i]["outcome"] = {
            "forward_ret": round(nxt["price"] / points[i]["price"] - 1, 6),
            "next_point": nxt["id"],
            "overnight": nxt["date"] != points[i]["date"],
        }

    first = min(p["news_window"]["start"] for p in points)
    last = max(p["news_window"]["end"] for p in points)
    headlines: dict[str, list[str]] = {}
    day = date.fromisoformat(first)
    while day <= date.fromisoformat(last):
        key = day.isoformat()
        headlines[key] = _fetch_day_headlines(m, key)
        time.sleep(0.15)
        day += timedelta(days=1)
    with_news = sum(1 for v in headlines.values() if v)
    print(f"{m.symbol} news archive: {with_news}/{len(headlines)} days have coverage",
          file=sys.stderr)

    fixture = {
        "symbol": m.symbol,
        "exchange": m.exchange,
        "currency": m.currency,
        "heartbeat": f"twice daily: {m.decisions[0][1]} (open+1h) and "
                     f"{m.decisions[1][1]} (close-1h) {m.tz}",
        "window_days": m.lookback_days,
        "generated_at": datetime.now(m.zone).isoformat(),
        "buy_and_hold_ret": round(points[-1]["price"] / points[0]["price"] - 1, 6),
        "decision_points": points,
        "headlines": headlines,
    }
    TESTDATA.mkdir(parents=True, exist_ok=True)
    (TESTDATA / m.fixture).write_text(json.dumps(fixture, indent=2, ensure_ascii=False))
    return fixture


def replay(symbol: str, use_llm: bool = False, news_only: bool = False,
           dump: bool = False) -> None:
    m = MARKETS[symbol]
    fixture_path = TESTDATA / m.fixture
    if not fixture_path.exists():
        build(symbol)
    fixture = json.loads(fixture_path.read_text())
    points = [p for p in fixture["decision_points"] if p["outcome"]]
    judge = default_judge() if use_llm else HeuristicJudge()
    tr_path = Path(f"/tmp/deltadesk-{symbol.lower()}-replay.json")
    tr_path.unlink(missing_ok=True)
    record = TrackRecord(path=tr_path, alpha=0.15)

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
            dumped.append({
                "id": p["id"],
                "date": p["date"],
                "label": p["label"],
                "price": p["price"],
                "transcript": [
                    {"sender": msg.sender, "text": msg.text, "mentions": msg.mentions}
                    for msg in room.history()
                ],
                "verdict": tv.model_dump(mode="json"),
                "credibility_before": credibility_before,
                "credibility_after": {a: record.credibility(a) for a in agents},
                "grading": {"forward_ret": fwd, "desk_was_right": right},
            })

    if dump:
        dump_path = TESTDATA / m.fixture.replace(".json", "_replay.json")
        dump_path.write_text(json.dumps({
            "symbol": fixture["symbol"],
            "heartbeat": fixture["heartbeat"],
            "committee": sorted(agents),
            "buy_and_hold_ret": fixture["buy_and_hold_ret"],
            "decisions": dumped,
        }, indent=2, ensure_ascii=False))
        print(f"wrote {dump_path} ({len(dumped)} decisions with full transcripts)",
              file=sys.stderr)

    committee = "news agent only" if news_only else "news agent + 3 price STAND-INS"
    print(f"REPLAY · {fixture['symbol']} · {len(points)} decisions over "
          f"{len({p['date'] for p in points})} sessions · committee: {committee} · "
          f"judge: {type(judge).__name__}")
    n = len(rows)
    print(f"hit rate: {hits}/{n} ({hits / n:.0%})")
    print(f"desk cumulative (sum of signed heartbeat returns): {desk_ret:+.2%}")
    print(f"buy-and-hold over the window:                      {fixture['buy_and_hold_ret']:+.2%}")
    print("final credibility:", {a: record.credibility(a) for a in agents})


# ------------------------------------------------------------------- push


def push(symbol: str, api_url: str = "http://127.0.0.1:8000") -> None:
    """Load a US example's replayed history into the running API so the
    dashboard shows its memos, outcomes and floor conversation. Same flow as
    voting.testset push (XTB), minus the portfolio section — the ledger has
    a single tracker and XTB owns it; the US examples surface through the
    memo/outcome/floor views. Idempotent per ticker."""
    from voting.testset import _fence_payload
    from voting.types import DecisionMemo, Direction, SizeClass, TickerDecision, Vote

    m = MARKETS[symbol]
    dump_path = TESTDATA / m.fixture.replace(".json", "_replay.json")
    dump = json.loads(dump_path.read_text())
    decisions = dump["decisions"]
    times = dict(m.decisions)  # label -> "HH:MM" local

    def decision_ts(pid: str) -> str:
        d, label = pid[:10], pid[11:]
        h, mm = map(int, times[label].split(":"))
        y, mo, day = map(int, d.split("-"))
        return datetime(y, mo, day, h, mm, tzinfo=m.zone).isoformat()

    memos, outcomes, floor = [], [], []
    for i, dec in enumerate(decisions):
        ts = decision_ts(dec["id"])
        graded_at = decision_ts(decisions[i + 1]["id"]) if i + 1 < len(decisions) else ts
        tv = dec["verdict"]
        fwd = dec["grading"]["forward_ret"]

        sides: dict[str, str] = {}
        cases: dict[str, str] = {}
        for msg in dec["transcript"]:
            floor.append(msg)
            payload = _fence_payload(msg["text"])
            if "STANCE" in msg["text"] and "side" in payload:
                sides[payload["agent"]] = payload["side"]
            elif "CASE" in msg["text"] and "argument" in payload:
                cases[payload["agent"]] = payload["argument"]

        score_by = {s["agent"]: s["score"] for s in tv["scores"]}
        votes = []
        for agent, side in sides.items():
            conf = max(score_by.get(agent, 0.5), 0.05)
            votes.append(Vote(
                analyst=agent, ticker=symbol, direction=Direction(side),
                signal=conf if side == "buy" else -conf, confidence=conf,
                size_class=SizeClass.HALF, rationale=cases.get(agent, ""),
            ))
        memos.append(DecisionMemo(
            cycle_id=dec["id"], as_of=ts,
            decisions=[TickerDecision(
                ticker=symbol, direction=Direction(tv["decision"]),
                size_factor=tv["conviction"], vote_share=tv["conviction"],
                unanimous=tv["unanimous"], votes=votes,
            )],
            weights=tv["contributions"],
            narrative=f"Desk decision: {tv['decision'].upper()} {symbol} "
                      f"at {tv['conviction']:.0%} conviction.",
        ).model_dump(mode="json"))

        for agent, side in sides.items():
            d = 1 if side == "buy" else -1
            outcomes.append({
                "agent": agent, "score": max(-1.0, min(1.0, d * fwd / 0.004)),
                "ticker": symbol,
                "credibility": dec["credibility_after"].get(agent, 0.55),
                "ts": graded_at,
            })

    record_state = {
        agent: {"ew_score": ((cred - 0.1) / 0.9) * 2 - 1, "executions": len(decisions)}
        for agent, cred in decisions[-1]["credibility_after"].items()
    }

    with httpx.Client(base_url=api_url, timeout=60.0) as client:
        r = client.post("/api/voting/replay/load", json={
            "memos": memos, "outcomes": outcomes, "floor": floor,
            "record": record_state,
        })
        r.raise_for_status()
        print(f"pushed {symbol} to {api_url}:", r.json())


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmd = args[0] if args else "build"
    symbol = (args[1] if len(args) > 1 else "ALL").upper()

    if cmd == "push":
        for sym in (list(MARKETS) if symbol == "ALL" else [symbol]):
            push(sym)
        return
    if cmd == "build":
        for sym in (list(MARKETS) if symbol == "ALL" else [symbol]):
            fx = build(sym)
            pts = fx["decision_points"]
            print(f"wrote {MARKETS[sym].fixture} — {len(pts)} decision points over "
                  f"{len({p['date'] for p in pts})} sessions, "
                  f"buy-and-hold {fx['buy_and_hold_ret']:+.2%}")
    elif cmd == "replay":
        for sym in (list(MARKETS) if symbol == "ALL" else [symbol]):
            replay(
                sym,
                use_llm="--llm" in sys.argv,
                news_only="--news-only" in sys.argv,
                dump="--dump" in sys.argv,
            )
    else:
        sys.exit(f"unknown command: {cmd} (use build|replay|push [SYMBOL|all])")


if __name__ == "__main__":
    main()
