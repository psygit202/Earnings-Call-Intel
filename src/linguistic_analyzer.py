"""Deterministic linguistic analysis: hedging, defensive phrases, certainty,
evasion, and pronoun ratios. All counters are normalized per 1000 words so
calls of different lengths can be compared apples-to-apples.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .parse_transcript import ParsedTranscript, QAExchange, Utterance

log = logging.getLogger(__name__)

MARKERS_PATH = Path(__file__).resolve().parent.parent / "config" / "linguistic_markers.yaml"


# ---------------------------------------------------------------------------
# Schemas (pydantic)
# ---------------------------------------------------------------------------
class CategoryMetric(BaseModel):
    raw_count: int
    per_1000_words: float
    top_markers: dict[str, int] = Field(default_factory=dict)


class HedgingMetrics(BaseModel):
    epistemic: CategoryMetric
    probability: CategoryMetric
    approximation: CategoryMetric
    conditional: CategoryMetric
    total_per_1000: float


class DefensiveMetrics(BaseModel):
    reframing: CategoryMetric
    attention_grabbing: CategoryMetric
    compliments: CategoryMetric
    non_answers: CategoryMetric
    total_per_1000: float


class PronounMetrics(BaseModel):
    we_count: int
    i_count: int
    they_count: int
    we_to_i_ratio: float
    deflection_ratio: float    # third-person / first-person


class QAEvasionMetrics(BaseModel):
    total_exchanges: int
    flagged_evasive: int
    evasion_rate: float
    handoff_count: int
    evasive_excerpts: list[dict[str, Any]] = Field(default_factory=list)


class LinguisticReport(BaseModel):
    word_count: int
    qa_word_count: int
    prepared_word_count: int
    hedging: HedgingMetrics
    defensive: DefensiveMetrics
    certainty: CategoryMetric
    pronouns: PronounMetrics
    qa_evasion: QAEvasionMetrics
    by_speaker: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Marker loading & matching
# ---------------------------------------------------------------------------
def load_markers(path: Path = MARKERS_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _compile_phrase(phrase: str) -> re.Pattern:
    """Compile a phrase as a word-bounded, case-insensitive regex."""
    escaped = re.escape(phrase.lower())
    # Allow flexible whitespace between words
    escaped = escaped.replace(r"\ ", r"\s+")
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


_COMPILED_CACHE: dict[str, re.Pattern] = {}


def _patt(phrase: str) -> re.Pattern:
    if phrase not in _COMPILED_CACHE:
        _COMPILED_CACHE[phrase] = _compile_phrase(phrase)
    return _COMPILED_CACHE[phrase]


def count_markers(text: str, markers: list[str]) -> tuple[int, Counter]:
    """Return (total, per-marker breakdown) for the given list of markers."""
    breakdown: Counter = Counter()
    total = 0
    if not text:
        return 0, breakdown
    for m in markers:
        hits = len(_patt(m).findall(text))
        if hits:
            breakdown[m] = hits
            total += hits
    return total, breakdown


def _build_metric(text: str, markers: list[str], total_words: int) -> CategoryMetric:
    count, breakdown = count_markers(text, markers)
    per_1000 = (count / total_words * 1000) if total_words else 0.0
    top = dict(breakdown.most_common(10))
    return CategoryMetric(raw_count=count, per_1000_words=round(per_1000, 2), top_markers=top)


# ---------------------------------------------------------------------------
# Q&A evasion: keyword-overlap distance between question and answer
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "for", "with", "as", "at", "by", "from", "that", "this", "it",
    "i", "we", "you", "they", "he", "she", "do", "does", "did", "have", "has", "had",
    "what", "how", "when", "where", "why", "which", "who", "can", "could", "would", "should",
    "will", "may", "might", "about", "so", "if", "than", "then", "there", "here", "just",
    "kind", "sort", "thing", "things", "really", "very", "much", "more", "less", "some",
    "any", "all", "your", "our", "my", "his", "her", "their", "no", "not", "yes", "well",
    "going", "go", "get", "got", "okay", "ok", "right", "good", "great", "thanks", "thank",
    "you", "question", "quarter", "year", "guidance",
}
_WORD_RE = re.compile(r"\b[a-z]{4,}\b")


def _keywords(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _evasion_score(exchange: QAExchange) -> float:
    """0.0 = perfectly on-topic, 1.0 = total non-sequitur."""
    q_kw = _keywords(exchange.question.text)
    if not q_kw:
        return 0.0
    a_text = " ".join(a.text for a in exchange.answers)
    a_kw = _keywords(a_text)
    if not a_kw:
        return 1.0
    overlap = q_kw & a_kw
    return 1.0 - (len(overlap) / max(len(q_kw), 1))


def analyze_qa_evasion(qa_pairs: list[QAExchange], threshold: float = 0.75) -> QAEvasionMetrics:
    if not qa_pairs:
        return QAEvasionMetrics(total_exchanges=0, flagged_evasive=0, evasion_rate=0.0, handoff_count=0)
    flagged: list[dict] = []
    handoffs = 0
    for pair in qa_pairs:
        score = _evasion_score(pair)
        handoffs += pair.handoffs
        if score >= threshold and pair.answers:
            flagged.append({
                "analyst": pair.question.speaker,
                "firm": pair.question.firm,
                "question_excerpt": pair.question.text[:300],
                "answer_excerpt": " ".join(a.text for a in pair.answers)[:300],
                "evasion_score": round(score, 2),
            })
    return QAEvasionMetrics(
        total_exchanges=len(qa_pairs),
        flagged_evasive=len(flagged),
        evasion_rate=round(len(flagged) / len(qa_pairs), 3),
        handoff_count=handoffs,
        evasive_excerpts=flagged[:5],
    )


# ---------------------------------------------------------------------------
# Pronoun analysis
# ---------------------------------------------------------------------------
def analyze_pronouns(text: str, markers: dict) -> PronounMetrics:
    pn = markers["pronouns"]
    we, _ = count_markers(text, pn["first_person_plural"])
    i, _ = count_markers(text, pn["first_person_singular"])
    they, _ = count_markers(text, pn["third_person"])
    we_to_i = (we / i) if i else float(we)
    deflection = (they / (we + i)) if (we + i) else 0.0
    return PronounMetrics(
        we_count=we, i_count=i, they_count=they,
        we_to_i_ratio=round(we_to_i, 2),
        deflection_ratio=round(deflection, 3),
    )


# ---------------------------------------------------------------------------
# Top-level analyze()
# ---------------------------------------------------------------------------
def analyze(parsed: ParsedTranscript, markers: dict | None = None) -> LinguisticReport:
    markers = markers or load_markers()

    # Scope analysis to executive speech — exclude operator boilerplate and
    # analyst questions when computing executive linguistic metrics.
    exec_utts = [u for u in parsed.utterances if u.role not in ("Operator", "Analyst")]
    exec_text = " ".join(u.text for u in exec_utts)
    qa_exec_text = " ".join(u.text for u in exec_utts if u.section == "qa")
    prepared_text = " ".join(u.text for u in exec_utts if u.section.startswith("prepared"))

    total_words = max(len(exec_text.split()), 1)
    qa_words = len(qa_exec_text.split())
    prep_words = len(prepared_text.split())

    h = markers["hedging"]
    hedging = HedgingMetrics(
        epistemic=_build_metric(exec_text, h["epistemic"], total_words),
        probability=_build_metric(exec_text, h["probability"], total_words),
        approximation=_build_metric(exec_text, h["approximation"], total_words),
        conditional=_build_metric(exec_text, h["conditional"], total_words),
        total_per_1000=0.0,
    )
    hedging.total_per_1000 = round(
        hedging.epistemic.per_1000_words + hedging.probability.per_1000_words
        + hedging.approximation.per_1000_words + hedging.conditional.per_1000_words,
        2,
    )

    d = markers["defensive_phrases"]
    defensive = DefensiveMetrics(
        reframing=_build_metric(exec_text, d["reframing"], total_words),
        attention_grabbing=_build_metric(exec_text, d["attention_grabbing"], total_words),
        compliments=_build_metric(exec_text, d["compliments"], total_words),
        non_answers=_build_metric(exec_text, d["non_answers"], total_words),
        total_per_1000=0.0,
    )
    defensive.total_per_1000 = round(
        defensive.reframing.per_1000_words + defensive.attention_grabbing.per_1000_words
        + defensive.compliments.per_1000_words + defensive.non_answers.per_1000_words,
        2,
    )

    cert_markers = markers["certainty"]
    cert_all = (
        cert_markers["absolutes"] + cert_markers["commitment"] + cert_markers["factual"]
    )
    certainty = _build_metric(exec_text, cert_all, total_words)

    pronouns = analyze_pronouns(exec_text, markers)
    qa_evasion = analyze_qa_evasion(parsed.qa_pairs)

    # Per-speaker hedging density — useful for spotting which exec is hedging more
    by_speaker: dict[str, dict[str, float]] = {}
    for name, role in parsed.speakers.items():
        if role in ("Analyst", "Operator"):
            continue
        spk_text = " ".join(u.text for u in exec_utts if u.speaker == name)
        spk_words = max(len(spk_text.split()), 1)
        if spk_words < 50:
            continue
        h_count, _ = count_markers(spk_text, cert_all)
        hedge_count, _ = count_markers(
            spk_text, h["epistemic"] + h["probability"] + h["approximation"] + h["conditional"],
        )
        by_speaker[name] = {
            "role": role,
            "word_count": spk_words,
            "hedging_per_1000": round(hedge_count / spk_words * 1000, 2),
            "certainty_per_1000": round(h_count / spk_words * 1000, 2),
        }

    return LinguisticReport(
        word_count=total_words,
        qa_word_count=qa_words,
        prepared_word_count=prep_words,
        hedging=hedging,
        defensive=defensive,
        certainty=certainty,
        pronouns=pronouns,
        qa_evasion=qa_evasion,
        by_speaker=by_speaker,
    )


# ---------------------------------------------------------------------------
# Composite defensiveness score (0-100)
# ---------------------------------------------------------------------------
def defensiveness_score(report: LinguisticReport) -> float:
    """Weighted composite:
        30% hedging density, 25% defensive phrases, 20% evasion rate,
        15% certainty decline, 10% pronoun deflection.

    Each component is squashed into 0-100 with empirically reasonable scaling.
    """
    # Hedging: typical exec calls land 20-80 per 1000 words. Map 0..100 → 0..100.
    hedging_component = min(report.hedging.total_per_1000, 100)
    # Defensive phrases: 0-20 per 1000 is the normal range; scale x5.
    defensive_component = min(report.defensive.total_per_1000 * 5, 100)
    # Evasion rate is already 0-1.
    evasion_component = report.qa_evasion.evasion_rate * 100
    # Certainty: invert. Typical 10-40 per 1000. Lower certainty = more defensive.
    certainty_inverse = max(0, 50 - report.certainty.per_1000_words) * 2
    certainty_component = min(certainty_inverse, 100)
    # Pronoun deflection: 0..1 range, scale x100.
    pronoun_component = min(report.pronouns.deflection_ratio * 100, 100)

    score = (
        0.30 * hedging_component
        + 0.25 * defensive_component
        + 0.20 * evasion_component
        + 0.15 * certainty_component
        + 0.10 * pronoun_component
    )
    return round(score, 1)
