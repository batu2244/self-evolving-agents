"""Testing set for the app: XTB (Warsaw) with a twice-daily heartbeat.

The desk trades XTB.WA every session at two decision points:
  open+1h   10:00 Europe/Warsaw (WSE opens 09:00)
  close-1h  16:00 Europe/Warsaw (WSE closes 17:00)

`build` turns the last ~30 days of real hourly bars into a fixture: one
record per decision point with the features each agent would have seen AT
that moment (no lookahead) and the realized forward return to the next
decision point (the grading label).

`replay` runs the voting committee over the fixture in order — grade the
previous decision, vote, repeat — exactly the loop the app runs live,
compressed to seconds. Deterministic by default (heuristic judge);
--llm uses the OpenRouter judge.

    python -m voting.testset build            # writes voting/testdata/xtb_wa_30d.json
    python -m voting.testset replay           # runs the committee over it
    python -m voting.testset replay --llm

Note: headline history can't be reconstructed after the fact, so the
backtest committee is the three price-based archetypes (tape / trend /
open-range); the live sentiment and news agents only vote in live cycles.
"""

from __future__ import annotations

import json
import sys
import zoneinfo
from datetime import datetime
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

TESTDATA = Path(__file__).parent / "testdata"
FIXTURE = TESTDATA / "xtb_wa_30d.json"

# Decision prices come from the close of the bar ENDING at the decision time:
# 10:00 decision = close of the 09:00-10:00 bar; 16:00 = the 15:00-16:00 bar.
DECISIONS = [("open+1h", 9), ("close-1h", 15)]


def _fetch(symbol: str, interval: str, range_: str) -> dict:
    r = httpx.get(
        CHART_URL.format(symbol=symbol),
        params={"interval": interval, "range": range_},
        headers=HEADERS,
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()["chart"]["result"][0]


def build() -> dict:
    hourly = _fetch(SYMBOL, "60m", "1mo")
    daily = _fetch(SYMBOL, "1d", "6mo")
    hq = hourly["indicators"]["quote"][0]

    # Group hourly bars by Warsaw session date.
    days: dict[str, list[dict]] = {}
    for ts, o, c, v in zip(hourly["timestamp"], hq["open"], hq["close"], hq["volume"]):
        if c is None or o is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=TZ)
        days.setdefault(dt.strftime("%Y-%m-%d"), []).append(
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
    for date in sorted(days):
        bars = {b["hour"]: b for b in days[date]}
        prior = [d for d in daily_dates if d < date]
        if len(prior) < 21 or 9 not in bars:
            continue
        prev_close = daily_closes[prior[-1]]
        day_open = bars[9]["open"]

        for label, end_hour in DECISIONS:
            if end_hour not in bars:
                continue
            price = bars[end_hour]["close"]
            prev_bar = bars.get(end_hour - 1)
            session_so_far = [b for h, b in sorted(bars.items()) if h <= end_hour]
            points.append({
                "id": f"{date}-{label}",
                "date": date,
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
                    "session_bars": len(session_so_far),
                },
                "outcome": None,  # filled below
            })

    for i in range(len(points) - 1):
        nxt = points[i + 1]
        points[i]["outcome"] = {
            "forward_ret": round(nxt["price"] / points[i]["price"] - 1, 6),
            "next_point": nxt["id"],
            "overnight": nxt["date"] != points[i]["date"],
        }

    fixture = {
        "symbol": SYMBOL,
        "exchange": "Warsaw Stock Exchange (GPW)",
        "currency": "PLN",
        "heartbeat": "twice daily: 10:00 (open+1h) and 16:00 (close-1h) Europe/Warsaw",
        "generated_at": datetime.now(TZ).isoformat(),
        "buy_and_hold_ret": round(points[-1]["price"] / points[0]["price"] - 1, 6),
        "decision_points": points,
    }
    TESTDATA.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=2))
    return fixture


# ------------------------------------------------------------------ replay


class TapeAgent:
    """Last hour of tape, volume-confirmed; thin-volume moves get faded."""

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
    """Multi-day trend follower."""

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
    """Morning: follow the opening gap. Afternoon: fade the day's move into
    the close (open-range reversion)."""

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
                    f"{f['ret_since_open']:+.2%}. Gaps at this exchange tend to run "
                    f"through the morning — {self.side.value.upper()}.")
        return (f"Day move {f['ret_since_open']:+.2%} since open with one hour left; "
                f"into the close I fade the stretch — {self.side.value.upper()}.")

    def rebut(self, own, opposing_case: Case) -> str:
        return (f"Session structure beats raw direction: {self.label} behavior is about "
                f"where the day's move exhausts, not where it points.")


def replay(use_llm: bool = False) -> None:
    fixture = json.loads(FIXTURE.read_text())
    points = [p for p in fixture["decision_points"] if p["outcome"]]
    judge = default_judge() if use_llm else HeuristicJudge()
    record = TrackRecord(path="/tmp/deltadesk-xtb-replay.json", alpha=0.15)
    Path("/tmp/deltadesk-xtb-replay.json").unlink(missing_ok=True)
    record = TrackRecord(path="/tmp/deltadesk-xtb-replay.json", alpha=0.15)

    tape, trend, orange = TapeAgent(), TrendAgent(), OpenRangeAgent()
    agents = {a.name: a for a in (tape, trend, orange)}

    desk_ret = 0.0
    hits = 0
    rows = []
    for p in points:
        f = p["features"]
        tape.load(f)
        trend.load(f)
        orange.load(f, p["label"])
        stances = [a.stance(fixture["symbol"]) for a in agents.values()]

        verdict = run_deliberation(p["id"], stances, agents, judge, record, InMemoryFloor())
        tv = verdict.verdicts[0]
        fwd = p["outcome"]["forward_ret"]
        direction = 1 if tv.decision == Side.BUY else -1
        desk_ret += direction * fwd
        right = direction * fwd > 0
        hits += right

        # Grade every stance against the realized move (the live loop's step 1).
        for s in stances:
            d = 1 if s.side == Side.BUY else -1
            record.record_outcome(s.agent, max(-1.0, min(1.0, d * fwd / 0.004)))

        rows.append((p["id"], tv.decision.value.upper(), tv.conviction, fwd, right))

    print(f"REPLAY · {fixture['symbol']} · {len(points)} decisions "
          f"({fixture['heartbeat']}) · judge: {type(judge).__name__}")
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
        replay(use_llm="--llm" in sys.argv)
    else:
        sys.exit(f"unknown command: {cmd} (use build | replay)")


if __name__ == "__main__":
    main()
