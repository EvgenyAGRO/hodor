# Multi-stage build for smaller final image
FROM python:3.13-slim AS builder

# Install system dependencies needed for building (git is required for OpenHands SDK)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

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

# Install system dependencies and common tools for PR reviews
# ripgrep is available in Debian Bookworm repos
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    gnupg \
    ca-certificates \
    jq \
    shellcheck \
    tree \
    vim \
    less \
    ripgrep && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (gh) - via official apt repo for multi-arch support
RUN mkdir -p -m 755 /etc/apt/keyrings && \
    wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install -y gh && \
    rm -rf /var/lib/apt/lists/*

# Install GitLab CLI (glab) - direct download with dynamic arch for robustness
RUN ARCH=$(dpkg --print-architecture) && \
    GLAB_VERSION="1.77.0" && \
    curl -fsSL "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_linux_${ARCH}.tar.gz" -o /tmp/glab.tar.gz && \
    tar -xzf /tmp/glab.tar.gz -C /tmp && \
    mv /tmp/bin/glab /usr/local/bin/ && \
    rm -rf /tmp/glab* /tmp/bin

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application files
COPY --from=builder /build /app
WORKDIR /app

# Ensure virtual environment is used
ENV PATH="/opt/venv/bin:$PATH"

# Set Python to run in unbuffered mode for better logging
ENV PYTHONUNBUFFERED=1

# Set wider terminal dimensions for better output formatting
ENV COLUMNS=200
ENV LINES=50

# Create a non-root user for security
RUN useradd -m -u 1000 hodor && \
    chown -R hodor:hodor /app /opt/venv && \
    # Create workspace directory for hodor user
    mkdir -p /workspace /tmp/hodor && \
    chown -R hodor:hodor /workspace /tmp/hodor

# Add labels for metadata
LABEL org.opencontainers.image.title="Hodor" \
    org.opencontainers.image.description="AI-powered code review agent for GitHub and GitLab" \
    org.opencontainers.image.url="https://github.com/mr-karan/hodor" \
    org.opencontainers.image.source="https://github.com/mr-karan/hodor" \
    org.opencontainers.image.vendor="Karan Sharma" \
    org.opencontainers.image.licenses="MIT"

USER hodor

# Set default workspace directory
ENV HODOR_WORKSPACE=/workspace

# Set entrypoint
ENTRYPOINT ["hodor"]

# Default command (shows help)
CMD ["--help"]
