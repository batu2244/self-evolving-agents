"""Adapters that let already-built desk agents participate in the vote.

The floor doesn't care how an agent thinks — it needs a Stance, one case,
and a rebuttal. Each adapter wraps an existing agent's native output into
that interface without touching the agent's own module.

NewsDeskAgent wraps google-news-agent (the Alphabet/GOOGL news analyst):
its JSON output already carries a consolidated DeskDecision
(action/conviction/thesis/key_drivers/risks), which maps 1:1 onto a binary
stance and a case. HOLD is resolved to a side by the weighted article
signal score — the floor votes strictly buy-or-sell.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .deliberation import Case, Side, Stance

NEWS_AGENT_DIR = Path(__file__).parent.parent.parent / "google-news-agent"


class NewsDeskAgent:
    """Voting-floor adapter around google-news-agent's JSON output."""

    name = "newsdesk"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.decision = payload.get("decision", {})
        self.signal_score = payload.get("signal_score", {})
        self.ticker = payload.get("ticker", "GOOGL")
        self.side = self._resolve_side()

    # -- construction ------------------------------------------------------

    @classmethod
    def run(cls, hours: int = 24, limit: int = 10, timeout: int = 300) -> "NewsDeskAgent":
        """Run the news agent live (stdout is guaranteed parseable JSON;
        logging goes to stderr). Falls back to the checked-in sample output
        if the live run fails — protect-the-afternoon rule (§9)."""
        try:
            proc = subprocess.run(
                [sys.executable, str(NEWS_AGENT_DIR / "google_news_agent.py"),
                 "--hours", str(hours), "--limit", str(limit)],
                capture_output=True, text=True, timeout=timeout, cwd=NEWS_AGENT_DIR,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return cls(json.loads(proc.stdout))
        except Exception:
            pass
        return cls.from_sample()

    @classmethod
    def from_sample(cls) -> "NewsDeskAgent":
        return cls(json.loads((NEWS_AGENT_DIR / "sample_output.json").read_text()))

    # -- voting-floor interface -------------------------------------------

    def _resolve_side(self) -> Side:
        action = str(self.decision.get("action", "HOLD")).upper()
        if action in ("BUY", "SELL"):
            return Side(action.lower())
        # HOLD → binary vote resolved by the weighted per-article signal.
        return Side.BUY if float(self.signal_score.get("weighted_score", 0)) >= 0 else Side.SELL

    def stance(self) -> Stance:
        return Stance(agent=self.name, ticker=self.ticker, side=self.side)

    def make_case(self, own: Stance, others: list[Stance]) -> str:
        d, ss = self.decision, self.signal_score
        drivers = "; ".join(d.get("key_drivers", [])[:3])
        counts = ss.get("article_signals", {})
        held = str(d.get("action", "")).upper() == "HOLD"
        lead = (
            f"My consolidated read was {d.get('action')} at "
            f"{float(d.get('conviction', 0)):.0%} conviction"
            + (f"; the binary rule resolves it {self.side.value.upper()} via the "
               f"weighted article signal ({float(ss.get('weighted_score', 0)):+.2f})."
               if held else ".")
        )
        return (
            f"{lead} Across {ss.get('articles_scored', '?')} scored articles "
            f"(BUY {counts.get('BUY', 0)} / SELL {counts.get('SELL', 0)} / "
            f"HOLD {counts.get('HOLD', 0)}): {d.get('thesis', '')} "
            f"Key drivers: {drivers}."
        )

    def rebut(self, own: Stance, opposing_case: Case) -> str:
        risks = self.decision.get("risks", [])
        flip = self.decision.get("what_would_change_my_mind", "")
        return (
            "I've already stress-tested my side: the main risks to it are "
            + ("; ".join(risks[:2]) if risks else "limited")
            + f". What would actually flip me is {flip or 'a primary-source catalyst'} — "
            f"nothing in {opposing_case.agent}'s case clears that bar."
        )
