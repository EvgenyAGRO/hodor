"""PR Review Prompt Builder for OpenHands-based Hodor.

This module provides prompt templates and builders for conducting PR reviews
using OpenHands' bash-based tool system instead of custom API tools.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build_pr_review_prompt(
    pr_url: str,
    owner: str,
    repo: str,
    pr_number: str,
    platform: str,
    target_branch: str = "main",
    diff_base_sha: str | None = None,
    custom_instructions: str | None = None,
    custom_prompt_file: Path | None = None,
) -> str:
    """Build a PR review prompt for OpenHands agent.

    Args:
        pr_url: Full PR URL
        owner: Repository owner
        repo: Repository name
        pr_number: PR number
        platform: "github" or "gitlab"
        target_branch: Target/base branch of the PR (e.g., "main", "develop")
        diff_base_sha: GitLab's calculated merge base SHA (most reliable for GitLab CI)
        custom_instructions: Optional custom prompt text (inline)
        custom_prompt_file: Optional path to custom prompt file

    Returns:
        Complete prompt for OpenHands agent
    """
    # Priority: custom_instructions > custom_prompt_file > default
    if custom_instructions:
        logger.info("Using custom inline prompt")
        return custom_instructions

    if custom_prompt_file:
        logger.info(f"Loading prompt from file: {custom_prompt_file}")
        try:
            with open(custom_prompt_file, "r", encoding="utf-8") as f:
                prompt_text = f.read()
            return prompt_text
        except Exception as e:
            logger.error(f"Failed to load prompt file: {e}")
            raise

    # Default prompt for OpenHands bash-based review
    if platform == "github":
        cli_tool = "gh"
        pr_view_cmd = f"gh pr view {pr_number}"
        pr_diff_cmd = f"gh pr diff {pr_number}"
        pr_checks_cmd = f"gh pr checks {pr_number}"
        # GitHub specific diff command (fallback)
        git_diff_cmd = f"git --no-pager diff origin/{target_branch}...HEAD"
    else:  # gitlab
        cli_tool = "glab"
        pr_view_cmd = f"glab mr view {pr_number}"
        pr_diff_cmd = f"glab mr diff {pr_number}"
        pr_checks_cmd = f"glab ci view"
        # GitLab specific diff command - use diff_base_sha if available (most reliable)
        if diff_base_sha:
            git_diff_cmd = f"git --no-pager diff {diff_base_sha} HEAD"
            logger.info(f"Using GitLab CI_MERGE_REQUEST_DIFF_BASE_SHA: {diff_base_sha[:8]}")
        else:
            git_diff_cmd = f"git --no-pager diff origin/{target_branch}...HEAD"

    prompt = f"""You are an automated code reviewer analyzing {pr_url}. PR branch is checked out.

## Your Mission
Find production bugs IN THE PR'S DIFF ONLY. You are READ-ONLY - analyze code, don't modify files.

## STEP 1: Get the Diff (MANDATORY FIRST STEP)
**Run this command FIRST and ONLY review files shown:**
```bash
{pr_diff_cmd}
```

This shows you the EXACT files changed in this PR. Write down the list of changed files.

## STEP 2: Review ONLY Those Files
**CRITICAL RULES:**
- ✅ ONLY review files that appear in the diff from Step 1
- ✅ ONLY analyze the actual code changes (+ and - lines in the diff)
- ✅ Use the most reliable diff command: `{git_diff_cmd}`
- ❌ NEVER review files not in the diff
- ❌ NEVER flag "files will be deleted when merging" - that's just outdated branch
- ❌ NEVER flag "dependency version downgrade" - that's just branch not rebased
- ❌ NEVER compare entire codebase to {target_branch} - DIFF ONLY

### Git Diff Command (MOST RELIABLE)
**Use this exact command to see changes:**
```bash
{git_diff_cmd}
```

{'**GitLab CI Advantage**: This uses GitLab\'s pre-calculated merge base SHA (`CI_MERGE_REQUEST_DIFF_BASE_SHA`), which matches exactly what the GitLab UI shows. This is more reliable than three-dot syntax because it handles force pushes, rebases, and messy histories correctly.' if diff_base_sha else '**Three-dot syntax** shows ONLY changes introduced on the source branch, excluding changes already on ' + target_branch + '.'}

**Example:**
- Diff shows: `cmd/dump/hub.go` and `pkg/batch/batch.go` changed
- You review: ONLY `cmd/dump/hub.go` and `pkg/batch/batch.go`
- You ignore: ALL other files (even if you see them in the repo)

## STEP 3: Analyze Each Changed File
For each file in the diff:
1. Read the diff to see what changed
2. Look for bugs in the NEW/MODIFIED code only
3. Ignore pre-existing code unless the PR breaks it

## Tools Available

**IMPORTANT: Disable git pager to avoid interactive sessions:**
```bash
export GIT_PAGER=cat
```
Run this ONCE at the start. All subsequent git commands will output directly without pagination.

