# Hodor

An AI-powered code review agent that performs actual code reviews by analyzing diffs line-by-line to identify bugs, security vulnerabilities, logic errors, and code quality issues.

**Supports**: GitHub and GitLab (including self-hosted instances)

## What This Does

Hodor performs line-by-line code review to identify:

- **Bugs**: Race conditions, null pointer errors, resource leaks, off-by-one errors
- **Security issues**: SQL injection, XSS, auth bypasses, exposed secrets
- **Performance problems**: N+1 queries, inefficient algorithms, memory leaks
- **Code smells**: Missing error handling, bad practices, duplicated code

## Features

- Reads every line of changed code in diffs
- Provides specific file:line references with problematic code
- Explains WHY something is a problem
- Suggests concrete fixes with code examples
- Works with any language (Go, Python, JavaScript, etc.)

## How it Works

Hodor is an agentic code reviewer that uses an AI agent loop to analyze pull requests:

1. **Fetches PR/MR data** - Gets diff, files, and metadata from GitHub/GitLab
2. **Agent loop** - The AI agent iteratively:
   - Analyzes code changes line-by-line using the unified diff format
   - Identifies potential issues (bugs, security, performance, code quality)
   - Uses tools to gather additional context when needed
   - Builds a comprehensive review with specific file:line references
3. **Generates review** - Produces structured feedback with:
   - Critical issues that must be fixed
   - Warnings about potential problems
   - Code examples showing how to fix issues
   - Positive feedback on well-written code
4. **(Optional) Posts comment** - Can automatically post the review as a PR/MR comment in CI/CD

The agent autonomously decides which parts of the code need deeper analysis and can run up to 20 iterations (configurable) to thoroughly review complex changes.

## Installation

### Using Docker (Recommended)

```bash
# Pull from GitHub Container Registry
docker pull ghcr.io/mr-karan/hodor:latest

# Run directly
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  ghcr.io/mr-karan/hodor:latest \
  https://github.com/owner/repo/pull/123
```

### Using Source

```bash
# Clone the repository
git clone https://github.com/mr-karan/hodor
cd hodor

# Sync dependencies (recommended - uses uv.lock for reproducibility)
uv sync

# Or sync with all extras including dev dependencies
uv sync --all-extras

# Run hodor
uv run hodor --help
```

## Quick Start

### 1. Set up your LLM API key

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your LLM API key
echo "ANTHROPIC_API_KEY=sk-ant-your_key_here" >> .env
# OR
echo "OPENAI_API_KEY=sk-your_key_here" >> .env
```

### 2. (Optional) Set up GitHub/GitLab token

A token is **optional** for public repositories but **recommended** for:
- Higher rate limits (5,000/hour vs 60/hour)
- Accessing private repositories
- Avoiding rate limit errors on large PRs

```bash
# For GitHub
echo "GITHUB_TOKEN=ghp_your_token_here" >> .env

# For GitLab
echo "GITLAB_TOKEN=glpat-your_token_here" >> .env
```

**Creating a GitHub Token:**

1. Go to **GitHub Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
2. Click **"Generate new token"**
3. Configure:
   - **Token name**: `hodor`
   - **Repository access**: Choose "Public Repositories (read-only)" or select specific repos
   - **Permissions** (Repository permissions):
     - ✅ **Pull requests**: Read-only
     - ✅ **Contents**: Read-only (for private repos)
     - ✅ **Metadata**: Read-only (auto-granted)
     - ✅ **Commit statuses**: Read-only
4. Click **"Generate token"** and copy it

**Alternative: Classic Token**
- Go to **Personal access tokens** → **Tokens (classic)**
- Select scopes: `repo` (for private) or `public_repo` (for public only)

See [GitHub's documentation](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens?apiVersion=2022-11-28) for details.

### 3. Review a pull request

```bash
# Using uv (recommended)
uv run hodor https://github.com/owner/repo/pull/123

# With custom model
uv run hodor https://github.com/owner/repo/pull/123 --model claude-sonnet-4-5

# Verbose mode
uv run hodor https://github.com/owner/repo/pull/123 -v
```

## Usage Examples

### Review a public PR (no token needed)

**Using Docker:**
```bash
# GitHub
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  ghcr.io/mr-karan/hodor:latest \
  https://github.com/mr-karan/logchef/pull/57

# GitLab
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  ghcr.io/mr-karan/hodor:latest \
  https://gitlab.com/owner/project/-/merge_requests/123
```

**Using source:**
```bash
# GitHub
uv run hodor https://github.com/mr-karan/logchef/pull/57

# GitLab
uv run hodor https://gitlab.com/owner/project/-/merge_requests/123
```

### Review with specific model

**Using Docker:**
```bash
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  ghcr.io/mr-karan/hodor:latest \
  https://github.com/owner/repo/pull/123 \
  --model claude-sonnet-4-5
```

**Using source:**
```bash
# Using GPT-5
uv run hodor https://github.com/owner/repo/pull/123 --model gpt-5

# Using Claude Sonnet
uv run hodor https://github.com/owner/repo/pull/123 --model claude-sonnet-4-5
```

### Review a private PR/MR

**Using Docker:**
```bash
# GitHub
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  -e GITHUB_TOKEN=ghp_xxxxx \
  ghcr.io/mr-karan/hodor:latest \
  https://github.com/myorg/private-repo/pull/456

# GitLab
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  -e GITLAB_TOKEN=glpat-xxxxx \
  ghcr.io/mr-karan/hodor:latest \
  https://gitlab.com/myorg/private-project/-/merge_requests/789
```

**Using source:**
```bash
# GitHub
uv run hodor https://github.com/myorg/private-repo/pull/456 --token ghp_xxxxx

