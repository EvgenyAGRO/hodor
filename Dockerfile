# Multi-stage build for smaller final image
FROM python:3.13-slim AS builder

# Install uv
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /build

# Copy Python version pin and dependency files
COPY .python-version pyproject.toml uv.lock README.md ./

# Copy source code
COPY hodor ./hodor

# Set UV_PROJECT_ENVIRONMENT to create venv at final location
# This avoids path issues in multi-stage builds
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Sync dependencies using modern uv workflow
# Using --frozen to ensure lock file is respected
# Using --no-editable for production build
RUN uv sync --no-dev --frozen --no-editable

# Final stage
FROM python:3.13-slim

# Install git (needed for cloning repos in some scenarios)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application files
COPY --from=builder /build /app
WORKDIR /app

# Ensure virtual environment is used
ENV PATH="/opt/venv/bin:$PATH"

# Set Python to run in unbuffered mode for better logging
ENV PYTHONUNBUFFERED=1

# Create a non-root user for security
RUN useradd -m -u 1000 hodor && \
    chown -R hodor:hodor /app /opt/venv

USER hodor

# Set entrypoint
ENTRYPOINT ["hodor"]

# Default command (shows help)
CMD ["--help"]
