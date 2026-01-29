"""Core agent for PR review using OpenHands SDK."""

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from openhands.sdk import Conversation
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.event import Event
from openhands.sdk.workspace import LocalWorkspace

from . import _tty as _terminal_safety  # noqa: F401
from .github import GitHubAPIError, fetch_github_pr_info, normalize_github_metadata
from .gitlab import (
    GitLabAPIError,
    fetch_gitlab_mr_info,
    post_gitlab_mr_comment,
    create_mr_discussion,
    get_latest_mr_diff_refs,
    get_merge_request_discussions,
)
from .llm import create_hodor_agent
from .metrics import MetricsCollector, check_memory_usage
from .prompts.pr_review_prompt import build_pr_review_prompt
from .review_parser import parse_review_output, looks_like_valid_json_with_findings
from .skills import discover_skills
from .workspace import cleanup_workspace, setup_workspace
from .duplicate_detector import parse_existing_comments, is_duplicate_finding

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Default timeout for review (30 minutes)
DEFAULT_REVIEW_TIMEOUT = 1800


class ReviewTimeoutError(Exception):
    """Raised when a review operation times out."""
    pass


class StuckPatternError(Exception):
    """Raised when agent is stuck producing empty responses and nudge recovery failed."""
    pass


class ToolErrorLoopError(Exception):
    """Raised when agent is stuck in a loop of repeated tool errors."""
    pass


class ParsingFailedError(Exception):
    """Raised when JSON parsing appears to have failed despite valid-looking output."""
    pass


# Threshold for detecting tool error loop: consecutive identical tool errors
TOOL_ERROR_LOOP_THRESHOLD = 3


class TimeoutHandler:
    """Context manager for signal-based timeout on Unix systems.

    LIMITATIONS:
    - Only works on Unix/Linux/macOS (not Windows)
    - Only works on the main thread
    - Uses SIGALRM which can interrupt at arbitrary points

    On Windows or non-main threads, the timeout is silently skipped
    and a warning is logged.
    """

    def __init__(self, timeout_seconds: int, message: str = "Operation timed out"):
        self.timeout_seconds = timeout_seconds
        self.message = message
        self._old_handler = None
        self._timeout_active = False

    def _timeout_handler(self, signum: int, frame: Any) -> None:
        raise ReviewTimeoutError(f"{self.message} after {self.timeout_seconds}s")

    def __enter__(self) -> "TimeoutHandler":
        if self.timeout_seconds > 0:
            # Check for Windows - signal.SIGALRM doesn't exist on Windows
            import sys
            if sys.platform == "win32":
                logger.warning(
                    "Signal-based timeout not supported on Windows. "
                    "Review may run without timeout protection."
                )
                return self

            # Only set signal handler on main thread
            try:
                self._old_handler = signal.signal(signal.SIGALRM, self._timeout_handler)
                signal.alarm(self.timeout_seconds)
                self._timeout_active = True
            except ValueError:
                # Not on main thread, skip signal-based timeout
                logger.warning(
                    "Cannot set timeout (not on main thread). "
                    "Review may run without timeout protection."
                )
        return self

    def __exit__(self, *args: Any) -> None:
        if self._timeout_active and self._old_handler is not None:
            signal.alarm(0)  # Cancel the alarm
            signal.signal(signal.SIGALRM, self._old_handler)
            self._timeout_active = False

# Threshold for detecting stuck pattern: consecutive empty MessageActions
STUCK_PATTERN_THRESHOLD = 3

# Maximum number of nudge attempts before giving up
MAX_NUDGE_ATTEMPTS = 2

# Default nudge prompt to unstick the agent
DEFAULT_NUDGE_PROMPT = """You appear to have paused without producing output. Please continue your code review.

If your previous search commands returned no results, that's normal - the pattern may not exist in this codebase.
Please proceed with your review by:
1. Examining the actual changed files in the diff
2. Looking for issues in the code that was modified
3. When ready, output your final review in the required JSON format

Remember: Your task is to review the code changes, not to find specific patterns. Focus on the diff and provide your findings."""

Platform = Literal["github", "gitlab"]


def detect_platform(pr_url: str) -> Platform:
    """Detect the platform (GitHub or GitLab) from the PR URL."""
    parsed = urlparse(pr_url)
    hostname = parsed.hostname or ""

    # Check for GitLab-specific patterns first (works for both gitlab.com and self-hosted)
    if "/-/merge_requests/" in pr_url or "gitlab" in hostname:
        return "gitlab"
    # Check for GitHub-specific patterns
    elif "/pull/" in pr_url or "github" in hostname:
        return "github"
    else:
        logger.debug(f"Unknown platform for URL {pr_url}, defaulting to GitHub")
        return "github"


