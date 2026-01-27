"""Metrics collection for Hodor reviews.

Provides structured metrics collection, timing utilities, and
memory monitoring for observability.
"""

from __future__ import annotations

import logging
import os
import resource
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return (
            self.prompt_tokens
            + self.completion_tokens
            + self.cache_read_tokens
            + self.reasoning_tokens
        )

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate as a percentage."""
        total_input = self.prompt_tokens + self.cache_read_tokens
        if total_input == 0:
            return 0.0
        return (self.cache_read_tokens / total_input) * 100


@dataclass
class TimingMetrics:
    """Timing metrics for a phase of execution."""

    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0

    def start(self) -> None:
        """Record start time."""
        self.start_time = time.time()

    def stop(self) -> None:
        """Record end time and calculate duration."""
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time


@dataclass
class ReviewMetrics:
    """Comprehensive metrics for a review session."""

    # Identification
    review_id: str = ""
    pr_url: str = ""
    platform: str = ""
    model: str = ""

    # Timestamps
    started_at: str = ""
    completed_at: str = ""

    # Timing (seconds)
    total_duration: float = 0.0
    workspace_setup_duration: float = 0.0
    agent_run_duration: float = 0.0
    post_review_duration: float = 0.0

    # Token usage
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    # Cost
    estimated_cost: float = 0.0

    # Review stats
    files_reviewed: int = 0
    findings_count: int = 0
    iterations: int = 0
    nudge_count: int = 0

    # Status
    success: bool = False
    error_message: str = ""
    fallback_used: bool = False

    # Memory
    peak_memory_mb: float = 0.0

    # Phase timings
    phase_timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary for logging/serialization."""
        return {
            "review_id": self.review_id,
            "pr_url": self.pr_url,
            "platform": self.platform,
            "model": self.model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_seconds": self.total_duration,
            "workspace_setup_seconds": self.workspace_setup_duration,
            "agent_run_seconds": self.agent_run_duration,
            "post_review_seconds": self.post_review_duration,
            "token_usage": {
                "prompt_tokens": self.token_usage.prompt_tokens,
                "completion_tokens": self.token_usage.completion_tokens,
                "cache_read_tokens": self.token_usage.cache_read_tokens,
                "cache_write_tokens": self.token_usage.cache_write_tokens,
                "reasoning_tokens": self.token_usage.reasoning_tokens,
                "total_tokens": self.token_usage.total_tokens,
                "cache_hit_rate": self.token_usage.cache_hit_rate,
            },
            "estimated_cost_usd": self.estimated_cost,
            "files_reviewed": self.files_reviewed,
            "findings_count": self.findings_count,
            "iterations": self.iterations,
            "nudge_count": self.nudge_count,
            "success": self.success,
            "error_message": self.error_message,
            "fallback_used": self.fallback_used,
            "peak_memory_mb": self.peak_memory_mb,
            "phase_timings": self.phase_timings,
        }


