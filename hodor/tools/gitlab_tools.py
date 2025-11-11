"""GitLab API tool implementations."""

import logging
import os
from typing import Any

import gitlab
from gitlab.exceptions import GitlabError

logger = logging.getLogger(__name__)


def _get_gitlab_client(token: str | None = None, gitlab_url: str = "https://gitlab.com") -> gitlab.Gitlab:
    """Create authenticated GitLab client.

    Args:
        token: GitLab API token
        gitlab_url: GitLab instance URL (default: https://gitlab.com for public GitLab)
    """
    auth_token = token or os.getenv("GITLAB_TOKEN")

    if auth_token:
        gl = gitlab.Gitlab(gitlab_url, private_token=auth_token)
        gl.auth()
        return gl
    else:
        logger.warning("No GitLab token provided - API rate limits will be very low")
        return gitlab.Gitlab(gitlab_url)


def parse_repo_url(repo_url: str) -> tuple[str, str, str | None, str]:
    """
    Parse GitLab repository URL to extract owner, repo, ref, and base URL.

    Examples:
        https://gitlab.com/owner/repo → ('owner', 'repo', None, 'https://gitlab.com')
        https://gitlab.example.com/owner/repo/-/tree/branch → ('owner', 'repo', 'branch', 'https://gitlab.example.com')
        https://gitlab.com/owner/repo/-/merge_requests/123 → ('owner', 'repo', None, 'https://gitlab.com')

    Returns:
        Tuple of (owner, repo, ref, gitlab_url)
    """
    from urllib.parse import urlparse

    parsed = urlparse(repo_url)
    path_parts = [p for p in parsed.path.split("/") if p]

    # Extract base URL (scheme + netloc)
    gitlab_url = f"{parsed.scheme}://{parsed.netloc}"

    if len(path_parts) >= 2:
        owner = path_parts[0]
        repo = path_parts[1]

        # Check if URL has a branch reference
        ref = None
        if len(path_parts) >= 4 and path_parts[2] == "-" and path_parts[3] == "tree":
            ref = path_parts[4] if len(path_parts) > 4 else None

        return owner, repo, ref, gitlab_url

    raise ValueError(f"Invalid GitLab URL format: {repo_url}")


