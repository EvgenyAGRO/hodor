import pytest
from unittest.mock import MagicMock, patch
from hodor.gitlab import get_latest_mr_diff_refs, create_mr_discussion, GitLabAPIError
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

def test_get_latest_mr_diff_refs_from_versions(mock_mr):
    # Setup revisions
    version = MagicMock()
    version.base_commit_sha = "v_base"
    version.start_commit_sha = "v_start"
    version.head_commit_sha = "v_head"
    mock_mr.versions.list.return_value = [version]

    refs = get_latest_mr_diff_refs("owner", "repo", 1)

    assert refs == {
        "base_sha": "v_base",
        "start_sha": "v_start",
        "head_sha": "v_head"
    }
    mock_mr.versions.list.assert_called_once()

def test_get_latest_mr_diff_refs_fallback(mock_mr):
    # Setup no versions
    mock_mr.versions.list.return_value = []

    refs = get_latest_mr_diff_refs("owner", "repo", 1)

    assert refs == {
        "base_sha": "base",
        "start_sha": "start",
        "head_sha": "head"
    }

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
    # Test fetching refs if not provided
    version = MagicMock()
    version.base_commit_sha = "v_base"
    version.start_commit_sha = "v_start"
    version.head_commit_sha = "v_head"
    mock_mr.versions.list.return_value = [version]
    
    create_mr_discussion(
        "owner", "repo", 1, "body",
        file_path="foo.py", line=10
    )
    
    mock_mr.discussions.create.assert_called_once()
    call_args = mock_mr.discussions.create.call_args[0][0]
    assert call_args["position"]["base_sha"] == "v_base"
