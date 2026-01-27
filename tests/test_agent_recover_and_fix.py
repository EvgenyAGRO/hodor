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


# ============================================================================
# Tests for stuck pattern detection (TDD - written first, implementation follows)
# ============================================================================

from hodor.agent import detect_stuck_pattern, STUCK_PATTERN_THRESHOLD


class MockEmptyMessageAction:
    """Mock for MessageAction with empty content (simulates stuck pattern)."""

    def __init__(self, content=""):
        self.content = content
        self.__class__.__name__ = "MessageAction"


class MockBashAction:
    """Mock for ExecuteBashAction (simulates normal agent work)."""

    def __init__(self, command="ls"):
        self.command = command
        self.__class__.__name__ = "ExecuteBashAction"


def test_detect_stuck_pattern_finds_consecutive_empty_responses():
    """Test that we detect stuck pattern when there are consecutive empty MessageActions."""
    # Create events with 3 consecutive empty MessageActions (matches the log output)
    events = []

    # First some normal activity
    event1 = MockEventBase()
    event1.action = MockBashAction("head -n 20 streets.txt")
    events.append(event1)

    event2 = MockEventBase()
    event2.action = MockBashAction("git grep NZCityKeyword")
    events.append(event2)

    # Now consecutive empty responses (the stuck pattern)
    for i in range(STUCK_PATTERN_THRESHOLD):
        event = MockEventBase()
        event.action = MockEmptyMessageAction("")
        events.append(event)

    is_stuck, empty_count = detect_stuck_pattern(events)

    assert is_stuck is True
    assert empty_count >= STUCK_PATTERN_THRESHOLD


def test_detect_stuck_pattern_ignores_single_empty_response():
    """Test that a single empty response doesn't trigger stuck detection."""
    events = []

    # Normal activity
    event1 = MockEventBase()
    event1.action = MockBashAction("ls")
    events.append(event1)

    # Single empty response (could be normal)
    event2 = MockEventBase()
    event2.action = MockEmptyMessageAction("")
    events.append(event2)

    # Back to normal
    event3 = MockEventBase()
    event3.action = MockBashAction("cat file.txt")
    events.append(event3)

    is_stuck, empty_count = detect_stuck_pattern(events)

    assert is_stuck is False


def test_detect_stuck_pattern_handles_none_content():
    """Test that None content is treated as empty."""
    events = []

    for i in range(STUCK_PATTERN_THRESHOLD):
        event = MockEventBase()
        action = MockEmptyMessageAction()
        action.content = None  # Explicitly None
        event.action = action
        events.append(event)

    is_stuck, empty_count = detect_stuck_pattern(events)

    assert is_stuck is True


def test_detect_stuck_pattern_returns_false_for_normal_activity():
    """Test that normal activity with content doesn't trigger stuck detection."""
    events = []

    # Normal activity with meaningful messages
    for i in range(5):
        event = MockEventBase()
        event.action = MockMessageAction(f"Thinking about step {i}...")
        events.append(event)

    is_stuck, empty_count = detect_stuck_pattern(events)

    assert is_stuck is False
    assert empty_count == 0


def test_detect_stuck_pattern_handles_empty_events():
    """Test that empty events list doesn't crash."""
    is_stuck, empty_count = detect_stuck_pattern([])

    assert is_stuck is False
    assert empty_count == 0


def test_detect_stuck_pattern_whitespace_only_is_empty():
    """Test that whitespace-only content is treated as empty."""
    events = []

    for i in range(STUCK_PATTERN_THRESHOLD):
        event = MockEventBase()
        event.action = MockEmptyMessageAction("   \n\t  ")  # Whitespace only
        events.append(event)

    is_stuck, empty_count = detect_stuck_pattern(events)

    assert is_stuck is True


def test_detect_stuck_pattern_mixed_with_bash_actions():
    """Test stuck detection with interleaved bash actions (matches real log)."""
    events = []

    # Pattern from the log: bash commands returning exit 1, then empty responses
    event1 = MockEventBase()
    event1.action = MockBashAction("git grep NZCityKeyword")
    events.append(event1)

    event2 = MockEventBase()
    event2.action = MockBashAction("grep commonKeywordPaths")
    events.append(event2)

    # Now the stuck pattern - consecutive empty MessageActions
    for i in range(STUCK_PATTERN_THRESHOLD):
        event = MockEventBase()
        event.action = MockEmptyMessageAction("")
        events.append(event)

    is_stuck, empty_count = detect_stuck_pattern(events)

    assert is_stuck is True


