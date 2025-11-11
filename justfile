# Justfile for Hodor development tasks
# Install just: https://github.com/casey/just

# Default recipe to display help information
default:
    @just --list

# Sync dependencies (creates venv, installs deps, generates lock file)
sync:
    uv sync

# Sync with all optional dependencies including dev
sync-all:
    uv sync --all-extras

# Format code with black
fmt:
    uv tool run black hodor

# Check code formatting without making changes
fmt-check:
    uv tool run black --check hodor

# Lint code with ruff
lint:
    uv tool run ruff check hodor

# Lint and attempt to fix issues
lint-fix:
    uv tool run ruff check --fix hodor

# Type check with mypy
typecheck:
    uv tool run mypy hodor

# Run all checks (format, lint, type)
check: fmt-check lint typecheck

# Format and lint fix
fix: fmt lint-fix

# Run tests
test:
    uv run pytest

# Run tests with coverage
test-cov:
    uv run pytest --cov=hodor --cov-report=html --cov-report=term-missing

# Run tests in watch mode (requires pytest-watch)
test-watch:
    uv tool run pytest-watch

# Clean up build artifacts and cache
clean:
    rm -rf build/
    rm -rf dist/
    rm -rf *.egg-info
    rm -rf .pytest_cache/
    rm -rf .mypy_cache/
    rm -rf .ruff_cache/
    rm -rf htmlcov/
    rm -rf .coverage
    find . -type d -name __pycache__ -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete

# Build Docker image locally (single platform, loaded to Docker)
docker-build:
    docker buildx build --load -t hodor:local .

# Build Docker image for multiple platforms (amd64 + arm64) - for registry push
docker-build-multi:
    docker buildx build --platform linux/amd64,linux/arm64 -t hodor:local .

# Build and push multi-platform image to registry
docker-push REGISTRY:
    docker buildx build --platform linux/amd64,linux/arm64 -t {{REGISTRY}} --push .

# Run hodor with Docker
docker-run URL:
    docker run --rm \
        -e ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} \
        -e OPENAI_API_KEY=${OPENAI_API_KEY} \
        -e GITHUB_TOKEN=${GITHUB_TOKEN} \
        -e GITLAB_TOKEN=${GITLAB_TOKEN} \
        hodor:local {{URL}}

# Review a PR (shortcut for uv run)
review URL *ARGS:
    uv run hodor {{URL}} {{ARGS}}

# Start IPython shell with hodor loaded
shell:
    uv run ipython -i -c "from hodor import agent, tools"

# Build distribution packages
build:
    python -m build

# Show project info
info:
    @echo "Hodor - AI-powered code review agent"
    @echo "=========================================="
    @uv pip list | grep -E "(hodor|litellm|anthropic|PyGithub|python-gitlab)"

# Run all checks and tests before committing
pre-commit: fix check test
    @echo "✅ All checks passed! Ready to commit."
