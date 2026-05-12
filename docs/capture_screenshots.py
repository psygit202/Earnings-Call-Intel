"""Capture dashboard screenshots for the README.

Prereqs:
  - dashboard running on http://localhost:8501
  - ACME fixtures seeded (run `python run_daily.py demo` first)
  - playwright + chromium installed

Run:
  python docs/capture_screenshots.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

URL = "http://localhost:8501"
VIEWPORT = {"width": 1440, "height": 900}


def wait_quiet(page, ms: int = 1500) -> None:
    """Streamlit re-renders on tab clicks; let the dust settle."""
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(ms)


def click_tab(page, label: str) -> None:
    page.locator(f'button[data-baseweb="tab"]:has-text("{label}")').first.click()
    wait_quiet(page)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = ctx.new_page()
        print(f"Loading {URL} ...")
        page.goto(URL, wait_until="networkidle", timeout=30000)
        wait_quiet(page, 3000)

        # 1. Overview tab — default
        print("Capturing overview...")
        page.screenshot(path=str(OUT / "01_overview.png"), full_page=True)

        # 2. Company Deep-Dive
        print("Capturing company deep-dive...")
        click_tab(page, "Company Deep-Dive")
        # Select Q1-2026 to get the more interesting (defensive) call
        page.locator('div[data-baseweb="select"]').nth(1).click()
        wait_quiet(page, 500)
        # The select will show options — try clicking Q1-2026
        try:
            page.locator('li:has-text("Q1-2026")').first.click(timeout=3000)
            wait_quiet(page, 1500)
        except Exception:
            page.keyboard.press("Escape")
        page.screenshot(path=str(OUT / "02_company_deepdive.png"), full_page=True)

        # 3. Compare
        print("Capturing compare...")
        click_tab(page, "Compare")
        page.screenshot(path=str(OUT / "03_compare.png"), full_page=True)

        # 4. Digest — generate and screenshot
        print("Capturing digest...")
        click_tab(page, "Digest")
        # Click "Generate digest" button
        try:
            page.locator('button:has-text("Generate digest")').first.click(timeout=3000)
            wait_quiet(page, 3000)
        except Exception:
            print("  (digest already generated)")
        page.screenshot(path=str(OUT / "04_digest.png"), full_page=True)

        # 5. Ingest tab — show YouTube option
        print("Capturing ingest (YouTube mode)...")
        click_tab(page, "Ingest")
        try:
            page.locator('label:has-text("YouTube URL")').first.click(timeout=3000)
            wait_quiet(page, 800)
        except Exception:
            pass
        page.screenshot(path=str(OUT / "05_ingest_youtube.png"), full_page=True)

        # 6. Hero shot — Overview cropped to viewport
        print("Capturing hero...")
        click_tab(page, "Overview")
        page.screenshot(path=str(OUT / "00_hero.png"), full_page=False)

        browser.close()
    print(f"\nWrote screenshots to {OUT}")
    for f in sorted(OUT.glob("*.png")):
        kb = f.stat().st_size / 1024
        print(f"  {f.name}  ({kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
