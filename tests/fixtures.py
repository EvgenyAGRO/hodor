"""Shared test fixtures for Hodor tests.

Provides common mock objects, sample data, and test utilities
to avoid duplication across test files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch


# Sample PR URLs for testing
SAMPLE_GITHUB_PR_URL = "https://github.com/test-owner/test-repo/pull/123"
SAMPLE_GITLAB_MR_URL = "https://gitlab.com/test-owner/test-repo/-/merge_requests/456"
SAMPLE_GITLAB_SELFHOSTED_URL = "https://gitlab.example.com/group/project/-/merge_requests/789"


# Sample review output
SAMPLE_REVIEW_JSON = json.dumps({
    "findings": [
        {
            "title": "[P1] Security Issue",
            "body": "Found a potential SQL injection vulnerability.",
            "code_location": {
                "absolute_file_path": "src/db.py",
                "line_range": {"start": 42, "end": 42}
            }
        },
        {
            "title": "[P2] Code Quality",
            "body": "Consider using a constant instead of magic number.",
            "code_location": {
                "absolute_file_path": "src/utils.py",
                "line_range": {"start": 100, "end": 105}
            }
        }
    ],
    "overall_correctness": "patch has blocking issues",
    "overall_explanation": "Found one critical security issue that should be addressed."
})

SAMPLE_REVIEW_MARKDOWN = """
### Issues Found

- **[P1] Security Issue** (`src/db.py:42`)
  Found a potential SQL injection vulnerability.

- **[P2] Code Quality** (`src/utils.py:100`)
  Consider using a constant instead of magic number.

### Summary

Found one critical security issue that should be addressed.

### Overall Verdict
**Status**: Patch has blocking issues
"""


# Sample PR metadata
SAMPLE_GITHUB_PR_INFO = {
    "number": 123,
    "title": "Add new feature",
    "body": "This PR adds a new feature.",
    "author": {"login": "testuser", "name": "Test User"},
    "baseRefName": "main",
    "headRefName": "feature/new-feature",
    "baseRefOid": "abc123",
    "headRefOid": "def456",
    "changedFiles": 3,
    "labels": [{"name": "enhancement"}],
    "comments": [],
    "state": "open",
    "isDraft": False,
    "createdAt": "2024-01-01T00:00:00Z",
    "updatedAt": "2024-01-02T00:00:00Z",
    "mergeable": "MERGEABLE",
    "url": SAMPLE_GITHUB_PR_URL,
}

SAMPLE_GITLAB_MR_INFO = {
    "iid": 456,
    "title": "Fix bug in login",
    "description": "This MR fixes a bug in the login flow.",
    "author": {"username": "testuser", "name": "Test User"},
    "source_branch": "fix/login-bug",
    "target_branch": "main",
    "state": "opened",
    "labels": ["bug"],
    "web_url": SAMPLE_GITLAB_MR_URL,
}


@dataclass
class MockEvent:
    """Mock event for testing agent event handling."""
    action: Any = None
    observation: Any = None
    error: str | None = None


@dataclass
class MockAction:
    """Mock action for testing."""
    content: str = ""

    @property
    def __class__(self) -> type:
        class MockMessageAction:
            __name__ = "MessageAction"
        return MockMessageAction


@dataclass
class MockConversationState:
    """Mock conversation state."""
    events: list[MockEvent]


class MockConversation:
    """Mock OpenHands conversation for testing."""

    def __init__(
        self,
        final_response: str | None = None,
        should_timeout: bool = False,
        should_fail: bool = False,
    ):
        self.final_response = final_response
        self.should_timeout = should_timeout
        self.should_fail = should_fail
        self.messages_sent: list[str] = []
        self.run_count = 0

        # Create mock state with events
        self.state = MockConversationState(events=[])

        # Mock conversation stats
        self.conversation_stats = MagicMock()
        self.conversation_stats.get_combined_metrics.return_value = MagicMock(
            accumulated_token_usage=MagicMock(
                prompt_tokens=1000,
                completion_tokens=500,
                cache_read_tokens=200,
                cache_write_tokens=100,
                reasoning_tokens=50,
            ),
            accumulated_cost=0.05,
            response_latencies=[],
        )

    def send_message(self, message: str) -> None:
        """Record sent message."""
        self.messages_sent.append(message)

    def run(self) -> None:
        """Simulate running the conversation."""
        self.run_count += 1

        if self.should_timeout:
            import time
            time.sleep(10)  # Will be interrupted by timeout

        if self.should_fail:
            raise RuntimeError("Conversation failed")

        # Add a mock event with the final response
        if self.final_response:
            action = MockAction(content=self.final_response)
            self.state.events.append(MockEvent(action=action))


class MockWorkspace:
    """Mock workspace for testing."""

    def __init__(self, path: Path | str = "/tmp/test-workspace"):
        self.path = Path(path)
        self.working_dir = str(self.path)


def create_mock_subprocess_result(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock subprocess.CompletedProcess result."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


def mock_subprocess_run(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    side_effect: Exception | None = None,
):
    """Create a patch for subprocess.run that returns a mock result.

    Usage:
        with mock_subprocess_run(stdout="output"):
            result = subprocess.run(...)
    """
    if side_effect:
        return patch("subprocess.run", side_effect=side_effect)

    mock_result = create_mock_subprocess_result(stdout, stderr, returncode)
    return patch("subprocess.run", return_value=mock_result)


def create_temp_workspace(tmp_path: Path, with_git: bool = True) -> Path:
    """Create a temporary workspace directory for testing.

    Args:
        tmp_path: pytest tmp_path fixture
        with_git: Initialize as a git repository

    Returns:
        Path to the workspace
    """
    workspace = tmp_path / "test-workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    if with_git:
        git_dir = workspace / ".git"
        git_dir.mkdir()
        # Create minimal git config
        (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    return workspace


def create_sample_diff_file(workspace: Path, filename: str = "test.py", lines: int = 100) -> Path:
    """Create a sample file with content for diff testing.

    Args:
        workspace: Workspace path
        filename: Name of file to create
        lines: Number of lines to generate

    Returns:
        Path to created file
    """
    filepath = workspace / filename
    content = "\n".join([f"# Line {i}" for i in range(1, lines + 1)])
    filepath.write_text(content)
    return filepath
