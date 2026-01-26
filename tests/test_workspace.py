"""Tests for workspace management functions."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import os


def test_cleanup_workspace_success():
    """Test successful workspace cleanup."""
    from hodor.workspace import cleanup_workspace

    # Create a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "test_workspace"
        workspace.mkdir()
        (workspace / "test_file.txt").write_text("test")

        # Should not raise
        cleanup_workspace(workspace)

        # Workspace should be gone
        assert not workspace.exists()


def test_cleanup_workspace_nonexistent_dir():
    """Test cleanup of non-existent directory doesn't raise."""
    from hodor.workspace import cleanup_workspace

    workspace = Path("/nonexistent/path/that/does/not/exist")

    # Should not raise even if directory doesn't exist
    cleanup_workspace(workspace)


def test_cleanup_workspace_permission_denied():
    """Test cleanup continues (warning only) when permission denied."""
    from hodor.workspace import cleanup_workspace

    # Create a temp directory manually
    tmpdir = tempfile.mkdtemp()
    workspace = Path(tmpdir)

    try:
        # Mock rmtree to simulate permission denied
        with patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
            # Should NOT raise - just log warning
            cleanup_workspace(workspace)
    finally:
        # Clean up manually since rmtree was mocked
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_cleanup_workspace_oserror():
    """Test cleanup continues (warning only) on OSError."""
    from hodor.workspace import cleanup_workspace

    # Create a temp directory manually
    tmpdir = tempfile.mkdtemp()
    workspace = Path(tmpdir)

    try:
        # Mock rmtree to simulate device busy
        with patch("shutil.rmtree", side_effect=OSError("Device or resource busy")):
            # Should NOT raise - just log warning
            cleanup_workspace(workspace)
    finally:
        # Clean up manually since rmtree was mocked
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_cleanup_workspace_logs_warning_on_error(caplog):
    """Test that cleanup logs a warning on error."""
    from hodor.workspace import cleanup_workspace
    import logging

    # Create a temp directory manually
    tmpdir = tempfile.mkdtemp()
    workspace = Path(tmpdir)

    try:
        # Mock rmtree to simulate permission denied
        with patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
            with caplog.at_level(logging.WARNING):
                cleanup_workspace(workspace)

        # Should have logged a warning
        assert any("Failed to cleanup" in record.message for record in caplog.records)
    finally:
        # Clean up manually since rmtree was mocked
        import shutil
        if workspace.exists():
            shutil.rmtree(workspace)


def test_cleanup_workspace_not_a_directory():
    """Test cleanup handles path that is not a directory."""
    from hodor.workspace import cleanup_workspace

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Should not raise even if path is a file
        cleanup_workspace(tmp_path)
        # File should still exist (cleanup only removes directories)
        assert tmp_path.exists()
    finally:
        tmp_path.unlink()
