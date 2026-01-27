"""Tests for health check utilities."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add the hodor package to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hodor.health import (
    check_git_available,
    check_gh_cli_available,
    check_glab_cli_available,
    check_llm_api_key,
    check_github_token,
    check_gitlab_token,
    check_disk_space,
    check_python_version,
    run_health_checks,
    validate_workspace,
    HealthCheckResult,
    HealthReport,
)

# For patching
import hodor.health as _module


class TestHealthCheckResult:
    """Tests for HealthCheckResult dataclass."""

    def test_passed_result(self):
        """Test passed health check result."""
        result = HealthCheckResult(
            name="Test",
            passed=True,
            message="All good",
        )
        assert result.passed
        assert "PASS" in str(result)

    def test_failed_required_result(self):
        """Test failed required health check result."""
        result = HealthCheckResult(
            name="Test",
            passed=False,
            message="Failed",
            required=True,
        )
        assert not result.passed
        assert "FAIL" in str(result)

    def test_failed_optional_result(self):
        """Test failed optional health check result."""
        result = HealthCheckResult(
            name="Test",
            passed=False,
            message="Warning",
            required=False,
        )
        assert not result.passed
        assert "WARN" in str(result)


class TestHealthReport:
    """Tests for HealthReport dataclass."""

    def test_all_passed(self):
        """Test report with all checks passed."""
        checks = [
            HealthCheckResult("A", True, "OK"),
            HealthCheckResult("B", True, "OK"),
        ]
        report = HealthReport(checks=checks)
        assert report.all_passed
        assert len(report.failed_checks) == 0

    def test_required_failed(self):
        """Test report with required check failed."""
        checks = [
            HealthCheckResult("A", True, "OK"),
            HealthCheckResult("B", False, "Failed", required=True),
        ]
        report = HealthReport(checks=checks)
        assert not report.all_passed
        assert len(report.failed_checks) == 1

    def test_optional_failed(self):
        """Test report with optional check failed."""
        checks = [
            HealthCheckResult("A", True, "OK"),
            HealthCheckResult("B", False, "Warning", required=False),
        ]
        report = HealthReport(checks=checks)
        assert report.all_passed  # Optional failures don't affect pass status
        assert len(report.warnings) == 1


class TestIndividualChecks:
    """Tests for individual health check functions."""

    @patch("subprocess.run")
    def test_check_git_available_success(self, mock_run):
        """Test git check when git is available."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="git version 2.40.0",
        )
        result = check_git_available()
        assert result.passed
        assert "2.40.0" in result.message

    @patch("subprocess.run")
    def test_check_git_available_not_found(self, mock_run):
        """Test git check when git is not found."""
        mock_run.side_effect = FileNotFoundError()
        result = check_git_available()
        assert not result.passed
        assert "not found" in result.message

    @patch("subprocess.run")
    def test_check_git_available_timeout(self, mock_run):
        """Test git check when command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("git", 10)
        result = check_git_available()
        assert not result.passed
        assert "timed out" in result.message

    @patch("subprocess.run")
    def test_check_gh_cli_available_success(self, mock_run):
        """Test gh check when gh is available."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gh version 2.30.0",
        )
        result = check_gh_cli_available()
        assert result.passed

    @patch("subprocess.run")
    def test_check_gh_cli_available_not_found(self, mock_run):
        """Test gh check when gh is not found."""
        mock_run.side_effect = FileNotFoundError()
        result = check_gh_cli_available()
        assert not result.passed
        assert "not found" in result.message

    def test_check_llm_api_key_found(self):
        """Test LLM API key check when key is present."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            result = check_llm_api_key()
            assert result.passed
            assert "ANTHROPIC_API_KEY" in result.message

    def test_check_llm_api_key_not_found(self):
        """Test LLM API key check when no key is present."""
        # Clear all API key env vars
        env_vars_to_clear = [
            "LLM_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
        ]
        with patch.dict(os.environ, {k: "" for k in env_vars_to_clear}, clear=False):
            for k in env_vars_to_clear:
                os.environ.pop(k, None)
            result = check_llm_api_key()
            assert not result.passed

    def test_check_github_token_found(self):
        """Test GitHub token check when token is present."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
            result = check_github_token()
            assert result.passed

    def test_check_gitlab_token_found(self):
        """Test GitLab token check when token is present."""
        with patch.dict(os.environ, {"GITLAB_TOKEN": "glpat-test"}):
            result = check_gitlab_token()
            assert result.passed

    def test_check_disk_space(self):
        """Test disk space check."""
        result = check_disk_space(min_gb=0.001)  # Very small requirement
        assert result.passed

    def test_check_python_version(self):
        """Test Python version check."""
        result = check_python_version()
        # Should pass if running on Python 3.13+
        import sys
        if sys.version_info >= (3, 13):
            assert result.passed
        else:
            assert not result.passed


