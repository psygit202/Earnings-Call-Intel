"""Claude-powered deep analysis layer.

Optional: if ANTHROPIC_API_KEY is unset, every public function returns None
and the pipeline keeps working with linguistic-only output. When the key IS
present, responses are cached on disk by (transcript_hash, prompt_id) so
re-runs cost nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")

Tone = Literal["Confident", "Cautious", "Defensive", "Evasive", "Combative"]


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------
class SectionTone(BaseModel):
    section: str
    tone: Tone
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str
    rationale: str


class ToneClassification(BaseModel):
    sections: list[SectionTone]
    overall_tone: Tone
    overall_rationale: str


class AvoidedTopic(BaseModel):
    topic: str
    times_probed: int
    exchange_quotes: list[str]
    avoidance_evidence: str


class TopicAvoidance(BaseModel):
    avoided_topics: list[AvoidedTopic]
    summary: str


class NarrativeShift(BaseModel):
    segment: str
    prior_framing: str
    current_framing: str
    shift_direction: Literal["more positive", "more negative", "neutralized", "no change"]
    significance: Literal["low", "medium", "high"]


class NarrativeShiftReport(BaseModel):
    shifts: list[NarrativeShift]
    overall_summary: str


class ForwardGuidanceAssessment(BaseModel):
    certainty_score: int = Field(ge=1, le=10)
    hedging_examples: list[str]
    key_caveats: list[str]
    rationale: str


class QoQNarrative(BaseModel):
    narrative: str = Field(max_length=1500)
    key_change: str
    direction: Literal["more defensive", "more confident", "mixed", "stable"]


# ---------------------------------------------------------------------------
# Client + cache
# ---------------------------------------------------------------------------
def _client():
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic
        return Anthropic()
    except ImportError:
        log.warning("anthropic SDK not installed; skipping AI analysis")
        return None


def _cache_path(transcript_hash: str, prompt_id: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{transcript_hash}_{prompt_id}.json"


def _load_cache(transcript_hash: str, prompt_id: str) -> dict | None:
    p = _cache_path(transcript_hash, prompt_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cache(transcript_hash: str, prompt_id: str, payload: dict) -> None:
    _cache_path(transcript_hash, prompt_id).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Core LLM call
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior equity-research analyst with 20 years of
experience reading earnings calls. You analyze management language for tone,
evasion, hedging, and narrative shifts.

You must ground every claim in direct quotations from the transcript. If the
transcript does not contain enough evidence to answer a question, you must
say so explicitly and return a structured "insufficient_evidence" response
rather than speculating.

You output strict JSON conforming to the schema provided in the user message.
No prose outside the JSON. No markdown fences."""


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(Exception),
)
def _call_claude(prompt: str, max_tokens: int = 2000) -> str:
    client = _client()
    if not client:
        raise RuntimeError("No Anthropic client available")
    msg = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if hasattr(block, "text"))


