"""Earnings Intelligence Engine — Streamlit dashboard.

Launch with:
    streamlit run dashboard.py
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import (  # noqa: E402
    comparison_engine,
    digest_generator,
    fetch_transcripts,
    linguistic_analyzer,
    parse_transcript,
    sentiment_engine,
    storage,
)

load_dotenv(ROOT / ".env")
storage.init_db()

# ---------------------------------------------------------------------------
# Page config + global CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Earnings Intelligence",
    page_icon="📰",  # rendered in the browser tab; not displayed inside the app
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
:root {
  --ink: #1a1a1a;
  --cream: #f6f1e7;
  --paper: #ecdfc8;
  --red: #a01818;
  --ochre: #d09b6e;
}
html, body, [class*="st-"], .stApp {
  font-family: Georgia, "Times New Roman", Times, serif !important;
  color: var(--ink);
}
.stApp { background: var(--cream) !important; }
section[data-testid="stSidebar"] { background: var(--paper) !important; border-right: 1px solid var(--ink); }
h1, h2, h3, h4 { font-family: "Times New Roman", Times, serif !important; letter-spacing: -0.3px; }
h1 { font-size: 38pt !important; border-bottom: 3px solid var(--ink); padding-bottom: 6px; margin-bottom: 0 !important; }
h2 { font-variant: small-caps; letter-spacing: 0.5px; border-bottom: 1px solid var(--ink); padding-bottom: 2px; }
.subtle { color: #555; text-transform: uppercase; letter-spacing: 1.4px; font-size: 9pt; }
.lede { font-style: italic; font-size: 14pt; border-left: 3px solid var(--ink); padding: 4px 0 4px 14px; margin: 8px 0 16px; }
.flag { display: inline-block; background: var(--ink); color: var(--cream); padding: 1px 8px; font-size: 9pt; letter-spacing: 1.2px; text-transform: uppercase; }
.kpi { border-top: 1.5px solid var(--ink); border-bottom: 1px solid var(--ink); padding: 8px 0 6px; margin-bottom: 8px; }
.kpi-label { font-size: 9pt; text-transform: uppercase; letter-spacing: 1.4px; color: #444; }
.kpi-value { font-size: 32pt; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.kpi-delta-up { color: var(--red); font-weight: 700; }
.kpi-delta-down { color: var(--ink); }
.exchange { border-left: 3px solid var(--red); padding: 4px 0 4px 14px; margin: 10px 0; }
.exchange .meta { font-size: 9pt; text-transform: uppercase; letter-spacing: 1.2px; color: #666; }
.exchange .q { font-weight: 700; margin-top: 4px; }
.exchange .a { color: #333; font-style: italic; margin-top: 4px; }
.hl-hedge { background: #f4e5b0; padding: 0 2px; border-radius: 2px; }
.hl-def { background: #f0c4c4; padding: 0 2px; border-radius: 2px; }
.hl-cert { background: #c9e0b8; padding: 0 2px; border-radius: 2px; }
.transcript-box { background: #fbf7ee; border: 1px solid var(--ink); padding: 16px 18px; max-height: 70vh; overflow-y: auto; line-height: 1.55; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1.5px solid var(--ink); }
.stTabs [data-baseweb="tab"] { font-family: Georgia, serif !important; font-size: 11pt; font-variant: small-caps; letter-spacing: 1px; padding: 8px 16px; background: transparent; }
.stTabs [aria-selected="true"] { background: var(--ink) !important; color: var(--cream) !important; }
.stButton > button { background: var(--ink); color: var(--cream); border: 1px solid var(--ink); border-radius: 0; font-family: Georgia, serif; font-variant: small-caps; letter-spacing: 1.2px; padding: 4px 14px; }
.stButton > button:hover { background: var(--red); border-color: var(--red); }
div[data-testid="stMetricValue"] { font-family: Georgia, serif !important; font-size: 26pt !important; }
table { font-family: Georgia, serif !important; }
hr { border: none; border-top: 1px solid var(--ink); }
.masthead-rule { border-top: 1px solid var(--ink); margin: 4px 0 18px; }
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TICKERS_PATH = ROOT / "config" / "tickers.yaml"


@st.cache_data(ttl=10)
def load_watchlist() -> list[dict]:
    if not TICKERS_PATH.exists():
        return []
    with TICKERS_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("watchlist", [])


def save_watchlist(items: list[dict]) -> None:
    with TICKERS_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"watchlist": items}, f, sort_keys=False)
    load_watchlist.clear()


@st.cache_data(ttl=5)
def get_all_transcripts() -> pd.DataFrame:
    with storage.connect() as conn:
        rows = conn.execute(
            """SELECT t.id, t.ticker, t.quarter, t.call_date, t.source, c.name,
                      a.defensiveness_score
               FROM transcripts t
               LEFT JOIN companies c ON c.ticker = t.ticker
               LEFT JOIN analyses a ON a.transcript_id = t.id
               ORDER BY t.call_date DESC, t.ticker"""
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=5)
def get_analysis_for(transcript_id: int) -> dict | None:
    return storage.get_analysis(transcript_id)


def clear_caches() -> None:
    get_all_transcripts.clear()
    get_analysis_for.clear()
    load_watchlist.clear()


def format_delta(value: float, suffix: str = "") -> str:
    cls = "kpi-delta-up" if value > 0 else "kpi-delta-down"
    sign = "+" if value > 0 else ""
    return f"<span class='{cls}'>{sign}{value:.1f}{suffix}</span>"


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
PALETTE = {"ink": "#1a1a1a", "cream": "#f6f1e7", "red": "#a01818", "ochre": "#d09b6e", "olive": "#8a7f4a"}


def gauge_chart(score: float, title: str = "Defensiveness") -> go.Figure:
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": title, "font": {"family": "Georgia, serif", "size": 16, "color": PALETTE["ink"]}},
        number={"font": {"family": "Georgia, serif", "size": 48, "color": PALETTE["ink"]}},
        gauge={
            "axis": {"range": [0, 100], "tickfont": {"family": "Georgia, serif", "color": PALETTE["ink"]}},
            "bar": {"color": PALETTE["ink"]},
            "bgcolor": PALETTE["cream"],
            "borderwidth": 1,
            "bordercolor": PALETTE["ink"],
            "steps": [
                {"range": [0, 30], "color": "#dde6cf"},
                {"range": [30, 55], "color": "#ecdfc8"},
                {"range": [55, 75], "color": "#d09b6e"},
                {"range": [75, 100], "color": "#a01818"},
            ],
            "threshold": {
                "line": {"color": PALETTE["red"], "width": 4},
                "thickness": 0.75,
                "value": score,
            },
        },
    ))
    fig.update_layout(
        paper_bgcolor=PALETTE["cream"], plot_bgcolor=PALETTE["cream"],
        height=280, margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def radar_components(metrics: dict, score: float) -> go.Figure:
    """Five-component radar of the defensiveness ingredients."""
    hedging = min(metrics.get("hedging", {}).get("total_per_1000", 0), 100)
    defensive = min(metrics.get("defensive", {}).get("total_per_1000", 0) * 5, 100)
    evasion = metrics.get("qa_evasion", {}).get("evasion_rate", 0) * 100
    cert_raw = metrics.get("certainty", {}).get("per_1000_words", 0)
    certainty_inv = min(max(0, 50 - cert_raw) * 2, 100)
    deflect = min(metrics.get("pronouns", {}).get("deflection_ratio", 0) * 100, 100)

    categories = ["Hedging", "Defensive", "Evasion", "Certainty (inv)", "Pronoun deflect"]
    values = [hedging, defensive, evasion, certainty_inv, deflect]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]],
        theta=categories + [categories[0]],
        fill="toself",
        fillcolor="rgba(160, 24, 24, 0.18)",
        line=dict(color=PALETTE["red"], width=2),
        name="Components",
    ))
    fig.update_layout(
        polar=dict(
            bgcolor=PALETTE["cream"],
            radialaxis=dict(visible=True, range=[0, 100], color=PALETTE["ink"], gridcolor="#c4b89e"),
            angularaxis=dict(color=PALETTE["ink"], gridcolor="#c4b89e", tickfont=dict(family="Georgia, serif", size=11)),
        ),
        paper_bgcolor=PALETTE["cream"], plot_bgcolor=PALETTE["cream"],
        showlegend=False,
        height=320, margin=dict(l=40, r=40, t=20, b=20),
        font=dict(family="Georgia, serif", color=PALETTE["ink"]),
    )
    return fig


def timeseries_chart(history_df: pd.DataFrame) -> go.Figure:
    """Defensiveness score over time, per ticker."""
    fig = go.Figure()
    for ticker, grp in history_df.groupby("ticker"):
        grp = grp.sort_values("call_date")
        fig.add_trace(go.Scatter(
            x=grp["call_date"], y=grp["defensiveness_score"],
            mode="lines+markers", name=ticker,
            line=dict(width=2),
            marker=dict(size=9, line=dict(width=1, color=PALETTE["ink"])),
        ))
    fig.update_layout(
        paper_bgcolor=PALETTE["cream"], plot_bgcolor=PALETTE["cream"],
        xaxis=dict(title="Call date", color=PALETTE["ink"], gridcolor="#c4b89e", tickfont=dict(family="Georgia, serif")),
        yaxis=dict(title="Defensiveness score", range=[0, 100], color=PALETTE["ink"], gridcolor="#c4b89e", tickfont=dict(family="Georgia, serif")),
        font=dict(family="Georgia, serif", color=PALETTE["ink"]),
        height=380, margin=dict(l=50, r=20, t=20, b=50),
        legend=dict(bgcolor=PALETTE["cream"], bordercolor=PALETTE["ink"], borderwidth=1),
    )
    fig.add_hline(y=55, line=dict(dash="dash", color=PALETTE["red"], width=1), annotation_text="elevated", annotation_position="right")
    return fig


def deltas_bar(deltas: list[comparison_engine.MetricDelta]) -> go.Figure:
    """Horizontal bars of metric % changes — red for rises, ink for declines."""
    rows = sorted(deltas, key=lambda d: abs(d.pct_delta), reverse=True)[:10]
    labels = [d.metric.replace("_", " ") for d in rows]
    values = [d.pct_delta for d in rows]
    colors = [PALETTE["red"] if v > 0 else PALETTE["ink"] for v in values]

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker=dict(color=colors, line=dict(color=PALETTE["ink"], width=1)),
        text=[f"{v:+.0f}%" for v in values], textposition="outside",
        textfont=dict(family="Georgia, serif", color=PALETTE["ink"]),
    ))
    fig.update_layout(
        paper_bgcolor=PALETTE["cream"], plot_bgcolor=PALETTE["cream"],
        xaxis=dict(title="% change vs prior quarter", color=PALETTE["ink"], gridcolor="#c4b89e", zerolinecolor=PALETTE["ink"]),
        yaxis=dict(color=PALETTE["ink"], autorange="reversed"),
        font=dict(family="Georgia, serif", color=PALETTE["ink"]),
        height=400, margin=dict(l=180, r=80, t=20, b=50), showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Transcript highlighting
# ---------------------------------------------------------------------------
import re as _re

_MARKERS_CACHE: dict | None = None


def _markers():
    global _MARKERS_CACHE
    if _MARKERS_CACHE is None:
        _MARKERS_CACHE = linguistic_analyzer.load_markers()
    return _MARKERS_CACHE


def highlight_text(text: str) -> str:
    """Return HTML with hedging/defensive/certainty markers wrapped in spans."""
    m = _markers()
    hedge = []
    for cat in m["hedging"].values():
        hedge.extend(cat)
    defensive_phrases = []
    for cat in m["defensive_phrases"].values():
        defensive_phrases.extend(cat)
    certainty = m["certainty"]["absolutes"] + m["certainty"]["commitment"] + m["certainty"]["factual"]

    safe = html.escape(text)

    def wrap(html_text: str, phrases: list[str], cls: str) -> str:
        # Sort longest-first so multi-word phrases match before their constituents
        for phrase in sorted(phrases, key=len, reverse=True):
            pattern = _re.compile(rf"\b({_re.escape(phrase)})\b", _re.IGNORECASE)
            html_text = pattern.sub(rf"<span class='{cls}'>\1</span>", html_text)
        return html_text

    safe = wrap(safe, defensive_phrases, "hl-def")
    safe = wrap(safe, hedge, "hl-hedge")
    safe = wrap(safe, certainty, "hl-cert")
    return safe.replace("\n", "<br>")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("# &mdash; Vol. 1 &mdash;", unsafe_allow_html=True)
    st.markdown("<div class='subtle'>Earnings Intelligence</div>", unsafe_allow_html=True)
    st.markdown("<hr>", unsafe_allow_html=True)

    df_all = get_all_transcripts()
    st.markdown("### Database")
    cols = st.columns(2)
    cols[0].metric("Calls", len(df_all))
    cols[1].metric("Companies", df_all["ticker"].nunique() if not df_all.empty else 0)

    st.markdown("### Watchlist")
    wl = load_watchlist()
    for w in wl:
        st.markdown(f"&nbsp;&nbsp;**{w['ticker']}** &mdash; {w.get('name','')[:24]}", unsafe_allow_html=True)

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("### Claude API")
    if sentiment_engine.is_available():
        st.success("Active")
    else:
        st.info("Not configured — running linguistic-only mode")

    if st.button("Refresh data", use_container_width=True):
        clear_caches()
        st.rerun()


# ---------------------------------------------------------------------------
# Masthead
# ---------------------------------------------------------------------------
st.markdown("# The Earnings Intelligence Engine")
st.markdown(
    f"<div class='subtle'>{datetime.utcnow().strftime('%A, %B %d, %Y')} "
    f"&middot; {len(df_all)} call{'s' if len(df_all)!=1 else ''} analyzed</div>",
    unsafe_allow_html=True,
)
st.markdown("<div class='masthead-rule'></div>", unsafe_allow_html=True)

if df_all.empty:
    st.info(
        "**No transcripts yet.** Click `Run demo (ACME fixtures)` below to seed the database with two sample calls, "
        "or use the **Ingest** tab to add your own."
    )
    if st.button("Run demo (ACME fixtures)"):
        fixtures = sorted((ROOT / "tests" / "fixtures").glob("sample_*.txt"))
        for fx in fixtures:
            raw = fx.read_text(encoding="utf-8")
            parsed = parse_transcript.parse(raw)
            text_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            storage.upsert_company(parsed.ticker, parsed.company_name or parsed.ticker)
            tid = storage.save_transcript(
                ticker=parsed.ticker, quarter=parsed.quarter, call_date=parsed.call_date,
                raw_text=raw, text_hash=text_hash, source=f"fixture:{fx.name}",
                parsed=parsed.to_dict(),
            )
            report = linguistic_analyzer.analyze(parsed)
            score = linguistic_analyzer.defensiveness_score(report)
            storage.save_analysis(tid, report.model_dump(), score, None)
        clear_caches()
        st.success(f"Seeded {len(fixtures)} fixtures.")
        st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_ingest, tab_company, tab_compare, tab_digest, tab_watchlist = st.tabs(
    ["Overview", "Ingest", "Company Deep-Dive", "Compare", "Digest", "Watchlist"]
)


# =============================================================================
# OVERVIEW
# =============================================================================
with tab_overview:
    latest_date = df_all["call_date"].max()
    today_calls = df_all[df_all["call_date"] == latest_date]
    flagged = []
    confident = []
    for _, row in today_calls.iterrows():
        cmp = comparison_engine.compare(row["ticker"])
        if cmp:
            if cmp.flagged_as_more_defensive:
                flagged.append(cmp)
            elif cmp.defensiveness_delta < -3:
                confident.append(cmp)

    if flagged:
        names = ", ".join(c.ticker for c in flagged)
        st.markdown(
            f"<p class='lede'>{len(flagged)} management team{'s' if len(flagged)!=1 else ''} "
            f"got significantly more defensive on {latest_date} &mdash; {names}.</p>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<p class='lede'>Tone across the {len(today_calls)} call(s) reporting on {latest_date} "
            f"was broadly steady quarter over quarter.</p>",
            unsafe_allow_html=True,
        )

    # KPI strip
    avg_score = df_all["defensiveness_score"].dropna().mean() or 0
    max_score = df_all["defensiveness_score"].dropna().max() or 0
    flagged_count = len(flagged)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(f"<div class='kpi'><div class='kpi-label'>Calls in DB</div><div class='kpi-value'>{len(df_all)}</div></div>", unsafe_allow_html=True)
    with k2:
        st.markdown(f"<div class='kpi'><div class='kpi-label'>Avg defensiveness</div><div class='kpi-value'>{avg_score:.1f}</div></div>", unsafe_allow_html=True)
    with k3:
        st.markdown(f"<div class='kpi'><div class='kpi-label'>Peak score</div><div class='kpi-value'>{max_score:.1f}</div></div>", unsafe_allow_html=True)
    with k4:
        st.markdown(f"<div class='kpi'><div class='kpi-label'>Flagged today</div><div class='kpi-value'>{flagged_count}</div></div>", unsafe_allow_html=True)

    st.markdown("## Defensiveness across the database")
    if df_all["defensiveness_score"].notna().any():
        st.plotly_chart(timeseries_chart(df_all.dropna(subset=["defensiveness_score"])), use_container_width=True)
    else:
        st.info("No analyses yet — run `analyze` on an ingested transcript.")

    st.markdown("## Latest calls")
    display_df = df_all[["ticker", "name", "quarter", "call_date", "defensiveness_score"]].copy()
    display_df.columns = ["Ticker", "Company", "Quarter", "Call date", "Score"]
    st.dataframe(display_df, use_container_width=True, hide_index=True)


# =============================================================================
# INGEST
# =============================================================================
with tab_ingest:
    st.markdown("## Ingest a new transcript")
    st.markdown("<div class='subtle'>Source &mdash; choose one</div>", unsafe_allow_html=True)

    mode = st.radio(
        "Source",
        ["Upload file (.txt or .pdf)", "Paste text", "URL (web page)", "YouTube URL"],
        horizontal=True, label_visibility="collapsed",
        key="ingest_mode",
    )

    # Persist fetched text across reruns — Streamlit reruns on every widget event,
    # and local variables don't survive that. Session state does.
    if st.session_state.get("ingest_mode_prev") != mode:
        st.session_state.pop("raw_text", None)
        st.session_state.pop("source_label", None)
        st.session_state["ingest_mode_prev"] = mode

    if mode == "Upload file (.txt or .pdf)":
        uploaded = st.file_uploader("Select a transcript file", type=["txt", "pdf"])
        if uploaded:
            if uploaded.name.lower().endswith(".pdf"):
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(io.BytesIO(uploaded.read()))
                    st.session_state["raw_text"] = "\n".join((p.extract_text() or "") for p in reader.pages)
                except ImportError:
                    st.error("pypdf is not installed. Run `pip install pypdf` to ingest PDFs.")
            else:
                st.session_state["raw_text"] = uploaded.read().decode("utf-8", errors="replace")
            st.session_state["source_label"] = f"file:{uploaded.name}"

    elif mode == "Paste text":
        pasted = st.text_area("Paste transcript", height=320, placeholder="Paste the full transcript here...")
        if pasted:
            st.session_state["raw_text"] = pasted
            st.session_state["source_label"] = "manual"

    elif mode == "URL (web page)":
        url = st.text_input("URL", placeholder="https://investor.example.com/q1-2026-transcript")
        if url and st.button("Fetch URL"):
            with st.spinner(f"Fetching {url}..."):
                try:
                    st.session_state["raw_text"] = fetch_transcripts.from_url(url)
                    st.session_state["source_label"] = f"url:{url}"
                    st.success(f"Fetched {len(st.session_state['raw_text']):,} characters.")
                except Exception as e:
                    st.error(f"Fetch failed: {e}")

    elif mode == "YouTube URL":
        url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...")
        if url and st.button("Pull captions"):
            with st.spinner("Pulling YouTube auto-captions..."):
                try:
                    st.session_state["raw_text"] = fetch_transcripts.from_youtube(url)
                    st.session_state["source_label"] = f"youtube:{url}"
                    st.success(f"Got {len(st.session_state['raw_text']):,} characters of captions.")
                except Exception as e:
                    st.error(f"YouTube fetch failed: {e}")

    raw_text = st.session_state.get("raw_text", "")
    source_label = st.session_state.get("source_label", "")

    if raw_text:
        st.markdown("### Preview & metadata")
        meta = parse_transcript.extract_metadata(raw_text)

        m1, m2, m3 = st.columns(3)
        ticker = m1.text_input("Ticker", value=meta["ticker"]).upper()
        quarter = m2.text_input("Quarter", value=meta["quarter"], help="e.g. Q1-2026")
        call_date = m3.text_input("Call date", value=meta["call_date"], help="YYYY-MM-DD")
        company = st.text_input("Company name", value=meta["company_name"])

        with st.expander("Raw text preview (first 1500 chars)"):
            st.text(raw_text[:1500] + ("..." if len(raw_text) > 1500 else ""))

        run_ai = st.checkbox("Run Claude deep analysis", value=False, disabled=not sentiment_engine.is_available(),
                             help="Requires ANTHROPIC_API_KEY. Costs ~$0.05-0.20 per call.")

        if st.button("Ingest + analyze", type="primary"):
            if not ticker or not quarter:
                st.error("Ticker and quarter are required.")
            else:
                with st.spinner("Parsing and analyzing..."):
                    parsed = parse_transcript.parse(raw_text, hint_ticker=ticker, hint_quarter=quarter)
                    parsed.ticker = ticker
                    parsed.quarter = quarter
                    parsed.call_date = call_date
                    text_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
                    storage.upsert_company(ticker, company or ticker)
                    tid = storage.save_transcript(
                        ticker=ticker, quarter=quarter, call_date=call_date,
                        raw_text=raw_text, text_hash=text_hash, source=source_label,
                        parsed=parsed.to_dict(),
                    )
                    report = linguistic_analyzer.analyze(parsed)
                    score = linguistic_analyzer.defensiveness_score(report)
                    claude_payload = {}
                    if run_ai and sentiment_engine.is_available():
                        sections = {
                            "prepared_ceo": parse_transcript.section_text(parsed, "prepared_ceo"),
                            "prepared_cfo": parse_transcript.section_text(parsed, "prepared_cfo"),
                            "qa": parse_transcript.section_text(parsed, "qa"),
                        }
                        sections = {k: v for k, v in sections.items() if v}
                        tone = sentiment_engine.classify_tones(parsed.text_hash, sections)
                        if tone:
                            claude_payload["tone"] = tone.model_dump()
                        avoidance = sentiment_engine.detect_topic_avoidance(parsed.text_hash, sections.get("qa", ""))
                        if avoidance:
                            claude_payload["avoidance"] = avoidance.model_dump()
                        guidance = sentiment_engine.assess_forward_guidance(parsed.text_hash, parse_transcript.all_executive_text(parsed))
                        if guidance:
                            claude_payload["guidance"] = guidance.model_dump()
                    storage.save_analysis(tid, report.model_dump(), score, claude_payload or None)
                    clear_caches()
                # Clear the fetched text so a fresh ingest starts clean next time
                st.session_state.pop("raw_text", None)
                st.session_state.pop("source_label", None)
                st.success(f"{ticker} {quarter} ingested. Defensiveness score: **{score:.1f}**")
                st.balloons()


# =============================================================================
# COMPANY DEEP-DIVE
# =============================================================================
with tab_company:
    tickers = sorted(df_all["ticker"].unique())
    if not tickers:
        st.info("Ingest a transcript first.")
    else:
        c1, c2 = st.columns([1, 3])
        with c1:
            sel_ticker = st.selectbox("Ticker", tickers)
        with c2:
            quarters = df_all[df_all["ticker"] == sel_ticker]["quarter"].tolist()
            sel_quarter = st.selectbox("Quarter", quarters)

        row = df_all[(df_all["ticker"] == sel_ticker) & (df_all["quarter"] == sel_quarter)].iloc[0]
        analysis = get_analysis_for(int(row["id"]))

        if not analysis:
            st.warning("No analysis stored. Re-ingest or run `python run_daily.py analyze --ticker {} --quarter {}`.".format(sel_ticker, sel_quarter))
        else:
            metrics = analysis["metrics"]
            score = analysis["defensiveness_score"]
            col_g, col_r = st.columns(2)
            with col_g:
                st.plotly_chart(gauge_chart(score), use_container_width=True)
            with col_r:
                st.plotly_chart(radar_components(metrics, score), use_container_width=True)

            # Metric cards
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Hedging /1k", f"{metrics['hedging']['total_per_1000']:.1f}")
            m2.metric("Defensive /1k", f"{metrics['defensive']['total_per_1000']:.1f}")
            m3.metric("Certainty /1k", f"{metrics['certainty']['per_1000_words']:.1f}")
            m4.metric("Q&A evasion", f"{metrics['qa_evasion']['evasion_rate']*100:.0f}%")

            # Top markers in each category
            st.markdown("## Most-used markers")
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.markdown("**Hedging**")
                top_hedge: dict[str, int] = {}
                for cat in ("epistemic", "probability", "approximation", "conditional"):
                    top_hedge.update(metrics["hedging"][cat]["top_markers"])
                if top_hedge:
                    st.dataframe(
                        pd.DataFrame(sorted(top_hedge.items(), key=lambda x: -x[1]), columns=["Phrase", "Count"]),
                        use_container_width=True, hide_index=True, height=240,
                    )
            with mc2:
                st.markdown("**Defensive**")
                top_def: dict[str, int] = {}
                for cat in ("reframing", "attention_grabbing", "compliments", "non_answers"):
                    top_def.update(metrics["defensive"][cat]["top_markers"])
                if top_def:
                    st.dataframe(
                        pd.DataFrame(sorted(top_def.items(), key=lambda x: -x[1]), columns=["Phrase", "Count"]),
                        use_container_width=True, hide_index=True, height=240,
                    )
            with mc3:
                st.markdown("**Certainty**")
                top_cert = metrics["certainty"]["top_markers"]
                if top_cert:
                    st.dataframe(
                        pd.DataFrame(sorted(top_cert.items(), key=lambda x: -x[1]), columns=["Phrase", "Count"]),
                        use_container_width=True, hide_index=True, height=240,
                    )

            # Evasive exchanges
            ex_list = metrics.get("qa_evasion", {}).get("evasive_excerpts", [])
            if ex_list:
                st.markdown("## Notable evasive exchanges")
                for ex in ex_list:
                    firm = f", {ex.get('firm','')}" if ex.get("firm") else ""
                    st.markdown(
                        f"<div class='exchange'>"
                        f"<div class='meta'>{ex.get('analyst','—')}{firm} &middot; Evasion score {ex.get('evasion_score','')}</div>"
                        f"<div class='q'>Q: {html.escape(ex.get('question_excerpt',''))}</div>"
                        f"<div class='a'>A: {html.escape(ex.get('answer_excerpt',''))}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            # Highlighted transcript
            st.markdown("## Highlighted transcript")
            st.markdown(
                "<span class='hl-hedge'>hedging</span>&nbsp;&nbsp;"
                "<span class='hl-def'>defensive</span>&nbsp;&nbsp;"
                "<span class='hl-cert'>certainty</span>",
                unsafe_allow_html=True,
            )
            with storage.connect() as conn:
                raw_row = conn.execute("SELECT raw_text FROM transcripts WHERE id=?", (int(row["id"]),)).fetchone()
            transcript_raw = raw_row["raw_text"] if raw_row else ""
            highlighted = highlight_text(transcript_raw)
            st.markdown(f"<div class='transcript-box'>{highlighted}</div>", unsafe_allow_html=True)

            # Claude analysis (if any)
            claude = analysis.get("claude_analysis")
            if claude:
                st.markdown("## Claude deep-analysis")
                if "tone" in claude:
                    st.markdown(f"**Overall tone:** {claude['tone'].get('overall_tone','')}")
                    st.markdown(f"_{claude['tone'].get('overall_rationale','')}_")
                if "guidance" in claude:
                    g = claude["guidance"]
                    st.markdown(f"**Forward guidance certainty:** {g.get('certainty_score','—')}/10")
                    st.markdown(g.get("rationale", ""))
                if "avoidance" in claude:
                    av = claude["avoidance"]
                    if av.get("avoided_topics"):
                        st.markdown("**Topic avoidance:**")
                        for t in av["avoided_topics"]:
                            st.markdown(f"- _{t.get('topic')}_ probed {t.get('times_probed')}× — {t.get('avoidance_evidence','')}")


# =============================================================================
# COMPARE
# =============================================================================
with tab_compare:
    tickers = sorted(df_all["ticker"].unique())
    if len(tickers) == 0:
        st.info("Ingest a transcript first.")
    else:
        sel = st.selectbox("Ticker to compare", tickers, key="cmp_ticker")
        cmp = comparison_engine.compare(sel)
        if not cmp:
            st.info(f"Need at least 2 analyzed transcripts for {sel}.")
        else:
            st.markdown(
                f"### {sel} &mdash; {cmp.current_quarter} vs {cmp.prior_quarter}"
            )

            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown(
                    f"<div class='kpi'><div class='kpi-label'>Prior score</div><div class='kpi-value'>{cmp.prior_defensiveness:.1f}</div></div>",
                    unsafe_allow_html=True,
                )
            with c2:
                st.markdown(
                    f"<div class='kpi'><div class='kpi-label'>Current score</div><div class='kpi-value'>{cmp.current_defensiveness:.1f}</div></div>",
                    unsafe_allow_html=True,
                )
            with c3:
                delta_html = format_delta(cmp.defensiveness_pct_change, "%")
                flag_html = " <span class='flag'>FLAG</span>" if cmp.flagged_as_more_defensive else ""
                st.markdown(
                    f"<div class='kpi'><div class='kpi-label'>Change{flag_html}</div><div class='kpi-value'>{delta_html}</div></div>",
                    unsafe_allow_html=True,
                )

            st.markdown("## Largest shifts")
            st.plotly_chart(deltas_bar(cmp.all_deltas), use_container_width=True)

            st.markdown("## Full delta table")
            delta_df = pd.DataFrame([{
                "Metric": d.metric.replace("_", " "),
                "Prior": d.prior_value,
                "Current": d.current_value,
                "Δ": round(d.abs_delta, 2),
                "% Δ": d.pct_delta,
            } for d in cmp.all_deltas])
            st.dataframe(delta_df, use_container_width=True, hide_index=True)


# =============================================================================
# DIGEST
# =============================================================================
with tab_digest:
    available_dates = sorted(df_all["call_date"].unique(), reverse=True)
    if not available_dates:
        st.info("No transcripts to build a digest from.")
    else:
        col_d, col_b = st.columns([3, 1])
        with col_d:
            sel_date = st.selectbox("Digest date", available_dates)
        with col_b:
            st.markdown("&nbsp;")
            generate = st.button("Generate digest", type="primary", use_container_width=True)

        if generate:
            with st.spinner("Rendering digest..."):
                html_str, html_path, pdf_path = digest_generator.render(sel_date, load_watchlist())
            st.success(f"Wrote {html_path}")
            if pdf_path:
                st.success(f"PDF: {pdf_path}")
            else:
                st.info("PDF skipped (weasyprint unavailable).")

        # Preview existing digest
        digest_path = digest_generator.OUTPUTS_DIR / f"digest_{sel_date}.html"
        if digest_path.exists():
            with digest_path.open(encoding="utf-8") as f:
                html_content = f.read()
            st.markdown("### Preview")
            st.components.v1.html(html_content, height=900, scrolling=True)
            with open(digest_path, "rb") as f:
                st.download_button("Download HTML", f, file_name=digest_path.name, mime="text/html")
            pdf_path = digest_generator.OUTPUTS_DIR / f"digest_{sel_date}.pdf"
            if pdf_path.exists():
                with open(pdf_path, "rb") as f:
                    st.download_button("Download PDF", f, file_name=pdf_path.name, mime="application/pdf")
        else:
            st.info("No digest rendered for this date yet — click `Generate digest`.")


# =============================================================================
# WATCHLIST
# =============================================================================
with tab_watchlist:
    st.markdown("## Watchlist")
    wl = load_watchlist()
    if wl:
        st.dataframe(pd.DataFrame(wl), use_container_width=True, hide_index=True)

    st.markdown("### Add ticker")
    add_cols = st.columns([1, 3, 2, 2, 1])
    new_ticker = add_cols[0].text_input("Ticker", key="wl_t")
    new_name = add_cols[1].text_input("Company", key="wl_n")
    new_sector = add_cols[2].text_input("Sector", key="wl_s")
    new_ind = add_cols[3].text_input("Industry", key="wl_i")
    if add_cols[4].button("Add", key="wl_add"):
        if new_ticker:
            wl = [w for w in wl if w["ticker"].upper() != new_ticker.upper()]
            wl.append({
                "ticker": new_ticker.upper(),
                "name": new_name or new_ticker.upper(),
                "sector": new_sector, "industry": new_ind,
            })
            save_watchlist(wl)
            st.rerun()

    if wl:
        st.markdown("### Remove ticker")
        rm = st.selectbox("Ticker", [w["ticker"] for w in wl], key="wl_rm")
        if st.button("Remove", key="wl_rm_btn"):
            wl = [w for w in wl if w["ticker"] != rm]
            save_watchlist(wl)
            st.rerun()
