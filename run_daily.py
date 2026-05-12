"""Earnings Intelligence Engine — CLI orchestrator.

Usage:
  python run_daily.py ingest --file path/to/transcript.pdf [--ticker AAPL --quarter Q1-2026]
  python run_daily.py ingest --url https://...
  python run_daily.py ingest --paste
  python run_daily.py analyze --ticker AAPL --quarter Q1-2026
  python run_daily.py compare --ticker AAPL
  python run_daily.py digest --date 2026-05-12
  python run_daily.py watchlist --add MSFT
  python run_daily.py watchlist --list
  python run_daily.py backfill --ticker AAPL --quarters 8
  python run_daily.py demo
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

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

LOG_PATH = ROOT / "logs" / "earnings.log"
TICKERS_PATH = ROOT / "config" / "tickers.yaml"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging(level: str = "INFO") -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def load_watchlist() -> list[dict]:
    if not TICKERS_PATH.exists():
        return []
    with TICKERS_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("watchlist", [])


def save_watchlist(items: list[dict]) -> None:
    TICKERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TICKERS_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"watchlist": items}, f, sort_keys=False)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_ingest(args: argparse.Namespace) -> int:
    if args.file:
        raw_text = fetch_transcripts.from_file(Path(args.file))
        source = f"file:{args.file}"
    elif args.url:
        raw_text = fetch_transcripts.from_url(args.url)
        source = f"url:{args.url}"
    elif args.paste:
        raw_text = fetch_transcripts.from_stdin_prompt()
        source = "manual"
    else:
        print("Provide --file, --url, or --paste", file=sys.stderr)
        return 2

    parsed = parse_transcript.parse(raw_text, hint_ticker=args.ticker or "", hint_quarter=args.quarter or "")
    if args.ticker:
        parsed.ticker = args.ticker.upper()
    if args.quarter:
        parsed.quarter = args.quarter
    if args.date:
        parsed.call_date = args.date

    if not parsed.ticker or not parsed.quarter:
        print(
            "Could not auto-detect ticker/quarter. Re-run with --ticker XYZ --quarter Q1-2026",
            file=sys.stderr,
        )
        return 2

    storage.init_db()
    storage.upsert_company(parsed.ticker, parsed.company_name or parsed.ticker)
    text_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    tid = storage.save_transcript(
        ticker=parsed.ticker,
        quarter=parsed.quarter,
        call_date=parsed.call_date,
        raw_text=raw_text,
        text_hash=text_hash,
        source=source,
        parsed=parsed.to_dict(),
    )

    # Persist raw text to disk for reproducibility
    raw_dir = ROOT / "data" / "transcripts" / parsed.ticker
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{parsed.quarter}.txt"
    raw_path.write_text(raw_text, encoding="utf-8")

    print(f"Ingested {parsed.ticker} {parsed.quarter} (call_date={parsed.call_date}, id={tid})")
    print(f"  words: {parsed.raw_word_count}, speakers: {len(parsed.speakers)}, Q&A pairs: {len(parsed.qa_pairs)}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    storage.init_db()
    t = storage.get_transcript(args.ticker, args.quarter)
    if not t:
        print(f"No transcript for {args.ticker} {args.quarter}. Run ingest first.", file=sys.stderr)
        return 2

    parsed_dict = json.loads(t["parsed_json"]) if t.get("parsed_json") else None
    if not parsed_dict:
        parsed = parse_transcript.parse(t["raw_text"], hint_ticker=t["ticker"], hint_quarter=t["quarter"])
    else:
        parsed = parse_transcript.parse(t["raw_text"], hint_ticker=t["ticker"], hint_quarter=t["quarter"])

    report = linguistic_analyzer.analyze(parsed)
    score = linguistic_analyzer.defensiveness_score(report)
    metrics = report.model_dump()

    # AI deep-analysis layer (optional)
    claude_payload: dict = {}
    if sentiment_engine.is_available():
        print("Running Claude analyses...", file=sys.stderr)
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
    else:
        print("ANTHROPIC_API_KEY not set — skipping AI deep analysis.", file=sys.stderr)

    storage.save_analysis(t["id"], metrics, score, claude_payload or None)

    # Persist analysis JSON for inspection
    out_dir = ROOT / "data" / "analyses" / args.ticker.upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.quarter}.json"
    out_path.write_text(json.dumps({
        "metrics": metrics,
        "defensiveness_score": score,
        "claude_analysis": claude_payload,
    }, indent=2), encoding="utf-8")

    print(f"Analyzed {args.ticker} {args.quarter} — defensiveness score: {score}")
    print(f"  hedging /1k: {metrics['hedging']['total_per_1000']}")
    print(f"  defensive phrases /1k: {metrics['defensive']['total_per_1000']}")
    print(f"  certainty /1k: {metrics['certainty']['per_1000_words']}")
    print(f"  Q&A evasion rate: {metrics['qa_evasion']['evasion_rate']:.1%}")
    print(f"  wrote {out_path}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    storage.init_db()
    report = comparison_engine.compare(args.ticker)
    if not report:
        print(f"Need at least 2 analyzed transcripts for {args.ticker} to compare.", file=sys.stderr)
        return 2

    print(f"\n{report.ticker}: {report.current_quarter} vs {report.prior_quarter}")
    print(f"  Defensiveness: {report.prior_defensiveness:.1f} → {report.current_defensiveness:.1f}  "
          f"(Δ {report.defensiveness_delta:+.1f}, {report.defensiveness_pct_change:+.1f}%)")
    if report.flagged_as_more_defensive:
        print("  *** FLAGGED: defensiveness up more than 25% QoQ ***")
    print("\n  Top 5 shifts:")
    for s in report.top_shifts:
        print(f"    {s.metric:32s}  {s.prior_value:>8.2f} → {s.current_value:>8.2f}  ({s.pct_delta:+.1f}%)")

    if sentiment_engine.is_available():
        history = storage.history_for_ticker(args.ticker, limit=2)
        # Tone summary fallback
        cur_summary = (history[0].get("claude_analysis") or {}).get("tone", {}).get("overall_rationale", "")
        prior_summary = (history[1].get("claude_analysis") or {}).get("tone", {}).get("overall_rationale", "")
        narrative = sentiment_engine.write_qoq_narrative(
            history[0]["transcript_id"].__str__(),
            args.ticker,
            {d.metric: d.pct_delta for d in report.top_shifts},
            cur_summary, prior_summary,
        )
        if narrative:
            print(f"\n  Narrative ({narrative.direction}):")
            print("  " + narrative.narrative.replace("\n", "\n  "))
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    storage.init_db()
    watchlist = load_watchlist()
    date_str = args.date or datetime.utcnow().date().isoformat()
    html, html_path, pdf_path = digest_generator.render(date_str, watchlist)
    print(f"Digest written: {html_path}")
    if pdf_path:
        print(f"PDF: {pdf_path}")
    else:
        print("PDF generation skipped (weasyprint unavailable).")
    return 0


def cmd_watchlist(args: argparse.Namespace) -> int:
    items = load_watchlist()
    if args.add:
        ticker = args.add.upper()
        if any(w["ticker"].upper() == ticker for w in items):
            print(f"{ticker} already on watchlist.")
            return 0
        items.append({"ticker": ticker, "name": args.name or ticker, "sector": "", "industry": ""})
        save_watchlist(items)
        print(f"Added {ticker} to watchlist.")
    elif args.remove:
        ticker = args.remove.upper()
        items = [w for w in items if w["ticker"].upper() != ticker]
        save_watchlist(items)
        print(f"Removed {ticker} from watchlist.")
    else:
        for w in items:
            print(f"  {w['ticker']:6s}  {w.get('name', '')}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """List which historical quarters are missing — actual ingestion still
    needs a source URL or file per quarter. Free-data scrapers are too
    fragile to silently bulk-fetch."""
    history = storage.list_transcripts_for_ticker(args.ticker)
    have = {h["quarter"] for h in history}
    today = datetime.utcnow().date()
    year, q = today.year, (today.month - 1) // 3 + 1
    targets = []
    for _ in range(args.quarters):
        targets.append(f"Q{q}-{year}")
        q -= 1
        if q == 0:
            q = 4
            year -= 1
    missing = [t for t in targets if t not in have]
    print(f"{args.ticker}: missing {len(missing)} of {args.quarters} target quarters")
    for m in missing:
        print(f"  {m}  — run `python run_daily.py ingest --url <source> --ticker {args.ticker} --quarter {m}`")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """End-to-end demo against the bundled sample transcripts."""
    storage.init_db()
    fixtures_dir = ROOT / "tests" / "fixtures"
    fixtures = sorted(fixtures_dir.glob("sample_*.txt"))
    if not fixtures:
        print("No fixtures found in tests/fixtures/", file=sys.stderr)
        return 2

    for fx in fixtures:
        print(f"\n=== Ingesting {fx.name} ===")
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
        print(f"  {parsed.ticker} {parsed.quarter} — score {score}, "
              f"hedging {report.hedging.total_per_1000}/1k, "
              f"defensive {report.defensive.total_per_1000}/1k, "
              f"evasion {report.qa_evasion.evasion_rate:.1%}")

    # Compare the most recent two quarters of ACME
    print("\n=== QoQ Comparison ===")
    cmp_report = comparison_engine.compare("ACME")
    if cmp_report:
        print(f"  ACME: {cmp_report.prior_defensiveness:.1f} → {cmp_report.current_defensiveness:.1f}  "
              f"({cmp_report.defensiveness_pct_change:+.1f}%)")
        for s in cmp_report.top_shifts[:3]:
            print(f"    {s.metric}: {s.prior_value:.2f} → {s.current_value:.2f} ({s.pct_delta:+.1f}%)")

    # Build a digest for today using the most recent fixture call_date
    last_date = max(storage.list_transcripts_for_ticker("ACME"), key=lambda t: t["call_date"])["call_date"]
    print(f"\n=== Generating digest for {last_date} ===")
    html, html_path, pdf_path = digest_generator.render(last_date, load_watchlist())
    print(f"  HTML: {html_path}")
    print(f"  PDF:  {pdf_path or '(skipped)'}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Earnings Intelligence Engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Ingest a transcript")
    src = ing.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Path to .txt or .pdf")
    src.add_argument("--url", help="URL to scrape (YouTube or web)")
    src.add_argument("--paste", action="store_true", help="Paste from stdin")
    ing.add_argument("--ticker", help="Override detected ticker")
    ing.add_argument("--quarter", help="Override detected quarter, e.g. Q1-2026")
    ing.add_argument("--date", help="Override call date YYYY-MM-DD")
    ing.set_defaults(func=cmd_ingest)

    an = sub.add_parser("analyze", help="Run linguistic + AI analysis")
    an.add_argument("--ticker", required=True)
    an.add_argument("--quarter", required=True)
    an.set_defaults(func=cmd_analyze)

    cmp = sub.add_parser("compare", help="QoQ comparison")
    cmp.add_argument("--ticker", required=True)
    cmp.set_defaults(func=cmd_compare)

    dig = sub.add_parser("digest", help="Generate daily HTML+PDF digest")
    dig.add_argument("--date", help="ISO date (defaults to today)")
    dig.set_defaults(func=cmd_digest)

    w = sub.add_parser("watchlist", help="Manage tracked tickers")
    w.add_argument("--add")
    w.add_argument("--remove")
    w.add_argument("--name", help="Company name when adding")
    w.set_defaults(func=cmd_watchlist)

    bf = sub.add_parser("backfill", help="Report missing historical quarters")
    bf.add_argument("--ticker", required=True)
    bf.add_argument("--quarters", type=int, default=8)
    bf.set_defaults(func=cmd_backfill)

    demo = sub.add_parser("demo", help="End-to-end demo on bundled fixtures")
    demo.set_defaults(func=cmd_demo)

    return p


def main() -> int:
    # Windows consoles default to cp1252 — force UTF-8 so we can print Δ, →, etc.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    load_dotenv(ROOT / ".env")
    setup_logging()
    storage.init_db()
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
