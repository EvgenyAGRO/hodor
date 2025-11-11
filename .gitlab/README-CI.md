# GitLab CI Integration for Hodor

This guide explains how to set up automatic code reviews on GitLab merge requests using Hodor.

## What This Does

When you create or update a merge request, Hodor will:
1. Automatically trigger on the MR
2. Analyze all changed files line-by-line
3. Identify bugs, security issues, and code quality problems
4. Post a detailed review as a comment on the MR

## Quick Start

### 1. Copy the CI configuration

Copy `.gitlab-ci.yml` to your repository root:

```bash
cp .gitlab-ci.yml /path/to/your/project/
```

### 2. Set up CI/CD Variables

Go to your GitLab project → **Settings** → **CI/CD** → **Variables** and add:

#### Required Variables

| Variable | Value | Protected | Masked |
|----------|-------|-----------|--------|
| `GITLAB_TOKEN` | Your GitLab access token (see below) | ✅ | ✅ |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (sk-ant-...) | ✅ | ✅ |

**OR** if using OpenAI:

| Variable | Value | Protected | Masked |
|----------|-------|-----------|--------|
| `GITLAB_TOKEN` | Your GitLab access token | ✅ | ✅ |
| `OPENAI_API_KEY` | Your OpenAI API key (sk-...) | ✅ | ✅ |

#### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HODOR_MODEL` | gpt-5 | LLM model to use |

**Supported Models:**
- `gpt-5` (OpenAI, recommended for balance)
- `claude-sonnet-4-5` (Anthropic, best quality)
- `o3-mini` (OpenAI, reasoning optimized)
- `gemini-2.5-flash` (Google, fast)

### 3. Create GitLab Access Token

**Option A: Personal Access Token (Recommended for Testing)**

1. Go to **GitLab** → **User Settings** → **Access Tokens**
2. Click **Add new token**
3. Configure:
   - **Token name**: `hodor-ci`
   - **Expiration date**: Set as needed (e.g., 90 days)
   - **Scopes**:
     - ✅ `api` - Full API access (includes read + write)
     - OR ✅ `read_api` + `write_repository` - More restrictive

**Token Format**: `glpat-xxxxxxxxxxxxxxxxxxxx`

**Option B: Project Access Token (Recommended for Production)**

1. Go to **Project** → **Settings** → **Access Tokens**
2. Click **Add new token**
3. Configure:
   - **Token name**: `hodor-ci`
   - **Role**: **Developer** (needs write access to post comments)
   - **Scopes**: `api` or `read_api` + `write_repository`

### 4. Get LLM API Key

**For Anthropic (Claude):**
1. Go to https://console.anthropic.com/
2. Create an API key
3. Copy the key (starts with `sk-ant-`)

**For OpenAI:**
1. Go to https://platform.openai.com/api-keys
2. Create an API key
3. Copy the key (starts with `sk-`)

### 5. Test It!

Create a test merge request:

```bash
git checkout -b test-hodor
echo "# Test" >> test.txt
git add test.txt
git commit -m "Test hodor CI"
git push origin test-hodor
```

Then create an MR on GitLab. You should see:
1. A pipeline starting automatically
2. A `hodor-review` job running
3. A comment posted on your MR with the review

## Troubleshooting

### Pipeline fails with "authentication failed"

**Problem**: GITLAB_TOKEN is missing or invalid

**Solution**:
1. Check that GITLAB_TOKEN is set in CI/CD variables
2. Verify the token hasn't expired
3. Ensure the token has `api` or `read_api` scope
4. Make sure the token is marked as "Masked" to keep it secure

### Pipeline fails with "API key not found"

**Problem**: LLM API key is missing

**Solution**:
1. Add either `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` to CI/CD variables
2. Make sure the variable is marked as "Masked" and "Protected"
3. Verify the API key is valid by testing it locally

### Review is posted but seems incomplete

**Problem**: Review might be timing out or hitting rate limits

**Solution**:
1. Increase the timeout in `.gitlab-ci.yml` (default: 15m)
2. Use a faster model like `gpt-5` or `gemini-2.5-flash`
3. Set `HODOR_MODEL` variable to a faster model

