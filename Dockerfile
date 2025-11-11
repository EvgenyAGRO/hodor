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

# Sync dependencies using modern uv workflow
# This uses the lock file for reproducible builds
RUN uv sync --no-dev --frozen

# Final stage
FROM python:3.13-slim

# Install git (needed for cloning repos in some scenarios)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /build/.venv /opt/venv

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
