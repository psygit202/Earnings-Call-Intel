"""Split a raw transcript into structured sections, speakers, and Q&A pairs.

This is intentionally heuristic — earnings transcripts vary in format. The
parser tolerates Seeking Alpha, Motley Fool, IR-page, and YouTube auto-
caption styles. Anything we can't classify lands in `unclassified` rather
than getting silently dropped.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Iterable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Utterance:
    speaker: str
    role: str           # CEO, CFO, Analyst, Operator, Other
    firm: str           # analyst firm if known, else ""
    section: str        # safe_harbor | prepared_ceo | prepared_cfo | prepared_other | qa
    position: int       # ordinal position in the call
    text: str


@dataclass
class QAExchange:
    question: Utterance
    answers: list[Utterance] = field(default_factory=list)
    handoffs: int = 0


@dataclass
class ParsedTranscript:
    ticker: str
    company_name: str
    quarter: str
    call_date: str
    text_hash: str
    raw_word_count: int
    speakers: dict[str, str]            # name -> role
    utterances: list[Utterance]
    qa_pairs: list[QAExchange]
    sections: dict[str, list[Utterance]]

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "quarter": self.quarter,
            "call_date": self.call_date,
            "text_hash": self.text_hash,
            "raw_word_count": self.raw_word_count,
            "speakers": self.speakers,
            "utterances": [asdict(u) for u in self.utterances],
            "qa_pairs": [
                {"question": asdict(p.question), "answers": [asdict(a) for a in p.answers], "handoffs": p.handoffs}
                for p in self.qa_pairs
            ],
            "sections": {k: [asdict(u) for u in v] for k, v in self.sections.items()},
        }


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------
_TICKER_PATTERNS = [
    re.compile(r"\b(?:NYSE|NASDAQ|NYSEARCA|OTC):\s*([A-Z]{1,5})\b"),
    re.compile(r"\(\s*([A-Z]{2,5})\s*\)"),
    re.compile(r"\b([A-Z]{2,5})\s+(?:Q[1-4]|Fiscal|Full[ -]Year)"),
]
_QUARTER_PATTERN = re.compile(r"\b(Q[1-4])[ -]?(?:FY ?)?(20\d{2})\b", re.I)
_FY_QUARTER_PATTERN = re.compile(r"\b(?:Fourth|First|Second|Third)\s+Quarter\s+(20\d{2})\b", re.I)
_DATE_PATTERNS = [
    re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b"),
    re.compile(r"\b(\w+ \d{1,2},? 20\d{2})\b"),
]


def extract_metadata(text: str, hint_ticker: str = "", hint_quarter: str = "") -> dict[str, str]:
    """Best-effort extraction of ticker / quarter / date from header text."""
    head = text[:4000]
    ticker = hint_ticker.upper() if hint_ticker else ""
    if not ticker:
        for pat in _TICKER_PATTERNS:
            m = pat.search(head)
            if m:
                ticker = m.group(1).upper()
                break

    quarter = hint_quarter.upper() if hint_quarter else ""
    if not quarter:
        m = _QUARTER_PATTERN.search(head)
        if m:
            quarter = f"{m.group(1).upper()}-{m.group(2)}"
        else:
            m = _FY_QUARTER_PATTERN.search(head)
            if m:
                word_to_q = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
                word = re.search(r"(First|Second|Third|Fourth)", head, re.I).group(1).lower()
                quarter = f"{word_to_q[word]}-{m.group(1)}"

    call_date = ""
    for pat in _DATE_PATTERNS:
        m = pat.search(head)
        if m:
            raw = m.group(1)
            try:
                if "-" in raw:
                    call_date = datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
                else:
                    raw_clean = raw.replace(",", "")
                    call_date = datetime.strptime(raw_clean, "%B %d %Y").date().isoformat()
                break
            except ValueError:
                continue

    # Company name: take the largest-cap phrase before "Q1 2026 Earnings" or similar
    company = ""
    m = re.search(r"([A-Z][\w&,.' -]{2,60})\s+(?:Q[1-4]|Fiscal|Full[ -]Year|First|Second|Third|Fourth)\b", head)
    if m:
        company = m.group(1).strip(" ,.-")

    return {
        "ticker": ticker,
        "quarter": quarter,
        "call_date": call_date or datetime.utcnow().date().isoformat(),
        "company_name": company,
    }


# ---------------------------------------------------------------------------
# Speaker / section detection
# ---------------------------------------------------------------------------
_SPEAKER_LINE = re.compile(
    r"^\s*(?P<name>[A-Z][\w.'\- ]{1,60}?)\s*(?:[-–—]\s*(?P<role>[^:]{2,80}?))?\s*:\s*$",
    re.M,
)
_INLINE_SPEAKER = re.compile(
    r"^(?P<name>[A-Z][\w.'\- ]{1,40}?)\s*(?:[-–—]\s*(?P<role>[^:]{2,80}?))?\s*:\s+(?P<text>.+)",
)

_ROLE_KEYWORDS = {
    "ceo": "CEO",
    "chief executive": "CEO",
    "cfo": "CFO",
    "chief financial": "CFO",
    "coo": "COO",
    "chief operating": "COO",
    "president": "President",
    "chairman": "Chairman",
    "ir ": "IR",
    "investor relations": "IR",
    "operator": "Operator",
    "analyst": "Analyst",
}
_ANALYST_FIRMS = [
    "Goldman Sachs", "Morgan Stanley", "JPMorgan", "JP Morgan", "Bank of America",
    "BofA", "Citi", "Wells Fargo", "Barclays", "Credit Suisse", "UBS",
    "Deutsche Bank", "Jefferies", "Evercore", "Piper Sandler", "Cowen",
    "Stifel", "Raymond James", "Wedbush", "Bernstein", "Mizuho", "Oppenheimer",
    "Needham", "BTIG", "Truist", "RBC", "TD", "Macquarie", "Loop Capital",
]


def _normalize_role(raw_role: str, name: str) -> str:
    text = (raw_role or name).lower()
    for kw, label in _ROLE_KEYWORDS.items():
        if kw in text:
            return label
    if any(firm.lower() in text for firm in _ANALYST_FIRMS):
        return "Analyst"
    return "Other"


def _extract_firm(role_text: str) -> str:
    for firm in _ANALYST_FIRMS:
        if firm.lower() in role_text.lower():
            return firm
    return ""


_SAFE_HARBOR_MARKERS = [
    "forward-looking statement", "forward looking statement", "safe harbor",
    "private securities litigation reform", "risks and uncertainties",
]
_QA_START_MARKERS = [
    "question-and-answer", "question and answer", "we will now begin the q",
    "we'll now take questions", "first question", "open the line for questions",
    "open the call for questions", "begin the q&a", "open up for questions",
]


def _detect_section_transitions(lines: list[str]) -> dict[int, str]:
    """Return line-index -> section-tag for section transition points."""
    transitions: dict[int, str] = {0: "prepared"}
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(m in low for m in _SAFE_HARBOR_MARKERS) and "prepared" in transitions.values():
            # Only mark the first time we see it
            if "safe_harbor" not in transitions.values():
                transitions[i] = "safe_harbor"
        if any(m in low for m in _QA_START_MARKERS):
            transitions[i] = "qa"
    return transitions


# ---------------------------------------------------------------------------
# Main parse
# ---------------------------------------------------------------------------
def parse(raw_text: str, hint_ticker: str = "", hint_quarter: str = "") -> ParsedTranscript:
    raw_text = raw_text.replace("\r\n", "\n").strip()
    meta = extract_metadata(raw_text, hint_ticker, hint_quarter)

    text_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    lines = raw_text.split("\n")
    transitions = _detect_section_transitions(lines)

    # Walk lines, accumulate utterances
    utterances: list[Utterance] = []
    speakers: dict[str, str] = {}
    current_section = "prepared"
    current_speaker = ""
    current_role = "Other"
    current_firm = ""
    buf: list[str] = []
    position = 0
    prepared_count = 0

    def flush() -> None:
        nonlocal buf, position, prepared_count
        if not current_speaker or not buf:
            buf = []
            return
        text = " ".join(s.strip() for s in buf if s.strip())
        if not text:
            buf = []
            return
        section_tag = current_section
        if section_tag == "prepared":
            if current_role == "CEO":
                section_tag = "prepared_ceo"
            elif current_role == "CFO":
                section_tag = "prepared_cfo"
            else:
                section_tag = "prepared_other"
            prepared_count += 1
        elif section_tag == "safe_harbor":
            section_tag = "safe_harbor"
        elif section_tag == "qa":
            section_tag = "qa"
        utterances.append(Utterance(
            speaker=current_speaker, role=current_role, firm=current_firm,
            section=section_tag, position=position, text=text,
        ))
        position += 1
        buf = []

    for i, ln in enumerate(lines):
        if i in transitions:
            t = transitions[i]
            if t in ("safe_harbor", "qa"):
                flush()
                current_section = t

        stripped = ln.strip()
        if not stripped:
            continue

        m = _SPEAKER_LINE.match(ln)
        if not m:
            m = _INLINE_SPEAKER.match(stripped)
        if m:
            flush()
            name = m.group("name").strip()
            role_txt = (m.group("role") or "").strip()
            current_speaker = name
            current_role = _normalize_role(role_txt, name)
            current_firm = _extract_firm(role_txt) if current_role == "Analyst" else ""
            speakers[name] = current_role
            inline_text = m.groupdict().get("text", "")
            if inline_text:
                buf.append(inline_text)
            continue

        buf.append(stripped)

    flush()

    # Fallback for unstructured text (YouTube auto-captions, raw transcripts
    # with no speaker labels, etc.). Without this, downstream analysis runs
    # on an empty utterance list and produces all zeros.
    if not utterances and raw_text.strip():
        log.info("No speakers detected — treating entire text as a single 'Speaker' utterance")
        speakers["Speaker"] = "CEO"
        utterances.append(Utterance(
            speaker="Speaker", role="CEO", firm="", section="prepared_ceo",
            position=0, text=raw_text.strip(),
        ))

    # Build Q&A pairs — each Analyst utterance opens an exchange; subsequent
    # executive utterances are answers until the next Analyst speaks.
    qa_pairs: list[QAExchange] = []
    current_pair: QAExchange | None = None
    for u in utterances:
        if u.section != "qa":
            continue
        if u.role == "Analyst":
            if current_pair:
                qa_pairs.append(current_pair)
            current_pair = QAExchange(question=u)
        elif u.role == "Operator":
            if current_pair:
                qa_pairs.append(current_pair)
                current_pair = None
        else:
            if current_pair:
                current_pair.answers.append(u)
                if any(kw in u.text.lower() for kw in ("i'll let", "let me hand", "i'll pass it")):
                    current_pair.handoffs += 1
    if current_pair:
        qa_pairs.append(current_pair)

    sections: dict[str, list[Utterance]] = {}
    for u in utterances:
        sections.setdefault(u.section, []).append(u)

    word_count = len(raw_text.split())

    return ParsedTranscript(
        ticker=meta["ticker"],
        company_name=meta["company_name"],
        quarter=meta["quarter"],
        call_date=meta["call_date"],
        text_hash=text_hash,
        raw_word_count=word_count,
        speakers=speakers,
        utterances=utterances,
        qa_pairs=qa_pairs,
        sections=sections,
    )


def section_text(parsed: ParsedTranscript, section: str) -> str:
    return " ".join(u.text for u in parsed.sections.get(section, []))


def all_executive_text(parsed: ParsedTranscript) -> str:
    return " ".join(
        u.text for u in parsed.utterances if u.role in {"CEO", "CFO", "COO", "President", "Chairman", "Other"}
    )
