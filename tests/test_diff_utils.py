import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load diff_utils module directly (avoids package import issues with openhands)
_module_path = Path(__file__).parent.parent / "hodor" / "diff_utils.py"
_spec = importlib.util.spec_from_file_location("diff_utils", _module_path)
_diff_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_diff_utils)

# Import functions from the loaded module
trim_patch = _diff_utils.trim_patch
FileDiffStats = _diff_utils.FileDiffStats
get_diff_stats = _diff_utils.get_diff_stats
analyze_and_limit_diff = _diff_utils.analyze_and_limit_diff


def test_trim_patch_preview():
    # Create a dummy patch with 300 lines
    lines = [f"line {i}" for i in range(300)]
    patch = "\n".join(lines)

    trimmed, is_trimmed = trim_patch(patch, max_lines=150, action="preview")

    assert is_trimmed is True
    assert "line 0" in trimmed
    assert "line 79" in trimmed
    assert "[TRIMMED DUE TO SIZE]" in trimmed
    assert "line 100" not in trimmed  # Middle part should be gone
    assert "line 220" in trimmed  # Should be in tail (last 80 lines: 220-299)
    assert "line 299" in trimmed


def test_trim_patch_skip():
    patch = "some patch content"
    trimmed, is_trimmed = trim_patch(patch, max_lines=5, action="skip")

    assert is_trimmed is True
    assert "[PATCH SKIPPED DUE TO SIZE LIMITS]" in trimmed


def test_trim_patch_no_trim_if_small():
    patch = "line 1\nline 2"
    trimmed, is_trimmed = trim_patch(patch, max_lines=10, action="preview")

    assert is_trimmed is False
    assert trimmed == patch


@patch("subprocess.run")
def test_get_diff_stats(mock_run):
    # Mock git diff --numstat
    mock_run.side_effect = [
        MagicMock(stdout="10\t5\tfile1.py\n100\t0\tfile2.txt\n", returncode=0),  # numstat
        MagicMock(stdout="patch1", returncode=0),  # size for file 1
        MagicMock(stdout="patch2" * 10, returncode=0),  # size for file 2
    ]

    global get_diff_stats
    stats = get_diff_stats(Path("/tmp"), "base", "head")

    assert len(stats) == 2
    assert stats[0].path == "file1.py"
    assert stats[0].added == 10
    assert stats[0].deleted == 5
    assert stats[1].path == "file2.txt"
    assert stats[1].size_bytes == 60  # "patch2" is 6 bytes. 6 * 10 = 60.


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff(mock_run, mock_stats):
    mock_stats.return_value = [
        FileDiffStats("small.py", 10, 2, 100),
        FileDiffStats("large.txt", 2000, 0, 500000)
    ]

    # Mock git diff to return a long enough patch to trigger trimming
    long_patch = "\n".join([f"line {i}" for i in range(200)])
    mock_run.return_value = MagicMock(stdout=long_patch, returncode=0)

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(Path("/tmp"), "base", action="preview")

    assert len(results) == 2
    assert results[0].is_large is False
    assert results[0].is_trimmed is False
    assert results[1].is_large is True
    assert results[1].is_trimmed is True
    assert "[TRIMMED DUE TO SIZE]" in results[1].patch


# New comprehensive tests for robustness

def test_trim_patch_sample_action():
    """Test sample action shows multiple hunks from the patch."""
    # Create a patch with multiple hunks
    lines = []
    for i in range(10):
        lines.append(f"@@ -{i * 10},5 +{i * 10},5 @@")
        for j in range(20):
            lines.append(f"+line {i * 100 + j}")
    patch = "\n".join(lines)

    trimmed, is_trimmed = trim_patch(patch, max_lines=50, action="sample")

    assert is_trimmed is True
    assert "@@" in trimmed  # Should contain hunk headers
    assert "[SAMPLED" in trimmed.upper() or "[TRIMMED" in trimmed.upper()


def test_trim_patch_summarize_action():
    """Test summarize action returns only stats, no content."""
    patch = "\n".join([f"+line {i}" for i in range(1000)])

    trimmed, is_trimmed = trim_patch(patch, max_lines=100, action="summarize")

    assert is_trimmed is True
    assert "STATS ONLY" in trimmed  # Should show stats marker
    assert "lines" in trimmed  # Should show line count
    assert "bytes" in trimmed  # Should show byte count
    # Should NOT contain actual patch content
    assert "+line 500" not in trimmed


def test_trim_patch_empty_diff():
    """Test handling of empty diffs."""
    patch = ""

    trimmed, is_trimmed = trim_patch(patch, max_lines=100, action="preview")

    assert is_trimmed is False
    assert trimmed == ""


def test_trim_patch_exactly_at_limit():
    """Test diff exactly at line limit is not trimmed."""
    lines = [f"line {i}" for i in range(160)]  # Exactly at preview threshold
    patch = "\n".join(lines)

    trimmed, is_trimmed = trim_patch(patch, max_lines=160, action="preview")

    # At exactly 160 lines, it should NOT be trimmed (> 160 triggers trim)
    assert is_trimmed is False
    assert trimmed == patch


def test_trim_patch_single_line():
    """Test single-line changes are not trimmed."""
    patch = "+single line change"

    trimmed, is_trimmed = trim_patch(patch, max_lines=100, action="preview")

    assert is_trimmed is False
    assert trimmed == patch


