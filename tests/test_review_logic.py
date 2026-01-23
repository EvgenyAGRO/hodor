import unittest
from unittest.mock import MagicMock, patch
import sys
import logging

# Mock logging to avoid clutter
logging.basicConfig(level=logging.CRITICAL)

# Mock gitlab module structure
class MockModule:
    pass

# Mock gitlab module structure
sys.modules["gitlab"] = MagicMock()
sys.modules["gitlab.exceptions"] = MagicMock()

# Mock openhands
m_openhands = MockModule()
sys.modules["openhands"] = m_openhands

m_sdk = MockModule()
m_sdk.Conversation = MagicMock()
m_sdk.LLM = MagicMock()
m_sdk.event = MagicMock() # Ensure it has event attribute if referenced
m_sdk.event.Event = MagicMock()
sys.modules["openhands.sdk"] = m_sdk
m_openhands.sdk = m_sdk

m_workspace = MockModule()
m_workspace.LocalWorkspace = MagicMock()
sys.modules["openhands.sdk.workspace"] = m_workspace

sys.modules["openhands.sdk.conversation"] = MagicMock()
sys.modules["openhands.sdk.event"] = MagicMock()
sys.modules["openhands.sdk.context"] = MagicMock()
sys.modules["openhands.tools"] = MagicMock()
sys.modules["openhands.tools.preset"] = MagicMock()
sys.modules["openhands.tools.preset.default"] = MagicMock()

# Import the module under test
from hodor.agent import _post_gitlab_inline_review

class TestGitLabReview(unittest.TestCase):
    
    @patch("hodor.gitlab.get_latest_mr_diff_refs")
    @patch("hodor.gitlab.get_merge_request_discussions")
    @patch("hodor.gitlab.create_mr_discussion")
    @patch("hodor.gitlab.post_gitlab_mr_discussion")
    @patch("hodor.gitlab.post_gitlab_mr_comment")
    @patch("hodor.review_parser.parse_review_output")
    def test_duplicate_check(self, mock_parse, mock_post_comment, mock_post_discussion, mock_create_discussion, mock_get_discussions, mock_get_refs):
        """Test that duplicate comments are skipped."""
        
        # Setup mocks
        mock_get_refs.return_value = {"base_sha": "a", "start_sha": "b", "head_sha": "c"}
        
        # Existing discussion at file.py:10 with "Fix this"
        mock_get_discussions.return_value = [{
            "notes": [{
                "position": {"new_path": "file.py", "new_line": 10},
                "body": "**[P1] Fix this**\n\nSome body"
            }]
        }]
        
        # New finding matches existing
        mock_finding = MagicMock()
        mock_finding.title = "[P1] Fix this"
        mock_finding.body = "Some body"
        mock_finding.code_location.absolute_file_path = "file.py"
        mock_finding.code_location.line_range.start = 10
        
        mock_parsed = MagicMock()
        mock_parsed.findings = [mock_finding]
        mock_parsed.overall_correctness = None
        mock_parsed.overall_explanation = None
        mock_parse.return_value = mock_parsed
        
        # Run
        _post_gitlab_inline_review("owner", "repo", 1, "json_output", None)
        
        # precise assertions
        mock_get_discussions.assert_called_once()
        mock_create_discussion.assert_not_called() # Should be skipped
        
    @patch("hodor.gitlab.get_latest_mr_diff_refs")
    @patch("hodor.gitlab.get_merge_request_discussions")
    @patch("hodor.gitlab.create_mr_discussion")
    @patch("hodor.gitlab.post_gitlab_mr_discussion")
    @patch("hodor.gitlab.post_gitlab_mr_comment")
    @patch("hodor.review_parser.parse_review_output")
    def test_no_thread_on_success(self, mock_parse, mock_post_comment, mock_post_discussion, mock_create_discussion, mock_get_discussions, mock_get_refs):
        """Test that no thread is opened if no findings (just summary)."""
        
        # Setup mocks
        mock_get_refs.return_value = {}
        mock_get_discussions.return_value = []
        
        # No findings, but overall explanation
        mock_parsed = MagicMock()
        mock_parsed.findings = []
        mock_parsed.overall_correctness = "patch is correct"
        mock_parsed.overall_explanation = "Good job"
        mock_parse.return_value = mock_parsed
        
        # Run
        _post_gitlab_inline_review("owner", "repo", 1, "json_output", None)
        
        # Should post COMMENT, not DISCUSSION
        mock_post_comment.assert_called_once()
        mock_post_discussion.assert_not_called()
        
    @patch("hodor.gitlab.get_latest_mr_diff_refs")
    @patch("hodor.gitlab.get_merge_request_discussions")
    @patch("hodor.gitlab.create_mr_discussion")
    @patch("hodor.gitlab.post_gitlab_mr_discussion")
    @patch("hodor.gitlab.post_gitlab_mr_comment")
    @patch("hodor.review_parser.parse_review_output")
    def test_thread_on_failure(self, mock_parse, mock_post_comment, mock_post_discussion, mock_create_discussion, mock_get_discussions, mock_get_refs):
        """Test that summaries are posted as comments even if findings exist."""
        
        # Setup mocks
        mock_get_refs.return_value = {}
        mock_get_discussions.return_value = []
        
        # Findings exist
        mock_finding = MagicMock()
        mock_finding.title = "Bug"
        mock_finding.body = "Fix it"
        mock_finding.code_location.absolute_file_path = "file.py"
        mock_finding.code_location.line_range.start = 10
        
        mock_parsed = MagicMock()
        mock_parsed.findings = [mock_finding]
        mock_parsed.overall_correctness = "blocking issues"
        mock_parsed.overall_explanation = "Bad code"
        mock_parse.return_value = mock_parsed
        
        # Run
        _post_gitlab_inline_review("owner", "repo", 1, "json_output", None)
        
        # Should post COMMENT for summary per user request
        mock_post_comment.assert_called_once()
        mock_post_discussion.assert_not_called()
        mock_create_discussion.assert_called_once() # The finding (still a discussion/thread)

if __name__ == "__main__":
    unittest.main()
