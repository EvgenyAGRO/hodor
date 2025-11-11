"""GitHub API tool implementations."""

import logging
import os
from typing import Any

from github import Github, Auth
from github.GithubException import GithubException

logger = logging.getLogger(__name__)


def _get_github_client(token: str | None = None) -> Github:
    """Create authenticated GitHub client."""
    auth_token = token or os.getenv("GITHUB_TOKEN")

    if auth_token:
        auth = Auth.Token(auth_token)
        return Github(auth=auth)
    else:
        logger.warning("No GitHub token provided - API rate limits will be very low")
        return Github()


def fetch_pr_metadata(owner: str, repo: str, pr_number: int, github_token: str | None = None) -> dict[str, Any]:
    """
    Fetch PR metadata including title, description, author, timestamps, labels.

    Returns:
        Dictionary with PR metadata
    """
    logger.info(f"Fetching PR metadata for {owner}/{repo}/pull/{pr_number}")

    try:
        g = _get_github_client(github_token)
        repository = g.get_repo(f"{owner}/{repo}")
        pr = repository.get_pull(pr_number)

        return {
            "title": pr.title,
            "description": pr.body or "",
            "state": pr.state,
            "author": pr.user.login,
            "created_at": pr.created_at.isoformat(),
            "updated_at": pr.updated_at.isoformat(),
            "merged": pr.merged,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "base_branch": pr.base.ref,
            "head_branch": pr.head.ref,
            "labels": [label.name for label in pr.labels],
            "additions": pr.additions,
            "deletions": pr.deletions,
            "changed_files": pr.changed_files,
            "url": pr.html_url,
        }

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        raise Exception(f"Failed to fetch PR metadata: {e}")


def fetch_pr_files(owner: str, repo: str, pr_number: int, github_token: str | None = None) -> dict[str, Any]:
    """
    Fetch list of all changed files with addition/deletion stats.

    Returns:
        Dictionary with list of changed files
    """
    logger.info(f"Fetching PR files for {owner}/{repo}/pull/{pr_number}")

    try:
        g = _get_github_client(github_token)
        repository = g.get_repo(f"{owner}/{repo}")
        pr = repository.get_pull(pr_number)

        files = []
        for file in pr.get_files():
            files.append(
                {
                    "filename": file.filename,
                    "status": file.status,  # added, removed, modified, renamed
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "changes": file.changes,
                    "patch": file.patch[:500] if file.patch else None,  # First 500 chars for preview
                }
            )

        return {"total_files": len(files), "files": files}

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        raise Exception(f"Failed to fetch PR files: {e}")


def fetch_file_diff(
    owner: str, repo: str, pr_number: int, file_path: str, github_token: str | None = None
) -> dict[str, Any]:
    """
    Fetch detailed diff for a specific file.

    Returns:
        Dictionary with file diff information
    """
    logger.info(f"Fetching diff for {file_path} in {owner}/{repo}/pull/{pr_number}")

    try:
        g = _get_github_client(github_token)
        repository = g.get_repo(f"{owner}/{repo}")
        pr = repository.get_pull(pr_number)

        # Find the file in PR files
        for file in pr.get_files():
            if file.filename == file_path:
                return {
                    "filename": file.filename,
                    "status": file.status,
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "changes": file.changes,
                    "patch": file.patch or "",
                    "previous_filename": file.previous_filename if hasattr(file, "previous_filename") else None,
                }

        return {"error": f"File {file_path} not found in PR"}

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        raise Exception(f"Failed to fetch file diff: {e}")


def fetch_pr_commits(owner: str, repo: str, pr_number: int, github_token: str | None = None) -> dict[str, Any]:
    """
    Fetch list of commits in the PR.

    Returns:
        Dictionary with commit list
    """
    logger.info(f"Fetching commits for {owner}/{repo}/pull/{pr_number}")

    try:
        g = _get_github_client(github_token)
        repository = g.get_repo(f"{owner}/{repo}")
        pr = repository.get_pull(pr_number)

        commits = []
        for commit in pr.get_commits():
            commits.append(
                {
                    "sha": commit.sha,
                    "message": commit.commit.message,
                    "author": commit.commit.author.name,
                    "author_email": commit.commit.author.email,
                    "date": commit.commit.author.date.isoformat(),
                    "url": commit.html_url,
                }
            )

        return {"total_commits": len(commits), "commits": commits}

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        raise Exception(f"Failed to fetch PR commits: {e}")


