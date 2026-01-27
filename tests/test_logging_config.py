"""Tests for logging configuration utilities."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add the hodor package to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hodor.logging_config import (
    JSONFormatter,
    ContextFilter,
    setup_logging,
    LogContext,
)


class TestJSONFormatter:
    """Tests for JSONFormatter class."""

    def test_format_basic_record(self):
        """Test formatting a basic log record."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Test message"
        assert data["line"] == 42
        assert "timestamp" in data

    def test_format_with_extra_fields(self):
        """Test formatting with extra fields."""
        formatter = JSONFormatter(include_extra=True)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.custom_field = "custom_value"

        output = formatter.format(record)
        data = json.loads(output)

        assert data.get("custom_field") == "custom_value"

    def test_format_without_extra_fields(self):
        """Test formatting without extra fields."""
        formatter = JSONFormatter(include_extra=False)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.custom_field = "custom_value"

        output = formatter.format(record)
        data = json.loads(output)

        # Extra field should not be included
        assert "custom_field" not in data

    def test_format_with_exception(self):
        """Test formatting with exception info."""
        formatter = JSONFormatter()
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="/test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestContextFilter:
    """Tests for ContextFilter class."""

    def test_filter_adds_context(self):
        """Test that filter adds context to records."""
        filter_instance = ContextFilter(context={"request_id": "abc123"})
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        result = filter_instance.filter(record)

        assert result is True  # Never filters out
        assert hasattr(record, "request_id")
        assert record.request_id == "abc123"

    def test_filter_doesnt_override_existing(self):
        """Test that filter doesn't override existing attributes."""
        filter_instance = ContextFilter(context={"existing": "from_filter"})
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.existing = "original_value"

        filter_instance.filter(record)

        assert record.existing == "original_value"

    def test_update_context(self):
        """Test updating context."""
        filter_instance = ContextFilter()
        filter_instance.update_context(key1="value1", key2="value2")

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        filter_instance.filter(record)

        assert record.key1 == "value1"
        assert record.key2 == "value2"


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_basic_logging(self):
        """Test basic logging setup."""
        context_filter = setup_logging(verbose=False)
        assert context_filter is not None

    def test_setup_verbose_logging(self):
        """Test verbose logging setup."""
        context_filter = setup_logging(verbose=True)
        assert context_filter is not None

    def test_setup_json_logging(self):
        """Test JSON logging setup."""
        context_filter = setup_logging(json_logs=True)
        assert context_filter is not None

    def test_setup_with_context(self):
        """Test setup with initial context."""
        context_filter = setup_logging(
            context={"pr_url": "https://github.com/test/test/pull/1"}
        )
        assert context_filter is not None
        assert context_filter._context.get("pr_url") == "https://github.com/test/test/pull/1"

    def test_setup_with_log_file(self, tmp_path):
        """Test setup with log file."""
        log_file = tmp_path / "test.log"
        context_filter = setup_logging(log_file=log_file)

        # Log something
        logger = logging.getLogger("test_setup")
        logger.info("Test message")

        # File should exist after logging
        # (may need flush)


class TestLogContext:
    """Tests for LogContext context manager."""

    def test_log_context_adds_fields(self):
        """Test that LogContext adds fields temporarily."""
        filter_instance = ContextFilter()

        with LogContext(filter_instance, test_field="test_value"):
            assert filter_instance._context.get("test_field") == "test_value"

        # Should be removed after context
        assert "test_field" not in filter_instance._context

    def test_log_context_restores_old_values(self):
        """Test that LogContext restores old values."""
        filter_instance = ContextFilter(context={"key": "original"})

        with LogContext(filter_instance, key="temporary"):
            assert filter_instance._context.get("key") == "temporary"

        # Should be restored
        assert filter_instance._context.get("key") == "original"

    def test_log_context_without_filter(self):
        """Test LogContext works without filter (no-op)."""
        # Should not raise
        with LogContext(None, test_field="value"):
            pass

    def test_log_context_multiple_fields(self):
        """Test LogContext with multiple fields."""
        filter_instance = ContextFilter()

        with LogContext(filter_instance, field1="value1", field2="value2"):
            assert filter_instance._context.get("field1") == "value1"
            assert filter_instance._context.get("field2") == "value2"

        assert "field1" not in filter_instance._context
        assert "field2" not in filter_instance._context
