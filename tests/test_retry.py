"""Tests for retry utilities."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Load module directly
_module_path = Path(__file__).parent.parent / "hodor" / "retry.py"
_spec = importlib.util.spec_from_file_location("retry", _module_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

retry_api = _module.retry_api
retry_network = _module.retry_network
retry_git = _module.retry_git
is_rate_limit_error = _module.is_rate_limit_error
is_transient_error = _module.is_transient_error
TransientError = _module.TransientError
RateLimitError = _module.RateLimitError


class TestRetryDecorators:
    """Tests for retry decorators."""

    def test_retry_api_success_first_try(self):
        """Test that successful function doesn't retry."""
        call_count = 0

        @retry_api(max_attempts=3)
        def successful_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = successful_func()
        assert result == "success"
        assert call_count == 1

    def test_retry_api_retries_on_failure(self):
        """Test that failures are retried."""
        call_count = 0

        @retry_api(max_attempts=3, min_wait=0.01, max_wait=0.02)
        def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Transient failure")
            return "success"

        result = failing_then_success()
        assert result == "success"
        assert call_count == 3

    def test_retry_api_max_attempts_exceeded(self):
        """Test that error is raised after max attempts."""
        call_count = 0

        @retry_api(max_attempts=2, min_wait=0.01, max_wait=0.02)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Always fails")

        with pytest.raises(RuntimeError, match="Always fails"):
            always_fails()

        assert call_count == 2

    def test_retry_network_decorator(self):
        """Test network retry decorator."""
        call_count = 0

        @retry_network(max_attempts=2, min_wait=0.01, max_wait=0.02)
        def network_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Network error")
            return "connected"

        result = network_func()
        assert result == "connected"
        assert call_count == 2

    def test_retry_git_decorator(self):
        """Test git retry decorator."""
        call_count = 0

        @retry_git(max_attempts=2, min_wait=0.01, max_wait=0.02)
        def git_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("Git error")
            return "cloned"

        result = git_func()
        assert result == "cloned"


class TestErrorDetection:
    """Tests for error detection functions."""

    def test_is_rate_limit_error_true(self):
        """Test detection of rate limit errors."""
        rate_limit_errors = [
            RuntimeError("rate limit exceeded"),
            RuntimeError("too many requests"),
            RuntimeError("429 error"),
            RuntimeError("quota exceeded"),
            RuntimeError("throttled response"),
        ]
        for error in rate_limit_errors:
            assert is_rate_limit_error(error), f"Should detect: {error}"

    def test_is_rate_limit_error_false(self):
        """Test non-rate-limit errors."""
        other_errors = [
            RuntimeError("authentication failed"),
            RuntimeError("not found"),
            RuntimeError("permission denied"),
        ]
        for error in other_errors:
            assert not is_rate_limit_error(error), f"Should not detect: {error}"

    def test_is_transient_error_true(self):
        """Test detection of transient errors."""
        transient_errors = [
            TransientError("transient"),
            RuntimeError("connection refused"),
            RuntimeError("timeout error"),
            RuntimeError("temporarily unavailable"),
            RuntimeError("503 service unavailable"),
            RuntimeError("502 bad gateway"),
            RuntimeError("504 gateway timeout"),
            RuntimeError("connection reset by peer"),
            RuntimeError("broken pipe"),
            RuntimeError("network error"),
        ]
        for error in transient_errors:
            assert is_transient_error(error), f"Should detect: {error}"

    def test_is_transient_error_false(self):
        """Test non-transient errors."""
        permanent_errors = [
            RuntimeError("authentication failed"),
            RuntimeError("invalid syntax"),
            RuntimeError("file not found"),
        ]
        for error in permanent_errors:
            assert not is_transient_error(error), f"Should not detect: {error}"


class TestExceptionClasses:
    """Tests for custom exception classes."""

    def test_transient_error(self):
        """Test TransientError exception."""
        error = TransientError("transient failure")
        assert str(error) == "transient failure"
        assert isinstance(error, Exception)

    def test_rate_limit_error(self):
        """Test RateLimitError exception."""
        error = RateLimitError("rate limited")
        assert str(error) == "rate limited"
        assert isinstance(error, TransientError)
