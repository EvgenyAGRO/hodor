import sys
from unittest.mock import MagicMock

# Mock openhands dependencies BEFORE importing hodor.agent
mock_openhands = MagicMock()
mock_sdk = MagicMock()
mock_event = MagicMock()
mock_action = MagicMock()
mock_conversation = MagicMock()
mock_workspace = MagicMock()

sys.modules["openhands"] = mock_openhands
sys.modules["openhands.sdk"] = mock_sdk
sys.modules["openhands.sdk.event"] = mock_event
sys.modules["openhands.sdk.action"] = mock_action
sys.modules["openhands.sdk.conversation"] = mock_conversation
sys.modules["openhands.sdk.workspace"] = mock_workspace

# Define the Event and other classes in the mocks so isinstance works
class MockEventBase:
    pass

mock_event.Event = MockEventBase

import pytest
from unittest.mock import patch

# Now we can import hodor.agent
import hodor.agent
from hodor.agent import _recover_last_json_response, _post_gitlab_inline_review

class MockMessageAction:
    def __init__(self, content):
        self.content = content
        self.__class__.__name__ = "MessageAction"

def test_recover_last_json_response_finds_json():
    # Setup mocks
    action1 = MockMessageAction("Thinking...")
    event1 = MockEventBase()
    event1.action = action1
    
    action2 = MockMessageAction('Here is the review:\n```json\n{"findings": [{"title": "Bug"}]}\n```')
    event2 = MockEventBase()
    event2.action = action2
    
    events = [event1, event2]
    
    recovered = _recover_last_json_response(events)
    
    assert recovered is not None
    assert '{"findings":' in recovered

def test_recover_last_json_response_finds_inline_json():
    action = MockMessageAction('{"findings": [{"title": "Bug"}]}')
    event = MockEventBase()
    event.action = action
    
    events = [event]
    
    recovered = _recover_last_json_response(events)
    
    assert recovered is not None
    assert '{"findings":' in recovered

def test_recover_last_json_response_returns_none_if_no_json():
    action = MockMessageAction("Checking code...")
    event = MockEventBase()
    event.action = action
    
    events = [event]
    
    recovered = _recover_last_json_response(events)
    
    assert recovered is None

@patch("hodor.agent.post_gitlab_mr_comment")
@patch("hodor.agent.parse_review_output")
@patch("hodor.agent.get_latest_mr_diff_refs") 
def test_post_gitlab_inline_review_handles_valid_empty_findings(mock_diff, mock_parse, mock_post):
    # Setup
    review_output = '{"findings": []}'
    mock_parse.return_value.findings = []
    mock_parse.return_value.overall_explanation = ""
    
    result = _post_gitlab_inline_review("owner", "repo", 1, review_output, host="gitlab.com")
    
    assert result is True
    mock_post.assert_called_once()
    assert "No issues found" in mock_post.call_args[0][3]

@patch("hodor.agent.post_gitlab_mr_comment")
@patch("hodor.agent.parse_review_output")
@patch("hodor.agent.get_latest_mr_diff_refs")
def test_post_gitlab_inline_review_returns_false_for_invalid_empty(mock_diff, mock_parse, mock_post):
    # Setup - empty result but NOT looking like JSON findings
    review_output = 'Some garbage output'
    mock_parse.return_value.findings = []
    mock_parse.return_value.overall_explanation = ""

    result = _post_gitlab_inline_review("owner", "repo", 1, review_output, host="gitlab.com")

    assert result is False
    mock_post.assert_not_called()


# Additional tests for fallback review generation and fail-soft mode

from hodor.agent import _generate_fallback_review
from hodor.diff_utils import FileDiffStats
import json


def test_generate_fallback_review_json_format():
    """Test fallback review is valid JSON when format is 'json'."""
    diff_stats = [
        FileDiffStats("file1.py", 10, 5, 500),
        FileDiffStats("large_wordlist.txt", 50000, 0, 1000000, is_large=True, is_trimmed=True),
    ]

    result = _generate_fallback_review(diff_stats, "json", "Test error")

    # Must be valid JSON
    parsed = json.loads(result)

    assert "summary" in parsed
    assert "findings" in parsed
    assert "overall_correctness" in parsed
    assert parsed["overall_correctness"] == "patch is correct"  # Fail-soft assumes correct
    assert "Test error" in parsed["summary"]


