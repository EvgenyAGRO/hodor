"""Jira integration for Hodor code review.

Extracts Jira issue URLs from MR descriptions, fetches issue details,
and provides context for AI-powered code reviews.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Regex to match Jira URLs like https://company.atlassian.net/browse/PROJECT-123
JIRA_URL_PATTERN = re.compile(
    r'https?://([a-zA-Z0-9-]+\.atlassian\.net)/browse/([A-Z][A-Z0-9]+-\d+)',
    re.IGNORECASE
)


class JiraAPIError(RuntimeError):
    """Raised when Jira API operations fail."""


def _get_jira_auth_header() -> dict[str, str] | None:
    """Build authentication header for Jira API.
    
    Uses JIRA_EMAIL and JIRA_API_KEY environment variables.
    Returns None if credentials are not configured.
    """
    email = os.getenv("JIRA_EMAIL")
    api_key = os.getenv("JIRA_API_KEY")
    
    if not email or not api_key:
        return None
    
    # Jira uses Basic auth with email:api_token
    credentials = f"{email}:{api_key}"
    encoded = base64.b64encode(credentials.encode()).decode()
    
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


def extract_jira_urls(text: str) -> list[tuple[str, str]]:
    """Extract Jira issue URLs from text.
    
    Args:
        text: Text to search (MR title, description, etc.)
        
    Returns:
        List of tuples: (jira_host, issue_key) e.g., ("company.atlassian.net", "EDR-1966")
    """
    if not text:
        return []
    
    matches = JIRA_URL_PATTERN.findall(text)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for host, key in matches:
        key_upper = key.upper()
        if (host, key_upper) not in seen:
            seen.add((host, key_upper))
            result.append((host, key_upper))
    
    return result


def fetch_jira_issue(host: str, issue_key: str) -> dict[str, Any] | None:
    """Fetch Jira issue details via REST API.
    
    Args:
        host: Jira host (e.g., "company.atlassian.net")
        issue_key: Issue key (e.g., "EDR-1966")
        
    Returns:
        Issue data dict or None if fetch fails
    """
    auth_header = _get_jira_auth_header()
    if not auth_header:
        logger.warning("Jira credentials not configured (JIRA_EMAIL, JIRA_API_KEY)")
        return None
    
    url = f"https://{host}/rest/api/3/issue/{issue_key}"
    
    try:
        response = requests.get(
            url,
            headers=auth_header,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Failed to fetch Jira issue {issue_key}: {e}")
        return None


def get_parent_issue(host: str, issue: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch parent issue if the given issue is a subtask.
    
    Args:
        host: Jira host
        issue: Issue data from fetch_jira_issue
        
    Returns:
        Parent issue data or None
    """
    fields = issue.get("fields", {})
    
    # Check if this is a subtask
    issue_type = fields.get("issuetype", {})
    if not issue_type.get("subtask", False):
        return None
    
    # Get parent reference
    parent = fields.get("parent")
    if not parent:
        return None
    
    parent_key = parent.get("key")
    if not parent_key:
        return None
    
    logger.info(f"Fetching parent issue: {parent_key}")
    return fetch_jira_issue(host, parent_key)


def summarize_jira_issue(issue: dict[str, Any], is_parent: bool = False) -> str:
    """Format a single Jira issue for prompt context.
    
    Args:
        issue: Issue data from Jira API
        is_parent: Whether this is a parent issue
        
    Returns:
        Formatted markdown string
    """
    fields = issue.get("fields", {})
    key = issue.get("key", "Unknown")
    
    summary = fields.get("summary", "No summary")
    issue_type = fields.get("issuetype", {}).get("name", "Issue")
    status = fields.get("status", {}).get("name", "Unknown")
    priority = fields.get("priority", {}).get("name", "")
    
    # Get description (can be complex Atlassian Document Format)
    description = ""
    desc_field = fields.get("description")
    if desc_field:
        if isinstance(desc_field, str):
            description = desc_field
        elif isinstance(desc_field, dict):
            # Parse ADF (Atlassian Document Format) - simplified
            description = _extract_text_from_adf(desc_field)
    
    # Truncate description to a sane limit to prevent context explosion
    if len(description) > 5000:
        description = description[:4997] + "..."
    
    prefix = "**Parent Issue**" if is_parent else "**Linked Issue**"
    
    lines = [
        f"{prefix}: [{key}] {summary}",
        f"- Type: {issue_type}",
        f"- Status: {status}",
    ]
    
    if priority:
        lines.append(f"- Priority: {priority}")
    
    if description:
        lines.append(f"- Description: {description}")
    
    return "\n".join(lines)


def _extract_text_from_adf(adf: dict[str, Any]) -> str:
    """Extract plain text from Atlassian Document Format.
    
    Args:
        adf: ADF document structure
        
    Returns:
        Plain text content
    """
    text_parts = []
    
    def recurse(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                text_parts.append(node.get("text", ""))
            for child in node.get("content", []):
                recurse(child)
        elif isinstance(node, list):
            for item in node:
                recurse(item)
    
    recurse(adf)
    return " ".join(text_parts).strip()


def build_jira_context(mr_title: str, mr_description: str) -> str:
    """Build Jira context section for the review prompt.
    
    Args:
        mr_title: MR title
        mr_description: MR description
        
    Returns:
        Formatted Jira context section (empty string if no Jira issues found)
    """
    # Combine title and description for URL extraction
    combined_text = f"{mr_title or ''}\n{mr_description or ''}"
    
    jira_urls = extract_jira_urls(combined_text)
    if not jira_urls:
        return ""
    
    logger.info(f"Found {len(jira_urls)} Jira issue(s) in MR")
    
    sections = []
    
    for host, issue_key in jira_urls:
        issue = fetch_jira_issue(host, issue_key)
        if not issue:
            continue
        
        # Add main issue
        sections.append(summarize_jira_issue(issue))
        
        # Check for parent
        parent = get_parent_issue(host, issue)
        if parent:
            sections.append(summarize_jira_issue(parent, is_parent=True))
    
    if not sections:
        return ""
    
    return "## Jira Context\n\n" + "\n\n".join(sections) + "\n"
