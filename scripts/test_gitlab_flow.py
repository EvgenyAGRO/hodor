#!/usr/bin/env python3
"""
Verify GitLab inline posting logic locally without a real MR or Docker build.

Usage:
    export PYTHONPATH=.
    python3 scripts/test_gitlab_flow.py
"""

import logging
import sys
from unittest.mock import MagicMock, patch

# Mock external dependencies to allow running without full env
# This allows testing the logic without installing openhands or other heavy deps
mock_openhands = MagicMock()
sys.modules["openhands"] = mock_openhands
sys.modules["openhands.sdk"] = mock_openhands
sys.modules["openhands.sdk.conversation"] = mock_openhands
sys.modules["openhands.sdk.event"] = mock_openhands
sys.modules["openhands.sdk.workspace"] = mock_openhands
sys.modules["openhands.tools"] = mock_openhands
sys.modules["openhands.tools.preset"] = mock_openhands
sys.modules["openhands.tools.preset.default"] = mock_openhands
sys.modules["litellm"] = mock_openhands
sys.modules["dotenv"] = MagicMock()

# Mock gitlab dependency
mock_gitlab = MagicMock()
mock_gitlab_exceptions = MagicMock()
mock_gitlab.exceptions = mock_gitlab_exceptions

# Define specific exceptions needed for the code to import/run
class MockGitlabError(Exception):
    def __init__(self, *args, **kwargs):
        self.error_message = str(args[0]) if args else "Error"
        super().__init__(*args, **kwargs)

class MockGitlabCreateError(MockGitlabError): pass

mock_gitlab_exceptions.GitlabError = MockGitlabError
mock_gitlab_exceptions.GitlabCreateError = MockGitlabCreateError
mock_gitlab_exceptions.GitlabAuthenticationError = MockGitlabError
mock_gitlab_exceptions.GitlabGetError = MockGitlabError

sys.modules["gitlab"] = mock_gitlab
sys.modules["gitlab.exceptions"] = mock_gitlab_exceptions

# Configure logging to see Hodor's output
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Mock the gitlab module BEFORE importing hodor modules that might use it
with patch("hodor.gitlab._create_gitlab_client") as mock_client_cls:
    from hodor.agent import _post_gitlab_inline_review
    from hodor.gitlab import get_latest_mr_diff_refs

    print("🔍 Setting up mock GitLab environment...")

    # specialized mock objects
    mock_client = MagicMock()
    mock_project = MagicMock()
    mock_mr = MagicMock()
    
    mock_client_cls.return_value = mock_client
    mock_client.projects.get.return_value = mock_project
    mock_project.mergerequests.get.return_value = mock_mr

    # Mock diff refs
    mock_version = MagicMock()
    mock_version.base_commit_sha = "aabbcc11"
    mock_version.start_commit_sha = "22334455"
    mock_version.head_commit_sha = "889900aa"
    mock_mr.versions.list.return_value = [mock_version]

    # Mock discussion creation to print what it receives
    def side_effect_create(data):
        print(f"\n[MOCK Gitlab] Creating discussion:")
        print(f"  Body: {data['body'][:100]}...")
        if "position" in data:
            pos = data["position"]
            print(f"  Position: {pos.get('new_path')}:{pos.get('new_line')}")
            print(f"  Type: {pos.get('position_type')}")
        else:
            print("  Type: Note (Non-inline)")
        return MagicMock(attributes={"id": "1"})

    mock_mr.discussions.create.side_effect = side_effect_create

    # Sample Agent Output (JSON)
    sample_output = """
    {
      "summary": "This is a test review summary.",
      "findings": [
        {
          "path": "src/main.py",
          "line": 42,
          "body": "Potential null pointer dereference here. Please check input.",
          "priority": 1
        },
        {
          "path": "README.md",
          "line": 10,
          "body": "Typo in documentation.",
          "priority": 3
        }
      ],
      "overall_correctness": "patch has blocking issues",
      "overall_explanation": "Critical bug found in main.py"
    }
    """

    print("\n🚀 Running _post_gitlab_inline_review with sample JSON output...")
    
    try:
        success = _post_gitlab_inline_review(
            owner="mock-group",
            repo="mock-repo",
            mr_number=123,
            review_output=sample_output,
            host="gitlab.com"
        )
        
        if success:
            print("\n✅ Verification Successful: Logic executed and posted discussions.")
        else:
            print("\n❌ Verification Failed: Function returned False.")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Verification Crashed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
