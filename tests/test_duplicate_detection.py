"""Tests for duplicate comment detection in code reviews.

This module tests the duplicate detection logic to ensure:
1. Exact duplicates are detected
2. Near-duplicates (case, whitespace, punctuation variations) are detected
3. Overlapping line ranges are detected
4. Semantic similarity is handled appropriately
"""

import unittest
import importlib.util
from pathlib import Path

# Load duplicate_detector module directly (avoids package import issues)
_module_path = Path(__file__).parent.parent / "hodor" / "duplicate_detector.py"
_spec = importlib.util.spec_from_file_location("duplicate_detector", _module_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

# Import functions from the loaded module
normalize_for_comparison = _module.normalize_for_comparison
extract_title = _module.extract_title
similarity_score = _module.similarity_score
is_duplicate_finding = _module.is_duplicate_finding
deduplicate_findings = _module.deduplicate_findings
parse_existing_comments = _module.parse_existing_comments


class TestNormalizeForComparison(unittest.TestCase):
    """Tests for text normalization before duplicate comparison."""

    def test_normalize_strips_whitespace(self):
        """Whitespace should be normalized for comparison."""
        text1 = "  Fix the bug  "
        text2 = "Fix the bug"

        self.assertEqual(
            normalize_for_comparison(text1),
            normalize_for_comparison(text2)
        )

    def test_normalize_case_insensitive(self):
        """Comparison should be case-insensitive."""
        text1 = "Fix The Bug"
        text2 = "fix the bug"

        self.assertEqual(
            normalize_for_comparison(text1),
            normalize_for_comparison(text2)
        )

    def test_normalize_removes_markdown_bold(self):
        """Markdown bold formatting should be stripped."""
        text1 = "**[P1] Fix this**"
        text2 = "[P1] Fix this"

        self.assertEqual(
            normalize_for_comparison(text1),
            normalize_for_comparison(text2)
        )

    def test_normalize_removes_markdown_italic(self):
        """Markdown italic formatting should be stripped."""
        text1 = "*important* issue"
        text2 = "important issue"

        self.assertEqual(
            normalize_for_comparison(text1),
            normalize_for_comparison(text2)
        )

    def test_normalize_collapses_multiple_spaces(self):
        """Multiple spaces should be collapsed to single space."""
        text1 = "Fix   the    bug"
        text2 = "Fix the bug"

        self.assertEqual(
            normalize_for_comparison(text1),
            normalize_for_comparison(text2)
        )

    def test_normalize_handles_newlines(self):
        """Newlines should be normalized to spaces."""
        text1 = "Fix\nthe\nbug"
        text2 = "Fix the bug"

        self.assertEqual(
            normalize_for_comparison(text1),
            normalize_for_comparison(text2)
        )

    def test_normalize_strips_priority_prefix_variations(self):
        """Priority prefixes with different formats should normalize."""
        text1 = "[P1] Fix this"
        text2 = "[p1] fix this"

        norm1 = normalize_for_comparison(text1)
        norm2 = normalize_for_comparison(text2)

        self.assertEqual(norm1, norm2)


class TestExtractTitle(unittest.TestCase):
    """Tests for extracting title from comment body."""

    def test_extract_title_from_bold_header(self):
        """Extract title from **[P1] Title** format."""
        body = "**[P1] Fix null pointer**\n\nThis causes a crash when..."
        title = extract_title(body)

        self.assertEqual(title, "[P1] Fix null pointer")

    def test_extract_title_plain_text(self):
        """Extract title when no markdown formatting."""
        body = "[P2] Memory leak in handler\n\nThe connection is not closed..."
        title = extract_title(body)

        self.assertEqual(title, "[P2] Memory leak in handler")

    def test_extract_title_no_priority(self):
        """Extract title when no priority prefix."""
        body = "Missing error handling\n\nThe function does not..."
        title = extract_title(body)

        self.assertEqual(title, "Missing error handling")


class TestIsDuplicate(unittest.TestCase):
    """Tests for the main duplicate detection function."""

    def test_exact_duplicate_detected(self):
        """Exact same title, file, and line should be duplicate."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL injection risk**\n\nUse parameterized queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 42,
            "title": "[P1] SQL injection risk",
            "body": "Use parameterized queries."
        }

        self.assertTrue(is_duplicate_finding(new_finding, existing))

    def test_case_variation_detected_as_duplicate(self):
        """Same finding with different case should be duplicate."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL Injection Risk**\n\nUse parameterized queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 42,
            "title": "[P1] sql injection risk",  # Different case
            "body": "use parameterized queries."
        }

        self.assertTrue(is_duplicate_finding(new_finding, existing))

    def test_whitespace_variation_detected_as_duplicate(self):
        """Same finding with different whitespace should be duplicate."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL  injection   risk**\n\nUse queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 42,
            "title": "[P1] SQL injection risk",  # Normalized whitespace
            "body": "Use queries."
        }

        self.assertTrue(is_duplicate_finding(new_finding, existing))

    def test_different_file_not_duplicate(self):
        """Same title but different file should NOT be duplicate."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL injection risk**\n\nUse queries."}
        ]

        new_finding = {
            "path": "src/database.py",  # Different file
            "line": 42,
            "title": "[P1] SQL injection risk",
            "body": "Use queries."
        }

        self.assertFalse(is_duplicate_finding(new_finding, existing))

    def test_nearby_line_detected_as_duplicate(self):
        """Same title within 5 lines should be duplicate (fuzzy line matching)."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL injection risk**\n\nUse queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 45,  # Within 5 lines
            "title": "[P1] SQL injection risk",
            "body": "Use queries."
        }

        self.assertTrue(is_duplicate_finding(new_finding, existing))

    def test_far_line_not_duplicate(self):
        """Same title but far away line should NOT be duplicate."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL injection risk**\n\nUse queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 100,  # More than 5 lines away
            "title": "[P1] SQL injection risk",
            "body": "Use queries."
        }

        self.assertFalse(is_duplicate_finding(new_finding, existing))

    def test_similar_title_detected_as_duplicate(self):
        """Titles with minor wording differences should be duplicate (fuzzy match)."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL injection vulnerability**\n\nUse queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 42,
            "title": "[P1] SQL injection risk",  # Similar but not identical
            "body": "Use queries."
        }

        self.assertTrue(is_duplicate_finding(new_finding, existing))

    def test_completely_different_title_not_duplicate(self):
        """Completely different titles should NOT be duplicate."""
        existing = [
            {"path": "src/auth.py", "line": 42, "body": "**[P1] SQL injection risk**\n\nUse queries."}
        ]

        new_finding = {
            "path": "src/auth.py",
            "line": 42,
            "title": "[P2] Memory leak detected",  # Completely different
            "body": "Close the connection."
        }

        self.assertFalse(is_duplicate_finding(new_finding, existing))

    def test_empty_existing_not_duplicate(self):
        """No existing comments means nothing is duplicate."""
        existing = []

        new_finding = {
            "path": "src/auth.py",
            "line": 42,
            "title": "[P1] SQL injection risk",
            "body": "Use queries."
        }

        self.assertFalse(is_duplicate_finding(new_finding, existing))


class TestDeduplicateFindings(unittest.TestCase):
    """Tests for batch deduplication of findings."""

    def test_removes_duplicates_from_batch(self):
        """Multiple duplicates in same batch should be deduplicated."""
        findings = [
            {"path": "a.py", "line": 10, "title": "[P1] Bug A", "body": "Fix it"},
            {"path": "a.py", "line": 10, "title": "[P1] Bug A", "body": "Fix it"},  # Exact dup
            {"path": "a.py", "line": 11, "title": "[P1] bug a", "body": "fix it"},  # Near dup
            {"path": "b.py", "line": 20, "title": "[P2] Bug B", "body": "Another"},
        ]

        unique = deduplicate_findings(findings, existing=[])

        # Should keep first occurrence of Bug A and Bug B
        self.assertEqual(len(unique), 2)
        self.assertEqual(unique[0]["title"], "[P1] Bug A")
        self.assertEqual(unique[1]["title"], "[P2] Bug B")

    def test_removes_findings_matching_existing(self):
        """Findings matching existing comments should be removed."""
        existing = [
            {"path": "a.py", "line": 10, "body": "**[P1] Existing bug**\n\nAlready reported."}
        ]

        findings = [
            {"path": "a.py", "line": 10, "title": "[P1] Existing bug", "body": "Already reported."},
            {"path": "b.py", "line": 20, "title": "[P2] New bug", "body": "Fresh finding."},
        ]

        unique = deduplicate_findings(findings, existing=existing)

        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]["title"], "[P2] New bug")

    def test_preserves_order(self):
        """Original order should be preserved for unique findings."""
        findings = [
            {"path": "c.py", "line": 30, "title": "[P3] Bug C", "body": "Third"},
            {"path": "a.py", "line": 10, "title": "[P1] Bug A", "body": "First"},
            {"path": "b.py", "line": 20, "title": "[P2] Bug B", "body": "Second"},
        ]

        unique = deduplicate_findings(findings, existing=[])

        self.assertEqual(len(unique), 3)
        self.assertEqual(unique[0]["title"], "[P3] Bug C")
        self.assertEqual(unique[1]["title"], "[P1] Bug A")
        self.assertEqual(unique[2]["title"], "[P2] Bug B")


class TestSimilarityScore(unittest.TestCase):
    """Tests for text similarity scoring."""

    def test_identical_texts_score_100(self):
        """Identical texts should have 100% similarity."""
        text = "Fix the SQL injection bug"
        score = similarity_score(text, text)

        self.assertEqual(score, 100)

    def test_completely_different_texts_score_low(self):
        """Completely different texts should have low similarity."""
        text1 = "Fix the SQL injection bug"
        text2 = "Memory allocation failed"
        score = similarity_score(text1, text2)

        self.assertLess(score, 50)

    def test_similar_texts_score_high(self):
        """Similar texts should have high similarity."""
        text1 = "SQL injection vulnerability detected"
        text2 = "SQL injection risk detected"
        score = similarity_score(text1, text2)

        self.assertGreater(score, 70)

    def test_empty_text_handling(self):
        """Empty texts should not crash."""
        score1 = similarity_score("", "")
        score2 = similarity_score("text", "")
        score3 = similarity_score("", "text")

        self.assertEqual(score1, 100)  # Both empty = identical
        self.assertEqual(score2, 0)
        self.assertEqual(score3, 0)


class TestParseExistingComments(unittest.TestCase):
    """Tests for parsing existing comments from API responses."""

    def test_parse_gitlab_discussions(self):
        """Parse GitLab discussion format into normalized format."""
        discussions = [
            {
                "notes": [
                    {
                        "position": {"new_path": "src/auth.py", "new_line": 42},
                        "body": "**[P1] SQL injection**\n\nUse parameterized queries."
                    }
                ]
            },
            {
                "notes": [
                    {
                        "position": {"new_path": "src/db.py", "new_line": 100},
                        "body": "**[P2] Connection leak**\n\nClose connections."
                    }
                ]
            }
        ]

        parsed = parse_existing_comments(discussions, platform="gitlab")

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["path"], "src/auth.py")
        self.assertEqual(parsed[0]["line"], 42)
        self.assertIn("SQL injection", parsed[0]["body"])

    def test_parse_gitlab_discussion_without_position(self):
        """GitLab discussions without position should be included (general comments)."""
        discussions = [
            {
                "notes": [
                    {
                        "body": "General comment about the MR"
                        # No position - this is a general comment
                    }
                ]
            }
        ]

        parsed = parse_existing_comments(discussions, platform="gitlab")

        # General comments should be included for body matching
        self.assertEqual(len(parsed), 1)
        self.assertIsNone(parsed[0]["path"])
        self.assertIsNone(parsed[0]["line"])

    def test_parse_empty_discussions(self):
        """Empty discussions list should return empty result."""
        parsed = parse_existing_comments([], platform="gitlab")

        self.assertEqual(parsed, [])

    def test_parse_handles_none_body(self):
        """None body values should be handled gracefully."""
        discussions = [
            {
                "notes": [
                    {
                        "position": {"new_path": "file.py", "new_line": 10},
                        "body": None  # None body
                    }
                ]
            }
        ]

        parsed = parse_existing_comments(discussions, platform="gitlab")

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["body"], "")


if __name__ == "__main__":
    unittest.main()
