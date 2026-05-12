"""Quarter-over-quarter comparison: deltas, biggest shifts, and the
composite defensiveness rise/fall flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import storage

log = logging.getLogger(__name__)


@dataclass
class MetricDelta:
    metric: str
    prior_value: float
    current_value: float
    abs_delta: float
    pct_delta: float


@dataclass
class ComparisonReport:
    ticker: str
    current_quarter: str
    prior_quarter: str
    current_defensiveness: float
    prior_defensiveness: float
    defensiveness_delta: float
    defensiveness_pct_change: float
    flagged_as_more_defensive: bool
    top_shifts: list[MetricDelta]
    all_deltas: list[MetricDelta]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "current_quarter": self.current_quarter,
            "prior_quarter": self.prior_quarter,
            "current_defensiveness": self.current_defensiveness,
            "prior_defensiveness": self.prior_defensiveness,
            "defensiveness_delta": round(self.defensiveness_delta, 1),
            "defensiveness_pct_change": round(self.defensiveness_pct_change, 1),
            "flagged_as_more_defensive": self.flagged_as_more_defensive,
            "top_shifts": [d.__dict__ for d in self.top_shifts],
            "all_deltas": [d.__dict__ for d in self.all_deltas],
        }


# Metrics we track for QoQ comparison. Path notation into the metrics_json.
TRACKED_METRICS: list[tuple[str, list[str]]] = [
    ("hedging_total_per_1000", ["hedging", "total_per_1000"]),
    ("hedging_epistemic", ["hedging", "epistemic", "per_1000_words"]),
    ("hedging_probability", ["hedging", "probability", "per_1000_words"]),
    ("hedging_approximation", ["hedging", "approximation", "per_1000_words"]),
    ("hedging_conditional", ["hedging", "conditional", "per_1000_words"]),
    ("defensive_total_per_1000", ["defensive", "total_per_1000"]),
    ("defensive_reframing", ["defensive", "reframing", "per_1000_words"]),
    ("defensive_non_answers", ["defensive", "non_answers", "per_1000_words"]),
    ("defensive_compliments", ["defensive", "compliments", "per_1000_words"]),
    ("certainty_per_1000", ["certainty", "per_1000_words"]),
    ("qa_evasion_rate", ["qa_evasion", "evasion_rate"]),
    ("we_to_i_ratio", ["pronouns", "we_to_i_ratio"]),
    ("deflection_ratio", ["pronouns", "deflection_ratio"]),
    ("handoff_count", ["qa_evasion", "handoff_count"]),
]


def _path(d: dict, path: list[str]) -> float:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return 0.0
        cur = cur[k]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return 0.0


def _pct(prior: float, current: float) -> float:
    if prior == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - prior) / abs(prior)) * 100


def compute_deltas(prior_metrics: dict, current_metrics: dict) -> list[MetricDelta]:
    out = []
    for name, path in TRACKED_METRICS:
        p = _path(prior_metrics, path)
        c = _path(current_metrics, path)
        out.append(MetricDelta(
            metric=name,
            prior_value=round(p, 3),
            current_value=round(c, 3),
            abs_delta=round(c - p, 3),
            pct_delta=round(_pct(p, c), 1),
        ))
    return out


def compare(ticker: str) -> ComparisonReport | None:
    """Compare the most recent transcript for `ticker` to its immediate predecessor.

    Returns None if fewer than 2 analyses exist for the ticker.
    """
    history = storage.history_for_ticker(ticker, limit=2)
    if len(history) < 2 or not history[0].get("metrics") or not history[1].get("metrics"):
        return None

    current, prior = history[0], history[1]
    deltas = compute_deltas(prior["metrics"], current["metrics"])

    cur_def = float(current.get("defensiveness_score") or 0)
    prior_def = float(prior.get("defensiveness_score") or 0)
    def_delta = cur_def - prior_def
    def_pct = _pct(prior_def, cur_def)

    flagged = def_pct > 25.0

    # Top shifts by absolute pct delta — but ignore tiny baselines (noise)
    significant = [d for d in deltas if abs(d.prior_value) + abs(d.current_value) > 0.3]
    top = sorted(significant, key=lambda d: abs(d.pct_delta), reverse=True)[:5]

    return ComparisonReport(
        ticker=ticker.upper(),
        current_quarter=current["quarter"],
        prior_quarter=prior["quarter"],
        current_defensiveness=cur_def,
        prior_defensiveness=prior_def,
        defensiveness_delta=def_delta,
        defensiveness_pct_change=def_pct,
        flagged_as_more_defensive=flagged,
        top_shifts=top,
        all_deltas=deltas,
    )


def compare_all_recent(date_str: str) -> list[ComparisonReport]:
    """Build comparison reports for every ticker that reported on `date_str`."""
    transcripts = storage.list_transcripts_on_date(date_str)
    reports = []
    for t in transcripts:
        report = compare(t["ticker"])
        if report:
            reports.append(report)
    return reports
