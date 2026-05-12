"""SQLite persistence for transcripts, analyses, and digests."""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "earnings.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sector      TEXT,
    industry    TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    quarter     TEXT NOT NULL,
    call_date   TEXT NOT NULL,
    source      TEXT,
    raw_text    TEXT NOT NULL,
    parsed_json TEXT,
    text_hash   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(ticker, quarter),
    FOREIGN KEY(ticker) REFERENCES companies(ticker)
);
CREATE INDEX IF NOT EXISTS idx_transcripts_date ON transcripts(call_date);
CREATE INDEX IF NOT EXISTS idx_transcripts_hash ON transcripts(text_hash);

CREATE TABLE IF NOT EXISTS analyses (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id         INTEGER NOT NULL,
    metrics_json          TEXT NOT NULL,
    claude_analysis_json  TEXT,
    defensiveness_score   REAL,
    created_at            TEXT NOT NULL,
    FOREIGN KEY(transcript_id) REFERENCES transcripts(id) ON DELETE CASCADE,
    UNIQUE(transcript_id)
);

CREATE TABLE IF NOT EXISTS digests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL UNIQUE,
    html_path   TEXT,
    pdf_path    TEXT,
    summary     TEXT,
    created_at  TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    log.info("Initialized database at %s", db_path)


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------
def upsert_company(ticker: str, name: str, sector: str = "", industry: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO companies(ticker, name, sector, industry) VALUES(?,?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET
                 name=excluded.name, sector=excluded.sector, industry=excluded.industry""",
            (ticker.upper(), name, sector, industry),
        )


def list_companies() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY ticker")]


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------
def save_transcript(
    ticker: str,
    quarter: str,
    call_date: str,
    raw_text: str,
    text_hash: str,
    source: str = "manual",
    parsed: dict | None = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO transcripts(ticker, quarter, call_date, source, raw_text, parsed_json, text_hash, created_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(ticker, quarter) DO UPDATE SET
                 call_date=excluded.call_date,
                 source=excluded.source,
                 raw_text=excluded.raw_text,
                 parsed_json=excluded.parsed_json,
                 text_hash=excluded.text_hash
               RETURNING id""",
            (
                ticker.upper(),
                quarter,
                call_date,
                source,
                raw_text,
                json.dumps(parsed) if parsed else None,
                text_hash,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.fetchone()[0]


def get_transcript(ticker: str, quarter: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE ticker=? AND quarter=?",
            (ticker.upper(), quarter),
        ).fetchone()
        return dict(row) if row else None


def list_transcripts_for_ticker(ticker: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, quarter, call_date, source FROM transcripts WHERE ticker=? ORDER BY call_date DESC",
            (ticker.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]


def list_transcripts_on_date(date_str: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM transcripts WHERE call_date=? ORDER BY ticker",
            (date_str,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
def save_analysis(
    transcript_id: int,
    metrics: dict,
    defensiveness_score: float,
    claude_analysis: dict | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO analyses(transcript_id, metrics_json, claude_analysis_json, defensiveness_score, created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(transcript_id) DO UPDATE SET
                 metrics_json=excluded.metrics_json,
                 claude_analysis_json=excluded.claude_analysis_json,
                 defensiveness_score=excluded.defensiveness_score""",
            (
                transcript_id,
                json.dumps(metrics),
                json.dumps(claude_analysis) if claude_analysis else None,
                defensiveness_score,
                datetime.utcnow().isoformat(),
            ),
        )


def get_analysis(transcript_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE transcript_id=?", (transcript_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metrics"] = json.loads(d.pop("metrics_json"))
        d["claude_analysis"] = json.loads(d["claude_analysis_json"]) if d["claude_analysis_json"] else None
        d.pop("claude_analysis_json", None)
        return d


def history_for_ticker(ticker: str, limit: int = 8) -> list[dict]:
    """Return prior analyses joined with transcript metadata, newest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.id AS transcript_id, t.ticker, t.quarter, t.call_date,
                      a.metrics_json, a.defensiveness_score, a.claude_analysis_json
               FROM transcripts t
               LEFT JOIN analyses a ON a.transcript_id = t.id
               WHERE t.ticker = ?
               ORDER BY t.call_date DESC
               LIMIT ?""",
            (ticker.upper(), limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d.pop("metrics_json")) if d.get("metrics_json") else None
            d["claude_analysis"] = (
                json.loads(d.pop("claude_analysis_json")) if d.get("claude_analysis_json") else None
            )
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------
def save_digest(date_str: str, html_path: str, pdf_path: str | None, summary: str) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO digests(date, html_path, pdf_path, summary, created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 html_path=excluded.html_path,
                 pdf_path=excluded.pdf_path,
                 summary=excluded.summary""",
            (date_str, html_path, pdf_path, summary, datetime.utcnow().isoformat()),
        )