class MetricsCollector:
    """Collects and manages metrics for a review session."""

    def __init__(self, review_id: str = "", pr_url: str = "", platform: str = "", model: str = "") -> None:
        """Initialize the metrics collector.

        Args:
            review_id: Unique identifier for the review
            pr_url: URL of the PR being reviewed
            platform: Platform (github/gitlab)
            model: LLM model being used
        """
        self.metrics = ReviewMetrics(
            review_id=review_id or self._generate_review_id(),
            pr_url=pr_url,
            platform=platform,
            model=model,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._start_time = time.time()
        self._phase_starts: dict[str, float] = {}

    def _generate_review_id(self) -> str:
        """Generate a unique review ID."""
        import uuid
        return str(uuid.uuid4())[:8]

    def start_phase(self, phase_name: str) -> None:
        """Mark the start of a phase.

        Args:
            phase_name: Name of the phase (e.g., "workspace_setup", "agent_run")
        """
        self._phase_starts[phase_name] = time.time()
        logger.debug(f"Started phase: {phase_name}")

    def end_phase(self, phase_name: str) -> float:
        """Mark the end of a phase and record duration.

        Args:
            phase_name: Name of the phase

        Returns:
            Duration of the phase in seconds
        """
        if phase_name not in self._phase_starts:
            logger.warning(f"Phase '{phase_name}' was not started")
            return 0.0

        duration = time.time() - self._phase_starts[phase_name]
        self.metrics.phase_timings[phase_name] = duration
        logger.debug(f"Ended phase: {phase_name} ({duration:.2f}s)")

        # Update specific duration fields
        if phase_name == "workspace_setup":
            self.metrics.workspace_setup_duration = duration
        elif phase_name == "agent_run":
            self.metrics.agent_run_duration = duration
        elif phase_name == "post_review":
            self.metrics.post_review_duration = duration

        return duration

    @contextmanager
    def phase(self, phase_name: str) -> Generator[None, None, None]:
        """Context manager for timing a phase.

        Args:
            phase_name: Name of the phase

        Example:
            with collector.phase("workspace_setup"):
                setup_workspace(...)
        """
        self.start_phase(phase_name)
        try:
            yield
        finally:
            self.end_phase(phase_name)

    def record_token_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        """Record token usage.

        Args:
            prompt_tokens: Input tokens
            completion_tokens: Output tokens
            cache_read_tokens: Tokens read from cache
            cache_write_tokens: Tokens written to cache
            reasoning_tokens: Tokens used for reasoning
        """
        self.metrics.token_usage.prompt_tokens += prompt_tokens
        self.metrics.token_usage.completion_tokens += completion_tokens
        self.metrics.token_usage.cache_read_tokens += cache_read_tokens
        self.metrics.token_usage.cache_write_tokens += cache_write_tokens
        self.metrics.token_usage.reasoning_tokens += reasoning_tokens

    def record_cost(self, cost: float) -> None:
        """Record estimated cost.

        Args:
            cost: Cost in USD
        """
        self.metrics.estimated_cost = cost

    def record_review_stats(
        self,
        files_reviewed: int = 0,
        findings_count: int = 0,
        iterations: int = 0,
        nudge_count: int = 0,
    ) -> None:
        """Record review statistics.

        Args:
            files_reviewed: Number of files reviewed
            findings_count: Number of findings
            iterations: Number of agent iterations
            nudge_count: Number of nudges sent
        """
        self.metrics.files_reviewed = files_reviewed
        self.metrics.findings_count = findings_count
        self.metrics.iterations = iterations
        self.metrics.nudge_count = nudge_count

    def record_success(self) -> None:
        """Mark the review as successful."""
        self.metrics.success = True

    def record_error(self, error_message: str, fallback_used: bool = False) -> None:
        """Record an error.

        Args:
            error_message: The error message
            fallback_used: Whether fallback review was generated
        """
        self.metrics.success = False
        self.metrics.error_message = error_message
        self.metrics.fallback_used = fallback_used

    def finalize(self) -> ReviewMetrics:
        """Finalize metrics collection.

        Returns:
            The completed ReviewMetrics
        """
        self.metrics.completed_at = datetime.now(timezone.utc).isoformat()
        self.metrics.total_duration = time.time() - self._start_time
        self.metrics.peak_memory_mb = get_peak_memory_mb()
        return self.metrics


def get_peak_memory_mb() -> float:
    """Get peak memory usage of the current process in MB.

    Returns:
        Peak memory usage in megabytes
    """
    try:
        # ru_maxrss is in bytes on Linux, kilobytes on macOS
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        max_rss = rusage.ru_maxrss

        # Detect platform and convert appropriately
        import platform
        if platform.system() == "Darwin":
            # macOS: already in bytes
            return max_rss / (1024 * 1024)
        else:
            # Linux: in kilobytes
            return max_rss / 1024
    except Exception:
        return 0.0


def get_current_memory_mb() -> float:
    """Get current memory usage of the process in MB.

    Returns:
        Current memory usage in megabytes
    """
    try:
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is peak, but we can use /proc for current on Linux
        import platform
        if platform.system() == "Linux":
            try:
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # VmRSS is in kB
                            return int(line.split()[1]) / 1024
            except Exception:
                pass

        # Fallback: use peak (not ideal but better than nothing)
        max_rss = rusage.ru_maxrss
        if platform.system() == "Darwin":
            return max_rss / (1024 * 1024)
        else:
            return max_rss / 1024
    except Exception:
        return 0.0


# Memory threshold for warnings (MB)
MEMORY_WARNING_THRESHOLD_MB = 2000


def check_memory_usage() -> tuple[float, bool]:
    """Check current memory usage and warn if high.

    Returns:
        Tuple of (current_memory_mb, is_warning)
    """
    current_mb = get_current_memory_mb()
    is_warning = current_mb > MEMORY_WARNING_THRESHOLD_MB

    if is_warning:
        logger.warning(
            f"High memory usage detected: {current_mb:.1f}MB "
            f"(threshold: {MEMORY_WARNING_THRESHOLD_MB}MB)"
        )

    return current_mb, is_warning


@contextmanager
def timed_operation(name: str) -> Generator[TimingMetrics, None, None]:
    """Context manager for timing an operation.

    Args:
        name: Name of the operation

    Yields:
        TimingMetrics object with recorded times

    Example:
        with timed_operation("api_call") as timing:
            result = call_api()
        print(f"Took {timing.duration_seconds}s")
    """
    timing = TimingMetrics(name=name)
    timing.start()
    try:
        yield timing
    finally:
        timing.stop()
        logger.debug(f"{name} completed in {timing.duration_seconds:.3f}s")
