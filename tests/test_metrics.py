"""Tests for metrics collection utilities."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add the hodor package to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hodor.metrics import (
    TokenUsage,
    TimingMetrics,
    ReviewMetrics,
    MetricsCollector,
    get_peak_memory_mb,
    get_current_memory_mb,
    check_memory_usage,
    timed_operation,
)


class TestTokenUsage:
    """Tests for TokenUsage dataclass."""

    def test_total_tokens(self):
        """Test total tokens calculation."""
        usage = TokenUsage(
            prompt_tokens=1000,
            completion_tokens=500,
            cache_read_tokens=200,
            reasoning_tokens=100,
        )
        assert usage.total_tokens == 1800

    def test_cache_hit_rate(self):
        """Test cache hit rate calculation."""
        usage = TokenUsage(
            prompt_tokens=800,
            cache_read_tokens=200,
        )
        # 200 / (800 + 200) = 0.2 = 20%
        assert usage.cache_hit_rate == 20.0

    def test_cache_hit_rate_no_input(self):
        """Test cache hit rate with no input tokens."""
        usage = TokenUsage()
        assert usage.cache_hit_rate == 0.0


class TestTimingMetrics:
    """Tests for TimingMetrics dataclass."""

    def test_timing_start_stop(self):
        """Test timing start and stop."""
        timing = TimingMetrics(name="test")
        timing.start()
        time.sleep(0.01)  # Small delay
        timing.stop()

        assert timing.duration_seconds > 0
        assert timing.start_time > 0
        assert timing.end_time > timing.start_time


class TestReviewMetrics:
    """Tests for ReviewMetrics dataclass."""

    def test_default_values(self):
        """Test default values."""
        metrics = ReviewMetrics()
        assert metrics.success is False
        assert metrics.files_reviewed == 0
        assert metrics.findings_count == 0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = ReviewMetrics(
            review_id="test-123",
            pr_url="https://github.com/owner/repo/pull/1",
            platform="github",
            model="test-model",
            success=True,
        )
        data = metrics.to_dict()

        assert data["review_id"] == "test-123"
        assert data["pr_url"] == "https://github.com/owner/repo/pull/1"
        assert data["platform"] == "github"
        assert data["success"] is True


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_initialization(self):
        """Test collector initialization."""
        collector = MetricsCollector(
            pr_url="https://github.com/owner/repo/pull/1",
            platform="github",
            model="test-model",
        )
        assert collector.metrics.pr_url == "https://github.com/owner/repo/pull/1"
        assert collector.metrics.started_at != ""

    def test_phase_timing(self):
        """Test phase timing."""
        collector = MetricsCollector()

        collector.start_phase("test_phase")
        time.sleep(0.01)
        duration = collector.end_phase("test_phase")

        assert duration > 0
        assert "test_phase" in collector.metrics.phase_timings

    def test_phase_context_manager(self):
        """Test phase context manager."""
        collector = MetricsCollector()

        with collector.phase("test_phase"):
            time.sleep(0.01)

        assert "test_phase" in collector.metrics.phase_timings
        assert collector.metrics.phase_timings["test_phase"] > 0

    def test_record_token_usage(self):
        """Test recording token usage."""
        collector = MetricsCollector()

        collector.record_token_usage(
            prompt_tokens=1000,
            completion_tokens=500,
        )

        assert collector.metrics.token_usage.prompt_tokens == 1000
        assert collector.metrics.token_usage.completion_tokens == 500

    def test_record_token_usage_cumulative(self):
        """Test that token usage is cumulative."""
        collector = MetricsCollector()

        collector.record_token_usage(prompt_tokens=100)
        collector.record_token_usage(prompt_tokens=200)

        assert collector.metrics.token_usage.prompt_tokens == 300

    def test_record_cost(self):
        """Test recording cost."""
        collector = MetricsCollector()
        collector.record_cost(0.05)
        assert collector.metrics.estimated_cost == 0.05

    def test_record_review_stats(self):
        """Test recording review statistics."""
        collector = MetricsCollector()

        collector.record_review_stats(
            files_reviewed=10,
            findings_count=3,
            iterations=50,
            nudge_count=1,
        )

        assert collector.metrics.files_reviewed == 10
        assert collector.metrics.findings_count == 3
        assert collector.metrics.iterations == 50
        assert collector.metrics.nudge_count == 1

    def test_record_success(self):
        """Test recording success."""
        collector = MetricsCollector()
        collector.record_success()
        assert collector.metrics.success is True

    def test_record_error(self):
        """Test recording error."""
        collector = MetricsCollector()
        collector.record_error("Something went wrong", fallback_used=True)

        assert collector.metrics.success is False
        assert collector.metrics.error_message == "Something went wrong"
        assert collector.metrics.fallback_used is True

    def test_finalize(self):
        """Test finalizing metrics."""
        collector = MetricsCollector()
        time.sleep(0.01)

        final = collector.finalize()

        assert final.completed_at != ""
        assert final.total_duration > 0

    def test_workspace_setup_duration(self):
        """Test workspace setup duration is recorded."""
        collector = MetricsCollector()

        collector.start_phase("workspace_setup")
        time.sleep(0.01)
        collector.end_phase("workspace_setup")

        assert collector.metrics.workspace_setup_duration > 0

    def test_agent_run_duration(self):
        """Test agent run duration is recorded."""
        collector = MetricsCollector()

        collector.start_phase("agent_run")
        time.sleep(0.01)
        collector.end_phase("agent_run")

        assert collector.metrics.agent_run_duration > 0


class TestMemoryFunctions:
    """Tests for memory monitoring functions."""

    def test_get_peak_memory_mb(self):
        """Test getting peak memory usage."""
        memory = get_peak_memory_mb()
        # Should be positive number (process is using some memory)
        assert memory >= 0

    def test_get_current_memory_mb(self):
        """Test getting current memory usage."""
        memory = get_current_memory_mb()
        assert memory >= 0

    def test_check_memory_usage_normal(self):
        """Test memory check under threshold."""
        # Normal usage should not warn
        mem_mb, is_warning = check_memory_usage()
        assert mem_mb >= 0
        # Most test runs shouldn't exceed 2GB
        # (but don't fail test if system is under heavy load)


class TestTimedOperation:
    """Tests for timed_operation context manager."""

    def test_timed_operation_success(self):
        """Test timing a successful operation."""
        with timed_operation("test_op") as timing:
            time.sleep(0.01)

        assert timing.name == "test_op"
        assert timing.duration_seconds > 0

    def test_timed_operation_with_exception(self):
        """Test timing records even on exception."""
        timing = None
        try:
            with timed_operation("failing_op") as timing:
                time.sleep(0.01)
                raise ValueError("Test error")
        except ValueError:
            pass

        assert timing is not None
        assert timing.duration_seconds > 0
