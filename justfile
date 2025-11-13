default:
    @just --list

# Install dependencies
sync:
    uv sync --all-extras

# Format code
fmt:
    uv tool run black hodor

# Lint code
lint:
    uv tool run ruff check hodor

# Fix lint issues
lint-fix:
    uv tool run ruff check --fix hodor

# Type check
typecheck:
    uv tool run mypy hodor

# Run all checks
check: fmt lint typecheck

# Auto-fix formatting and linting
fix: fmt lint-fix

# Run tests
test:
    uv run pytest

# Run tests with coverage report
test-cov:
    uv run pytest --cov=hodor --cov-report=html --cov-report=term-missing

# Clean build artifacts and caches
clean:
    rm -rf build/ dist/ *.egg-info .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/ .coverage
    find . -type d -name __pycache__ -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete

# Build distribution
build:
    uv build

# Build Docker image
docker-build:
    docker buildx build --load -t hodor:local .

# Build Docker image (no cache)
docker-build-clean:
    docker buildx build --no-cache --load -t hodor:local .

# Run with Docker
docker-run URL:
    docker run --rm \
        -e ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-} \
        -e OPENAI_API_KEY=${OPENAI_API_KEY:-} \
        -e GITHUB_TOKEN=${GITHUB_TOKEN:-} \
        -e GITLAB_TOKEN=${GITLAB_TOKEN:-} \
        hodor:local {{URL}}

# Review PR
review URL *ARGS:
    uv run hodor {{URL}} {{ARGS}}

# Pre-commit checks
pre-commit: fix check test
