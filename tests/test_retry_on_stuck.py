"""Tests for the retry-on-stuck mechanism in review_pr."""

import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

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


class MockEventBase:
    pass


mock_event.Event = MockEventBase

import pytest

from hodor.agent import review_pr, StuckPatternError


class MockMessageAction:
    def __init__(self, content):
        self.content = content
        self.__class__.__name__ = "MessageAction"


# ============================================================================
# Tests for retry-on-stuck behavior
# ============================================================================


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_retry_on_stuck_retries_from_scratch(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_run_with_nudge,
):
    """Test that review_pr retries from scratch when stuck pattern detected."""
    # Setup mocks
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # First attempt: raises StuckPatternError
    # Second attempt: succeeds
    mock_run_with_nudge.side_effect = [
        StuckPatternError("Stuck on first attempt"),
        '{"findings": [], "overall_correctness": "patch is correct"}'
    ]

    result = review_pr(
        "https://gitlab.com/owner/repo/-/merge_requests/1",
        max_retries_when_stuck=1,
        output_format="json"
    )

    assert result is not None
    assert '{"findings":' in result
    # Should have called setup_workspace twice (once per attempt)
    assert mock_setup.call_count == 2
    # Should have called run_with_nudge_recovery twice
    assert mock_run_with_nudge.call_count == 2


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_retry_disabled_when_max_is_zero(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_run_with_nudge,
):
    """Test that retries are disabled when max_retries_when_stuck=0."""
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # Always raises StuckPatternError
    mock_run_with_nudge.side_effect = StuckPatternError("Stuck")

    # Should raise immediately since retries disabled
    with pytest.raises(RuntimeError) as exc_info:
        review_pr(
            "https://gitlab.com/owner/repo/-/merge_requests/1",
            max_retries_when_stuck=0,  # Disable retries
            output_format="json"
        )

    assert "stuck pattern" in str(exc_info.value).lower()
    # Should only call setup_workspace once (no retries)
    assert mock_setup.call_count == 1


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_all_retries_exhausted_raises_error(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_run_with_nudge,
):
    """Test that RuntimeError is raised after all retries exhausted."""
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # Always raises StuckPatternError
    mock_run_with_nudge.side_effect = StuckPatternError("Stuck")

    with pytest.raises(RuntimeError) as exc_info:
        review_pr(
            "https://gitlab.com/owner/repo/-/merge_requests/1",
            max_retries_when_stuck=2,
            output_format="json"
        )

    assert "stuck pattern" in str(exc_info.value).lower()
    assert "3 attempt(s)" in str(exc_info.value)  # 1 initial + 2 retries
    # Should call setup_workspace 3 times
    assert mock_setup.call_count == 3


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent._recover_last_json_response")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_partial_content_recovered_on_retry_exhaustion(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_recover,
        mock_run_with_nudge,
):
    """Test that partial content is recovered and returned after retries exhausted."""
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # Always raises StuckPatternError
    mock_run_with_nudge.side_effect = StuckPatternError("Stuck")

    # But partial content can be recovered
    mock_recover.return_value = '{"findings": [{"title": "Partial Bug"}]}'

    result = review_pr(
        "https://gitlab.com/owner/repo/-/merge_requests/1",
        max_retries_when_stuck=1,
        output_format="json"
    )

    # Should return the recovered partial content
    assert result is not None
    assert "Partial Bug" in result


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_retry_cleans_up_previous_workspace(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_run_with_nudge,
):
    """Test that workspace is cleaned up between retry attempts."""
    workspace_paths = [Path("/tmp/workspace1"), Path("/tmp/workspace2")]
    mock_setup.side_effect = [
        (workspace_paths[0], "main", "abc123"),
        (workspace_paths[1], "main", "abc123"),
    ]
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # First: stuck, Second: success
    mock_run_with_nudge.side_effect = [
        StuckPatternError("Stuck"),
        '{"findings": []}'
    ]

    review_pr(
        "https://gitlab.com/owner/repo/-/merge_requests/1",
        max_retries_when_stuck=1,
        output_format="json"
    )

    # Should have cleaned up workspace between attempts
    # Cleanup is called: once for workspace1 before retry
    assert mock_cleanup.call_count >= 1


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_non_stuck_errors_not_retried(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_run_with_nudge,
):
    """Test that non-StuckPatternError exceptions are not retried."""
    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # Raises a different error (not StuckPatternError)
    mock_run_with_nudge.side_effect = RuntimeError("Some other error")

    with pytest.raises(RuntimeError) as exc_info:
        review_pr(
            "https://gitlab.com/owner/repo/-/merge_requests/1",
            max_retries_when_stuck=3,  # Many retries available
            fail_on_error=True,
            output_format="json"
        )

    assert "Some other error" in str(exc_info.value)
    # Should only call setup_workspace once (no retries for non-stuck errors)
    assert mock_setup.call_count == 1


@patch("hodor.agent.run_with_nudge_recovery")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.build_pr_review_prompt")
@patch("hodor.agent.cleanup_workspace")
def test_retry_logs_attempts(
        mock_cleanup,
        mock_build_prompt,
        mock_setup,
        mock_create_agent,
        mock_conversation_class,
        mock_run_with_nudge,
        caplog,
):
    """Test that retry attempts are logged."""
    import logging

    mock_setup.return_value = (Path("/tmp/workspace"), "main", "abc123")
    mock_build_prompt.return_value = "Test prompt"
    mock_create_agent.return_value = MagicMock()

    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation_class.return_value = mock_conversation

    # First: stuck, Second: success
    mock_run_with_nudge.side_effect = [
        StuckPatternError("Stuck"),
        '{"findings": []}'
    ]

    with caplog.at_level(logging.INFO):
        review_pr(
            "https://gitlab.com/owner/repo/-/merge_requests/1",
            max_retries_when_stuck=1,
            output_format="json"
        )

    # Should log about retry
    assert any("retry" in record.message.lower() for record in caplog.records)
