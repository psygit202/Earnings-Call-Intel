"""Capture a static screenshot of the Live Decode tab mid-run.

Runs the live decode for ~30 seconds at 50x speed so the metrics + chart
have built up meaningful data, then snaps a single still.

Run:
    python docs/capture_live_screenshot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots" / "live_decode.png"

URL = "http://localhost:8501"
VIEWPORT = {"width": 1440, "height": 900}


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)

        # Switch to Live Decode tab
        page.locator('button[data-baseweb="tab"]:has-text("Live Decode")').first.click()
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1500)

        # Bump speed to 50x
        slider = page.locator('div[data-baseweb="slider"]').first
        slider.click()
        for _ in range(8):
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(80)

        # Start
        page.locator('button:has-text("Start live decode")').first.click()
        # Wait long enough for several chunks + chart to render
        print("  decoding for 50s...")
        page.wait_for_timeout(50_000)

        # Streamlit renders into a main element, not document.body, so screenshot
        # the main container directly to get the full content height.
        print(f"Capturing {OUT}...")
        main_el = page.locator('section.main, div[data-testid="stMain"]').first
        try:
            main_el.screenshot(path=str(OUT))
        except Exception as e:
            print(f"  main-element capture failed ({e}); falling back to full_page")
            page.screenshot(path=str(OUT), full_page=True)
        kb = OUT.stat().st_size / 1024
        print(f"  {kb:.0f} KB")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
