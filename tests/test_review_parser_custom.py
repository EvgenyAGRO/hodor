import json
from pathlib import Path
from hodor.review_parser import parse_review_output, ReviewFinding

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
