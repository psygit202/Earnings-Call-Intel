"""Record an animated GIF demonstrating YouTube ingest in the dashboard.

Workflow recorded:
    1. Empty ingest tab
    2. YouTube URL mode + URL pasted
    3. Captions fetched, metadata auto-detected
    4. Ingested + scored
    5. Compare tab showing QoQ delta vs prior quarter

Run:
    1. Reset DB to have ONLY TSLA Q4-2023 (this script does that automatically)
    2. Launch dashboard (must already be running on :8501)
    3. python docs/record_demo_gif.py
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import fetch_transcripts, linguistic_analyzer, parse_transcript, storage  # noqa: E402

OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
FRAMES_DIR = OUT / "_gif_frames"
FRAMES_DIR.mkdir(exist_ok=True)
GIF_OUT = OUT / "demo_youtube.gif"

URL = "http://localhost:8501"
TESLA_Q1_URL = "https://www.youtube.com/watch?v=xoCrPnI-o6s"

VIEWPORT = {"width": 1280, "height": 820}


def reset_db_to_q4_only() -> None:
    """Clear DB and pre-seed only Q4-2023 so the demo adds Q1-2024."""
    storage.init_db()
    with storage.connect() as conn:
        conn.execute("DELETE FROM analyses")
        conn.execute("DELETE FROM transcripts")
        conn.execute("DELETE FROM companies")

    print("Pre-seeding Tesla Q4-2023...")
    raw = fetch_transcripts.from_youtube("https://www.youtube.com/watch?v=A_2Z-AEtdAg")
    parsed = parse_transcript.parse(raw, hint_ticker="TSLA", hint_quarter="Q4-2023")
    parsed.ticker = "TSLA"
    parsed.quarter = "Q4-2023"
    parsed.call_date = "2024-01-24"
    text_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    storage.upsert_company("TSLA", "Tesla, Inc.", sector="Consumer Cyclical", industry="Auto Manufacturers")
    tid = storage.save_transcript(
        ticker="TSLA", quarter="Q4-2023", call_date="2024-01-24",
        raw_text=raw, text_hash=text_hash, source="youtube",
        parsed=parsed.to_dict(),
    )
    report = linguistic_analyzer.analyze(parsed)
    score = linguistic_analyzer.defensiveness_score(report)
    storage.save_analysis(tid, report.model_dump(), score, None)
    print(f"  seeded, score={score}")


def click_tab(page, label: str) -> None:
    page.locator(f'button[data-baseweb="tab"]:has-text("{label}")').first.click()
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(1500)


def snap(page, name: str) -> Path:
    path = FRAMES_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print(f"  captured {path.name}")
    return path


def main() -> int:
    print("Resetting DB to single-quarter state...")
    reset_db_to_q4_only()

    frames: list[tuple[Path, int]] = []  # (path, duration_ms)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        page = ctx.new_page()

        print("\nOpening dashboard...")
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # Frame 1: empty Ingest tab (no source selected yet)
        click_tab(page, "Ingest")
        frames.append((snap(page, "f1_ingest_empty"), 1800))

        # Frame 2: YouTube mode selected
        page.locator('label:has-text("YouTube URL")').first.click()
        page.wait_for_timeout(800)
        frames.append((snap(page, "f2_youtube_mode"), 1400))

        # Frame 3: URL pasted — press Tab so Streamlit registers the value
        url_input = page.locator('input[type="text"]').first
        url_input.click()
        url_input.fill(TESLA_Q1_URL)
        url_input.press("Tab")  # commits the value to Streamlit state
        # Wait for the "Pull captions" button to appear after rerun
        page.wait_for_selector('button:has-text("Pull captions")', timeout=10000)
        page.wait_for_timeout(800)
        frames.append((snap(page, "f3_url_pasted"), 1800))

        # Frame 4: Click "Pull captions" — fetch takes ~5-10s for YouTube
        page.locator('button:has-text("Pull captions")').first.click()
        print("  waiting for YouTube fetch (up to 30s)...")
        # Wait for success banner OR for "Preview & metadata" heading to appear
        try:
            page.wait_for_selector('h3:has-text("Preview")', timeout=45000)
        except Exception:
            page.wait_for_timeout(15000)
        page.wait_for_timeout(2000)
        frames.append((snap(page, "f4_captions_fetched"), 2400))

        # Fill ticker (parser can't auto-detect from raw captions)
        ticker_input = page.locator('input[aria-label="Ticker"]').first
        ticker_input.click()
        ticker_input.fill("TSLA")
        ticker_input.press("Tab")
        page.wait_for_timeout(800)

        # Frame 5: scroll down to the ingest button so it's visible
        page.evaluate("window.scrollBy(0, 380)")
        page.wait_for_timeout(1200)
        frames.append((snap(page, "f5_metadata_ready"), 2200))

        # Frame 6: Click "Ingest + analyze"
        page.locator('button:has-text("Ingest + analyze")').first.click()
        # The success message contains "ingested." — wait for the alert to appear
        try:
            page.wait_for_selector('div[data-testid="stAlert"]:has-text("ingested")', timeout=30000)
        except Exception:
            page.wait_for_timeout(8000)
        page.wait_for_timeout(2000)
        frames.append((snap(page, "f6_ingested"), 2600))

        # Frame 7: Compare tab — show the new QoQ delta
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        click_tab(page, "Compare")
        # Make sure the bar chart has rendered
        page.wait_for_timeout(2500)
        frames.append((snap(page, "f7_compare_delta"), 3500))

        browser.close()

    # Stitch frames into a GIF
    print(f"\nStitching {len(frames)} frames into GIF...")
    images = []
    durations = []
    for path, dur in frames:
        img = Image.open(path).convert("P", palette=Image.ADAPTIVE, colors=192)
        # Resize for smaller file
        w, h = img.size
        target_w = 900
        if w > target_w:
            ratio = target_w / w
            img = img.resize((target_w, int(h * ratio)), Image.LANCZOS)
            img = img.convert("P", palette=Image.ADAPTIVE, colors=192)
        images.append(img)
        durations.append(dur)

    images[0].save(
        GIF_OUT,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"\nWrote {GIF_OUT}  ({GIF_OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