**FIRST** (always start here):
- `{pr_diff_cmd}` - Get list of changed files (RUN THIS FIRST!)
  - This is the MOST RELIABLE method - it automatically handles target branch detection
  - Works in detached HEAD CI environments where `origin/{target_branch}` may not exist

**THEN** (only for files in the diff):
- **MOST PREFERRED**: Use `{pr_diff_cmd}` to see full changes for all files (automatically handles target branch)
- **ALTERNATIVE**: Use `{git_diff_cmd} -- path/to/file` to see changes for specific file
  {'- ✅ **GitLab CI Advantage**: Uses pre-calculated merge base SHA - most reliable!' if diff_base_sha else f'- ⚠️  May fail in CI if `origin/{target_branch}` doesn\'t exist'}
- `planning_file_editor` - Read full file with context
- `grep` - Search for patterns in changed files only

**CRITICAL**: Always prefer `{pr_diff_cmd}` over raw git commands when reviewing all changes!

## Bug Criteria (ALL must apply)
- Meaningfully impacts production (accuracy/performance/security/maintainability)
- Discrete and actionable fix needed
- Introduced in THIS PR's diff (not pre-existing)
- Provably affected: Identify the specific failing code
- Not intentional design choice
- Author would fix if aware
- No unstated assumptions about inputs or environment
- Not style/preference issues
- Not branch sync issues (outdated dependencies, files not in diff)

**For EVERY finding, you MUST provide:**
- **Trigger**: Exact input/scenario/environment that causes the issue
- **Impact**: Specific production failure that will occur
- **Proof**: Point to the exact failing code in the diff

## Priority Levels
**P0 (Critical)**: Drop everything. Blocking release/operations. **Universal issue** (affects ANY input/environment, no assumptions). Examples: Race conditions, null derefs, SQL injection, XSS, auth bypasses, data corruption
**P1 (High)**: Will break in production under specific conditions. Examples: Logic errors, resource leaks, memory leaks
**P2 (Important)**: Performance or maintainability issues. Examples: N+1 queries, O(n²) algorithms, missing validation, incorrect error handling
**P3 (Low)**: Code quality concerns. Examples: Code smells, magic numbers, overly complex logic, missing error messages

## Review Process
1. Run `{pr_diff_cmd}` to see changed files
2. Use `grep` to search for common bug patterns (null, undefined, TODO, FIXME, etc.)
3. Use `planning_file_editor` to read changed files with context
4. Check edge cases: empty inputs, null values, boundary conditions, error paths
5. Think: What user input or race condition breaks this?

## Comment Guidelines
- **Brief**: 1 paragraph max per finding, no unnecessary line breaks
- **Matter-of-fact**: State facts, avoid praise or politeness filler
- **Avoid**: "Great job", "Thanks for", "Consider", "Perhaps"
- **Severity honesty**: Don't soften critical issues
- **Immediate clarity**: Reader should understand within 5 seconds
- **Line ranges**: Keep as short as possible (5-10 lines max), pinpoint the exact problem location
- **Code examples**: Max 3 lines, use inline `code` or code blocks
- **Scenario explicit**: Clearly state the exact inputs/environments/scenarios that trigger the bug

## Output Format
```markdown
### Issues Found

**🔴 Critical (P0/P1)**
- **[P0] Brief descriptive title** (`file.py:45-52`)
  - **Issue**: What's wrong
  - **Impact**: How this breaks in production
  - **Trigger**: Specific input/scenario that causes the bug
- **[P1] Title** (`file.go:78-82`)
  - **Issue**: What's wrong
  - **Impact**: How this breaks under specific conditions
  - **Trigger**: Specific scenario that causes the bug

**🟡 Important (P2)**
- **[P2] Title** (`file.js:89-94`)
  - **Issue**: Performance/validation problem
  - **Impact**: User impact or degradation

**🟢 Minor (P3)**
- **[P3] Title** (`util.ts:34`)
  - **Issue**: Code quality concern
  - **Suggestion**: How to improve

### Summary
1-2 sentences. **If no critical issues found, say so explicitly.**
Total issues: X critical, Y important, Z minor.

### Overall Verdict
**Status**: ✅ Patch is correct | ❌ Patch has blocking issues

**Explanation**: 1-2 sentences. Ignore non-blocking issues (style, formatting, typos, docs).

*Correct = existing code won't break, no bugs, free of blocking issues.*
```

## Important Rules
1. **Be specific**: Include exact line numbers and scenarios
2. **Be thorough**: Check ALL changed files
3. **Be honest**: If code is clean, say so - don't invent issues
4. **Focus on bugs**: Not style, formatting, or subjective preferences
5. **Provide value**: Each issue should have clear impact and trigger
6. **Stay on-branch**: Never file bugs that only exist because the feature branch is missing commits already present on `{target_branch}`

Start by running `{pr_diff_cmd}` and then using grep/glob to search for potential issues.
"""

    return prompt