def fetch_pr_metadata(
    owner: str, repo: str, pr_number: int, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
    """
    Fetch merge request metadata including title, description, author, timestamps.

    Note: Parameter named 'github_token' for compatibility but uses GITLAB_TOKEN.

    Returns:
        Dictionary with MR metadata
    """
    logger.info(f"Fetching MR metadata for {owner}/{repo}/merge_requests/{pr_number}")

    try:
        gl = _get_gitlab_client(github_token, gitlab_url)
        project = gl.projects.get(f"{owner}/{repo}")
        mr = project.mergerequests.get(pr_number)

        return {
            "title": mr.title,
            "description": mr.description or "",
            "state": mr.state,  # opened, closed, merged
            "author": mr.author["username"],
            "created_at": mr.created_at,
            "updated_at": mr.updated_at,
            "merged": mr.merged_at is not None,
            "merged_at": mr.merged_at,
            "source_branch": mr.source_branch,
            "target_branch": mr.target_branch,
            "labels": mr.labels,
            "url": mr.web_url,
            "changes_count": mr.changes_count,
        }

    except GitlabError as e:
        logger.error(f"GitLab API error: {e}")
        raise Exception(f"Failed to fetch MR metadata: {e}")


def fetch_pr_files(
    owner: str, repo: str, pr_number: int, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
    """
    Fetch list of all changed files with addition/deletion stats.

    Returns:
        Dictionary with list of changed files
    """
    logger.info(f"Fetching MR files for {owner}/{repo}/merge_requests/{pr_number}")

    try:
        gl = _get_gitlab_client(github_token, gitlab_url)
        project = gl.projects.get(f"{owner}/{repo}")
        mr = project.mergerequests.get(pr_number)

        # Get changes (diffs)
        changes = mr.changes()

        files = []
        for change in changes["changes"]:
            # Calculate additions and deletions from diff
            diff_lines = change.get("diff", "").split("\n")
            additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
            deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

            files.append(
                {
                    "filename": change["new_path"],
                    "status": (
                        "modified"
                        if not change["new_file"] and not change["deleted_file"]
                        else (
                            "added"
                            if change["new_file"]
                            else (
                                "deleted"
                                if change["deleted_file"]
                                else "renamed" if change["renamed_file"] else "modified"
                            )
                        )
                    ),
                    "additions": additions,
                    "deletions": deletions,
                    "changes": additions + deletions,
                    "patch": change.get("diff", "")[:500] if change.get("diff") else None,  # First 500 chars
                }
            )

        return {"total_files": len(files), "files": files}

    except GitlabError as e:
        logger.error(f"GitLab API error: {e}")
        raise Exception(f"Failed to fetch MR files: {e}")


def fetch_file_diff(
    owner: str, repo: str, pr_number: int, file_path: str, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
    """
    Fetch detailed diff for a specific file.

    Returns:
        Dictionary with file diff information
    """
    logger.info(f"Fetching diff for {file_path} in {owner}/{repo}/merge_requests/{pr_number}")

    try:
        gl = _get_gitlab_client(github_token, gitlab_url)
        project = gl.projects.get(f"{owner}/{repo}")
        mr = project.mergerequests.get(pr_number)

        # Get changes
        changes = mr.changes()

        # Find the file in changes
        for change in changes["changes"]:
            if change["new_path"] == file_path or change["old_path"] == file_path:
                diff_lines = change.get("diff", "").split("\n")
                additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
                deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

                return {
                    "filename": change["new_path"],
                    "status": (
                        "modified"
                        if not change["new_file"] and not change["deleted_file"]
                        else (
                            "added"
                            if change["new_file"]
                            else (
                                "deleted"
                                if change["deleted_file"]
                                else "renamed" if change["renamed_file"] else "modified"
                            )
                        )
                    ),
                    "additions": additions,
                    "deletions": deletions,
                    "changes": additions + deletions,
                    "patch": change.get("diff", ""),
                    "previous_filename": change.get("old_path") if change.get("renamed_file") else None,
                }

        return {"error": f"File {file_path} not found in MR"}

    except GitlabError as e:
        logger.error(f"GitLab API error: {e}")
        raise Exception(f"Failed to fetch file diff: {e}")


def fetch_pr_commits(
    owner: str, repo: str, pr_number: int, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
    """
    Fetch list of commits in the merge request.

    Returns:
        Dictionary with commit list
    """
    logger.info(f"Fetching commits for {owner}/{repo}/merge_requests/{pr_number}")

    try:
        gl = _get_gitlab_client(github_token, gitlab_url)
        project = gl.projects.get(f"{owner}/{repo}")
        mr = project.mergerequests.get(pr_number)

        # Get commits
        commits_list = mr.commits()

        commits = []
        for commit in commits_list:
            commits.append(
                {
                    "sha": commit.id,
                    "message": commit.message,
                    "author": commit.author_name,
                    "author_email": commit.author_email,
                    "date": commit.created_at,
                    "url": commit.web_url,
                }
            )

        return {"total_commits": len(commits), "commits": commits}

    except GitlabError as e:
        logger.error(f"GitLab API error: {e}")
        raise Exception(f"Failed to fetch MR commits: {e}")


def fetch_ci_status(
    owner: str, repo: str, pr_number: int, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
    """
    Fetch CI/CD pipeline status for the merge request.

    Returns:
        Dictionary with CI status information
    """
    logger.info(f"Fetching CI status for {owner}/{repo}/merge_requests/{pr_number}")

    try:
        gl = _get_gitlab_client(github_token, gitlab_url)
        project = gl.projects.get(f"{owner}/{repo}")
        mr = project.mergerequests.get(pr_number)

        # Get pipelines for this MR
        pipelines = mr.pipelines.list()

        if not pipelines:
            return {"error": "No pipelines found for this MR", "pipelines": []}

        # Get the latest pipeline
        latest_pipeline = project.pipelines.get(pipelines[0]["id"])

        # Get jobs for the latest pipeline
        jobs = latest_pipeline.jobs.list()

        job_list = []
        for job in jobs:
            job_list.append(
                {
                    "name": job.name,
                    "status": job.status,  # pending, running, success, failed, canceled, skipped
                    "stage": job.stage,
                    "started_at": getattr(job, "started_at", None),
                    "finished_at": getattr(job, "finished_at", None),
                    "url": job.web_url,
                }
            )

        return {
            "pipeline_id": latest_pipeline.id,
            "pipeline_status": latest_pipeline.status,
            "pipeline_url": latest_pipeline.web_url,
            "total_jobs": len(job_list),
            "jobs": job_list,
        }

    except GitlabError as e:
        logger.error(f"GitLab API error: {e}")
        raise Exception(f"Failed to fetch CI status: {e}")


def search_tests(
    owner: str, repo: str, file_path: str, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
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
            f"{name_without_ext}_test",
            f"{name_without_ext}.test",
            f"{name_without_ext}.spec",
            f"test_{name_without_ext}",
        ]

        return {
            "source_file": file_path,
            "test_patterns": test_patterns,
            "note": "GitLab test search not fully implemented - check these patterns manually",
        }

    except Exception as e:
        logger.error(f"Error searching tests: {e}")
        raise Exception(f"Failed to search tests: {e}")


def post_mr_comment(
    owner: str, repo: str, mr_number: int, comment_body: str, github_token: str | None = None, gitlab_url: str = "https://gitlab.com"
) -> dict[str, Any]:
    """
    Post a comment on a GitLab merge request.

    Args:
        owner: Project owner/namespace
        repo: Project name
        mr_number: Merge request number
        comment_body: Comment text (supports markdown)
        github_token: GitLab API token (named for compatibility)
        gitlab_url: GitLab instance URL (default: https://gitlab.com for public GitLab)

    Returns:
        Dictionary with comment information

    Raises:
        Exception: If posting comment fails
    """
    logger.info(f"Posting comment on {owner}/{repo}/merge_requests/{mr_number}")

    try:
        gl = _get_gitlab_client(github_token, gitlab_url)
        project = gl.projects.get(f"{owner}/{repo}")
        mr = project.mergerequests.get(mr_number)

        # Create a note (comment) on the MR
        note = mr.notes.create({"body": comment_body})

        return {
            "success": True,
            "comment_id": note.id,
            "comment_url": f"{gitlab_url}/{owner}/{repo}/-/merge_requests/{mr_number}#note_{note.id}",
            "message": "Comment posted successfully",
        }

    except GitlabError as e:
        logger.error(f"GitLab API error when posting comment: {e}")
        raise Exception(f"Failed to post MR comment: {e}")
    except Exception as e:
        logger.error(f"Error posting comment: {e}")
        raise Exception(f"Failed to post MR comment: {e}")