# ============================================================================
# Integration tests: review_pr handles stuck pattern and recovers
# ============================================================================

@patch("hodor.agent.get_agent_final_response")
@patch("hodor.agent.detect_stuck_pattern")
@patch("hodor.diff_utils.get_diff_stats")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_review_pr_detects_stuck_pattern_and_uses_fallback(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_get_diff_stats,
        mock_detect_stuck,
        mock_get_response,
):
    """Test that review_pr detects stuck pattern and generates fallback review."""
    from hodor.agent import review_pr
    from pathlib import Path

    # Setup mocks
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    # Simulate stuck pattern: conversation runs but produces no content
    mock_conversation = MagicMock()
    mock_conversation.state.events = []  # Events will be checked by detect_stuck_pattern
    mock_conversation.run.return_value = None  # Run completes without error
    mock_conversation_class.return_value = mock_conversation

    # get_agent_final_response returns empty (no review produced)
    mock_get_response.return_value = ""

    # detect_stuck_pattern returns True (we're stuck)
    mock_detect_stuck.return_value = (True, 3)

    # Provide diff stats for fallback
    mock_get_diff_stats.return_value = [
        MagicMock(path="file.py", added=10, deleted=5)
    ]

    # Should NOT raise, should return fallback
    result = review_pr(
        "https://gitlab.com/owner/repo/-/merge_requests/1",
        fail_on_error=False,
        output_format="json"
    )

    assert result is not None
    # Fallback review should mention the stuck pattern
    assert "fallback" in result.lower() or "stuck" in result.lower() or "summary" in result.lower()


@patch("hodor.agent.get_agent_final_response")
@patch("hodor.agent.detect_stuck_pattern")
@patch("hodor.diff_utils.get_diff_stats")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_review_pr_logs_stuck_pattern_detection(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_get_diff_stats,
        mock_detect_stuck,
        mock_get_response,
        caplog
):
    """Test that stuck pattern detection is logged."""
    from hodor.agent import review_pr
    from pathlib import Path
    import logging

    # Setup mocks
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.run.return_value = None
    mock_conversation_class.return_value = mock_conversation

    mock_get_response.return_value = ""
    mock_detect_stuck.return_value = (True, 3)
    mock_get_diff_stats.return_value = []

    with caplog.at_level(logging.WARNING):
        review_pr(
            "https://gitlab.com/owner/repo/-/merge_requests/1",
            fail_on_error=False,
            output_format="json"
        )

    # Should log about stuck pattern
    assert any("stuck" in record.message.lower() or "empty" in record.message.lower()
               for record in caplog.records)


# ============================================================================
# Tests for nudge recovery mechanism (TDD)
# ============================================================================

from hodor.agent import (
    get_nudge_prompt,
    run_with_nudge_recovery,
)


def test_get_nudge_prompt_returns_default():
    """Test that get_nudge_prompt returns a non-empty default prompt."""
    prompt = get_nudge_prompt()
    assert prompt is not None
    assert len(prompt) > 50  # Should be a meaningful prompt
    assert "review" in prompt.lower() or "continue" in prompt.lower()


def test_get_nudge_prompt_with_context():
    """Test that get_nudge_prompt can incorporate context from recent events."""
    # Simulate events where grep returned empty
    events = []
    event = MockEventBase()
    event.action = MockBashAction("git grep SomePattern")
    event.observation = MagicMock()
    event.observation.exit_code = 1  # Failed grep
    events.append(event)

    prompt = get_nudge_prompt(recent_events=events)
    assert prompt is not None
    # Should mention that search returned no results
    assert "search" in prompt.lower() or "continue" in prompt.lower() or "review" in prompt.lower()


