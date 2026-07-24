"""One 10-minute trading cycle — the entry point for the recurring loop.

Run this every 10 minutes (cron, /loop, Guild trigger):

    python -m voting.cycle NVDA
    python -m voting.cycle BTC-USD --verbose

Each invocation:
  1. GRADES the previous cycle first: the realized move since the last
     verdict scores every agent's stance (right side of a ±0.2% move =
     full ±1) and updates its credibility — the desk digests its last
     mistake before it votes again.
  2. Runs the binary deliberation on fresh intraday data
     (stances → cases → rebuttals → LLM-judged verdict).
  3. Persists the cycle so the next run can grade it.

State lives in voting/data/cycle_state_<symbol>.json (gitignored).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from voting.deliberation import Case, Side, Stance, run_deliberation
from voting.judge import default_judge
from voting.simulate import (
    HEADERS,
    CHART_URL,
    MarketView,
    fetch_bars,
    fetch_headlines,
    headline_score,
)
from voting.track_record import TrackRecord
from voting.transport import InMemoryFloor

DATA_DIR = Path(__file__).parent / "data"
# A 0.2% move over 10 minutes counts as a full-magnitude outcome.
FULL_MOVE = 0.002


# ---------------------------------------------------------------- intraday


class IntradayView:
    """Last sessions of 5-minute bars."""

    def __init__(self, symbol: str) -> None:
        r = httpx.get(
            CHART_URL.format(symbol=symbol),
            params={"interval": "5m", "range": "5d"},
            headers=HEADERS,
            timeout=10.0,
        )
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        rows = [
            (ts, c, v)
            for ts, c, v in zip(result["timestamp"], quote["close"], quote["volume"])
            if c is not None
        ]
        self.symbol = symbol
        self.ts = [t for t, _, _ in rows]
        self.closes = [c for _, c, _ in rows]
        self.volumes = [v or 0 for _, _, v in rows]

    @property
    def price(self) -> float:
        return self.closes[-1]

    def ret_bars(self, bars: int) -> float:
        i = max(0, len(self.closes) - 1 - bars)
        return self.closes[-1] / self.closes[i] - 1

    @property
    def volume_ratio(self) -> float:
        """Last 2 bars (~10 min) vs the average 2-bar volume this window."""
        if len(self.volumes) < 12:
            return 1.0
        recent = sum(self.volumes[-2:])
        baseline = (sum(self.volumes[:-2]) / (len(self.volumes) - 2)) * 2
        return recent / baseline if baseline else 1.0

    @property
    def vwap_today(self) -> float:
        day = datetime.fromtimestamp(self.ts[-1], tz=timezone.utc).date()
        pv = vol = 0.0
        for t, c, v in zip(self.ts, self.closes, self.volumes):
            if datetime.fromtimestamp(t, tz=timezone.utc).date() == day:
                pv += c * v
                vol += v
        return pv / vol if vol else self.price


# ------------------------------------------------------- 10-minute analysts


class RealtimeAgent:
    name = "realtime"

    def __init__(self, iv: IntradayView):
        self.iv = iv
        self.r10 = iv.ret_bars(2)  # last ~10 minutes
        signal = self.r10 * (1 if iv.volume_ratio >= 0.8 else -0.5)
        self.side = Side.BUY if signal >= 0 else Side.SELL

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.iv.symbol, side=self.side)

    def make_case(self, own, others) -> str:
        iv = self.iv
        confirmed = iv.volume_ratio >= 0.8
        return (
            f"Last 10 minutes: {self.r10:+.3%} to {iv.price:.2f} on "
            f"{iv.volume_ratio:.1f}x recent volume. "
            + (f"Flow confirms — {self.side.value.upper()} with the tape."
               if confirmed else
               f"Thin flow makes the move unconvincing — I fade it: {self.side.value.upper()}.")
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (
            f"At a 10-minute horizon the tape IS the fundamentals: {self.r10:+.3%} on "
            f"{self.iv.volume_ratio:.1f}x flow is happening now; everything else is lag."
        )


class HistoricalAgent:
    name = "historical"

    def __init__(self, iv: IntradayView, mv: MarketView):
        self.iv, self.mv = iv, mv
        self.r60 = iv.ret_bars(12)  # last hour
        self.above_vwap = iv.price >= iv.vwap_today
        # Hour trend, tie broken by VWAP side.
        self.side = (
            Side.BUY if (self.r60 > 0 or (self.r60 == 0 and self.above_vwap)) else Side.SELL
        )

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.iv.symbol, side=self.side)

    def make_case(self, own, others) -> str:
        return (
            f"Hour trend {self.r60:+.3%}; price is "
            f"{'above' if self.above_vwap else 'below'} today's VWAP "
            f"({self.iv.vwap_today:.2f}); daily context: 5-day {self.mv.ret(5):+.2%}, "
            f"20-day {self.mv.ret(20):+.2%}. Intraday structure says "
            f"{self.side.value.upper()}."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (
            f"Two bars of tape is noise. The hour trend ({self.r60:+.3%}) and the VWAP "
            f"side are the tradeable structure at this cadence."
        )


class SentimentAgent:
    name = "sentiment"

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.headlines = fetch_headlines(symbol)  # last 48h window
        self.score, self.pos, self.neg = headline_score(self.headlines)
        self.side = Side.BUY if self.score >= 0 else Side.SELL

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.symbol, side=self.side)

    def make_case(self, own, others) -> str:
        sample = "; ".join(f'"{h}"' for h in self.headlines[:2]) or "(no headlines)"
        return (
            f"News backdrop: {self.pos} positive vs {self.neg} negative of "
            f"{len(self.headlines)} recent headlines (net {self.score:+.2f}). "
            f"E.g. {sample}. Backdrop says {self.side.value.upper()}."
        )

    def rebut(self, own, opposing_case: Case) -> str:
        return (
            f"Ten-minute bars mean nothing against the narrative: coverage is running "
            f"{self.pos}-{self.neg} {'positive' if self.score >= 0 else 'negative'}."
        )


# ----------------------------------------------------------- cycle machinery


def _state_path(symbol: str) -> Path:
    return DATA_DIR / f"cycle_state_{symbol.replace('/', '-')}.json"


def grade_previous(symbol: str, price_now: float, record: TrackRecord) -> dict | None:
    path = _state_path(symbol)
    if not path.exists():
        return None
    prev = json.loads(path.read_text())
    move = price_now / prev["price"] - 1
    graded = []
    for s in prev["stances"]:
        direction = 1 if s["side"] == "buy" else -1
        score = max(-1.0, min(1.0, direction * move / FULL_MOVE))
        cred = record.record_outcome(s["agent"], score)
        graded.append({"agent": s["agent"], "side": s["side"], "score": round(score, 3),
                       "credibility": cred})
    desk_dir = 1 if prev["decision"] == "buy" else -1
    return {
        "cycle_id": prev["cycle_id"],
        "move": move,
        "desk_was_right": desk_dir * move > 0,
        "graded": graded,
    }


def save_state(symbol: str, cycle_id: str, price: float, stances, decision: Side) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(symbol).write_text(json.dumps({
        "cycle_id": cycle_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "price": price,
        "stances": [{"agent": s.agent, "side": s.side.value} for s in stances],
        "decision": decision.value,
    }, indent=2))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    symbol = args[0] if args else "NVDA"
    verbose = "--verbose" in sys.argv

    iv = IntradayView(symbol)
    mv = fetch_bars(symbol)
    record = TrackRecord(
        path=DATA_DIR / f"track_record_{symbol.replace('/', '-')}.json",
        alpha=0.1,  # 10-minute cadence: damp per-cycle noise
    )

    now = datetime.now(timezone.utc)
    cycle_id = f"{symbol.lower()}-{now.strftime('%Y%m%d-%H%M')}"
    print(f"── cycle {cycle_id} · price {iv.price:.2f} ──")

    # 1. Grade the previous cycle before voting again.
    grading = grade_previous(symbol, iv.price, record)
    if grading:
        verdict_mark = "✓" if grading["desk_was_right"] else "✗"
        print(f"grading {grading['cycle_id']}: move {grading['move']:+.3%} → desk {verdict_mark}")
        for g in grading["graded"]:
            print(f"  {g['agent']:<11} {g['side']:<4} scored {g['score']:+.2f} "
                  f"→ credibility {g['credibility']:.2f}")
    else:
        print("first cycle for this symbol — nothing to grade yet")

    # 2. Deliberate on fresh data.
    agents = {
        "realtime": RealtimeAgent(iv),
        "historical": HistoricalAgent(iv, mv),
        "sentiment": SentimentAgent(symbol),
    }
    if symbol.upper() in ("GOOGL", "GOOG"):
        from voting.adapters import NewsDeskAgent

        newsdesk = NewsDeskAgent.from_sample()
        newsdesk.ticker = symbol
        agents["newsdesk"] = newsdesk

    stances = [a.stance() for a in agents.values()]
    room = InMemoryFloor()
    verdict = run_deliberation(cycle_id, stances, agents, default_judge(), record, room)

    if verbose:
        for m in room.history():
            print(f"\n[{m.sender}]")
            print(m.text.split("```json")[0].rstrip())

    tv = verdict.verdicts[0]
    flag = " UNANIMOUS" if tv.unanimous else ""
    print(f"decision: {tv.decision.value.upper()} {symbol} "
          f"at {tv.conviction:.0%} conviction{flag}")
    print("  " + ", ".join(f"{a} {w:.0%}" for a, w in sorted(tv.contributions.items())))

    # 3. Persist for the next run to grade.
    save_state(symbol, cycle_id, iv.price, stances, tv.decision)


if __name__ == "__main__":
    main()
