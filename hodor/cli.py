"""Command-line interface for Hodor PR Review Agent."""

import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

from . import _tty as _terminal_safety  # noqa: F401
from .agent import detect_platform, post_review_comment, review_pr, DEFAULT_REVIEW_TIMEOUT
from .health import run_health_checks
from .logging_config import setup_logging

console = Console()


def parse_llm_args(ctx, param, value):
    """Parse --llm arguments into a dictionary.

    Supports formats like:
    - --llm key=value
    - --llm flag  (sets to True)
    """
    if not value:
        return {}

    config = {}
    for arg in value:
        if "=" in arg:
            key, val = arg.split("=", 1)
            # Try to convert to appropriate type
            if val.lower() == "true":
                config[key] = True
            elif val.lower() == "false":
                config[key] = False
            elif val.replace(".", "", 1).replace("-", "", 1).isdigit():
                config[key] = float(val) if "." in val else int(val)
            else:
                config[key] = val
        else:
            config[arg] = True

    return config


@click.command()
@click.argument("pr_url")
@click.option(
    "--model",
    default="anthropic/claude-sonnet-4-5-20250929",
    help="LLM model to use. Recommended: anthropic/claude-sonnet-4-20250514, anthropic/claude-sonnet-4-5-20250929 (default), openai/gpt-5-2025-08-07, gemini/gemini-2.5-pro, deepseek/deepseek-chat, moonshot/kimi-k2-0711-preview. Supports any LiteLLM model (https://docs.litellm.ai/docs/providers).",
)
@click.option(
    "--temperature",
    default=None,
    type=float,
    help="LLM temperature (0.0-2.0). Auto-selected if not specified based on model capabilities.",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh"], case_sensitive=False),
    default=None,
    help="Reasoning effort level for extended thinking (low/medium/high/xhigh)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging (shows OpenHands agent activity)",
)
@click.option(
    "--llm",
    multiple=True,
    callback=parse_llm_args,
    help="Additional LLM parameters in key=value format (can be specified multiple times)",
)
@click.option(
    "--post/--no-post",
    default=False,
    help="Post the review directly to the PR/MR as a comment (useful for CI/CD). Default: no-post (print to stdout)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output structured JSON format instead of markdown (useful for CI/CD automation and parsing)",
)
@click.option(
    "--prompt",
    default=None,
    help="Custom inline prompt text (overrides default and any prompt file)",
)
@click.option(
    "--prompt-file",
    default=None,
    type=click.Path(exists=True),
    help="Path to file containing custom prompt instructions",
)
@click.option(
    "--workspace",
    default=None,
    type=click.Path(),
    help="Workspace directory to use (creates temp dir if not specified). Reuses workspace if same repo.",
)
@click.option(
    "--max-iterations",
    default=500,
    type=int,
    help="Maximum number of agent iterations/steps (default: 500, use -1 for unlimited)",
)
@click.option(
    "--max-file-diff-lines",
    default=1500,
    type=int,
    help="Maximum lines allowed per file diff before trimming (default: 1500)",
)
@click.option(
    "--max-file-diff-bytes",
    default=200000,
    type=int,
    help="Maximum bytes allowed per file diff before trimming (default: 200,000)",
)
@click.option(
    "--large-diff-action",
    type=click.Choice(["skip", "preview", "sample", "summarize"], case_sensitive=False),
    default="preview",
    help="Action to take for large diffs (default: preview)",
)
@click.option(
    "--fail-on-review-error",
    is_flag=True,
    default=False,
    help="Fail the CI job if review fails (default: False/fail-soft)",
)
@click.option(
    "--ultrathink",
    is_flag=True,
    help="Enable maximum reasoning effort with extended thinking budget (shortcut for --reasoning-effort high)",
)
@click.option(
    "--timeout",
    default=DEFAULT_REVIEW_TIMEOUT,
    type=int,
    help=f"Maximum time in seconds for the review (default: {DEFAULT_REVIEW_TIMEOUT} = 30 minutes)",
)
@click.option(
    "--json-logs",
    is_flag=True,
    help="Output logs in JSON format for log aggregation systems",
)
@click.option(
    "--log-file",
    default=None,
    type=click.Path(),
    help="Path to write log file (in addition to console output)",
)
@click.option(
    "--skip-health-checks",
    is_flag=True,
    help="Skip pre-flight health checks (not recommended)",
)
def main(
        pr_url: str,
        model: str,
        temperature: float | None,
        reasoning_effort: str | None,
        verbose: bool,
        llm: dict,
        post: bool,
        output_json: bool,
        prompt: str | None,
        prompt_file: str | None,
        workspace: str | None,
        max_iterations: int,
        max_file_diff_lines: int,
        max_file_diff_bytes: int,
        large_diff_action: str,
        fail_on_review_error: bool,
        ultrathink: bool,
        timeout: int,
        json_logs: bool,
        log_file: str | None,
        skip_health_checks: bool,
):
    """
    Review a GitHub pull request or GitLab merge request using AI.

    Hodor uses OpenHands SDK to run an AI agent that clones the repository,
    checks out the PR branch, and analyzes the code using bash tools (gh, git,
    glab) for metadata fetching and comment posting.

    \b
    Examples:
        # Review GitHub PR (output to console)
        hodor https://github.com/owner/repo/pull/123

        # Review and post directly to PR
        hodor https://github.com/owner/repo/pull/123 --post

        # Review GitLab MR (self-hosted)
        export GITLAB_HOST=gitlab.example.com
        hodor https://gitlab.example.com/owner/project/-/merge_requests/456

        # Custom model with reasoning
        hodor URL --model anthropic/claude-opus-4 --reasoning-effort high

        # Custom prompt
        hodor URL --prompt-file prompts/security-focused.txt

        # Additional LLM params
        hodor URL --llm max_tokens=8000 --llm stop="```"

    \b
    Environment Variables:
        LLM_API_KEY or ANTHROPIC_API_KEY or OPENAI_API_KEY - LLM API key (required)
        LLM_BASE_URL - Custom LLM endpoint (optional)
        GITHUB_TOKEN - GitHub API token (for gh CLI authentication)
        GITLAB_TOKEN / GITLAB_PRIVATE_TOKEN / CI_JOB_TOKEN - GitLab API tokens for glab CLI
        GITLAB_HOST - GitLab host for self-hosted instances (default: gitlab.com)

    \b
    Authentication:
        - GitHub: gh auth login  or set GITHUB_TOKEN
        - GitLab: provide a token with api scope via GITLAB_TOKEN (or CI_JOB_TOKEN in CI)
    """
    # Configure structured logging
    log_path = Path(log_file) if log_file else None
    setup_logging(
        json_logs=json_logs,
        log_file=log_path,
        verbose=verbose,
        context={"pr_url": pr_url, "model": model},
    )

    # Check platform and token availability
    platform = detect_platform(pr_url)

    # Run health checks unless skipped
    if not skip_health_checks:
        health_report = run_health_checks(platform=platform)
        if not health_report.all_passed:
            console.print("\n[bold red]Health Check Failed[/bold red]")
            for check in health_report.failed_checks:
                console.print(f"  [red]✗[/red] {check.name}: {check.message}")
            console.print("\n[dim]Use --skip-health-checks to bypass (not recommended)[/dim]\n")
            sys.exit(1)
        # Show warnings for non-critical failures
        for warning in health_report.warnings:
            console.print(f"[yellow]⚠️  {warning.name}: {warning.message}[/yellow]")
    github_token = os.getenv("GITHUB_TOKEN")
    gitlab_token = os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_PRIVATE_TOKEN") or os.getenv("CI_JOB_TOKEN")

    if platform == "github" and not github_token:
        console.print(
            "[yellow]⚠️  Warning: GITHUB_TOKEN not set. You may encounter rate limits or authentication issues.[/yellow]"
        )
        console.print("[dim]   Set GITHUB_TOKEN environment variable or run: gh auth login[/dim]\n")
    elif platform == "gitlab" and not gitlab_token:
        console.print(
            "[yellow]⚠️  Warning: No GitLab token detected. Set GITLAB_TOKEN (api scope) or rely on CI_JOB_TOKEN for "
            "CI environments.[/yellow]"
        )
        console.print("[dim]   Export GITLAB_TOKEN and optionally GITLAB_HOST for self-hosted instances.[/dim]\n")

    # Parse prompt file path
    prompt_file_path = Path(prompt_file) if prompt_file else None

    # Handle ultrathink flag
    if ultrathink:
        reasoning_effort = "high"
        # Ensure extended_thinking_budget is high if not already set
        if "extended_thinking_budget" not in llm:
            llm = {**llm, "extended_thinking_budget": 500000}

    console.print("\n[bold cyan]🚪 Hodor - AI Code Review Agent[/bold cyan]")
    console.print(f"[dim]Platform: {platform.upper()}[/dim]")
    console.print(f"[dim]PR URL: {pr_url}[/dim]")
    console.print(f"[dim]Model: {model}[/dim]")
    if reasoning_effort:
        console.print(f"[dim]Reasoning Effort: {reasoning_effort}[/dim]")
    if max_iterations == -1:
        console.print(f"[dim]Max Iterations: Unlimited[/dim]")
    else:
        console.print(f"[dim]Max Iterations: {max_iterations}[/dim]")
    console.print()

    try:
        with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
        ) as progress:
            task = progress.add_task("Setting up workspace and running review...", total=None)

            # Run the review
            workspace_path = Path(workspace) if workspace else None
            review_output = review_pr(
                pr_url=pr_url,
                model=model,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                custom_prompt=prompt,
                prompt_file=prompt_file_path,
                user_llm_params=llm,
                verbose=verbose,
                cleanup=workspace is None,  # Only cleanup if using temp dir
                workspace_dir=workspace_path,
                output_format="json" if output_json else "markdown",
                max_iterations=max_iterations,
                max_diff_lines=max_file_diff_lines,
                max_diff_bytes=max_file_diff_bytes,
                large_diff_action=large_diff_action,
                fail_on_error=fail_on_review_error,
                timeout=timeout,
            )

            progress.update(task, description="Review complete!")
            progress.stop()

        # Display result
        if post:
            # Post to PR/MR (always as markdown, never raw JSON)
            console.print("\n[cyan]📤 Posting review to PR/MR...[/cyan]\n")
            try:
                # If output is JSON, we need to format it as markdown for posting
                if output_json:
                    from .review_parser import parse_review_output, format_review_markdown

                    # For GitLab, we want to try inline comments, so pass raw JSON
                    if platform == "gitlab":
                        review_text = review_output
                    else:
                        parsed = parse_review_output(review_output)
                        review_text = format_review_markdown(parsed)
                else:
                    review_text = review_output

                result = post_review_comment(
                    pr_url=pr_url,
                    review_text=review_text,
                    model=model,
                )

                if result.get("success"):
                    console.print("[bold green]✅ Review posted successfully![/bold green]")
                    if platform == "github":
                        console.print(f"[dim]   PR: {pr_url}[/dim]")
                    else:
                        console.print(f"[dim]   MR: {pr_url}[/dim]")
                else:
                    console.print(f"[bold red]❌ Failed to post review:[/bold red] {result.get('error')}")
                    console.print("\n[yellow]Review output:[/yellow]\n")
                    if output_json:
                        console.print(review_output)
                    else:
                        console.print(Markdown(review_output))

            except Exception as e:
                console.print(f"[bold red]❌ Error posting review:[/bold red] {str(e)}")
                console.print("\n[yellow]Review output:[/yellow]\n")
                if output_json:
                    console.print(review_output)
                else:
                    console.print(Markdown(review_output))

        else:
            # Print to console
            console.print("[bold green]✅ Review Complete[/bold green]\n")
            if output_json:
                # Output raw JSON (no markdown rendering)
                console.print(review_output)
            else:
                console.print(Markdown(review_output))
                console.print("\n[dim]💡 Tip: Use --post to automatically post this review to the PR/MR[/dim]")

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Review cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]❌ Error:[/bold red] {str(e)}")
        if verbose:
            console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    main()