@patch("subprocess.run")
def test_get_diff_stats_with_byte_sizes(mock_run):
    """Test that byte sizes are calculated correctly."""
    # Mock git diff --numstat
    mock_run.side_effect = [
        MagicMock(stdout="50\t20\tfile1.py\n", returncode=0),  # numstat
        MagicMock(stdout="a" * 250000, returncode=0),  # 250KB patch for file1
    ]

    global get_diff_stats
    stats = get_diff_stats(Path("/tmp"), "base", "head")

    assert len(stats) == 1
    assert stats[0].path == "file1.py"
    assert stats[0].added == 50
    assert stats[0].deleted == 20
    assert stats[0].size_bytes == 250000


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff_byte_limit(mock_run, mock_stats):
    """Test that files exceeding byte limit are marked as large."""
    mock_stats.return_value = [
        FileDiffStats("small.py", 10, 5, 1000),  # 1KB - small
        FileDiffStats("huge.txt", 50, 20, 300000)  # 300KB - large
    ]

    small_patch = "diff content"
    large_patch = "x" * 300000
    mock_run.side_effect = [
        MagicMock(stdout=small_patch, returncode=0),
        MagicMock(stdout=large_patch, returncode=0)
    ]

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(
        Path("/tmp"), "base", "head",
        max_lines=1500,
        max_bytes=200000,  # 200KB limit
        action="preview"
    )

    assert len(results) == 2
    assert results[0].is_large is False
    assert results[1].is_large is True  # Exceeds byte limit
    assert results[1].is_trimmed is True


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff_line_limit(mock_run, mock_stats):
    """Test that files exceeding line limit are marked as large."""
    mock_stats.return_value = [
        FileDiffStats("huge.py", 2000, 500, 50000)  # 2500 lines total
    ]

    large_patch = "\n".join([f"line {i}" for i in range(200)])
    mock_run.return_value = MagicMock(stdout=large_patch, returncode=0)

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(
        Path("/tmp"), "base", "head",
        max_lines=1500,
        max_bytes=200000,
        action="preview"
    )

    assert len(results) == 1
    assert results[0].is_large is True  # Exceeds line limit
    assert results[0].is_trimmed is True


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff_skip_action(mock_run, mock_stats):
    """Test skip action omits patch content entirely."""
    mock_stats.return_value = [
        FileDiffStats("wordlist.txt", 50000, 0, 1000000)
    ]

    mock_run.return_value = MagicMock(stdout="huge patch", returncode=0)

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(
        Path("/tmp"), "base", "head",
        action="skip"
    )

    assert len(results) == 1
    assert results[0].is_large is True
    assert results[0].is_trimmed is True
    assert "[PATCH SKIPPED" in results[0].patch


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff_binary_file(mock_run, mock_stats):
    """Test handling of binary files."""
    mock_stats.return_value = [
        FileDiffStats("image.png", 0, 0, 500000)
    ]

    # Binary diff shows "Binary files differ"
    mock_run.return_value = MagicMock(
        stdout="Binary files a/image.png and b/image.png differ",
        returncode=0
    )

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(
        Path("/tmp"), "base", "head",
        max_bytes=200000,
        action="preview"
    )

    assert len(results) == 1
    assert results[0].is_large is True  # Exceeds byte limit
    assert "Binary files" in results[0].patch or "[TRIMMED" in results[0].patch


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff_error_fetching_patch(mock_run, mock_stats):
    """Test graceful handling when git diff fails."""
    mock_stats.return_value = [
        FileDiffStats("missing.py", 10, 5, 1000)
    ]

    # Simulate git diff failure
    mock_run.side_effect = subprocess.CalledProcessError(1, "git diff", stderr="fatal: bad revision")

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(Path("/tmp"), "base", "head")

    assert len(results) == 1
    assert "[ERROR" in results[0].patch  # Should contain error marker


@patch.object(_diff_utils, "get_diff_stats")
@patch("subprocess.run")
def test_analyze_and_limit_diff_mixed_files(mock_run, mock_stats):
    """Test handling mixed set of small and large files."""
    mock_stats.return_value = [
        FileDiffStats("small1.py", 10, 5, 500),
        FileDiffStats("small2.py", 20, 10, 800),
        FileDiffStats("large1.txt", 2000, 0, 50000),
        FileDiffStats("large2.txt", 100, 50, 300000),
    ]

    mock_run.side_effect = [
        MagicMock(stdout="small patch 1", returncode=0),
        MagicMock(stdout="small patch 2", returncode=0),
        MagicMock(stdout="\n".join([f"line {i}" for i in range(200)]), returncode=0),
        MagicMock(stdout="x" * 300000, returncode=0),
    ]

    global analyze_and_limit_diff
    results = analyze_and_limit_diff(
        Path("/tmp"), "base", "head",
        max_lines=1500,
        max_bytes=200000,
        action="preview"
    )

    assert len(results) == 4
    # First two should be small and not trimmed
    assert results[0].is_large is False
    assert results[0].is_trimmed is False
    assert results[1].is_large is False
    assert results[1].is_trimmed is False
    # Last two should be large and trimmed
    assert results[2].is_large is True
    assert results[2].is_trimmed is True
    assert results[3].is_large is True
    assert results[3].is_trimmed is True
