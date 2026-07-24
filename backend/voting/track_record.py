"""Per-agent credibility from execution history.

Agents execute every cycle, so each one accumulates a track record. An
agent's *credibility* is an exponentially-weighted average of its past
outcome scores mapped into [CREDIBILITY_FLOOR, 1.0] — agents that performed
poorly recently carry less weight in the verdict, but are never fully
silenced (they may be right tomorrow; see solution-design §6 Loop 1).

Outcome scores are signed hits in [-1, +1]: after the outcome window, the
evaluator scores each agent's proposed position change against the realized
move (direction · magnitude, like §5 attribution) and records it here.
"""

from __future__ import annotations

import json
from pathlib import Path

# One bad day moves credibility a little; a streak moves it a lot (damping).
EW_ALPHA = 0.3
CREDIBILITY_FLOOR = 0.1
# Fresh agents start mid-pack: no bonus for being new, no penalty either.
NEUTRAL_SCORE = 0.0

DEFAULT_STORE = Path(__file__).parent / "data" / "track_record.json"


class TrackRecord:
    def __init__(self, path: Path | str | None = None, alpha: float = EW_ALPHA) -> None:
        # alpha tunes to cadence: ~0.3 for daily cycles, ~0.1 for 10-minute
        # cycles (so one noisy bar doesn't crater an agent's standing).
        self._path = Path(path) if path else DEFAULT_STORE
        self._alpha = alpha
        self._ew: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._load()

    def record_outcome(self, agent: str, score: float) -> float:
        """Record a signed outcome score in [-1, +1]; returns new credibility."""
        score = max(-1.0, min(1.0, score))
        prev = self._ew.get(agent, NEUTRAL_SCORE)
        self._ew[agent] = (1 - self._alpha) * prev + self._alpha * score
        self._counts[agent] = self._counts.get(agent, 0) + 1
        self._save()
        return self.credibility(agent)

    def credibility(self, agent: str) -> float:
        """Map the EW score from [-1, 1] to [floor, 1.0]."""
        ew = self._ew.get(agent, NEUTRAL_SCORE)
        scaled = (ew + 1) / 2  # -> [0, 1]
        return round(CREDIBILITY_FLOOR + (1 - CREDIBILITY_FLOOR) * scaled, 4)

    def executions(self, agent: str) -> int:
        return self._counts.get(agent, 0)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        agents = set(self._ew) | set(self._counts)
        return {
            a: {
                "credibility": self.credibility(a),
                "ew_score": round(self._ew.get(a, NEUTRAL_SCORE), 4),
                "executions": self.executions(a),
            }
            for a in sorted(agents)
        }

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            self._ew = data.get("ew", {})
            self._counts = data.get("counts", {})

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"ew": self._ew, "counts": self._counts}, indent=2))
