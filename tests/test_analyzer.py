"""Tests for the deterministic linguistic analyzer.

Run with: python -m pytest tests/ -v
Or:        python tests/test_analyzer.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import linguistic_analyzer, parse_transcript


FIXTURES = ROOT / "tests" / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class MarkerMatchingTests(unittest.TestCase):
    def test_load_markers_has_all_categories(self):
        markers = linguistic_analyzer.load_markers()
        self.assertIn("hedging", markers)
        self.assertIn("defensive_phrases", markers)
        self.assertIn("certainty", markers)
        self.assertIn("pronouns", markers)
        for cat in ("epistemic", "probability", "approximation", "conditional"):
            self.assertIn(cat, markers["hedging"])

    def test_count_markers_basic(self):
        text = "We believe we might possibly see some improvement. We believe that."
        markers = ["believe", "might", "possibly"]
        count, breakdown = linguistic_analyzer.count_markers(text, markers)
        self.assertEqual(count, 4)
        self.assertEqual(breakdown["believe"], 2)
        self.assertEqual(breakdown["might"], 1)
        self.assertEqual(breakdown["possibly"], 1)

    def test_count_markers_case_insensitive(self):
        text = "Believe BELIEVE believe"
        count, _ = linguistic_analyzer.count_markers(text, ["believe"])
        self.assertEqual(count, 3)

    def test_count_markers_word_boundary(self):
        # "if" should not match inside "wife"
        text = "My wife says if we go."
        count, _ = linguistic_analyzer.count_markers(text, ["if"])
        self.assertEqual(count, 1)

    def test_count_multi_word_phrase(self):
        text = "That's a great question, really. Great question. that is a great question."
        count, _ = linguistic_analyzer.count_markers(text, ["that's a great question", "great question"])
        # word-boundary version of "that's a great question" matches the
        # contracted form once and "great question" twice (once standalone,
        # once inside the contracted match) — both are valid counts.
        self.assertGreaterEqual(count, 3)


class PronounAnalysisTests(unittest.TestCase):
    def test_pronoun_ratios(self):
        text = "I think we are doing well. I am proud. We are confident. We will deliver."
        markers = linguistic_analyzer.load_markers()
        pn = linguistic_analyzer.analyze_pronouns(text, markers)
        self.assertGreater(pn.we_count, 0)
        self.assertGreater(pn.i_count, 0)
        self.assertAlmostEqual(pn.we_to_i_ratio, pn.we_count / pn.i_count, places=2)

    def test_pronouns_no_singular(self):
        text = "We are confident. We will deliver. We have the team."
        markers = linguistic_analyzer.load_markers()
        pn = linguistic_analyzer.analyze_pronouns(text, markers)
        self.assertEqual(pn.i_count, 0)
        # Falls back to raw we_count, not div-by-zero
        self.assertEqual(pn.we_to_i_ratio, float(pn.we_count))


class QAEvasionTests(unittest.TestCase):
    def test_evasion_score_full_overlap(self):
        text = """Operator: First question.

Jennifer Walsh - Morgan Stanley: Can you talk about cloud platform revenue growth?

David Chen - CEO: Cloud platform revenue growth was strong this quarter, up 47 percent.
"""
        parsed = parse_transcript.parse(text)
        report = linguistic_analyzer.analyze_qa_evasion(parsed.qa_pairs)
        self.assertEqual(report.total_exchanges, 1)
        self.assertEqual(report.flagged_evasive, 0)

    def test_evasion_score_no_overlap(self):
        text = """Operator: First question.

Jennifer Walsh - Morgan Stanley: Can you talk about gross margin pressure from chip supply constraints?

David Chen - CEO: We are excited about our partnership announcements next month and the new office opening in Singapore.
"""
        parsed = parse_transcript.parse(text)
        report = linguistic_analyzer.analyze_qa_evasion(parsed.qa_pairs)
        self.assertEqual(report.total_exchanges, 1)
        self.assertEqual(report.flagged_evasive, 1)


class EndToEndFixtureTests(unittest.TestCase):
    """Run the analyzer against both fixtures and check that Q1-2026
    (defensive transcript) is materially more defensive than Q4-2025."""

    def setUp(self) -> None:
        self.q4 = parse_transcript.parse(_load("sample_acme_q4_2025.txt"))
        self.q1 = parse_transcript.parse(_load("sample_acme_q1_2026.txt"))
        self.r_q4 = linguistic_analyzer.analyze(self.q4)
        self.r_q1 = linguistic_analyzer.analyze(self.q1)

    def test_parser_detected_metadata(self):
        self.assertEqual(self.q4.ticker, "ACME")
        self.assertEqual(self.q1.ticker, "ACME")
        self.assertTrue(self.q4.quarter.startswith("Q4"))
        self.assertTrue(self.q1.quarter.startswith("Q1"))

    def test_parser_found_speakers(self):
        for parsed in (self.q4, self.q1):
            roles = set(parsed.speakers.values())
            self.assertIn("CEO", roles)
            self.assertIn("CFO", roles)
            self.assertIn("Analyst", roles)

    def test_parser_built_qa_pairs(self):
        self.assertGreaterEqual(len(self.q4.qa_pairs), 3)
        self.assertGreaterEqual(len(self.q1.qa_pairs), 3)

    def test_q1_more_defensive_than_q4(self):
        score_q4 = linguistic_analyzer.defensiveness_score(self.r_q4)
        score_q1 = linguistic_analyzer.defensiveness_score(self.r_q1)
        self.assertGreater(
            score_q1, score_q4,
            f"Q1 ({score_q1}) should be more defensive than Q4 ({score_q4})",
        )

    def test_q1_has_more_hedging(self):
        self.assertGreater(
            self.r_q1.hedging.total_per_1000,
            self.r_q4.hedging.total_per_1000,
        )

    def test_q1_has_more_defensive_phrases(self):
        self.assertGreater(
            self.r_q1.defensive.total_per_1000,
            self.r_q4.defensive.total_per_1000,
        )

    def test_q4_has_more_certainty(self):
        self.assertGreater(
            self.r_q4.certainty.per_1000_words,
            self.r_q1.certainty.per_1000_words,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