def parse_pr_url(pr_url: str) -> tuple[str, str, int, str]:
    """
    Parse PR/MR URL to extract owner, repo, PR/MR number, and host.

    Examples:
        GitHub: https://github.com/owner/repo/pull/123 -> ('owner', 'repo', 123, 'github.com')
        GitLab: https://gitlab.com/owner/repo/-/merge_requests/123 -> ('owner', 'repo', 123, 'gitlab.com')
        Self-hosted: https://gitlab.example.com/group/repo/-/merge_requests/118 -> ('group', 'repo', 118, 'gitlab.example.com')
    """
    parsed = urlparse(pr_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    host = parsed.netloc

    # GitHub format: /owner/repo/pull/123
    if len(path_parts) >= 4 and path_parts[2] == "pull":
        owner = path_parts[0]
        repo = path_parts[1]
        pr_number = int(path_parts[3])
        return owner, repo, pr_number, host

    # GitLab format: /group/subgroup/repo/-/merge_requests/123
    elif "merge_requests" in path_parts:
        mr_index = path_parts.index("merge_requests")
        if mr_index < 2 or mr_index + 1 >= len(path_parts):
            raise ValueError(f"Invalid GitLab MR URL format: {pr_url}. Expected .../-/merge_requests/<number>")
        if path_parts[mr_index - 1] != "-":
            raise ValueError(f"Invalid GitLab MR URL format: {pr_url}. Missing '/-/' segment before merge_requests.")

        repo = path_parts[mr_index - 2]
        owner_parts = path_parts[: mr_index - 2]
        owner = "/".join(owner_parts) if owner_parts else path_parts[0]
        pr_number = int(path_parts[mr_index + 1])
        return owner, repo, pr_number, host

    else:
        raise ValueError(
            f"Invalid PR/MR URL format: {pr_url}. Expected GitHub pull request or GitLab merge request URL."
        )


def post_review_comment(
        pr_url: str,
        review_text: str,
        model: str | None = None,
) -> dict[str, Any]:
    """
    Post a review comment on a GitHub PR or GitLab MR using CLI tools.

    Args:
        pr_url: URL of the pull request or merge request
        review_text: The review text to post as a comment
        model: LLM model used for the review (optional, for transparency)

    Returns:
        Dictionary with comment posting result
    """
    platform = detect_platform(pr_url)
    logger.info(f"Posting comment to {platform} PR/MR: {pr_url}")

    try:
        owner, repo, pr_number, host = parse_pr_url(pr_url)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Append model information to review text for transparency
    if model:
        review_text_with_footer = f"{review_text}\n\n---\n\n*Review generated by Hodor using `{model}`*"
    else:
        review_text_with_footer = review_text

    try:
        if platform == "github":
            # Use gh CLI to post comment
            subprocess.run(
                [
                    "gh",
                    "pr",
                    "review",
                    str(pr_number),
                    "--repo",
                    f"{owner}/{repo}",
                    "--comment",
                    "--body",
                    review_text_with_footer,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"Successfully posted review to GitHub PR #{pr_number}")
            return {"success": True, "platform": "github", "pr_number": pr_number}

        elif platform == "gitlab":
            # Try to post inline discussions first (if structured data is available)
            # The inline poster handles parsing and verification of JSON data
            if _post_gitlab_inline_review(owner, repo, pr_number, review_text, host):
                logger.info(f"Successfully posted inline review to GitLab MR !{pr_number} on {owner}/{repo}")
                return {"success": True, "platform": "gitlab", "mr_number": pr_number}

            # Fallback to single comment (legacy behavior or if parsing failed/no findings)
            post_gitlab_mr_comment(
                owner,
                repo,
                pr_number,
                review_text_with_footer,
                host=host,
            )
            logger.info(f"Successfully posted review comment to GitLab MR !{pr_number} on {owner}/{repo}")
            return {"success": True, "platform": "gitlab", "mr_number": pr_number}

        else:
            return {"success": False, "error": f"Unsupported platform: {platform}"}

    except GitLabAPIError as e:
        logger.error(f"Failed to post GitLab comment: {e}")
        return {"success": False, "error": str(e)}
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to post GitHub comment: {e}")
        logger.error(f"Command output: {e.stderr if hasattr(e, 'stderr') else 'N/A'}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error posting comment: {str(e)}")
        return {"success": False, "error": str(e)}


def _post_gitlab_inline_review(
        owner: str,
        repo: str,
        mr_number: int,
        review_output: str,
        host: str | None,
) -> bool:
    """Helper to post inline GitLab discussions from review output."""
    # Note: gitlab helper functions are already imported at module level

    # Parse the output
    parsed = parse_review_output(review_output)

    logger.info(
        f"Parsed review output: {len(parsed.findings)} findings, explanation length: {len(parsed.overall_explanation or '')}")

    # If no findings and no explanation, it might be a valid "no issues" JSON
    if not parsed.findings and not parsed.overall_explanation:
        # If the output contains "findings", it likely was a valid JSON with empty list
        if "findings" in review_output:
            logger.info("Valid empty findings detected. Posting LGTM.")
            post_gitlab_mr_comment(
                owner,
                repo,
                mr_number,
                "**Review Complete**\n\nNo issues found! 🎉",
                host=host,
            )
            return True

        logger.warning("No findings or explanation parsed from model output.")
        return False

    if not parsed.findings and parsed.overall_explanation:
        logger.warning(
            "Findings list is empty. If the model produced JSON, parsing might have failed or fallen back to raw text.")
        logger.warning(f"Raw output sample (first 500 chars): {review_output[:500]!r}")
        if len(review_output) > 500:
            logger.warning(f"...Raw output tail (last 500 chars): {review_output[-500:]!r}")

    # Get diff refs once
    diff_refs = None
    try:
        diff_refs = get_latest_mr_diff_refs(owner, repo, mr_number, host)
    except Exception as e:
        logger.warning(f"Failed to get diff refs: {e}")

    # Fetch existing discussions for deduplication
    existing_discussions = []
    try:
        existing_discussions = get_merge_request_discussions(owner, repo, mr_number, host=host)
    except Exception as e:
        logger.warning(f"Failed to fetch existing discussions for deduplication: {e}")

    # Parse existing comments into normalized format for duplicate detection
    existing_comments = parse_existing_comments(existing_discussions, platform="gitlab")

    posted_count = 0
    skipped_count = 0
    errors = []

    # Post findings as discussions
    for finding in parsed.findings:
        file_path = str(finding.code_location.absolute_file_path)
        start_line = finding.code_location.line_range.start
        title = finding.title

        # Build finding dict for duplicate check
        new_finding = {
            "path": file_path,
            "line": start_line,
            "title": title,
            "body": finding.body,
        }

        # Check for duplicates using improved fuzzy matching
        if is_duplicate_finding(new_finding, existing_comments):
            logger.info(f"Skipping duplicate finding at {file_path}:{start_line} ('{title}')")
            skipped_count += 1
            continue

        body = f"**{finding.title}**\n\n{finding.body}"

        try:
            create_mr_discussion(
                owner,
                repo,
                mr_number,
                body,
                file_path=file_path,
                line=start_line,
                side="new",  # Default to new
                diff_refs=diff_refs,
                host=host,
            )
            posted_count += 1
            # Add to existing comments to avoid duplicates within same run
            existing_comments.append({
                "path": file_path,
                "line": start_line,
                "body": body,
            })
        except Exception as e:
            errors.append(str(e))
            logger.error(f"Failed to post finding at {file_path}:{start_line}: {e}")

    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} duplicate findings")

    # If there's an overall explanation/verdict
    if parsed.overall_correctness or parsed.overall_explanation:
        summary_body = ""
        if parsed.overall_correctness:
            status = "correct" if parsed.overall_correctness == "patch is correct" else "blocking issues"
            summary_body += f"**Review Status**: {status}\n\n"

        if parsed.overall_explanation:
            summary_body += parsed.overall_explanation

        if summary_body:
            # Always post summary as a top-level comment (not a thread) per user request
            logger.info("Posting summary as a general comment.")
            post_gitlab_mr_comment(owner, repo, mr_number, summary_body, host=host)

    if errors:
        logger.warning(f"Encountered {len(errors)} errors while posting findings.")

    return True  # We attempted structured posting


def review_pr(
        pr_url: str,
        model: str = "anthropic/claude-sonnet-4-5-20250929",
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        custom_prompt: str | None = None,
        prompt_file: Path | None = None,
        user_llm_params: dict[str, Any] | None = None,
        verbose: bool = False,
        cleanup: bool = True,
        workspace_dir: Path | None = None,
        output_format: str = "markdown",
        max_iterations: int = 100,
        max_diff_lines: int = 1500,
        max_diff_bytes: int = 200000,
        large_diff_action: str = "preview",
        fail_on_error: bool = False,
        timeout: int = DEFAULT_REVIEW_TIMEOUT,
        max_retries_when_stuck: int = 1,
        max_retries_on_parse_failure: int = 1,
) -> str:
    """
    Review a pull request using OpenHands agent with bash tools.

    Args:
        pr_url: URL of the pull request or merge request
        model: LLM model name (default: Claude Sonnet 4.5)
        temperature: Sampling temperature (if None, auto-selected)
        reasoning_effort: For reasoning models: "low", "medium", or "high"
        custom_prompt: Optional custom prompt text (inline)
        prompt_file: Optional path to custom prompt file
        user_llm_params: Additional LLM parameters
        verbose: Enable verbose logging
        cleanup: Clean up workspace after review (default: True)
        workspace_dir: Directory to use for workspace (if None, creates temp dir). Reuses if same repo.
        output_format: Output format - "markdown" or "json" (default: "markdown")
        max_iterations: Maximum number of agent iterations (default: 100, use -1 for unlimited)
        max_diff_lines: Maximum lines per file diff before trimming
        max_diff_bytes: Maximum bytes per file diff before trimming
        large_diff_action: Action for large diffs (skip, preview, sample, summarize)
        fail_on_error: If True, raise exception on review failure instead of fallback
        timeout: Maximum time in seconds for the review (default: 1800 = 30 minutes)
        max_retries_when_stuck: Maximum retries when stuck pattern detected (default: 1, 0 to disable)
        max_retries_on_parse_failure: Maximum retries when JSON parsing fails (default: 1, 0 to disable)

    Returns:
        Review text as string (format depends on output_format)

    Raises:
        ValueError: If URL is invalid
        RuntimeError: If review fails and fail_on_error is True, or if no meaningful content after retries
        ReviewTimeoutError: If review exceeds timeout
        StuckPatternError: If stuck pattern persists after all retries (only if fail_on_error is True)
    """
    logger.info(f"Starting PR review for: {pr_url}")

    # Parse PR URL (done once, outside retry loop)
    try:
        owner, repo, pr_number, host = parse_pr_url(pr_url)
        platform = detect_platform(pr_url)
    except ValueError as e:
        logger.error(f"Invalid PR URL: {e}")
        raise

    logger.info(f"Platform: {platform}, Repo: {owner}/{repo}, PR: {pr_number}, Host: {host}")

    # Initialize metrics collector (tracks across retries)
    metrics = MetricsCollector(
        pr_url=pr_url,
        platform=platform,
        model=model,
    )

    # Track state for recovery after all retries exhausted
    last_conversation = None
    last_workspace = None
    last_diff_base_sha = None
    last_error = None

    # Event callback for monitoring agent progress (defined once, used across retries)
    def on_event(event: Any) -> None:
        """Callback for streaming agent events in verbose mode."""
        if not verbose:
            return

        event_type = type(event).__name__

        # Log LLM API calls (for detailed token/cost tracking)
        if isinstance(event, Event):
            # This captures raw LLM messages for detailed analysis
            # Useful for debugging prompt engineering or cost optimization
            logger.debug(f"🤖 LLM Event: {event_type}")

        # Log agent actions
        if hasattr(event, "action") and event.action:
            action_type = type(event.action).__name__
            if action_type == "ExecuteBashAction":
                logger.info(f"🔧 Executing: {event.action.command[:100]}")
            elif action_type == "FileEditAction":
                logger.info(f"✏️  Editing file: {getattr(event.action, 'file_path', 'unknown')}")
            elif action_type == "MessageAction":
                logger.info(f"💬 Agent thinking...")

        # Log observations (results)
        if hasattr(event, "observation") and event.observation:
            obs_type = type(event.observation).__name__
            if obs_type == "ExecuteBashObservation" and hasattr(event.observation, "exit_code"):
                exit_code = event.observation.exit_code
                status = "✓" if exit_code == 0 else "✗"
                logger.info(f"   {status} Exit code: {exit_code}")

        # Log errors
        if hasattr(event, "error") and event.error:
            logger.warning(f"⚠️  Error: {event.error}")

    start_time = time.time()

    # Retry loop for stuck pattern recovery
    total_attempts = max_retries_when_stuck + 1
    for attempt in range(total_attempts):
        is_last_attempt = (attempt == max_retries_when_stuck)

        if attempt > 0:
            logger.info(f"🔄 Retry attempt {attempt}/{max_retries_when_stuck} after stuck pattern...")
            # Clean up previous attempt's workspace before retrying
            if last_workspace and cleanup:
                logger.info("Cleaning up previous attempt's workspace...")
                cleanup_workspace(last_workspace)
                last_workspace = None

        # Setup workspace (clone repo and checkout PR branch)
        workspace = None
        target_branch = "main"  # Default fallback
        diff_base_sha = None  # GitLab CI provides this for deterministic diffs
        try:
            workspace, target_branch, diff_base_sha = setup_workspace(
                platform=platform,
                owner=owner,
                repo=repo,
                pr_number=str(pr_number),
                host=host,
                working_dir=workspace_dir,
                reuse=workspace_dir is not None,  # Only reuse if user specified a workspace dir
            )
            logger.info(
                f"Workspace ready: {workspace} (target branch: {target_branch}, "
                f"diff_base_sha: {diff_base_sha[:8] if diff_base_sha else 'N/A'})"
            )
            last_workspace = workspace
            last_diff_base_sha = diff_base_sha
        except Exception as e:
            logger.error(f"Failed to setup workspace: {e}")
            raise RuntimeError(f"Failed to setup workspace: {e}") from e

        # Discover repository skills (from .cursorrules, agents.md, .hodor/skills/)
        skills = []
        try:
            skills = discover_skills(workspace)
            if skills:
                logger.info(f"Discovered {len(skills)} repository skill(s)")
            else:
                logger.debug("No repository skills found")
        except Exception as e:
            logger.warning(f"Failed to discover skills (continuing without skills): {e}")

        # Create OpenHands agent with repository skills
        try:
            agent = create_hodor_agent(
                model=model,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                verbose=verbose,
                llm_overrides=user_llm_params,
                skills=skills,
            )
        except Exception as e:
            logger.error(f"Failed to create OpenHands agent: {e}")
            if workspace and cleanup:
                cleanup_workspace(workspace)
            raise RuntimeError(f"Failed to create agent: {e}") from e

        mr_metadata = None
        if platform == "gitlab":
            try:
                mr_metadata = fetch_gitlab_mr_info(owner, repo, pr_number, host, include_comments=True)
            except GitLabAPIError as e:
                logger.warning(f"Failed to fetch GitLab metadata: {e}")
        elif platform == "github":
            try:
                github_raw = fetch_github_pr_info(owner, repo, pr_number)
                mr_metadata = normalize_github_metadata(github_raw)
            except GitHubAPIError as e:
                logger.warning(f"Failed to fetch GitHub metadata: {e}")

        # Build prompt
        try:
            prompt = build_pr_review_prompt(
                pr_url=pr_url,
                owner=owner,
                repo=repo,
                pr_number=str(pr_number),
                platform=platform,
                target_branch=target_branch,
                diff_base_sha=diff_base_sha,
                mr_metadata=mr_metadata,
                custom_instructions=custom_prompt,
                custom_prompt_file=prompt_file,
                output_format=output_format,
                workspace_path=workspace,
                max_diff_lines=max_diff_lines,
                max_diff_bytes=max_diff_bytes,
                large_diff_action=large_diff_action,
            )
        except Exception as e:
            logger.error(f"Failed to build prompt: {e}")
            if workspace and cleanup:
                cleanup_workspace(workspace)
            raise RuntimeError(f"Failed to build prompt: {e}") from e

        conversation = None  # Initialize for exception handler

        try:
            logger.info("Creating OpenHands conversation...")
            metrics.start_phase("conversation_setup")
            workspace_obj = LocalWorkspace(working_dir=str(workspace))

            iteration_limit = 1_000_000 if max_iterations == -1 else max_iterations

            # Collect secrets to mask in agent output (prevents accidental exposure)
            secrets: dict[str, str] = {}
            for env_var in [
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "LLM_API_KEY",
                "GITHUB_TOKEN",
                "GITLAB_TOKEN",
                "GITLAB_PRIVATE_TOKEN",
                "CI_JOB_TOKEN",
            ]:
                val = os.getenv(env_var)
                if val:
                    secrets[env_var] = val

            conversation = Conversation(
                agent=agent,
                workspace=workspace_obj,
                callbacks=[on_event] if verbose else None,
                max_iteration_per_run=iteration_limit,
                secrets=secrets if secrets else None,
            )
            metrics.end_phase("conversation_setup")

            logger.info("Sending prompt to agent...")
            conversation.send_message(prompt)

            logger.info(f"Running agent review (timeout: {timeout}s)...")
            metrics.start_phase("agent_run")

            # Check memory before starting
            mem_mb, mem_warning = check_memory_usage()
            if mem_warning:
                logger.warning(f"Starting review with high memory usage: {mem_mb:.1f}MB")

            # Use nudge recovery to handle stuck patterns, with timeout
            # run_with_nudge_recovery raises StuckPatternError or RuntimeError on failure
            with TimeoutHandler(timeout, "Review timed out"):
                review_content = run_with_nudge_recovery(conversation)

            metrics.end_phase("agent_run")

            # Calculate review time
            review_time_seconds = time.time() - start_time
            review_time_str = f"{int(review_time_seconds // 60)}m {int(review_time_seconds % 60)}s"

            logger.info(f"Review complete ({len(review_content)} chars)")

            # Always print metrics (not just in verbose mode)
            # Access metrics via conversation.conversation_stats (SDK API)
            if hasattr(conversation, "conversation_stats"):
                try:
                    combined = conversation.conversation_stats.get_combined_metrics()

                    if combined and combined.accumulated_token_usage:
                        # Token usage breakdown from Metrics object
                        usage = combined.accumulated_token_usage
                        prompt_tokens = usage.prompt_tokens or 0
                        completion_tokens = usage.completion_tokens or 0
                        cache_read_tokens = usage.cache_read_tokens or 0
                        cache_write_tokens = usage.cache_write_tokens or 0
                        reasoning_tokens = usage.reasoning_tokens or 0
                        total_tokens = prompt_tokens + completion_tokens + cache_read_tokens + reasoning_tokens

                        # Cost estimate
                        cost = combined.accumulated_cost or 0

                        # Record to metrics collector
                        metrics.record_token_usage(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            cache_read_tokens=cache_read_tokens,
                            cache_write_tokens=cache_write_tokens,
                            reasoning_tokens=reasoning_tokens,
                        )
                        metrics.record_cost(cost)

                        # Calculate cache hit rate
                        cache_hit_rate = 0
                        if cache_read_tokens > 0 and (prompt_tokens + cache_read_tokens) > 0:
                            cache_hit_rate = (cache_read_tokens / (prompt_tokens + cache_read_tokens)) * 100

                        # Print metrics (always, not just verbose)
                        print("\n" + "=" * 60)
                        print("📊 Token Usage Metrics:")
                        print(f"  • Input tokens:       {prompt_tokens:,}")
                        print(f"  • Output tokens:      {completion_tokens:,}")
                        if cache_read_tokens > 0:
                            print(f"  • Cache hits:         {cache_read_tokens:,} ({cache_hit_rate:.1f}%)")
                        if reasoning_tokens > 0:
                            print(f"  • Reasoning tokens:   {reasoning_tokens:,}")
                        print(f"  • Total tokens:       {total_tokens:,}")
                        print(f"\n💰 Cost Estimate:      ${cost:.4f}")
                        print(f"⏱️  Review Time:        {review_time_str}")
                        print("=" * 60 + "\n")

                        # Verbose mode: additional details
                        if verbose:
                            if cache_write_tokens > 0:
                                logger.info(f"  • Cache writes:       {cache_write_tokens:,}")
                            if combined.response_latencies:
                                avg_latency = sum(lat.latency for lat in combined.response_latencies) / len(
                                    combined.response_latencies
                                )
                                logger.info(f"  • Avg API latency:    {avg_latency:.2f}s")
                except Exception as e:
                    logger.warning(f"Failed to get metrics: {e}")

            # Check for parsing failure before declaring success
            # If the output looks like it has findings but parsing returns empty, retry
            parsed = parse_review_output(review_content)
            if (
                not parsed.findings
                and looks_like_valid_json_with_findings(review_content)
                and max_retries_on_parse_failure > 0
            ):
                logger.warning(
                    "JSON parsing returned 0 findings but output appears to contain valid findings. "
                    "This may indicate truncation or encoding issues."
                )
                logger.warning(f"Raw output sample (first 500 chars): {review_content[:500]!r}")
                raise ParsingFailedError(
                    "Valid-looking JSON with findings parsed to empty list"
                )

            # Record success and finalize metrics
            metrics.record_success()
            final_metrics = metrics.finalize()
            logger.info(f"Review metrics: {final_metrics.to_dict()}")

            # Clean up workspace on success
            if workspace and cleanup:
                logger.info("Cleaning up workspace...")
                cleanup_workspace(workspace)

            # Reset terminal state
            _terminal_safety.restore_terminal_state()

            return review_content

        except ParsingFailedError as e:
            # Parsing failed despite valid-looking output - retry
            last_conversation = conversation
            last_error = e
            max_retries_on_parse_failure -= 1  # Decrement retry counter

            if max_retries_on_parse_failure >= 0 and not is_last_attempt:
                logger.warning(f"Parsing failed on attempt {attempt + 1}: {e}")
                continue
            else:
                logger.error(f"Parsing failure persists after retries: {e}")
                # Return the raw content anyway - let caller decide what to do
                if workspace and cleanup:
                    cleanup_workspace(workspace)
                _terminal_safety.restore_terminal_state()
                return review_content

        except StuckPatternError as e:
            # Save conversation for potential recovery after all retries exhausted
            last_conversation = conversation
            last_error = e

            if not is_last_attempt:
                logger.warning(f"Stuck pattern detected on attempt {attempt + 1}: {e}")
                # Don't clean up here - cleanup happens at start of next iteration
                continue
            else:
                logger.error(f"Stuck pattern persists after {total_attempts} attempt(s): {e}")
                # Fall through to post-loop fallback handling
                break

        except ToolErrorLoopError as e:
            # Tool error loops should trigger retry (the model may behave differently on retry)
            last_conversation = conversation
            last_error = e

            if not is_last_attempt:
                logger.warning(f"Tool error loop detected on attempt {attempt + 1}: {e}")
                # Don't clean up here - cleanup happens at start of next iteration
                continue
            else:
                logger.error(f"Tool error loop persists after {total_attempts} attempt(s): {e}")
                # Fall through to post-loop fallback handling
                break

        except ReviewTimeoutError as e:
            # Don't retry on timeout - it's likely a systemic issue
            logger.error(f"Review timed out: {e}")
            last_error = e
            last_conversation = conversation
            break

        except Exception as e:
            # Don't retry on other errors (workspace setup, agent creation, etc.)
            logger.error(f"Review failed: {e}")
            last_error = e
            last_conversation = conversation
            break

    # --- Post-loop: All retries exhausted or non-retryable error ---
    # Reset terminal state
    _terminal_safety.restore_terminal_state()

    # Try to recover partial content from last conversation
    recovered_content = None
    if last_conversation is not None and hasattr(last_conversation, 'state'):
        try:
            logger.info("Attempting to recover partial review from conversation history...")
            recovered_content = _recover_last_json_response(last_conversation.state.events)
            if recovered_content:
                logger.info(f"Recovered {len(recovered_content)} chars from history")
        except Exception as recovery_error:
            logger.warning(f"Failed to recover partial review: {recovery_error}")

    # Clean up final workspace
    if last_workspace and cleanup:
        logger.info("Cleaning up workspace...")
        cleanup_workspace(last_workspace)

    # Handle based on whether we have meaningful content
    if recovered_content:
        # We have partial content - use it as fallback
        metrics.record_success()  # Partial success
        metrics.finalize()
        return recovered_content

    # No meaningful content recovered
    error_msg = str(last_error) if last_error else "Unknown error"

    # For StuckPatternError with no content: always fail (per user requirement)
    if isinstance(last_error, StuckPatternError):
        metrics.record_error(error_msg, fallback_used=False)
        metrics.finalize()
        raise RuntimeError(
            f"Review failed after {total_attempts} attempt(s) due to stuck pattern with no recoverable content: {error_msg}"
        )

    # For ToolErrorLoopError with no content: always fail
    if isinstance(last_error, ToolErrorLoopError):
        metrics.record_error(error_msg, fallback_used=False)
        metrics.finalize()
        raise RuntimeError(
            f"Review failed after {total_attempts} attempt(s) due to tool error loop with no recoverable content: {error_msg}"
        )

    # For timeout: generate fallback if not fail_on_error
    if isinstance(last_error, ReviewTimeoutError):
        metrics.record_error(error_msg, fallback_used=not fail_on_error)
        metrics.finalize()
        if fail_on_error:
            raise last_error
        logger.warning("Fail-soft: generating fallback review due to timeout")
        diff_stats = []
        if last_workspace and last_diff_base_sha:
            from .diff_utils import get_diff_stats
            diff_stats = get_diff_stats(last_workspace, last_diff_base_sha, "HEAD")
        return _generate_fallback_review(diff_stats, output_format, error_msg)

    # For other errors: generate fallback if not fail_on_error
    metrics.record_error(error_msg, fallback_used=not fail_on_error)
    metrics.finalize()
    if fail_on_error:
        raise RuntimeError(f"Review failed: {error_msg}") from last_error
    logger.warning(f"Fail-soft: generating fallback review due to error: {error_msg}")
    diff_stats = []
    if last_workspace and last_diff_base_sha:
        from .diff_utils import get_diff_stats
        diff_stats = get_diff_stats(last_workspace, last_diff_base_sha, "HEAD")
    return _generate_fallback_review(diff_stats, output_format, error_msg)


def _recover_last_json_response(events: list[Any]) -> str | None:
    """Scan events backwards for the last valid JSON response from the agent."""
    # Look for the last message from the agent
    # Use string-based type checking to be resilient to SDK changes and test mocking
    for event in reversed(events):
        # Check if event looks like an Event object (has action attribute)
        action = getattr(event, "action", None)
        if not action:
            continue

        # Check if action is a MessageAction
        if action.__class__.__name__ == "MessageAction":
            content = getattr(action, "content", "")
            if not content:
                continue

            # Check if it looks like JSON review
            if '{"findings":' in content or "```json" in content:
                return content

    return None


def _generate_fallback_review(diff_stats: list[Any], output_format: str, error_msg: str) -> str:
    """Generate a fail-soft fallback review when the agent fails."""
    import json

    summary = (
        f"⚠️ **Review Fallback**: The AI agent encountered an issue during a complete analysis ({error_msg}). "
        "A basic automated scan of the changed files was performed as a safety fallback."
    )

    findings = []

    # Analyze files for basic guidance
    large_files = [s for s in diff_stats if s.added + s.deleted > 1000]  # Use a heuristic for fallback

    if large_files:
        paths = ", ".join([f"`{s.path}`" for s in large_files])
        findings.append({
            "path": large_files[0].path,  # Assign to first one for schema compliance
            "line": 1,
            "title": "[P2] Large data or wordlists detected",
            "body": f"Large diffs detected in {paths}. Please verify source, licensing, encoding, and format. Ensure no secrets are present in these data files.",
            "priority": 2
        })

    if not findings and diff_stats:
        findings.append({
            "path": diff_stats[0].path,
            "line": 1,
            "title": "[P3] Automated Scan Complete",
            "body": f"Review agent failed to complete full analysis, but {len(diff_stats)} files were identified. Please review changes manually as a precaution.",
            "priority": 3
        })

    if output_format == "json":
        return json.dumps({
            "summary": summary,
            "findings": findings,
            "overall_correctness": "patch is correct",  # Fail soft: assume correct but warn
            "overall_explanation": "Automated fallback review generated due to agent failure."
        }, indent=2)
    else:
        lines = [f"### Issues Found\n"]
        for f in findings:
            lines.append(f"- **{f['title']}** (`{f['path']}:{f['line']}`)")
            lines.append(f"  {f['body']}\n")
        lines.append(f"### Summary\n{summary}\n")
        lines.append(f"### Overall Verdict\n**Status**: Patch is correct\n\n**Explanation**: {summary}")
        return "\n".join(lines)


def detect_stuck_pattern(events: list[Any]) -> tuple[bool, int]:
    """
    Detect if the agent is stuck producing consecutive empty responses.

    This function scans the event history to detect a "stuck pattern" where
    the LLM produces multiple consecutive empty MessageActions. This typically
    happens when the agent gets confused (e.g., after grep commands return no results).

    Args:
        events: List of conversation events from OpenHands SDK

    Returns:
        Tuple of (is_stuck: bool, consecutive_empty_count: int)
        - is_stuck is True if consecutive empty responses >= STUCK_PATTERN_THRESHOLD
        - consecutive_empty_count is the number of consecutive empty responses at the end
    """
    if not events:
        return False, 0

    consecutive_empty = 0

    # Scan events from the end to find consecutive empty MessageActions
    for event in reversed(events):
        action = getattr(event, "action", None)
        if not action:
            continue

        # Only count MessageActions (not bash commands, file reads, etc.)
        if action.__class__.__name__ != "MessageAction":
            # Non-message action breaks the streak
            break

        content = getattr(action, "content", None)

        # Check if content is empty or whitespace-only
        if content is None or (isinstance(content, str) and not content.strip()):
            consecutive_empty += 1
        else:
            # Non-empty message breaks the streak
            break

    is_stuck = consecutive_empty >= STUCK_PATTERN_THRESHOLD
    return is_stuck, consecutive_empty


def detect_tool_error_loop(events: list[Any]) -> tuple[bool, int, str | None]:
    """
    Detect if the agent is stuck in a loop of repeated tool errors.

    This function scans the event history to detect when the same tool error
    occurs multiple times consecutively, indicating the agent is stuck making
    invalid tool calls (e.g., "Cannot use reset=True with is_input=True").

    Args:
        events: List of conversation events from OpenHands SDK

    Returns:
        Tuple of (is_in_error_loop: bool, consecutive_error_count: int, error_message: str | None)
        - is_in_error_loop is True if consecutive identical errors >= TOOL_ERROR_LOOP_THRESHOLD
        - consecutive_error_count is the number of consecutive identical errors
        - error_message is the repeated error message (if in loop)
    """
    if not events:
        return False, 0, None

    consecutive_errors = 0
    last_error_message: str | None = None

    # Scan events from the end to find consecutive identical tool errors
    for event in reversed(events):
        # Check for error in the event
        error = getattr(event, "error", None)
        if not error:
            # Also check observation for tool execution errors
            observation = getattr(event, "observation", None)
            if observation:
                # Check for error_message attribute (tool execution errors)
                error = getattr(observation, "error_message", None)
                if not error:
                    # Check content for error patterns
                    content = getattr(observation, "content", "")
                    if isinstance(content, str) and "Error executing tool" in content:
                        error = content

        if error and isinstance(error, str):
            if last_error_message is None:
                last_error_message = error
                consecutive_errors = 1
            elif error == last_error_message or _errors_are_similar(error, last_error_message):
                consecutive_errors += 1
            else:
                # Different error breaks the streak
                break
        elif last_error_message is not None:
            # Non-error event after errors - check if we have enough
            break

    is_in_loop = consecutive_errors >= TOOL_ERROR_LOOP_THRESHOLD
    return is_in_loop, consecutive_errors, last_error_message if is_in_loop else None


def _errors_are_similar(error1: str, error2: str) -> bool:
    """Check if two error messages are similar enough to be considered the same error type."""
    # Extract key error patterns
    patterns = [
        "Cannot use reset=True with is_input=True",
        "Error executing tool",
        "terminal",
    ]
    for pattern in patterns:
        if pattern in error1 and pattern in error2:
            return True
    return False


def get_nudge_prompt(recent_events: list[Any] | None = None) -> str:
    """
    Generate a context-aware nudge prompt to unstick the agent.

    Analyzes recent events to provide relevant guidance. For example,
    if the agent was stuck after grep commands returned empty, the nudge
    will mention that no results is normal and to continue the review.

    Args:
        recent_events: Optional list of recent events for context

    Returns:
        A nudge prompt string to send to the agent
    """
    if not recent_events:
        return DEFAULT_NUDGE_PROMPT

    # Check if recent events include failed search commands
    has_failed_search = False
    for event in reversed(recent_events[-10:]):  # Check last 10 events
        action = getattr(event, "action", None)
        if action and action.__class__.__name__ == "ExecuteBashAction":
            command = getattr(action, "command", "")
            # Check if it's a search command
            if any(cmd in command.lower() for cmd in ["grep", "find", "rg", "ag", "git grep"]):
                # Check if it failed (exit code 1 typically means no matches)
                observation = getattr(event, "observation", None)
                if observation:
                    exit_code = getattr(observation, "exit_code", None)
                    if exit_code == 1:
                        has_failed_search = True
                        break

    if has_failed_search:
        return """Your search command returned no results, which is completely normal - the pattern may not exist in this codebase.

Please continue your code review by:
1. Focusing on the actual changed files shown in the diff
2. Analyzing the code modifications for potential issues
3. Looking for bugs, security issues, or code quality problems

When you've completed your analysis, output your final review in the required JSON format with your findings."""

    return DEFAULT_NUDGE_PROMPT


def run_with_nudge_recovery(
    conversation: Any,
    max_nudges: int = MAX_NUDGE_ATTEMPTS,
) -> Any | None:
    """
    Run the conversation with automatic nudge recovery for stuck patterns.

    If the agent gets stuck producing empty responses or in a tool error loop,
    this function will inject a nudge prompt and resume the conversation.
    It will attempt up to max_nudges times before giving up.

    Args:
        conversation: OpenHands Conversation object
        max_nudges: Maximum number of nudge attempts (default: MAX_NUDGE_ATTEMPTS)

    Returns:
        The review content if successful.

    Raises:
        StuckPatternError: If agent is stuck and nudge recovery failed.
        ToolErrorLoopError: If agent is stuck in repeated tool errors.
        RuntimeError: If no content produced but agent not stuck.
    """
    # Use module-level import for testability
    nudge_count = 0

    while nudge_count <= max_nudges:
        # Run the conversation
        conversation.run()

        # Check for review content
        review_content = get_agent_final_response(conversation.state.events)

        if review_content:
            if nudge_count > 0:
                logger.info(f"Successfully recovered after {nudge_count} nudge(s)")
            return review_content

        # Check for tool error loop first (more specific)
        is_in_error_loop, error_count, error_msg = detect_tool_error_loop(conversation.state.events)

        if is_in_error_loop:
            # Tool error loops are not recoverable via nudge - need to retry from scratch
            logger.warning(
                f"Tool error loop detected ({error_count} consecutive errors): {error_msg}"
            )
            raise ToolErrorLoopError(
                f"Agent stuck in tool error loop ({error_count} consecutive errors): {error_msg}"
            )

        # Check if stuck (empty responses)
        is_stuck, empty_count = detect_stuck_pattern(conversation.state.events)

        if not is_stuck:
            # Not stuck but no content - might be other issue
            logger.warning("No review content produced but agent not stuck")
            raise RuntimeError("Agent did not produce any review content")

        # We're stuck - try to nudge
        if nudge_count < max_nudges:
            nudge_count += 1
            nudge_prompt = get_nudge_prompt(conversation.state.events)
            logger.info(
                f"Stuck pattern detected ({empty_count} empty responses). "
                f"Sending nudge {nudge_count}/{max_nudges}..."
            )
            conversation.send_message(nudge_prompt)
        else:
            logger.warning(
                f"Stuck pattern persists after {max_nudges} nudge(s). "
                "Nudge recovery failed."
            )
            raise StuckPatternError(
                f"Agent stuck with {empty_count} consecutive empty responses after {max_nudges} nudge attempts"
            )
    return None