# GitLab
uv run hodor https://gitlab.com/myorg/private-project/-/merge_requests/789 --token glpat-xxxxx
```

### Custom parameters
```bash
uv run hodor https://github.com/owner/repo/pull/123 \
  --max-iterations 30 \
  --max-workers 20 \
  -v
```

### Post review as comment
```bash
# Post review as a comment on the PR/MR (useful for CI/CD)
uv run hodor https://github.com/owner/repo/pull/123 --post-comment
```

### Self-hosted GitLab
```bash
# Hodor automatically detects and supports self-hosted GitLab instances
# Just provide the MR URL from your instance
uv run hodor https://gitlab.yourcompany.com/team/project/-/merge_requests/42

# With Docker
docker run --rm \
  -e ANTHROPIC_API_KEY=your_key_here \
  -e GITLAB_TOKEN=glpat-your-token \
  ghcr.io/mr-karan/hodor:latest \
  https://gitlab.yourcompany.com/team/project/-/merge_requests/42
```

**Note**: The GitLab URL is automatically extracted from the MR URL, so no additional configuration is needed for self-hosted instances.

### Custom Prompts

Hodor allows you to customize the review prompt to focus on specific concerns (security, performance, etc.):

**Using inline prompt:**
```bash
uv run hodor https://github.com/owner/repo/pull/123 \
  --prompt "Focus exclusively on security vulnerabilities. Check for SQL injection, XSS, and auth bypasses."
```

**Using prompt from file:**
```bash
# Use a pre-defined security-focused prompt
uv run hodor https://github.com/owner/repo/pull/123 \
  --prompt-file prompts/security-focused.txt

# Or your own custom prompt file
uv run hodor https://github.com/owner/repo/pull/123 \
  --prompt-file .github/review-prompt.txt
```

**Example custom prompt files:**
- `prompts/security-focused.txt` - Focus on security vulnerabilities
- `prompts/performance-focused.txt` - Focus on performance issues

### Reasoning Effort

Control how deeply the model thinks about the code (for reasoning-capable models):

```bash
# Quick review (faster, less thorough)
uv run hodor https://github.com/owner/repo/pull/123 --reasoning-effort low

# Balanced (moderate speed and quality)
uv run hodor https://github.com/owner/repo/pull/123 --reasoning-effort medium

# Deep review (slower, more thorough - default)
uv run hodor https://github.com/owner/repo/pull/123 --reasoning-effort high
```

## CI/CD Integration

Hodor can automatically review pull requests/merge requests and post comments in your CI/CD pipeline.

### GitLab CI

**Quick Setup:**

1. Copy `.gitlab-ci.yml` to your repository root
2. Add CI/CD variables in **Settings → CI/CD → Variables**:
   - `GITLAB_TOKEN` - GitLab access token with `api` scope
   - `ANTHROPIC_API_KEY` - Your Anthropic API key (or `OPENAI_API_KEY`)
3. Create a merge request - Hodor will review and comment automatically!

**Example `.gitlab-ci.yml`:**

```yaml
stages:
  - review

hodor-review:
  stage: review
  image: ghcr.io/mr-karan/hodor:latest
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - MR_URL="${CI_PROJECT_URL}/-/merge_requests/${CI_MERGE_REQUEST_IID}"
    - MODEL="${HODOR_MODEL:-gpt-5}"
    - |
      hodor "$MR_URL" \
        --model "$MODEL" \
        --token "$GITLAB_TOKEN" \
        --post-comment \
        -v
  allow_failure: true
  timeout: 15m
```

See [.gitlab/README-CI.md](.gitlab/README-CI.md) for detailed setup instructions.

### GitHub Actions

**Example `.github/workflows/hodor.yml`:**

```yaml
name: Code Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/mr-karan/hodor:latest
    steps:
      - name: Run review
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          hodor \
            "https://github.com/${{ github.repository }}/pull/${{ github.event.pull_request.number }}" \
            --model gpt-5 \
            --token "$GITHUB_TOKEN" \
            --post-comment
```

**Required Secrets** (Settings → Secrets and variables → Actions):
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` - Your LLM API key
- `GITHUB_TOKEN` - Automatically provided by GitHub Actions

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--max-iterations` | 20 | Maximum agent loop iterations |
| `--max-workers` | 15 | Maximum parallel tool calls |
| `--token` | $GITHUB_TOKEN or $GITLAB_TOKEN | API token (optional for public repos) |
| `--model` | gpt-5 | LLM model to use |
| `--temperature` | 0.0* | LLM temperature (auto-adjusted for GPT-5/O3) |
| `--reasoning-effort` | high | Reasoning effort level (low/medium/high) |
| `--prompt` | (default prompt) | Custom inline prompt text |
| `--prompt-file` | (none) | Path to file with custom prompt |
| `--post-comment` | False | Post review as comment on PR/MR (for CI/CD) |
| `-v, --verbose` | False | Enable verbose logging |

*Note: Temperature is automatically handled for models like GPT-5 that don't support temperature=0.0

**Token Environment Variables**:
- GitHub: Set `GITHUB_TOKEN` environment variable
- GitLab: Set `GITLAB_TOKEN` environment variable
- The tool auto-detects the platform from the URL

## Supported Models

Any model supported by [LiteLLM](https://docs.litellm.ai/docs/providers):

- **Anthropic**: `claude-sonnet-4-5` (latest Sonnet), `claude-opus-4` (highest quality)
- **OpenAI**: `gpt-5` (latest GPT-5), `o3-mini` (reasoning optimized)
- **Google**: `gemini-2.5-pro`, `gemini-2.5-flash`
- **Open Source**: `ollama/llama3`, `together_ai/mixtral-8x7b`

## Contributing

Contributions welcome!

## License

MIT
