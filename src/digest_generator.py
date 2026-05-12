"""Daily digest generator — produces a Grant's-style HTML report and
optionally a PDF via weasyprint. Cream background, Georgia serif, ruled
tables, red/black only.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Template

from . import comparison_engine, storage

log = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs" / "digests"


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Earnings Intelligence Digest — {{ date }}</title>
<style>
  @page { size: Letter; margin: 0.7in 0.8in; }
  body {
    background: #f6f1e7;
    color: #1a1a1a;
    font-family: Georgia, "Times New Roman", Times, serif;
    font-size: 11pt;
    line-height: 1.42;
    margin: 0;
    padding: 36px 48px 60px;
    max-width: 880px;
    margin-left: auto; margin-right: auto;
  }
  .masthead {
    border-top: 3px solid #1a1a1a;
    border-bottom: 1px solid #1a1a1a;
    padding: 16px 0 12px;
    margin-bottom: 28px;
  }
  .masthead h1 {
    font-family: "Times New Roman", Times, serif;
    font-size: 28pt;
    font-weight: 700;
    letter-spacing: -0.5px;
    margin: 0;
  }
  .masthead .meta {
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: #444;
    margin-top: 6px;
  }
  h2 {
    font-size: 14pt;
    border-bottom: 1px solid #1a1a1a;
    padding-bottom: 4px;
    margin-top: 36px;
    margin-bottom: 14px;
    font-variant: small-caps;
    letter-spacing: 0.5px;
  }
  h3 {
    font-size: 11pt;
    margin: 18px 0 6px;
    font-weight: 700;
  }
  p { margin: 0 0 10px; text-align: justify; }
  .lede {
    font-size: 12pt;
    line-height: 1.55;
    border-left: 3px solid #1a1a1a;
    padding-left: 14px;
    margin-bottom: 22px;
    font-style: italic;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0 18px;
    font-size: 10pt;
  }
  th, td {
    padding: 6px 8px;
    border-bottom: 1px solid #c4b89e;
    text-align: left;
    vertical-align: top;
  }
  th {
    border-bottom: 1.5px solid #1a1a1a;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-size: 8.5pt;
    font-weight: 700;
    background: transparent;
  }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .ticker { font-weight: 700; letter-spacing: 0.5px; }
  .up { color: #a01818; font-weight: 700; }
  .down { color: #1a1a1a; }
  .flag {
    display: inline-block;
    padding: 1px 6px;
    background: #1a1a1a;
    color: #f6f1e7;
    font-size: 8.5pt;
    letter-spacing: 1px;
    text-transform: uppercase;
  }
  .exchange {
    border-left: 2px solid #a01818;
    padding: 6px 0 6px 14px;
    margin: 10px 0;
  }
  .exchange .q { font-weight: 700; }
  .exchange .a { color: #333; font-style: italic; margin-top: 4px; }
  .exchange .meta { font-size: 8.5pt; text-transform: uppercase; color: #666; letter-spacing: 1px; }
  .heatmap {
    display: grid;
    grid-template-columns: 80px repeat({{ heatmap_cols }}, 1fr);
    gap: 1px;
    background: #1a1a1a;
    border: 1px solid #1a1a1a;
    margin: 12px 0 20px;
    font-size: 8.5pt;
  }
  .heatmap .h {
    background: #1a1a1a;
    color: #f6f1e7;
    padding: 4px 6px;
    text-align: center;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .heatmap .label { background: #f6f1e7; padding: 4px 6px; font-weight: 700; }
  .heatmap .cell {
    padding: 4px 6px;
    text-align: center;
    font-variant-numeric: tabular-nums;
    color: #1a1a1a;
    background: #f6f1e7;
  }
  .heatmap .cell.i1 { background: #ecdfc8; }
  .heatmap .cell.i2 { background: #e2c89c; }
  .heatmap .cell.i3 { background: #d09b6e; color: #fff; }
  .heatmap .cell.i4 { background: #a01818; color: #fff; }
  .footer {
    margin-top: 40px;
    padding-top: 10px;
    border-top: 1px solid #1a1a1a;
    font-size: 8.5pt;
    color: #555;
    text-align: center;
    letter-spacing: 1px;
    text-transform: uppercase;
  }
  .no-data { font-style: italic; color: #666; }
</style>
</head>
<body>

<div class="masthead">
  <h1>The Earnings Intelligence Digest</h1>
  <div class="meta">{{ date_long }} &middot; Vol. 1 &middot; No. {{ issue_no }} &middot; {{ call_count }} call{{ "s" if call_count != 1 else "" }} analyzed</div>
</div>

<p class="lede">{{ lede }}</p>

<h2>Top Defensive Shifts</h2>
{% if defensive_shifts %}
<table>
  <thead><tr>
    <th>Ticker</th><th>Quarter</th><th class="num">Prior</th><th class="num">Current</th>
    <th class="num">&Delta;</th><th class="num">% Chg</th><th>Largest Metric Shift</th>
  </tr></thead>
  <tbody>
  {% for r in defensive_shifts %}
    <tr>
      <td class="ticker">{{ r.ticker }}{% if r.flagged_as_more_defensive %} <span class="flag">FLAG</span>{% endif %}</td>
      <td>{{ r.current_quarter }} vs {{ r.prior_quarter }}</td>
      <td class="num">{{ "%.1f"|format(r.prior_defensiveness) }}</td>
      <td class="num">{{ "%.1f"|format(r.current_defensiveness) }}</td>
      <td class="num up">{{ "%+.1f"|format(r.defensiveness_delta) }}</td>
      <td class="num up">{{ "%+.1f"|format(r.defensiveness_pct_change) }}%</td>
      <td>{{ r.top_shift_label }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p class="no-data">No companies showed material defensiveness rises today.</p>{% endif %}

<h2>Most Confident</h2>
{% if confident_shifts %}
<table>
  <thead><tr>
    <th>Ticker</th><th>Quarter</th><th class="num">Prior</th><th class="num">Current</th>
    <th class="num">&Delta;</th><th>Note</th>
  </tr></thead>
  <tbody>
  {% for r in confident_shifts %}
    <tr>
      <td class="ticker">{{ r.ticker }}</td>
      <td>{{ r.current_quarter }} vs {{ r.prior_quarter }}</td>
      <td class="num">{{ "%.1f"|format(r.prior_defensiveness) }}</td>
      <td class="num">{{ "%.1f"|format(r.current_defensiveness) }}</td>
      <td class="num down">{{ "%+.1f"|format(r.defensiveness_delta) }}</td>
      <td>{{ r.top_shift_label }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p class="no-data">No companies showed notable confidence gains today.</p>{% endif %}

<h2>Notable Exchanges</h2>
{% if notable_exchanges %}
  {% for ex in notable_exchanges %}
  <div class="exchange">
    <div class="meta">{{ ex.ticker }} &middot; {{ ex.analyst }}{% if ex.firm %}, {{ ex.firm }}{% endif %} &middot; Evasion score {{ ex.evasion_score }}</div>
    <div class="q">Q: {{ ex.question_excerpt }}</div>
    <div class="a">A: {{ ex.answer_excerpt }}</div>
  </div>
  {% endfor %}
{% else %}<p class="no-data">No exchanges flagged as evasive in today's calls.</p>{% endif %}

<h2>Watchlist Updates</h2>
{% if watchlist_hits %}
<table>
  <thead><tr><th>Ticker</th><th>Company</th><th>Quarter</th><th class="num">Defensiveness</th></tr></thead>
  <tbody>
  {% for w in watchlist_hits %}
    <tr>
      <td class="ticker">{{ w.ticker }}</td><td>{{ w.name }}</td>
      <td>{{ w.quarter }}</td><td class="num">{{ "%.1f"|format(w.defensiveness) }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p class="no-data">No watchlist names reported today.</p>{% endif %}

<h2>Linguistic Heatmap</h2>
{% if heatmap_rows %}
<div class="heatmap">
  <div class="h">Ticker</div>
  {% for col in heatmap_cols_labels %}<div class="h">{{ col }}</div>{% endfor %}
  {% for row in heatmap_rows %}
    <div class="label">{{ row.ticker }}</div>
    {% for cell in row.cells %}
      <div class="cell i{{ cell.intensity }}">{{ cell.value }}</div>
    {% endfor %}
  {% endfor %}
</div>
{% else %}<p class="no-data">No data available.</p>{% endif %}

{% if all_calls %}
<h2>Per-Call Summary</h2>
<table>
  <thead><tr>
    <th>Ticker</th><th>Quarter</th><th class="num">Hedging /1k</th>
    <th class="num">Defensive /1k</th><th class="num">Certainty /1k</th>
    <th class="num">Evasion</th><th class="num">Score</th>
  </tr></thead>
  <tbody>
  {% for c in all_calls %}
    <tr>
      <td class="ticker">{{ c.ticker }}</td><td>{{ c.quarter }}</td>
      <td class="num">{{ "%.1f"|format(c.hedging) }}</td>
      <td class="num">{{ "%.1f"|format(c.defensive) }}</td>
      <td class="num">{{ "%.1f"|format(c.certainty) }}</td>
      <td class="num">{{ "%.0f"|format(c.evasion * 100) }}%</td>
      <td class="num">{{ "%.1f"|format(c.score) }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

<div class="footer">Generated {{ generated_at }} &middot; Earnings Intelligence Engine</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _heatmap_intensity(value: float, scale: list[float]) -> int:
    """Return 0..4 intensity bucket. `scale` is ascending thresholds."""
    for i, threshold in enumerate(scale):
        if value < threshold:
            return i
    return len(scale)


_HEATMAP_COLS = [
    ("Hedging", ["hedging", "total_per_1000"], [20, 40, 60, 80]),
    ("Defensive", ["defensive", "total_per_1000"], [3, 6, 10, 15]),
    ("Certainty", ["certainty", "per_1000_words"], [10, 20, 30, 40]),
    ("Evasion %", ["qa_evasion", "evasion_rate"], [0.15, 0.30, 0.45, 0.60]),
    ("Score", None, [25, 45, 60, 75]),
]


def _path(d: dict | None, p: list[str]) -> float:
    if not d:
        return 0.0
    cur: Any = d
    for k in p:
        if not isinstance(cur, dict) or k not in cur:
            return 0.0
        cur = cur[k]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return 0.0


def _top_shift_label(report: comparison_engine.ComparisonReport) -> str:
    if not report.top_shifts:
        return "—"
    s = report.top_shifts[0]
    direction = "↑" if s.abs_delta > 0 else "↓"
    name = s.metric.replace("_", " ")
    return f"{name} {direction}{abs(s.pct_delta):.0f}%"


def _lede(call_count: int, defensive_count: int, confident_count: int, top_ticker: str | None) -> str:
    if call_count == 0:
        return "No earnings calls analyzed today."
    parts = []
    if defensive_count:
        plural = "CEOs" if defensive_count != 1 else "CEO"
        parts.append(
            f"{defensive_count} {plural} got significantly more defensive today"
            + (f", led by {top_ticker}" if top_ticker else "") + "."
        )
    if confident_count:
        parts.append(f"{confident_count} management team{'s' if confident_count != 1 else ''} struck a more confident tone.")
    if not parts:
        parts.append(f"Tone across today's {call_count} call{'s' if call_count != 1 else ''} was broadly steady quarter over quarter.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Build digest
# ---------------------------------------------------------------------------
def build_digest(date_str: str, watchlist: list[dict] | None = None) -> dict[str, Any]:
    """Render context for the digest template."""
    transcripts = storage.list_transcripts_on_date(date_str)
    watchlist = watchlist or []
    watchlist_tickers = {w["ticker"].upper() for w in watchlist}

    # Per-call rows
    all_calls = []
    heatmap_rows = []
    notable_exchanges: list[dict] = []
    watchlist_hits: list[dict] = []

    for t in transcripts:
        analysis = storage.get_analysis(t["id"])
        if not analysis:
            continue
        metrics = analysis["metrics"]
        score = float(analysis["defensiveness_score"] or 0)
        all_calls.append({
            "ticker": t["ticker"],
            "quarter": t["quarter"],
            "hedging": _path(metrics, ["hedging", "total_per_1000"]),
            "defensive": _path(metrics, ["defensive", "total_per_1000"]),
            "certainty": _path(metrics, ["certainty", "per_1000_words"]),
            "evasion": _path(metrics, ["qa_evasion", "evasion_rate"]),
            "score": score,
        })

        # Heatmap row
        cells = []
        for label, path, scale in _HEATMAP_COLS:
            val = score if path is None else _path(metrics, path)
            display = f"{val * 100:.0f}%" if label == "Evasion %" else f"{val:.1f}"
            cells.append({"value": display, "intensity": _heatmap_intensity(val, scale)})
        heatmap_rows.append({"ticker": t["ticker"], "cells": cells})

        # Notable exchanges
        for ex in _path_list(metrics, ["qa_evasion", "evasive_excerpts"]):
            notable_exchanges.append({
                "ticker": t["ticker"],
                "analyst": ex.get("analyst", "—"),
                "firm": ex.get("firm", ""),
                "evasion_score": ex.get("evasion_score", 0),
                "question_excerpt": ex.get("question_excerpt", ""),
                "answer_excerpt": ex.get("answer_excerpt", ""),
            })

        # Watchlist
        if t["ticker"].upper() in watchlist_tickers:
            name = next((w["name"] for w in watchlist if w["ticker"].upper() == t["ticker"].upper()), t["ticker"])
            watchlist_hits.append({
                "ticker": t["ticker"], "name": name,
                "quarter": t["quarter"], "defensiveness": score,
            })

    # Sort notable by evasion score, cap at 6
    notable_exchanges = sorted(notable_exchanges, key=lambda x: -x.get("evasion_score", 0))[:6]

    # QoQ comparisons
    reports = comparison_engine.compare_all_recent(date_str)
    enriched = []
    for r in reports:
        d = r.to_dict()
        d["top_shift_label"] = _top_shift_label(r)
        enriched.append(d)
    defensive_shifts = sorted(
        [r for r in enriched if r["defensiveness_delta"] > 0],
        key=lambda r: -r["defensiveness_pct_change"],
    )[:8]
    confident_shifts = sorted(
        [r for r in enriched if r["defensiveness_delta"] < 0],
        key=lambda r: r["defensiveness_pct_change"],
    )[:5]

    top_def_ticker = defensive_shifts[0]["ticker"] if defensive_shifts else None
    flagged_count = sum(1 for r in defensive_shifts if r["flagged_as_more_defensive"])

    dt = datetime.fromisoformat(date_str)

    context = {
        "date": date_str,
        "date_long": dt.strftime("%B %d, %Y"),
        "issue_no": dt.strftime("%j"),
        "call_count": len(all_calls),
        "lede": _lede(len(all_calls), flagged_count, len(confident_shifts), top_def_ticker),
        "defensive_shifts": defensive_shifts,
        "confident_shifts": confident_shifts,
        "notable_exchanges": notable_exchanges,
        "watchlist_hits": watchlist_hits,
        "heatmap_rows": heatmap_rows,
        "heatmap_cols": len(_HEATMAP_COLS),
        "heatmap_cols_labels": [c[0] for c in _HEATMAP_COLS],
        "all_calls": sorted(all_calls, key=lambda c: -c["score"]),
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }
    return context


def _path_list(d: dict, p: list[str]) -> list:
    cur: Any = d
    for k in p:
        if not isinstance(cur, dict) or k not in cur:
            return []
        cur = cur[k]
    return cur if isinstance(cur, list) else []


def render(date_str: str, watchlist: list[dict] | None = None) -> tuple[str, Path, Path | None]:
    """Render the HTML, write it to disk, attempt PDF, persist to DB.

    Returns (html_string, html_path, pdf_path_or_None).
    """
    context = build_digest(date_str, watchlist)
    template = Template(TEMPLATE)
    html = template.render(**context)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUTS_DIR / f"digest_{date_str}.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("Wrote HTML digest: %s", html_path)

    pdf_path: Path | None = OUTPUTS_DIR / f"digest_{date_str}.pdf"
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(pdf_path))
        log.info("Wrote PDF digest: %s", pdf_path)
    except Exception as e:
        log.warning("PDF generation skipped (weasyprint failed): %s", e)
        pdf_path = None

    summary = context["lede"]
    storage.save_digest(date_str, str(html_path), str(pdf_path) if pdf_path else None, summary)
    return html, html_path, pdf_path
