import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from hodor.agent import review_pr

@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.get_agent_final_response")
@patch("hodor.agent.discover_skills")
@patch("hodor.diff_utils.get_diff_stats")
@patch("hodor.diff_utils.subprocess.run")
def test_large_diff_fallback_integration(
    mock_subproc, mock_stats, mock_skills, mock_final, mock_conv, mock_agent, mock_setup
):
    # 1. Setup workspace mockup
    workspace_path = Path("/tmp/hodor-test-large")
    mock_setup.return_value = (workspace_path, "main", "base_sha_123")
    mock_skills.return_value = []
    
    # 2. Setup "Huge File" stats
    from hodor.diff_utils import FileDiffStats
    mock_stats.return_value = [
        FileDiffStats("huge_data.txt", 50000, 0, 2000000)
    ]
    
    # 3. Simulate Agent Failure (Stuck/Empty)
    # We'll make hodor/agent.py raise an exception during conversation.run()
    mock_conv.return_value.run.side_effect = Exception("Stuck pattern detected")
    mock_final.return_value = None
    
    # 4. Run review_pr with fail_on_error=False (default)
    review_output = review_pr(
        pr_url="https://gitlab.com/owner/repo/-/merge_requests/1",
        output_format="json",
        fail_on_error=False
    )
    
    # 5. Verify results
    assert review_output is not None
    data = json.loads(review_output)
    
    assert "findings" in data
    assert len(data["findings"]) > 0
    assert "huge_data.txt" in data["findings"][0]["body"]
    assert "Review Fallback" in data["summary"]
    assert data["overall_correctness"] == "patch is correct"

@patch("hodor.agent.setup_workspace")
@patch("hodor.agent.create_hodor_agent")
@patch("hodor.agent.Conversation")
@patch("hodor.agent.get_agent_final_response")
@patch("hodor.agent.discover_skills")
@patch("hodor.diff_utils.analyze_and_limit_diff")
def test_large_diff_prompt_trimming_integration(
    mock_analyze, mock_skills, mock_final, mock_conv, mock_agent, mock_setup
):
    # This test verifies that build_pr_review_prompt actually uses the trimmed diffs
    workspace_path = Path("/tmp/hodor-test-prompt")
    mock_setup.return_value = (workspace_path, "main", "base_sha_123")
    
    from hodor.diff_utils import FileDiffStats
    mock_analyze.return_value = [
        FileDiffStats("large.txt", 2000, 0, 500000, is_large=True, patch="TRIMMED_CONTENT", is_trimmed=True)
    ]
    
    # We want to check the prompt sent to conversation.send_message
    with patch("hodor.agent.build_pr_review_prompt") as mock_build:
        mock_build.side_effect = lambda **kwargs: "Mocked Prompt"
        
        # We need mock_final to return something so it doesn't fail
        mock_final.return_value = '{"findings": []}'
        
        review_pr(
            pr_url="https://gitlab.com/owner/repo/-/merge_requests/2",
            workspace_dir=workspace_path
        )
        
        # Verify build_pr_review_prompt was called with workspace and limits
        mock_build.assert_called_once()
        args = mock_build.call_args[1]
        assert args["workspace_path"] == workspace_path
        assert args["max_diff_lines"] == 1500
