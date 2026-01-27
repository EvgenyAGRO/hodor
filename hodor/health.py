"""Health checks and pre-flight validation for Hodor.

Provides utilities to verify system prerequisites before running reviews.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Platform = Literal["github", "gitlab"]


@dataclass
class HealthCheckResult:
    """Result of a single health check."""

    name: str
    passed: bool
    message: str
    required: bool = True

    def __str__(self) -> str:
        status = "PASS" if self.passed else ("FAIL" if self.required else "WARN")
        return f"[{status}] {self.name}: {self.message}"


@dataclass
class HealthReport:
    """Overall health check report."""

    checks: list[HealthCheckResult]

    @property
    def all_passed(self) -> bool:
        """Check if all required checks passed."""
        return all(check.passed for check in self.checks if check.required)

    @property
    def failed_checks(self) -> list[HealthCheckResult]:
        """Get list of failed required checks."""
        return [check for check in self.checks if not check.passed and check.required]

    @property
    def warnings(self) -> list[HealthCheckResult]:
        """Get list of failed optional checks (warnings)."""
        return [check for check in self.checks if not check.passed and not check.required]

    def __str__(self) -> str:
        lines = ["Health Check Report", "=" * 40]
        for check in self.checks:
            lines.append(str(check))
        lines.append("=" * 40)
        if self.all_passed:
            lines.append("All required checks passed!")
        else:
            lines.append(f"FAILED: {len(self.failed_checks)} required check(s) failed")
        return "\n".join(lines)


def check_git_available() -> HealthCheckResult:
    """Check if git is available."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            return HealthCheckResult(
                name="Git",
                passed=True,
                message=f"Available ({version})",
            )
        return HealthCheckResult(
            name="Git",
            passed=False,
            message="git command failed",
        )
    except FileNotFoundError:
        return HealthCheckResult(
            name="Git",
            passed=False,
            message="git not found in PATH",
        )
    except subprocess.TimeoutExpired:
        return HealthCheckResult(
            name="Git",
            passed=False,
            message="git command timed out",
        )
    except Exception as e:
        return HealthCheckResult(
            name="Git",
            passed=False,
            message=f"Error checking git: {e}",
        )


def check_gh_cli_available() -> HealthCheckResult:
    """Check if GitHub CLI (gh) is available."""
    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Extract version from first line
            version_line = result.stdout.strip().split("\n")[0]
            return HealthCheckResult(
                name="GitHub CLI (gh)",
                passed=True,
                message=f"Available ({version_line})",
                required=False,  # Only required for GitHub PRs
            )
        return HealthCheckResult(
            name="GitHub CLI (gh)",
            passed=False,
            message="gh command failed",
            required=False,
        )
    except FileNotFoundError:
        return HealthCheckResult(
            name="GitHub CLI (gh)",
            passed=False,
            message="gh not found in PATH. Install from https://cli.github.com",
            required=False,
        )
    except Exception as e:
        return HealthCheckResult(
            name="GitHub CLI (gh)",
            passed=False,
            message=f"Error checking gh: {e}",
            required=False,
        )


