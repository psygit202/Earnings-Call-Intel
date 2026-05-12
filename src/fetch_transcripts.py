"""Transcript ingestion from free sources only.

Supported inputs:
  * Local .txt or .pdf file (PDF via pypdf)
  * YouTube URL (auto-captions via youtube-transcript-api, no API key)
  * Generic web URL (best-effort HTML scrape, works on many IR pages and
    blog-style transcript hosts)
  * Manual paste (CLI hands the text directly to ingest_text)
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File ingest
# ---------------------------------------------------------------------------
def from_file(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".pdf":
        return _read_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf not installed — run: pip install pypdf") from e
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:
            log.warning("PDF page extraction failed: %s", e)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# YouTube ingest
# ---------------------------------------------------------------------------
_YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")


def is_youtube_url(url: str) -> bool:
    try:
        return urlparse(url).hostname in _YT_HOSTS
    except Exception:
        return False


def _youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname == "youtu.be":
        return parsed.path.lstrip("/")
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    # /shorts/<id> or /embed/<id>
    m = re.match(r"/(?:shorts|embed)/([^/?]+)", parsed.path)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract YouTube video ID from {url}")


def from_youtube(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as e:
        raise RuntimeError("youtube-transcript-api not installed") from e

    vid = _youtube_video_id(url)
    # Prefer English; fall back to first available
    try:
        chunks = YouTubeTranscriptApi.get_transcript(vid, languages=["en", "en-US", "en-GB"])
    except Exception:
        chunks = YouTubeTranscriptApi.get_transcript(vid)

    # YouTube auto-captions arrive as a stream of short fragments with no
    # speaker labels. Join into sentences for downstream heuristics.
    text = " ".join(c["text"].replace("\n", " ").strip() for c in chunks)
    # Try to insert paragraph breaks at long pauses (estimated by gaps in
    # timestamps).
    rebuilt: list[str] = []
    last_end = 0.0
    for c in chunks:
        start = float(c.get("start", 0))
        if start - last_end > 3.0 and rebuilt:
            rebuilt.append("\n")
        rebuilt.append(c["text"].replace("\n", " ").strip())
        last_end = start + float(c.get("duration", 0))
    return " ".join(rebuilt).replace(" \n ", "\n\n")


# ---------------------------------------------------------------------------
# Generic URL ingest
# ---------------------------------------------------------------------------
def from_url(url: str) -> str:
    if is_youtube_url(url):
        return from_youtube(url)

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise RuntimeError("requests / beautifulsoup4 not installed") from e

    headers = {
        "User-Agent": "Mozilla/5.0 (Earnings Intel Engine) AppleWebKit/537.36",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Strip noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "form"]):
        tag.decompose()

    # Try common transcript containers first
    candidates = []
    for sel in ["article", "main", "div.transcript", "div.article-body", "div.entry-content"]:
        candidates.extend(soup.select(sel))
    if not candidates:
        candidates = [soup.body or soup]

    best = max(candidates, key=lambda c: len(c.get_text(strip=True)))
    text = best.get_text(separator="\n", strip=True)
    # Collapse 3+ blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ---------------------------------------------------------------------------
# Manual paste
# ---------------------------------------------------------------------------
def from_stdin_prompt() -> str:
    sys.stderr.write(
        "Paste the transcript below. End with a single line containing 'EOF':\n"
    )
    sys.stderr.flush()
    lines: list[str] = []
    for line in sys.stdin:
        if line.strip() == "EOF":
            break
        lines.append(line)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------
def ingest(source: str | Path) -> tuple[str, str]:
    """Detect source type and return (raw_text, source_label).

    `source` is either a path string, URL, or '-' for stdin.
    """
    s = str(source)
    if s == "-":
        return from_stdin_prompt(), "manual"
    if s.startswith(("http://", "https://")):
        if is_youtube_url(s):
            return from_youtube(s), f"youtube:{s}"
        return from_url(s), f"url:{s}"
    return from_file(Path(s)), f"file:{s}"
