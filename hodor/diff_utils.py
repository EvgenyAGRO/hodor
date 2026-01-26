"""Utilities for local diff analysis and ingestion limiting."""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class FileDiffStats:
    path: str
    added: int
    deleted: int
    size_bytes: int
    is_large: bool = False
    status: str = "modified"  # modified, added, deleted, renamed
    patch: Optional[str] = None
    is_trimmed: bool = False

def get_diff_stats(workspace_path: Path, base_sha: str, head_sha: str = "HEAD") -> list[FileDiffStats]:
    """Get line stats for all files in the diff."""
    try:
        # Run git diff --numstat to get added/deleted lines
        # Output format: added \t deleted \t path
        cmd = ["git", "diff", "--numstat", base_sha, head_sha]
        result = subprocess.run(cmd, cwd=workspace_path, capture_output=True, text=True, check=True)
        
        stats_list = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            
            added_str, deleted_str, path = parts
            added = int(added_str) if added_str.isdigit() else 0
            deleted = int(deleted_str) if deleted_str.isdigit() else 0
            
            # Get file size (bytes)
            # For deleted files, size is 0 or we check the base?
            # Usually we care about the size of the *diff* or the *new file*?
            # User says "diff_bytes > MAX_FILE_DIFF_BYTES".
            # We can get the size of the patch itself.
            patch_cmd = ["git", "diff", base_sha, head_sha, "--", path]
            patch_result = subprocess.run(patch_cmd, cwd=workspace_path, capture_output=True, text=True, check=True)
            size_bytes = len(patch_result.stdout.encode("utf-8"))
            
            stats_list.append(FileDiffStats(
                path=path,
                added=added,
                deleted=deleted,
                size_bytes=size_bytes
            ))
        return stats_list
    except Exception as e:
        logger.error(f"Failed to get diff stats: {e}")
        return []

def trim_patch(patch: str, max_lines: int = 1500, action: str = "preview", force_trim: bool = False) -> tuple[str, bool]:
    """Trim a patch based on the specified action.

    Args:
        patch: The patch content to potentially trim
        max_lines: Maximum lines before triggering preview trim
        action: One of "skip", "preview", "sample", "summarize"
        force_trim: If True, apply trim action even if line count is low (for byte-limited files)

    Returns:
        Tuple of (trimmed_patch, is_trimmed)
    """
    lines = patch.split("\n")
    line_count = len(lines)

    # Skip action: omit patch entirely
    if action == "skip":
        return "[PATCH SKIPPED DUE TO SIZE LIMITS]", True

    # Summarize action: show only metadata, no content
    if action == "summarize":
        return f"[STATS ONLY: {line_count} lines, {len(patch)} bytes]", True

    # For preview/sample: trim if naturally long OR forced due to byte limits
    should_trim = line_count > 160 or force_trim

    if action == "preview" and should_trim:
        if line_count > 160:
            # Show first 80 and last 80 lines
            head = lines[:80]
            tail = lines[-80:]
            trimmed_patch = "\n".join(head) + "\n\n... [TRIMMED DUE TO SIZE] ...\n\n" + "\n".join(tail)
            return trimmed_patch, True
        elif force_trim and line_count > 0:
            # File is large by bytes but has few lines (e.g., minified code)
            # Show a representative sample
            preview_lines = min(100, line_count)
            head = lines[:preview_lines]
            trimmed_patch = "\n".join(head) + f"\n\n... [TRIMMED: showing {preview_lines}/{line_count} lines, large file] ..."
            return trimmed_patch, True

    if action == "sample" and should_trim:
        # Sample action: show multiple hunks from the patch
        # Extract hunk headers and show a few complete hunks
        hunks = []
        current_hunk = []

        for line in lines:
            if line.startswith("@@") and current_hunk:
                hunks.append(current_hunk)
                current_hunk = [line]
            else:
                current_hunk.append(line)

        if current_hunk:
            hunks.append(current_hunk)

        # Show first 2 hunks and last hunk if available
        if len(hunks) > 3:
            sampled_hunks = hunks[:2] + [["... [SAMPLED: showing 3 of {} hunks] ...".format(len(hunks))]] + [hunks[-1]]
            sampled_lines = []
            for hunk in sampled_hunks:
                sampled_lines.extend(hunk)
            return "\n".join(sampled_lines), True
        elif line_count > 160:
            # No hunks found, fall back to preview
            head = lines[:80]
            tail = lines[-80:]
            trimmed_patch = "\n".join(head) + "\n\n... [TRIMMED DUE TO SIZE] ...\n\n" + "\n".join(tail)
            return trimmed_patch, True

    return patch, False


def analyze_and_limit_diff(
    workspace_path: Path, 
    base_sha: str, 
    head_sha: str = "HEAD",
    max_lines: int = 1500,
    max_bytes: int = 200000,
    action: str = "preview"
) -> list[FileDiffStats]:
    """Analyze all changed files and apply limits."""
    stats_list = get_diff_stats(workspace_path, base_sha, head_sha)
    
    for stats in stats_list:
        # Check if file is large
        total_lines = stats.added + stats.deleted
        if total_lines > max_lines or stats.size_bytes > max_bytes or action == "skip":
            stats.is_large = True
            
        # Get patch
        patch_cmd = ["git", "diff", base_sha, head_sha, "--", stats.path]
        try:
            patch_result = subprocess.run(patch_cmd, cwd=workspace_path, capture_output=True, text=True, check=True)
            raw_patch = patch_result.stdout
        except Exception:
            raw_patch = "[ERROR FETCHING PATCH]"
        
        if stats.is_large:
            # Force trim even if line count is low (for byte-limited files)
            stats.patch, stats.is_trimmed = trim_patch(raw_patch, max_lines, action, force_trim=True)
        else:
            stats.patch = raw_patch
            stats.is_trimmed = False
            
    return stats_list