### Pipeline doesn't trigger on MRs

**Problem**: CI/CD might be disabled or rules misconfigured

**Solution**:
1. Check that CI/CD is enabled: **Settings** → **General** → **Visibility**
2. Verify `.gitlab-ci.yml` is in the repository root
3. Check pipeline rules - should have `if: $CI_PIPELINE_SOURCE == "merge_request_event"`

### Comments are not being posted

**Problem**: Token might not have write permissions

**Solution**:
1. Verify GITLAB_TOKEN has `api` scope (not just `read_api`)
2. If using Project Access Token, ensure role is **Developer** or higher
3. Check pipeline logs for specific error messages

## Advanced Configuration

### Using a Specific Model

Set the `HODOR_MODEL` variable:

```yaml
# In .gitlab-ci.yml
variables:
  HODOR_MODEL: "gpt-5"  # Use GPT-5 for balanced reviews
```

### Customizing Review Depth

Edit `.gitlab-ci.yml` to add custom parameters:

```yaml
script:
  - |
    uv run hodor "$MR_URL" \
      --model "$MODEL" \
      --token "$GITLAB_TOKEN" \
      --post-comment \
      --max-iterations 30 \    # More thorough review
      --max-workers 20 \       # More parallel tool calls
      -v                       # Verbose logging
```

### Running Only on Specific Branches

Modify the `rules` section in `.gitlab-ci.yml`:

```yaml
rules:
  # Only run on MRs targeting main or develop branches
  - if: $CI_PIPELINE_SOURCE == "merge_request_event" && ($CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "main" || $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "develop")
```

### Using Manual Trigger

Change to manual trigger to review only when requested:

```yaml
hodor-review:
  stage: review
  when: manual  # Add this line
  # ... rest of config
```

### Running for Specific File Types Only

Add a filter to skip MRs that don't touch code files:

```yaml
rules:
  - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    changes:
      - "**/*.py"
      - "**/*.js"
      - "**/*.go"
      - "**/*.rs"
      # Add your code file extensions
```

## Cost Considerations

Running AI reviews on every MR has API costs. Here are some tips:

**Model Costs (approximate per review):**
- `gpt-5`: $0.05 - $0.20 per review (recommended, good balance)
- `claude-sonnet-4-5`: $0.10 - $0.40 per review (best quality)
- `o3-mini`: $0.03 - $0.15 per review (reasoning tasks)
- `gemini-2.5-flash`: $0.02 - $0.12 per review (fastest)

**Cost Optimization Strategies:**

1. **Use manual trigger** for large MRs (add `when: manual`)
2. **Filter by file changes** to skip non-code MRs
3. **Use `gpt-5` for most reviews** (good balance of speed and quality)
4. **Reserve `claude-sonnet-4-5`** for important branches (main, release)
5. **Set max-iterations lower** to cap review depth (e.g., `--max-iterations 15`)

## GitHub Actions Alternative

If you're using GitHub instead of GitLab, you can adapt this setup:

```yaml
# .github/workflows/hodor.yml
name: Code Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Install hodor
        run: |
          pip install uv
          git clone https://github.com/mr-karan/hodor.git
          cd hodor && uv venv && uv pip install -e .

      - name: Run review
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          cd hodor
          uv run hodor \
            "https://github.com/${{ github.repository }}/pull/${{ github.event.pull_request.number }}" \
            --model gpt-5 \
            --token "$GITHUB_TOKEN" \
            --post-comment \
            -v
```

## Security Best Practices

1. **Always mask sensitive variables** (GITLAB_TOKEN, API keys)
2. **Mark variables as Protected** to restrict to protected branches
3. **Rotate tokens regularly** (every 90 days recommended)
4. **Use Project Access Tokens** instead of Personal tokens for production
5. **Limit token scope** to minimum required (`read_api` + write only)
6. **Review pipeline logs** carefully - they may contain sensitive data
7. **Enable "Mask sensitive information"** in job logs

## Support

- **Issues**: https://github.com/mr-karan/hodor/issues
- **Documentation**: https://github.com/mr-karan/hodor
- **Examples**: See `.gitlab-ci.yml` in the repository

## License

MIT License - see LICENSE file for details
