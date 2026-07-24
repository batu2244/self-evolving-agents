"""Deterministic, data-aware selection of each agent's analysis equation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import config


@dataclass(frozen=True)
class ModeSelection:
    agent: str
    policy: str
    equation: str
    reason: str
    evidence: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def _configured(agent: str) -> ModeSelection:
    equation = config.EQUATION_BY_AGENT[agent]
    return ModeSelection(
        agent=agent,
        policy="configured",
        equation=equation,
        reason="A named equation was locked for this run.",
        evidence={"configured_equation": equation},
    )


def select_historical(
    closes: Sequence[float], policy: str | None = None
) -> ModeSelection:
    policy = policy or config.ANALYSIS_POLICY_BY_AGENT["historical"]
    if policy == "configured":
        return _configured("historical")

    count = len(closes)
    if count < config.MA_LONG:
        return ModeSelection(
            "historical", policy, "slope_only",
            "History is too short for the long moving average, so slope is the usable trend read.",
            {"closes": count, "required_for_ma": config.MA_LONG},
        )

    # The derivation computes this identically for every candidate, so inspect
    # one candidate to identify a stretched final move.
    import derive

    baseline = derive.derive_trend(closes, equation="trend_blend")
    if baseline["mean_reversion_flag"]:
        return ModeSelection(
            "historical", policy, "ma_cross",
            "The latest return is stretched, so the established moving-average regime gets priority.",
            {
                "closes": count,
                "mean_reversion_z": baseline["mean_reversion_z"],
                "threshold": config.MEAN_REVERSION_Z,
                "ma_relationship": baseline["ma_relationship"],
            },
        )
    return ModeSelection(
        "historical", policy, "trend_blend",
        "Enough history is available and no stretch regime is active, so slope and moving averages are blended.",
        {
            "closes": count,
            "mean_reversion_z": baseline["mean_reversion_z"],
            "ma_relationship": baseline["ma_relationship"],
        },
    )


def select_realtime(quote: dict, policy: str | None = None) -> ModeSelection:
    policy = policy or config.ANALYSIS_POLICY_BY_AGENT["realtime"]
    if policy == "configured":
        return _configured("realtime")

    has_open = bool(quote.get("open"))
    has_previous = bool(quote.get("previous_close"))
    if has_open and not has_previous:
        return ModeSelection(
            "realtime", policy, "open_weighted",
            "Only the session open is available as a valid reference.",
            {"has_open": True, "has_previous_close": False},
        )
    if has_previous and not has_open:
        return ModeSelection(
            "realtime", policy, "previous_close_only",
            "Only the previous close is available as a valid reference.",
            {"has_open": False, "has_previous_close": True},
        )

    import derive

    baseline = derive.derive_momentum(quote, equation="balanced_momentum")
    vs_open = baseline["vs_open_pct"]
    vs_previous = baseline["vs_prev_close_pct"]
    references_conflict = vs_open * vs_previous < 0
    if references_conflict:
        reason = "Open and previous-close comparisons disagree, so neither reference is allowed to dominate."
    else:
        reason = "Both quote references are available and directionally compatible, so they are balanced."
    return ModeSelection(
        "realtime", policy, "balanced_momentum", reason,
        {
            "has_open": has_open,
            "has_previous_close": has_previous,
            "vs_open_pct": vs_open,
            "vs_previous_close_pct": vs_previous,
            "references_conflict": references_conflict,
            "volume_anomaly": baseline["volume_anomaly"],
        },
    )


def select_news(
    decision: dict, signal_score: dict, policy: str | None = None
) -> ModeSelection:
    policy = policy or config.ANALYSIS_POLICY_BY_AGENT["news"]
    if policy == "configured":
        return _configured("news")

    scored = int(signal_score.get("articles_scored", 0) or 0)
    weighted = float(signal_score.get("weighted_score", 0.0) or 0.0)
    action = str(decision.get("action", "HOLD")).upper()
    conviction = float(decision.get("conviction", 0.0) or 0.0)
    evidence = {
        "articles_scored": scored,
        "weighted_score": weighted,
        "action": action,
        "conviction": conviction,
    }
    if 0 < scored <= config.NEWS_THIN_COVERAGE_MAX:
        return ModeSelection(
            "news", policy, "article_count_fade",
            "Coverage is thin, so the mechanical score is faded by article breadth.",
            evidence,
        )
    action_has_signal = (
        action in {"BUY", "SELL"}
        and conviction >= config.NEWS_ACTION_CONVICTION_MIN
    )
    action_sign = 1 if action == "BUY" else (-1 if action == "SELL" else 0)
    score_conflicts = bool(weighted and action_sign and (weighted > 0) != (action_sign > 0))
    if action_has_signal and (
        abs(weighted) < config.NEWS_WEAK_SCORE_ABS or score_conflicts
    ):
        return ModeSelection(
            "news", policy, "action_conviction_blend",
            "A high-conviction desk action adds information that the weak or conflicting score does not capture.",
            {**evidence, "score_conflicts_with_action": score_conflicts},
        )
    return ModeSelection(
        "news", policy, "weighted_score",
        "Article breadth is sufficient and the mechanical score is consistent with the desk decision.",
        evidence,
    )


def select_forecaster(signals: dict, policy: str | None = None) -> ModeSelection:
    policy = policy or config.ANALYSIS_POLICY_BY_AGENT["forecaster"]
    if policy == "configured":
        return _configured("forecaster")

    present = {
        source: signal for source, signal in signals.items()
        if source in config.SIGNAL_SOURCES
    }
    nonzero = [signal for signal in present.values() if signal.direction != 0]
    signs = {1 if signal.direction > 0 else -1 for signal in nonzero}
    mean_confidence = (
        sum(signal.confidence for signal in present.values()) / len(present)
        if present else 0.0
    )
    evidence = {
        "sources": sorted(present),
        "reporting_sources": len(present),
        "directional_sources": len(nonzero),
        "direction_signs": sorted(signs),
        "mean_confidence": round(mean_confidence, 4),
    }
    if len(signs) > 1:
        return ModeSelection(
            "forecaster", policy, "consensus",
            "Analysts disagree on direction, so dissenting weight dampens the combined forecast.",
            evidence,
        )
    if len(nonzero) >= 2 and mean_confidence < config.FORECAST_LOW_CONFIDENCE:
        return ModeSelection(
            "forecaster", policy, "direction_only",
            "Several analysts agree but report low confidence, so this mode tests directional agreement separately from calibration.",
            evidence,
        )
    return ModeSelection(
        "forecaster", policy, "confidence_weighted",
        "Analyst directions are compatible, so calibrated confidence scales each contribution.",
        evidence,
    )