def check_glab_cli_available() -> HealthCheckResult:
    """Check if GitLab CLI (glab) is available."""
    try:
        result = subprocess.run(
            ["glab", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version_line = result.stdout.strip().split("\n")[0]
            return HealthCheckResult(
                name="GitLab CLI (glab)",
                passed=True,
                message=f"Available ({version_line})",
                required=False,  # Only required for GitLab MRs
            )
        return HealthCheckResult(
            name="GitLab CLI (glab)",
            passed=False,
            message="glab command failed",
            required=False,
        )
    except FileNotFoundError:
        return HealthCheckResult(
            name="GitLab CLI (glab)",
            passed=False,
            message="glab not found in PATH. Install from https://gitlab.com/gitlab-org/cli",
            required=False,
        )
    except Exception as e:
        return HealthCheckResult(
            name="GitLab CLI (glab)",
            passed=False,
            message=f"Error checking glab: {e}",
            required=False,
        )


def check_llm_api_key() -> HealthCheckResult:
    """Check if an LLM API key is configured."""
    # Check for various API key environment variables
    api_keys = [
        "LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ]

    for key_name in api_keys:
        if os.getenv(key_name):
            return HealthCheckResult(
                name="LLM API Key",
                passed=True,
                message=f"Found {key_name}",
            )

    return HealthCheckResult(
        name="LLM API Key",
        passed=False,
        message="No LLM API key found. Set one of: " + ", ".join(api_keys),
    )


def check_github_token() -> HealthCheckResult:
    """Check if GitHub token is configured."""
    if os.getenv("GITHUB_TOKEN"):
        return HealthCheckResult(
            name="GitHub Token",
            passed=True,
            message="GITHUB_TOKEN is set",
            required=False,
        )

    # Check if gh is authenticated
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return HealthCheckResult(
                name="GitHub Token",
                passed=True,
                message="gh CLI is authenticated",
                required=False,
            )
    except Exception:
        pass

    return HealthCheckResult(
        name="GitHub Token",
        passed=False,
        message="GITHUB_TOKEN not set and gh CLI not authenticated",
        required=False,
    )


def check_gitlab_token() -> HealthCheckResult:
    """Check if GitLab token is configured."""
    gitlab_tokens = ["GITLAB_TOKEN", "GITLAB_PRIVATE_TOKEN", "CI_JOB_TOKEN"]

    for token_name in gitlab_tokens:
        if os.getenv(token_name):
            return HealthCheckResult(
                name="GitLab Token",
                passed=True,
                message=f"Found {token_name}",
                required=False,
            )

    return HealthCheckResult(
        name="GitLab Token",
        passed=False,
        message="No GitLab token found. Set GITLAB_TOKEN with api scope",
        required=False,
    )


def check_disk_space(min_gb: float = 1.0) -> HealthCheckResult:
    """Check if there's enough disk space.

    Args:
        min_gb: Minimum required free space in GB
    """
    try:
        import tempfile

        # Check temp directory space
        temp_dir = tempfile.gettempdir()
        usage = shutil.disk_usage(temp_dir)
        free_gb = usage.free / (1024**3)

        if free_gb >= min_gb:
            return HealthCheckResult(
                name="Disk Space",
                passed=True,
                message=f"{free_gb:.1f}GB free in {temp_dir}",
                required=False,
            )
        else:
            return HealthCheckResult(
                name="Disk Space",
                passed=False,
                message=f"Only {free_gb:.1f}GB free (need {min_gb}GB)",
                required=False,
            )
    except Exception as e:
        return HealthCheckResult(
            name="Disk Space",
            passed=False,
            message=f"Could not check disk space: {e}",
            required=False,
        )


def check_python_version() -> HealthCheckResult:
    """Check Python version meets requirements."""
    import sys

    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version >= (3, 13):
        return HealthCheckResult(
            name="Python Version",
            passed=True,
            message=f"Python {version_str} (>=3.13 required)",
        )
    else:
        return HealthCheckResult(
            name="Python Version",
            passed=False,
            message=f"Python {version_str} is too old (>=3.13 required)",
        )


def run_health_checks(
    platform: Platform | None = None,
    skip_optional: bool = False,
) -> HealthReport:
    """Run all health checks.

    Args:
        platform: If specified, mark platform-specific checks as required
        skip_optional: Skip optional checks

    Returns:
        HealthReport with all check results
    """
    checks: list[HealthCheckResult] = []

    # Core checks (always run)
    checks.append(check_python_version())
    checks.append(check_git_available())
    checks.append(check_llm_api_key())

    if not skip_optional:
        checks.append(check_disk_space())

    # Platform-specific checks
    gh_check = check_gh_cli_available()
    gitlab_check = check_glab_cli_available()
    github_token_check = check_github_token()
    gitlab_token_check = check_gitlab_token()

    # Mark platform-specific checks as required if platform is known
    if platform == "github":
        gh_check.required = True
        github_token_check.required = True
        checks.append(gh_check)
        checks.append(github_token_check)
        if not skip_optional:
            checks.append(gitlab_check)
            checks.append(gitlab_token_check)
    elif platform == "gitlab":
        gitlab_token_check.required = True
        checks.append(gitlab_check)
        checks.append(gitlab_token_check)
        if not skip_optional:
            checks.append(gh_check)
            checks.append(github_token_check)
    else:
        # Platform not known yet, run all as optional
        if not skip_optional:
            checks.append(gh_check)
            checks.append(github_token_check)
            checks.append(gitlab_check)
            checks.append(gitlab_token_check)

    return HealthReport(checks=checks)


def validate_workspace(workspace_path: str) -> HealthCheckResult:
    """Validate a CI workspace is usable.

    Args:
        workspace_path: Path to the workspace

    Returns:
        HealthCheckResult indicating if workspace is valid
    """
    from pathlib import Path

    workspace = Path(workspace_path)

    if not workspace.exists():
        return HealthCheckResult(
            name="CI Workspace",
            passed=False,
            message=f"Workspace does not exist: {workspace_path}",
        )

    if not workspace.is_dir():
        return HealthCheckResult(
            name="CI Workspace",
            passed=False,
            message=f"Workspace is not a directory: {workspace_path}",
        )

    git_dir = workspace / ".git"
    if not git_dir.exists():
        return HealthCheckResult(
            name="CI Workspace",
            passed=False,
            message=f"Workspace is not a git repository: {workspace_path}",
        )

    # Check we can run git commands
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return HealthCheckResult(
                name="CI Workspace",
                passed=False,
                message=f"git status failed in workspace: {result.stderr}",
            )
    except Exception as e:
        return HealthCheckResult(
            name="CI Workspace",
            passed=False,
            message=f"Error running git in workspace: {e}",
        )

    return HealthCheckResult(
        name="CI Workspace",
        passed=True,
        message=f"Workspace is valid: {workspace_path}",
    )