def _parse_json_response(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip().rstrip("`").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("Failed to parse Claude JSON response: %s", e)
        return None


def _structured(
    transcript_hash: str,
    prompt_id: str,
    prompt: str,
    model_cls: type[BaseModel],
    max_tokens: int = 2000,
) -> BaseModel | None:
    """Run a single Claude prompt, validate, and cache. Returns None on any
    failure (missing key, parse error, validation error)."""
    cached = _load_cache(transcript_hash, prompt_id)
    if cached is not None:
        try:
            return model_cls.model_validate(cached)
        except ValidationError:
            log.warning("Cached %s for %s failed validation; refetching", prompt_id, transcript_hash)

    if not os.getenv("ANTHROPIC_API_KEY"):
        return None

    try:
        raw = _call_claude(prompt, max_tokens=max_tokens)
    except Exception as e:
        log.error("Claude call failed for %s: %s", prompt_id, e)
        return None

    parsed = _parse_json_response(raw)
    if parsed is None:
        return None
    try:
        validated = model_cls.model_validate(parsed)
    except ValidationError as e:
        log.warning("Validation failed for %s: %s", prompt_id, e)
        return None
    _save_cache(transcript_hash, prompt_id, parsed)
    return validated


# ---------------------------------------------------------------------------
# Public analyzers
# ---------------------------------------------------------------------------
def classify_tones(transcript_hash: str, sections: dict[str, str]) -> ToneClassification | None:
    section_block = "\n\n".join(
        f"=== SECTION: {name} ===\n{text[:6000]}" for name, text in sections.items() if text
    )
    prompt = f"""ROLE: Senior equity-research analyst.

TASK: Classify the tone of each transcript section below.

ALLOWED TONES: Confident, Cautious, Defensive, Evasive, Combative.

EVIDENCE REQUIREMENT: For each section you classify, you MUST quote a
specific 5-25 word passage from THAT section as evidence. If a section is
too short to classify (under 50 words), use "Cautious" with low confidence
and note the brevity in the rationale.

OUTPUT JSON SCHEMA:
{{
  "sections": [
    {{
      "section": "<section_name>",
      "tone": "<Confident|Cautious|Defensive|Evasive|Combative>",
      "confidence": 0.0 to 1.0,
      "evidence_quote": "<direct quote, 5-25 words>",
      "rationale": "<one sentence>"
    }}
  ],
  "overall_tone": "<one of the five>",
  "overall_rationale": "<one paragraph>"
}}

REFUSAL: If a section is missing or empty, omit it from the sections array.

TRANSCRIPT SECTIONS:
{section_block}
"""
    return _structured(transcript_hash, "tone", prompt, ToneClassification, max_tokens=2500)


def detect_topic_avoidance(transcript_hash: str, qa_text: str) -> TopicAvoidance | None:
    prompt = f"""ROLE: Senior equity-research analyst.

TASK: From the Q&A excerpts below, identify topics that analysts probed
multiple times but management did not address directly. Look for repeated
questions on the same subject where management pivoted, deferred, or gave
non-answers.

EVIDENCE REQUIREMENT: For each avoided topic, list 1-3 short verbatim
exchange snippets showing the avoidance pattern. Do not invent quotes.

OUTPUT JSON SCHEMA:
{{
  "avoided_topics": [
    {{
      "topic": "<short topic name>",
      "times_probed": <int>,
      "exchange_quotes": ["<verbatim snippet>", ...],
      "avoidance_evidence": "<one sentence on what made the answers evasive>"
    }}
  ],
  "summary": "<one paragraph synthesizing the pattern, or 'No clear avoidance patterns detected.'>"
}}

REFUSAL: If the Q&A is too short (under 500 words) or contains no clear
avoidance patterns, return an empty avoided_topics list and say so in
summary.

Q&A TRANSCRIPT:
{qa_text[:18000]}
"""
    return _structured(transcript_hash, "avoidance", prompt, TopicAvoidance, max_tokens=2500)


def detect_narrative_shifts(
    transcript_hash: str,
    current_prepared: str,
    prior_prepared: str,
    prior_quarter: str,
) -> NarrativeShiftReport | None:
    prompt = f"""ROLE: Senior equity-research analyst.

TASK: Compare how management framed the business in this quarter's prepared
remarks vs the prior quarter's prepared remarks. Identify business segments
or themes where the FRAMING changed — not just the numbers.

EVIDENCE REQUIREMENT: For each shift, quote or paraphrase the prior framing
and the current framing in 1-2 short sentences each. Be specific about
which business segment or theme.

OUTPUT JSON SCHEMA:
{{
  "shifts": [
    {{
      "segment": "<segment or theme>",
      "prior_framing": "<short>",
      "current_framing": "<short>",
      "shift_direction": "<more positive|more negative|neutralized|no change>",
      "significance": "<low|medium|high>"
    }}
  ],
  "overall_summary": "<one paragraph>"
}}

REFUSAL: If the prior-quarter remarks are missing or too brief to compare,
return an empty shifts list and explain in overall_summary.

PRIOR QUARTER ({prior_quarter}):
{prior_prepared[:10000]}

CURRENT QUARTER PREPARED REMARKS:
{current_prepared[:10000]}
"""
    return _structured(transcript_hash, "shifts", prompt, NarrativeShiftReport, max_tokens=2500)


def assess_forward_guidance(transcript_hash: str, exec_text: str) -> ForwardGuidanceAssessment | None:
    prompt = f"""ROLE: Senior equity-research analyst.

TASK: Rate management's certainty about FORWARD guidance (next quarter,
next year, multi-year targets) on a 1-10 scale where:
  1 = pervasive hedging, no firm numbers
 10 = explicit numerical targets stated with conviction

EVIDENCE REQUIREMENT: Cite 3-6 verbatim hedging phrases from the transcript
that informed your score (e.g., "could potentially", "if conditions hold").
Also list the most material caveats management attached to guidance.

OUTPUT JSON SCHEMA:
{{
  "certainty_score": <int 1-10>,
  "hedging_examples": ["<verbatim phrase>", ...],
  "key_caveats": ["<short paraphrase>", ...],
  "rationale": "<one paragraph>"
}}

REFUSAL: If the transcript contains no forward-looking guidance at all,
return certainty_score 1 and explain in rationale.

TRANSCRIPT (executive speech only):
{exec_text[:15000]}
"""
    return _structured(transcript_hash, "guidance", prompt, ForwardGuidanceAssessment, max_tokens=2000)


def write_qoq_narrative(
    transcript_hash: str,
    ticker: str,
    deltas: dict[str, Any],
    current_summary: str,
    prior_summary: str,
) -> QoQNarrative | None:
    prompt = f"""ROLE: Senior equity-research analyst writing for an institutional readership.

TASK: Write a 200-word narrative summarizing how management's TONE changed
quarter-over-quarter for {ticker}. Focus on language patterns, not numbers.

INPUTS:
- Quarter-over-quarter linguistic metric deltas (positive = increase):
{json.dumps(deltas, indent=2)[:3000]}

- Current quarter brief summary:
{current_summary[:2000]}

- Prior quarter brief summary:
{prior_summary[:2000]}

OUTPUT JSON SCHEMA:
{{
  "narrative": "<~200 word paragraph in a serious financial-publication voice>",
  "key_change": "<one sentence summarizing the single most important shift>",
  "direction": "<more defensive|more confident|mixed|stable>"
}}

REFUSAL: If deltas are all near zero (no shift), return direction='stable'
and write a one-sentence narrative noting the absence of meaningful change.

VOICE: Grant's Interest Rate Observer. Sober, evidence-led, no fluff."""
    return _structured(transcript_hash, "qoq_narrative", prompt, QoQNarrative, max_tokens=1500)


def is_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))
