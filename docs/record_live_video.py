"""Record a screen-capture video of the Live Decode tab in action.

Playwright records the browser viewport to WebM (no ffmpeg needed).

Prereqs:
  - Dashboard running on http://localhost:8501
  - playwright + chromium installed

Run:
    python docs/record_live_video.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "screenshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_PATH = OUT_DIR / "live_decode.webm"

URL = "http://localhost:8501"
VIEWPORT = {"width": 1280, "height": 800}


def main() -> int:
    tmp_video_dir = OUT_DIR / "_video_tmp"
    if tmp_video_dir.exists():
        shutil.rmtree(tmp_video_dir)
    tmp_video_dir.mkdir(parents=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            record_video_dir=str(tmp_video_dir),
            record_video_size=VIEWPORT,
        )
        page = ctx.new_page()
        print(f"Loading {URL}...")
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)

        # Click the Live Decode tab
        print("Switching to Live Decode tab...")
        page.locator('button[data-baseweb="tab"]:has-text("Live Decode")').first.click()
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1500)

        # Click "Start live decode" — uses the default Tesla URL + 50x speed
        # Bump speed to 50x for a faster recording
        print("Setting replay speed to 50x...")
        speed_slider = page.locator('div[data-baseweb="slider"]').first
        # The slider is a range — keyboard arrows are easier than dragging
        speed_slider.click()
        for _ in range(8):  # press right to push to 50x
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(80)

        print("Starting live decode (this records the action live)...")
        page.locator('button:has-text("Start live decode")').first.click()

        # Let it run for ~45 seconds — at 50x replay speed this covers
        # most of a 1-hour earnings call
        print("Recording for ~45 seconds...")
        # Wait for at least one chart to render (means decode started)
        try:
            page.wait_for_selector('div.js-plotly-plot', timeout=20000)
        except Exception:
            pass

        # Scroll down so the chart + transcript are visible in the recording
        page.evaluate("window.scrollBy(0, 250)")
        page.wait_for_timeout(45_000)
        # Scroll back up briefly to show the metrics at the end
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(3000)

        print("Closing browser to flush video...")
        page.close()
        ctx.close()
        browser.close()

    # Find the WebM file and move it
    webms = list(tmp_video_dir.glob("*.webm"))
    if not webms:
        print("ERROR: No video file produced", file=sys.stderr)
        return 1
    src = webms[0]
    if VIDEO_PATH.exists():
        VIDEO_PATH.unlink()
    shutil.move(str(src), str(VIDEO_PATH))
    shutil.rmtree(tmp_video_dir, ignore_errors=True)

    kb = VIDEO_PATH.stat().st_size / 1024
    print(f"\nWrote {VIDEO_PATH}  ({kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
