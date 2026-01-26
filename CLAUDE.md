# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Development Commands

```bash
just sync        # Install dependencies with uv
just check       # Format (black) + lint (ruff) + typecheck (mypy)
just test        # Run pytest
just test-cov    # Run pytest with coverage report
just fix         # Auto-fix formatting and linting issues
just review URL  # Run hodor review on a PR URL
```

**Run a single test file:**
```bash
python -m pytest tests/test_<name>.py -v -c /dev/null
```

Note: The `-c /dev/null` flag avoids pytest config issues with coverage options in pyproject.toml.

**Docker:**
```bash
just docker-build     # Build local image
just docker-run URL   # Run review in container
```

## Architecture Overview

Hodor is an agentic code reviewer powered by the OpenHands SDK. It reviews GitHub/GitLab PRs by running an AI agent that can execute commands, read files, and provide contextual feedback.

### Core Flow

```
CLI (cli.py) → review_pr (agent.py) → OpenHands Conversation → Post Results
```

1. **CLI** (`cli.py`): Click-based entrypoint, parses URL, detects platform (GitHub/GitLab)
2. **Agent** (`agent.py`): Creates OpenHands conversation, sends review prompt, extracts findings
3. **Workspace** (`workspace.py`): Clones repo or reuses CI workspace, checks out PR branch
4. **Prompt Builder** (`prompts/pr_review_prompt.py`): Builds platform-specific review instructions
5. **Result Posting**: Posts inline discussions (GitLab) or review comments (GitHub)

### Key Modules

| Module | Purpose |
|--------|---------|
| `agent.py` | Main review loop, fail-soft fallback, result extraction |
| `workspace.py` | Repo cloning, CI detection (GitLab CI, GitHub Actions) |
| `llm/openhands_client.py` | OpenHands SDK configuration, model setup |
| `prompts/pr_review_prompt.py` | Review prompt templates with variable interpolation |
| `review_parser.py` | Parses agent JSON output into structured findings |
| `duplicate_detector.py` | Fuzzy matching to prevent duplicate comments |
| `diff_utils.py` | Large diff trimming (preview/sample/summarize/skip) |
| `gitlab.py` / `github.py` | Platform API interactions |
| `skills.py` | Discovers `.hodor/skills/` for repo-specific guidelines |

### OpenHands SDK Integration

- **Agent Creation**: `create_hodor_agent()` in `llm/openhands_client.py`
- **Workspace**: `LocalWorkspace` with temp dir or CI workspace (`$CI_PROJECT_DIR`, `$GITHUB_WORKSPACE`)
- **Conversation**: Sends prompt via `conversation.send_message()`, runs with `conversation.run()`
- **Event Callbacks**: `on_event()` for real-time verbose logging
- **Terminal Type**: Uses `subprocess` PTY (not tmux) to avoid env var length limits

### Fail-Soft Mode

On agent failure, Hodor generates a fallback review rather than failing CI:
- `_recover_last_json_response()`: Extracts partial results from failed conversations
- `_generate_fallback_review()`: Creates basic review with file list
- Exit code is always 0 unless `--fail-on-review-error` is set

### Duplicate Detection

`duplicate_detector.py` prevents posting duplicate comments using:
- Text normalization (case, whitespace, markdown stripping)
- Fuzzy title matching (70% similarity threshold via SequenceMatcher)
- Line proximity (within 5 lines = same location)

## Test Import Pattern

When creating new test files that import from `hodor`, use direct module loading to avoid openhands SDK dependency issues:

```python
import importlib.util
from pathlib import Path

# Load module directly (avoids package import issues)
_module_path = Path(__file__).parent.parent / "hodor" / "<module_name>.py"
_spec = importlib.util.spec_from_file_location("<module_name>", _module_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

# Import functions from the loaded module
function_name = _module.function_name
```

For mocking, use `@patch.object(_module, "function_name")` instead of `@patch("hodor.module.function")`.

## Testing Requirements

**ALWAYS run all tests after making changes to verify no regressions were introduced.**

```bash
python -m pytest tests/ -v -c /dev/null
```

## Code Style

- Python 3.13, Black formatting (120-char lines), Ruff linting
- Run `just fix` before committing
