import pytest
from unittest.mock import MagicMock, patch
from hodor.gitlab import get_latest_mr_diff_refs, create_mr_discussion, GitLabAPIError, summarize_gitlab_notes
from gitlab import exceptions as gitlab_exceptions

@pytest.fixture
def mock_gitlab_client():
    with patch("hodor.gitlab._create_gitlab_client") as mock:
        yield mock

@pytest.fixture
def mock_project(mock_gitlab_client):
    project = MagicMock()
    mock_gitlab_client.return_value.projects.get.return_value = project
    return project

@pytest.fixture
def mock_mr(mock_project):
    mr = MagicMock()
    mock_project.mergerequests.get.return_value = mr
    # Default attributes
    mr.diff_refs = {"base_sha": "base", "start_sha": "start", "head_sha": "head"}
    return mr

def test_get_latest_mr_diff_refs_from_diff_refs(mock_mr):
    # Setup revisions on the object itself
    mock_mr.diff_refs = {"base_sha": "b1", "start_sha": "s1", "head_sha": "h1"}
    
    refs = get_latest_mr_diff_refs("owner", "repo", 1)
    
    assert refs == {"base_sha": "b1", "start_sha": "s1", "head_sha": "h1"}
    # Should NOT have called any API for versions
    assert not hasattr(mock_mr, "versions.list") or mock_mr.versions.list.call_count == 0

def test_get_latest_mr_diff_refs_from_rest_fallback(mock_mr, mock_gitlab_client, mock_project):
    # Clear diff_refs to trigger fallback
    mock_mr.diff_refs = None
    mock_mr.iid = 123
    mock_project.id = 456
    
    # Mock the direct REST call
    mock_gitlab_client.return_value.http_get.return_value = [
        {
            "base_commit_sha": "rb",
            "start_commit_sha": "rs",
            "head_commit_sha": "rh"
        }
    ]
    
    refs = get_latest_mr_diff_refs("owner", "repo", 123)
    
    assert refs == {"base_sha": "rb", "start_sha": "rs", "head_sha": "rh"}
    mock_gitlab_client.return_value.http_get.assert_called_with("/projects/456/merge_requests/123/versions")

def test_create_mr_discussion_inline(mock_mr):
    mr_discussion_mock = mock_mr.discussions.create
    mr_discussion_mock.return_value.attributes = {"id": "123"}
    
    diff_refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}

    create_mr_discussion(
        "owner", "repo", 1, "test body",
        file_path="foo.py", line=10, side="new",
        diff_refs=diff_refs
    )

    mr_discussion_mock.assert_called_once()
    call_args = mr_discussion_mock.call_args[0][0]
    
    assert call_args["body"] == "test body"
    assert call_args["position"]["new_path"] == "foo.py"
    assert call_args["position"]["new_line"] == 10
    assert call_args["position"]["base_sha"] == "b"

def test_create_mr_discussion_inline_fallback_on_error(mock_mr):
    # Fail first attempt
    err = gitlab_exceptions.GitlabCreateError()
    err.response_code = 400
    err.error_message = "Bad position"

    mock_mr.discussions.create.side_effect = [
        err,
        MagicMock(attributes={"id": "fallback"})
    ]

    diff_refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}

    create_mr_discussion(
        "owner", "repo", 1, "test body",
        file_path="foo.py", line=10, side="new",
        diff_refs=diff_refs
    )

    assert mock_mr.discussions.create.call_count == 2
    # Check fallback call
    fallback_args = mock_mr.discussions.create.call_args[0][0]
    assert "**[foo.py:10]**" in fallback_args["body"]
    assert "position" not in fallback_args

def test_create_mr_discussion_implicit_refs(mock_mr):
    # Test fetching refs if not provided (uses diff_refs in fixture)
    mock_mr.diff_refs = {"base_sha": "ib", "start_sha": "is", "head_sha": "ih"}
    
    create_mr_discussion(
        "owner", "repo", 1, "body",
        file_path="foo.py", line=10
    )
    
    mock_mr.discussions.create.assert_called_once()
    call_args = mock_mr.discussions.create.call_args[0][0]
    assert call_args["position"]["base_sha"] == "ib"

def test_summarize_gitlab_notes_handles_none_body() -> None:
    notes = [
        {"author": {"username": "user1"}, "body": "valid comment that is definitely longer than twenty characters", "created_at": "2023-01-01T12:00:00Z"},
        {"author": {"username": "user2"}, "body": None, "created_at": "2023-01-01T12:05:00Z"},
    ]
    
    summary = summarize_gitlab_notes(notes)
    
    assert "user1" in summary
    assert "valid comment" in summary
    assert "user2" not in summary