def test_generate_fallback_review_markdown_format():
    """Test fallback review in markdown format."""
    diff_stats = [
        FileDiffStats("file1.py", 10, 5, 500),
    ]

    result = _generate_fallback_review(diff_stats, "markdown", "Agent stuck")

    assert "### Issues Found" in result
    assert "### Summary" in result
    assert "Agent stuck" in result
    assert "file1.py" in result


def test_generate_fallback_review_with_large_files():
    """Test fallback review correctly identifies large files."""
    diff_stats = [
        FileDiffStats("small.py", 10, 5, 500),
        FileDiffStats("wordlist.txt", 50000, 0, 1000000),
        FileDiffStats("another_large.json", 2000, 500, 300000),
    ]

    result = _generate_fallback_review(diff_stats, "json", "Timeout")
    parsed = json.loads(result)

    # Should have a finding about large files
    assert len(parsed["findings"]) >= 1
    large_file_finding = next((f for f in parsed["findings"] if "Large" in f["title"]), None)
    assert large_file_finding is not None
    assert "wordlist.txt" in large_file_finding["body"] or "another_large.json" in large_file_finding["body"]


def test_generate_fallback_review_empty_diff_stats():
    """Test fallback review when no diff stats available."""
    diff_stats = []

    result = _generate_fallback_review(diff_stats, "json", "No workspace")
    parsed = json.loads(result)

    assert "summary" in parsed
    assert "findings" in parsed
    assert parsed["overall_correctness"] == "patch is correct"


def test_generate_fallback_review_includes_trimmed_file_info():
    """Test that fallback review indicates which files were trimmed."""
    diff_stats = [
        FileDiffStats("huge.txt", 5000, 0, 500000, is_large=True, is_trimmed=True),
        FileDiffStats("normal.py", 50, 10, 2000),
    ]

    result = _generate_fallback_review(diff_stats, "json", "LLM produced empty response")
    parsed = json.loads(result)

    assert "findings" in parsed
    # The fallback should mention the issue
    assert any("huge.txt" in str(f) or "Large" in str(f.get("title", "")) for f in parsed["findings"])


def test_fallback_review_valid_for_posting():
    """Test that fallback review can be parsed by review_parser."""
    from hodor.review_parser import parse_review_output

    diff_stats = [
        FileDiffStats("file.py", 100, 50, 5000),
    ]

    result = _generate_fallback_review(diff_stats, "json", "Stuck pattern detected")

    # Should be parseable
    parsed = parse_review_output(result)

    assert parsed is not None
    assert parsed.overall_correctness is not None


# Tests for fail-soft behavior (exit code 0)

@patch("hodor.diff_utils.get_diff_stats")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_review_pr_failsoft_returns_fallback_on_error(
    mock_cleanup,
    mock_build_prompt,
    mock_setup,
    mock_create_agent,
    mock_conversation_class,
    mock_get_diff_stats
):
    """Test that review_pr returns fallback review instead of raising when fail_on_error=False."""
    from hodor.agent import review_pr
    from pathlib import Path

    # Setup mocks
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    # Make the conversation fail
    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.run.side_effect = RuntimeError("Agent did not produce any review content")
    mock_conversation_class.return_value = mock_conversation

    # Provide diff stats for fallback
    mock_get_diff_stats.return_value = [
        MagicMock(path="file.py", added=10, deleted=5)
    ]

    # Should NOT raise, should return fallback
    result = review_pr(
        "https://gitlab.com/owner/repo/-/merge_requests/1",
        fail_on_error=False,  # Fail-soft mode (default)
        output_format="json"
    )

    assert result is not None
    assert "fallback" in result.lower() or "summary" in result.lower()


@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_review_pr_fail_on_error_raises(
    mock_cleanup,
    mock_build_prompt,
    mock_setup,
    mock_create_agent,
    mock_conversation_class
):
    """Test that review_pr raises when fail_on_error=True."""
    from hodor.agent import review_pr
    from pathlib import Path

    # Setup mocks
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    # Make the conversation fail
    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.run.side_effect = RuntimeError("Agent did not produce any review content")
    mock_conversation_class.return_value = mock_conversation

    # Should raise with fail_on_error=True
    with pytest.raises(RuntimeError):
        review_pr(
            "https://gitlab.com/owner/repo/-/merge_requests/1",
            fail_on_error=True,
            output_format="json"
        )
