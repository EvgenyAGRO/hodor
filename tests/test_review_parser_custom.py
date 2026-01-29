import json
from pathlib import Path
from hodor.review_parser import parse_review_output, ReviewFinding, looks_like_valid_json_with_findings

def test_parse_simplified_schema_gitlab():
    json_output = """
    {
        "findings": [
            {
                "path": "hodor/gitlab.py",
                "line": 42,
                "body": "Fix this bug",
                "priority": 1
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    
    # Check simplified binding
    assert finding.code_location.absolute_file_path == Path("hodor/gitlab.py")
    assert finding.code_location.line_range.start == 42
    assert finding.code_location.line_range.end == 42
    assert finding.body == "Fix this bug"
    assert finding.priority == 1

def test_parse_mixed_schema():
    # Ensure old schema still works
    json_output = """
    {
        "findings": [
            {
                "title": "Title",
                "body": "Body",
                "confidence_score": 0.9,
                "code_location": {
                    "absolute_file_path": "/abs/path.py",
                    "line_range": {"start": 10, "end": 20}
                }
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].code_location.line_range.start == 10


# ============================================================================
# Tests for looks_like_valid_json_with_findings()
# ============================================================================


def test_looks_like_valid_json_with_findings_returns_true_for_valid():
    """Test that valid JSON with findings is detected."""
    text = '{"findings": [{"path": "file.py", "line": 1, "body": "Fix this"}]}'
    assert looks_like_valid_json_with_findings(text) is True


def test_looks_like_valid_json_with_findings_returns_false_for_empty_array():
    """Test that empty findings array returns False."""
    text = '{"findings": []}'
    assert looks_like_valid_json_with_findings(text) is False


def test_looks_like_valid_json_with_findings_returns_false_for_empty_array_no_space():
    """Test that empty findings array without space returns False."""
    text = '{"findings":[]}'
    assert looks_like_valid_json_with_findings(text) is False


def test_looks_like_valid_json_with_findings_returns_false_for_no_findings():
    """Test that text without findings key returns False."""
    text = '{"summary": "All good", "verdict": "pass"}'
    assert looks_like_valid_json_with_findings(text) is False


def test_looks_like_valid_json_with_findings_returns_false_for_empty_text():
    """Test that empty text returns False."""
    assert looks_like_valid_json_with_findings("") is False
    assert looks_like_valid_json_with_findings(None) is False


def test_looks_like_valid_json_with_findings_requires_path_and_body():
    """Test that both path and body are required."""
    # Has findings key but missing body
    text = '{"findings": [{"path": "file.py", "line": 1}]}'
    assert looks_like_valid_json_with_findings(text) is False

    # Has findings key but missing path
    text = '{"findings": [{"body": "Fix this", "line": 1}]}'
    assert looks_like_valid_json_with_findings(text) is False


def test_looks_like_valid_json_with_findings_detects_standard_schema():
    """Test that standard schema with absolute_file_path is detected."""
    text = '''{"findings": [{"title": "Bug", "body": "Fix", "code_location": {"absolute_file_path": "f.py"}}]}'''
    assert looks_like_valid_json_with_findings(text) is True


# ============================================================================
# Tests for robust parsing (malformed finding handling)
# ============================================================================


def test_parse_skips_malformed_finding_gracefully():
    """Test that malformed findings are skipped without breaking the parse."""
    json_output = """
    {
        "findings": [
            {
                "path": "good.py",
                "line": 1,
                "body": "Valid finding"
            },
            {
                "invalid": "missing required fields"
            },
            {
                "path": "also_good.py",
                "line": 2,
                "body": "Another valid finding"
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)

    # Should have 2 valid findings, the malformed one skipped
    assert len(parsed.findings) == 2
    assert parsed.findings[0].body == "Valid finding"
    assert parsed.findings[1].body == "Another valid finding"


def test_parse_handles_string_line_number():
    """Test that string line numbers are converted to int."""
    json_output = """
    {
        "findings": [
            {
                "path": "file.py",
                "line": "42",
                "body": "Finding with string line"
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].code_location.line_range.start == 42


def test_parse_handles_invalid_line_number():
    """Test that invalid line numbers default to 1."""
    json_output = """
    {
        "findings": [
            {
                "path": "file.py",
                "line": "not_a_number",
                "body": "Finding with invalid line"
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].code_location.line_range.start == 1


def test_parse_handles_missing_body():
    """Test that missing body defaults to empty string."""
    json_output = """
    {
        "findings": [
            {
                "path": "file.py",
                "line": 10
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].body == ""


def test_parse_handles_string_confidence_score():
    """Test that string confidence scores are converted to float."""
    json_output = """
    {
        "findings": [
            {
                "path": "file.py",
                "line": 1,
                "body": "Finding",
                "confidence_score": "0.75"
            }
        ]
    }
    """
    parsed = parse_review_output(json_output)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].confidence_score == 0.75