class TestRunHealthChecks:
    """Tests for the run_health_checks function."""

    @patch.object(_module, "check_git_available")
    @patch.object(_module, "check_python_version")
    @patch.object(_module, "check_llm_api_key")
    @patch.object(_module, "check_gh_cli_available")
    @patch.object(_module, "check_github_token")
    @patch.object(_module, "check_glab_cli_available")
    @patch.object(_module, "check_gitlab_token")
    @patch.object(_module, "check_disk_space")
    def test_run_all_checks_pass(
        self,
        mock_disk,
        mock_gitlab_token,
        mock_glab,
        mock_github_token,
        mock_gh,
        mock_api_key,
        mock_python,
        mock_git,
    ):
        """Test running all health checks when all pass."""
        # Set up all mocks to return passing results
        mock_git.return_value = HealthCheckResult("Git", True, "OK")
        mock_python.return_value = HealthCheckResult("Python", True, "OK")
        mock_api_key.return_value = HealthCheckResult("API Key", True, "OK")
        mock_gh.return_value = HealthCheckResult("gh", True, "OK", required=False)
        mock_github_token.return_value = HealthCheckResult("GitHub Token", True, "OK", required=False)
        mock_glab.return_value = HealthCheckResult("glab", True, "OK", required=False)
        mock_gitlab_token.return_value = HealthCheckResult("GitLab Token", True, "OK", required=False)
        mock_disk.return_value = HealthCheckResult("Disk", True, "OK", required=False)

        report = run_health_checks()
        assert report.all_passed

    @patch.object(_module, "check_git_available")
    @patch.object(_module, "check_python_version")
    @patch.object(_module, "check_llm_api_key")
    def test_run_checks_with_platform_github(
        self, mock_api_key, mock_python, mock_git
    ):
        """Test running checks with GitHub platform specified."""
        mock_git.return_value = HealthCheckResult("Git", True, "OK")
        mock_python.return_value = HealthCheckResult("Python", True, "OK")
        mock_api_key.return_value = HealthCheckResult("API Key", True, "OK")

        with patch.object(_module, "check_gh_cli_available") as mock_gh:
            with patch.object(_module, "check_github_token") as mock_gh_token:
                mock_gh.return_value = HealthCheckResult("gh", True, "OK", required=True)
                mock_gh_token.return_value = HealthCheckResult("GitHub Token", True, "OK", required=True)

                report = run_health_checks(platform="github", skip_optional=True)
                # gh should be required for GitHub
                assert mock_gh.called


class TestValidateWorkspace:
    """Tests for workspace validation."""

    def test_validate_workspace_not_exists(self, tmp_path):
        """Test validation of non-existent workspace."""
        result = validate_workspace(str(tmp_path / "nonexistent"))
        assert not result.passed
        assert "does not exist" in result.message

    def test_validate_workspace_not_git(self, tmp_path):
        """Test validation of non-git workspace."""
        workspace = tmp_path / "not-git"
        workspace.mkdir()
        result = validate_workspace(str(workspace))
        assert not result.passed
        assert "not a git repository" in result.message

    def test_validate_workspace_valid(self, tmp_path):
        """Test validation of valid git workspace."""
        workspace = tmp_path / "valid-git"
        workspace.mkdir()
        (workspace / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = validate_workspace(str(workspace))
            assert result.passed
