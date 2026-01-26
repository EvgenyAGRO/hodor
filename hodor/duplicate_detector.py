"""Duplicate detection for code review comments.

This module provides robust duplicate detection for code review findings,
handling variations in case, whitespace, markdown formatting, and semantic
similarity to prevent posting duplicate comments.
"""

import re
from difflib import SequenceMatcher
from typing import Any


# Similarity threshold for considering two titles as duplicates (0-100)
SIMILARITY_THRESHOLD = 70

# Line proximity threshold - findings within this many lines are considered same location
LINE_PROXIMITY_THRESHOLD = 5


def normalize_for_comparison(text: str) -> str:
    """Normalize text for duplicate comparison.

    Strips whitespace, removes markdown formatting, normalizes case,
    and collapses multiple spaces.

    Args:
        text: The text to normalize

    Returns:
        Normalized text suitable for comparison
    """
    if not text:
        return ""

    # Remove markdown bold (**text** or __text__)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)

    # Remove markdown italic (*text* or _text_)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', text)

    # Remove markdown code backticks
    text = re.sub(r'`(.+?)`', r'\1', text)

    # Convert newlines to spaces
    text = text.replace('\n', ' ').replace('\r', ' ')

    # Collapse multiple spaces to single space
    text = re.sub(r'\s+', ' ', text)

    # Strip leading/trailing whitespace
    text = text.strip()

    # Convert to lowercase for case-insensitive comparison
    text = text.lower()

    return text


def extract_title(body: str) -> str:
    """Extract the title from a comment body.

    Handles various formats:
    - **[P1] Title**\\n\\nBody
    - [P1] Title\\nBody
    - Title\\nBody

    Args:
        body: The full comment body

    Returns:
        The extracted title (first line, without markdown formatting)
    """
    if not body:
        return ""

    # Get first line
    first_line = body.split('\n')[0].strip()

    # Remove markdown bold
    first_line = re.sub(r'\*\*(.+?)\*\*', r'\1', first_line)
    first_line = re.sub(r'__(.+?)__', r'\1', first_line)

    return first_line.strip()


def similarity_score(text1: str, text2: str) -> int:
    """Calculate similarity score between two texts.

    Uses SequenceMatcher for fuzzy string matching.

    Args:
        text1: First text
        text2: Second text

    Returns:
        Similarity score from 0 to 100
    """
    # Handle empty strings
    if not text1 and not text2:
        return 100  # Both empty = identical
    if not text1 or not text2:
        return 0

    # Normalize both texts
    norm1 = normalize_for_comparison(text1)
    norm2 = normalize_for_comparison(text2)

    # Use SequenceMatcher for fuzzy comparison
    ratio = SequenceMatcher(None, norm1, norm2).ratio()

    return int(ratio * 100)


def is_duplicate_finding(
    new_finding: dict[str, Any],
    existing: list[dict[str, Any]],
    similarity_threshold: int = SIMILARITY_THRESHOLD,
    line_threshold: int = LINE_PROXIMITY_THRESHOLD,
) -> bool:
    """Check if a finding is a duplicate of an existing comment.

    A finding is considered duplicate if:
    1. Same file AND
    2. Same or nearby line (within threshold) AND
    3. Similar title (above similarity threshold)

    Args:
        new_finding: The new finding to check, with keys: path, line, title, body
        existing: List of existing comments with keys: path, line, body
        similarity_threshold: Minimum similarity score to consider duplicate (0-100)
        line_threshold: Maximum line distance to consider same location

    Returns:
        True if the finding is a duplicate
    """
    if not existing:
        return False

    new_path = new_finding.get("path", "")
    new_line = new_finding.get("line", 0)
    new_title = new_finding.get("title", "")

    for ex in existing:
        ex_path = ex.get("path")
        ex_line = ex.get("line")
        ex_body = ex.get("body", "")

        # Extract title from existing comment body
        ex_title = extract_title(ex_body)

        # Check file match (required)
        if ex_path and new_path != ex_path:
            continue

        # Check line proximity (if both have line numbers)
        if ex_line is not None and new_line:
            if abs(new_line - ex_line) > line_threshold:
                continue

        # Check title similarity
        score = similarity_score(new_title, ex_title)
        if score >= similarity_threshold:
            return True

        # Also check if the new title is contained in the existing body (fallback)
        norm_new_title = normalize_for_comparison(new_title)
        norm_ex_body = normalize_for_comparison(ex_body)
        if norm_new_title and norm_new_title in norm_ex_body:
            return True

    return False


def deduplicate_findings(
    findings: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    similarity_threshold: int = SIMILARITY_THRESHOLD,
    line_threshold: int = LINE_PROXIMITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Remove duplicate findings from a list.

    Removes:
    1. Findings that match existing comments
    2. Duplicate findings within the batch itself

    Preserves the original order for unique findings.

    Args:
        findings: List of findings to deduplicate
        existing: List of existing comments
        similarity_threshold: Minimum similarity score to consider duplicate
        line_threshold: Maximum line distance to consider same location

    Returns:
        List of unique findings with duplicates removed
    """
    unique: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = list(existing)  # Start with existing as "seen"

    for finding in findings:
        # Check against both existing and already-seen in this batch
        if is_duplicate_finding(finding, seen, similarity_threshold, line_threshold):
            continue

        # Not a duplicate - add to results and mark as seen
        unique.append(finding)

        # Add to seen list in the format expected by is_duplicate_finding
        seen.append({
            "path": finding.get("path"),
            "line": finding.get("line"),
            "body": f"**{finding.get('title', '')}**\n\n{finding.get('body', '')}"
        })

    return unique


def parse_existing_comments(
    discussions: list[dict[str, Any]],
    platform: str = "gitlab",
) -> list[dict[str, Any]]:
    """Parse existing comments from API response into normalized format.

    Args:
        discussions: Raw discussions/comments from API
        platform: "gitlab" or "github"

    Returns:
        List of normalized comments with keys: path, line, body
    """
    parsed: list[dict[str, Any]] = []

    if platform == "gitlab":
        for discussion in discussions:
            notes = discussion.get("notes", [])
            for note in notes:
                position = note.get("position") or {}
                path = position.get("new_path")
                line = position.get("new_line")
                body = note.get("body") or ""

                parsed.append({
                    "path": path,
                    "line": line,
                    "body": body,
                })

    elif platform == "github":
        # GitHub comments format
        for comment in discussions:
            path = comment.get("path")
            line = comment.get("line") or comment.get("original_line")
            body = comment.get("body") or ""

            parsed.append({
                "path": path,
                "line": line,
                "body": body,
            })

    return parsed