def fetch_ci_status(owner: str, repo: str, pr_number: int, github_token: str | None = None) -> dict[str, Any]:
    """
    Fetch CI/CD check status for the PR.

    Returns:
        Dictionary with CI status information
    """
    logger.info(f"Fetching CI status for {owner}/{repo}/pull/{pr_number}")

    try:
        g = _get_github_client(github_token)
        repository = g.get_repo(f"{owner}/{repo}")
        pr = repository.get_pull(pr_number)

        # Get the latest commit
        commits = list(pr.get_commits())
        if not commits:
            return {"error": "No commits found in PR"}

        latest_commit = commits[-1]

        # Get check runs
        check_runs = latest_commit.get_check_runs()

        checks = []
        for check in check_runs:
            checks.append(
                {
                    "name": check.name,
                    "status": check.status,  # queued, in_progress, completed
                    "conclusion": check.conclusion,  # success, failure, neutral, cancelled, skipped, timed_out, action_required
                    "started_at": check.started_at.isoformat() if check.started_at else None,
                    "completed_at": check.completed_at.isoformat() if check.completed_at else None,
                    "url": check.html_url,
                }
            )

        # Get combined status
        combined_status = latest_commit.get_combined_status()
        statuses = []
        for status in combined_status.statuses:
            statuses.append(
                {
                    "context": status.context,
                    "state": status.state,  # pending, success, failure, error
                    "description": status.description,
                    "target_url": status.target_url,
                }
            )

        return {
            "commit_sha": latest_commit.sha,
            "combined_state": combined_status.state,
            "total_checks": len(checks),
            "checks": checks,
            "total_statuses": len(statuses),
            "statuses": statuses,
        }

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        raise Exception(f"Failed to fetch CI status: {e}")


def search_tests(owner: str, repo: str, file_path: str, github_token: str | None = None) -> dict[str, Any]:
    """
    Search for test files related to the given source file.

    Returns:
        Dictionary with list of potential test files
    """
    logger.info(f"Searching for tests related to {file_path} in {owner}/{repo}")

    try:
        # Generate test file patterns
        import os.path

        base_name = os.path.basename(file_path)
        name_without_ext = os.path.splitext(base_name)[0]

        # Common test file patterns
        test_patterns = [
            f"test_{name_without_ext}",
            f"{name_without_ext}.test",
            f"{name_without_ext}.spec",
            f"{name_without_ext}_test",
        ]

        found_tests = []

        # Search for test files using GitHub code search
        for pattern in test_patterns:
            try:
                # This is a simplified search - in production, you'd use recursive search
                # or GitHub's code search API
                # For MVP, we'll just return the patterns
                found_tests.append(
                    {"pattern": pattern, "note": "Test search requires repository indexing - check manually for now"}
                )
            except Exception:
                pass

        return {
            "source_file": file_path,
            "test_patterns": test_patterns,
            "found_tests": found_tests,
            "note": "MVP version - returns test patterns to check manually",
        }

    except GithubException as e:
        logger.error(f"GitHub API error: {e}")
        raise Exception(f"Failed to search tests: {e}")


def post_pr_comment(
    owner: str, repo: str, pr_number: int, comment_body: str, github_token: str | None = None
) -> dict[str, Any]:
    """
    Post a comment on a GitHub pull request.

    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: Pull request number
        comment_body: Comment text (supports markdown)
        github_token: GitHub API token

    Returns:
        Dictionary with comment information

    Raises:
        Exception: If posting comment fails
    """
    logger.info(f"Posting comment on {owner}/{repo}/pull/{pr_number}")

    try:
        g = _get_github_client(github_token)
        repository = g.get_repo(f"{owner}/{repo}")
        pr = repository.get_pull(pr_number)

        # Create a comment on the PR
        comment = pr.create_issue_comment(comment_body)

        return {
            "success": True,
            "comment_id": comment.id,
            "comment_url": comment.html_url,
            "message": "Comment posted successfully",
        }

    except GithubException as e:
        logger.error(f"GitHub API error when posting comment: {e}")
        raise Exception(f"Failed to post PR comment: {e}")
    except Exception as e:
        logger.error(f"Error posting comment: {e}")
        raise Exception(f"Failed to post PR comment: {e}")