def test_run_with_nudge_recovery_succeeds_without_stuck():
    """Test that run_with_nudge_recovery works normally when not stuck."""
    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.run.return_value = None

    # Simulate successful response - use patch.object on the module
    import hodor.agent as agent_module

    with patch.object(agent_module, "get_agent_final_response") as mock_get_response:
        with patch.object(agent_module, "detect_stuck_pattern") as mock_detect:
            mock_get_response.return_value = '{"findings": []}'
            mock_detect.return_value = (False, 0)

            result = run_with_nudge_recovery(mock_conversation)

    assert result == '{"findings": []}'
    # Should only call run() once (no nudges needed)
    assert mock_conversation.run.call_count == 1
    assert mock_conversation.send_message.call_count == 0


def test_run_with_nudge_recovery_nudges_on_stuck():
    """Test that run_with_nudge_recovery sends nudge when stuck detected."""
    mock_conversation = MagicMock()
    mock_conversation.state.events = []

    # First run: stuck, no content
    # Second run (after nudge): success
    call_count = [0]

    def mock_run():
        call_count[0] += 1

    mock_conversation.run.side_effect = mock_run

    import hodor.agent as agent_module

    with patch.object(agent_module, "get_agent_final_response") as mock_get_response:
        with patch.object(agent_module, "detect_stuck_pattern") as mock_detect:
            # First call: empty response, stuck detected
            # Second call: valid response
            mock_get_response.side_effect = ["", '{"findings": []}']
            mock_detect.side_effect = [(True, 3), (False, 0)]

            result = run_with_nudge_recovery(mock_conversation)

    assert result == '{"findings": []}'
    # Should call run() twice (initial + after nudge)
    assert mock_conversation.run.call_count == 2
    # Should send one nudge message
    assert mock_conversation.send_message.call_count == 1


def test_run_with_nudge_recovery_respects_max_attempts():
    """Test that run_with_nudge_recovery stops after max nudge attempts."""
    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.run.return_value = None

    import hodor.agent as agent_module

    with patch.object(agent_module, "get_agent_final_response") as mock_get_response:
        with patch.object(agent_module, "detect_stuck_pattern") as mock_detect:
            # Always return empty and stuck
            mock_get_response.return_value = ""
            mock_detect.return_value = (True, 3)

            result = run_with_nudge_recovery(mock_conversation, max_nudges=2)

    # Should return None after exhausting nudges
    assert result is None
    # Should call run() 3 times (initial + 2 nudges)
    assert mock_conversation.run.call_count == 3
    # Should send 2 nudge messages
    assert mock_conversation.send_message.call_count == 2


def test_run_with_nudge_recovery_extracts_partial_on_failure():
    """Test that partial content is extracted when nudges fail."""
    mock_conversation = MagicMock()

    # Create events with some partial JSON
    event1 = MockEventBase()
    event1.action = MockMessageAction("Looking at the code...")
    event2 = MockEventBase()
    event2.action = MockMessageAction('Partial: {"findings": [{"title": "Bug"}]}')
    mock_conversation.state.events = [event1, event2]
    mock_conversation.run.return_value = None

    import hodor.agent as agent_module

    with patch.object(agent_module, "get_agent_final_response") as mock_get_response:
        with patch.object(agent_module, "detect_stuck_pattern") as mock_detect:
            mock_get_response.return_value = ""  # No final response
            mock_detect.return_value = (True, 3)  # Always stuck

            result = run_with_nudge_recovery(mock_conversation, max_nudges=1)

    # Should recover partial JSON from events
    assert result is not None
    assert '{"findings":' in result


def test_run_with_nudge_recovery_logs_nudge_attempts(caplog):
    """Test that nudge attempts are logged."""
    import logging

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.run.return_value = None

    import hodor.agent as agent_module

    with patch.object(agent_module, "get_agent_final_response") as mock_get_response:
        with patch.object(agent_module, "detect_stuck_pattern") as mock_detect:
            # First: stuck, Second: success
            mock_get_response.side_effect = ["", '{"findings": []}']
            mock_detect.side_effect = [(True, 3), (False, 0)]

            with caplog.at_level(logging.INFO):
                run_with_nudge_recovery(mock_conversation)

    # Should log about nudge attempt
    assert any("nudge" in record.message.lower() for record in caplog.records)
