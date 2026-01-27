"""GitHub helper utilities for Hodor."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from .retry import retry_network, is_rate_limit_error

logger = logging.getLogger(__name__)

# Timeout for GitHub CLI commands (seconds)
GH_CMD_TIMEOUT = 60


class GitHubAPIError(RuntimeError):
    """Raised when gh fails or returns invalid data."""


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub rate limits are hit."""


def _run_gh_json_command(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int = GH_CMD_TIMEOUT,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubAPIError(f"GitHub CLI command timed out after {timeout}s: {' '.join(args)}") from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - passthrough path
        error_msg = exc.stderr if getattr(exc, "stderr", None) else str(exc)
        # Check for rate limiting
        if is_rate_limit_error(Exception(error_msg)):
            raise GitHubRateLimitError(f"GitHub rate limit exceeded: {error_msg}") from exc
        raise GitHubAPIError(error_msg) from exc

    output = result.stdout.strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:  # pragma: no cover - passthrough path
        raise GitHubAPIError(f"Unable to parse gh JSON output: {exc}") from exc


@retry_network()
def fetch_github_pr_info(
        owner: str,
        repo: str,
        pr_number: str | int,
) -> dict[str, Any]:
    """Fetch GitHub PR information using gh CLI.

    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: Pull request number

    Returns:
        Dictionary with PR metadata

    Raises:
        GitHubAPIError: If the API call fails
        GitHubRateLimitError: If rate limits are exceeded
    """
    fields = [
        "number",
        "title",
        "body",
        "author",
        "baseRefName",
        "headRefName",
        "baseRefOid",
        "headRefOid",
        "changedFiles",
        "labels",
        "comments",
        "state",
        "isDraft",
        "createdAt",
        "updatedAt",
        "mergeable",
        "url",
    ]
    repo_full_path = f"{owner}/{repo}"
    args = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "-R",
        repo_full_path,
        "--json",
        ",".join(fields),
    ]
    return _run_gh_json_command(args)


def normalize_github_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "title": raw.get("title"),
        "description": raw.get("body", ""),
        "source_branch": raw.get("headRefName"),
        "target_branch": raw.get("baseRefName"),
        "changes_count": raw.get("changedFiles"),
        "labels": [{"name": lbl.get("name") or lbl.get("id")} for lbl in (raw.get("labels") or [])],
        "author": {
            "username": raw.get("author", {}).get("login") or raw.get("author", {}).get("name"),
            "name": raw.get("author", {}).get("name"),
        },
        "Notes": _github_comments_to_notes(raw.get("comments")),
    }
    return metadata


def _github_comments_to_notes(
        comments: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not comments:
        return []

    if isinstance(comments, list):
        nodes = comments
    elif isinstance(comments, dict):
        nodes = comments.get("nodes") or comments.get("edges") or []
        # Handle GraphQL edge format
        if nodes and isinstance(nodes[0], dict) and "node" in nodes[0]:
            nodes = [edge.get("node", {}) for edge in nodes]
    else:
        nodes = []
    notes: list[dict[str, Any]] = []
    for node in nodes:
        notes.append(
            {
                "body": node.get("body", ""),
                "author": {
                    "username": node.get("author", {}).get("login") or node.get("author", {}).get("name"),
                    "name": node.get("author", {}).get("name"),
                },
                "created_at": node.get("createdAt"),
            }
        )
    return notes
