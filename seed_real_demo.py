"""Seed the database with REAL Tesla earnings-call data.

Pulls auto-captions from YouTube for two consecutive Tesla quarters,
parses, analyzes, and stores. After this runs you can launch the
dashboard and explore real data.

Usage:
    python seed_real_demo.py [--clear]

Requirements:
    pip install -r requirements.txt
    (No API keys needed — uses YouTube auto-captions, free.)
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import (  # noqa: E402
    fetch_transcripts,
    linguistic_analyzer,
    parse_transcript,
    storage,
)


# Real public Tesla earnings calls. Both have YouTube auto-captions
# available for free, no API key.
DEMOS = [
    {
        "ticker": "TSLA",
        "name": "Tesla, Inc.",
        "quarter": "Q4-2023",
        "call_date": "2024-01-24",
        "url": "https://www.youtube.com/watch?v=A_2Z-AEtdAg",
    },
    {
        "ticker": "TSLA",
        "name": "Tesla, Inc.",
        "quarter": "Q1-2024",
        "call_date": "2024-04-23",
        "url": "https://www.youtube.com/watch?v=xoCrPnI-o6s",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true",
                        help="Drop existing data before seeding")
    args = parser.parse_args()

    storage.init_db()
    if args.clear:
        with storage.connect() as conn:
            conn.execute("DELETE FROM analyses")
            conn.execute("DELETE FROM transcripts")
            conn.execute("DELETE FROM companies")
            conn.execute("DELETE FROM digests")
        print("Cleared existing data.\n")

    for d in DEMOS:
        print(f"=== {d['ticker']} {d['quarter']} ===")
        print(f"  Source: {d['url']}")
        print("  Pulling captions...", end=" ", flush=True)
        try:
            raw = fetch_transcripts.from_youtube(d["url"])
        except Exception as e:
            print(f"FAIL: {e}")
            continue
        print(f"got {len(raw):,} chars")

        parsed = parse_transcript.parse(raw, hint_ticker=d["ticker"], hint_quarter=d["quarter"])
        parsed.ticker = d["ticker"]
        parsed.quarter = d["quarter"]
        parsed.call_date = d["call_date"]
        parsed.company_name = d["name"]

        text_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        storage.upsert_company(d["ticker"], d["name"], sector="Consumer Cyclical", industry="Auto Manufacturers")
        tid = storage.save_transcript(
            ticker=d["ticker"], quarter=d["quarter"], call_date=d["call_date"],
            raw_text=raw, text_hash=text_hash, source=f"youtube:{d['url']}",
            parsed=parsed.to_dict(),
        )

        report = linguistic_analyzer.analyze(parsed)
        score = linguistic_analyzer.defensiveness_score(report)
        storage.save_analysis(tid, report.model_dump(), score, None)

        print(f"  Defensiveness score: {score}")
        print(f"  Hedging /1k:    {report.hedging.total_per_1000}")
        print(f"  Defensive /1k:  {report.defensive.total_per_1000}")
        print(f"  Certainty /1k:  {report.certainty.per_1000_words}")
        print()

    print("Done. Launch the dashboard:")
    print("    python -m streamlit run dashboard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
