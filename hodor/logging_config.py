"""Structured logging configuration for Hodor.

Provides JSON logging formatter and configuration utilities for
production debugging and observability.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging.

    Outputs logs as JSON objects with consistent fields for parsing
    by log aggregation systems.
    """

    def __init__(self, include_extra: bool = True) -> None:
        """Initialize the JSON formatter.

        Args:
            include_extra: Include extra fields from log records
        """
        super().__init__()
        self.include_extra = include_extra
        # Standard fields to exclude from extra
        self._standard_fields = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
        }

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON.

        Args:
            record: The log record to format

        Returns:
            JSON-formatted log string
        """
        log_data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if self.include_extra:
            for key, value in record.__dict__.items():
                if key not in self._standard_fields:
                    try:
                        # Ensure value is JSON-serializable
                        json.dumps(value)
                        log_data[key] = value
                    except (TypeError, ValueError):
                        log_data[key] = str(value)

        return json.dumps(log_data, default=str)


class ContextFilter(logging.Filter):
    """Filter that adds context to log records.

    Injects common context fields like review_id, platform, etc.
    into all log records.
    """

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        """Initialize the context filter.

        Args:
            context: Dictionary of context fields to add to all records
        """
        super().__init__()
        self._context = context or {}

    def filter(self, record: logging.LogRecord) -> bool:
        """Add context to the log record.

        Args:
            record: The log record to modify

        Returns:
            Always True (never filters out records)
        """
        for key, value in self._context.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True

    def update_context(self, **kwargs: Any) -> None:
        """Update the context with new values.

        Args:
            **kwargs: Key-value pairs to add/update in context
        """
        self._context.update(kwargs)


def setup_logging(
    json_logs: bool = False,
    log_file: Path | str | None = None,
    verbose: bool = False,
    context: dict[str, Any] | None = None,
) -> ContextFilter:
    """Configure logging for Hodor.

    Args:
        json_logs: Use JSON format for logs
        log_file: Optional file path to write logs
        verbose: Enable debug level logging
        context: Initial context to add to all log records

    Returns:
        ContextFilter instance for updating context during execution
    """
    # Determine log level
    level = logging.DEBUG if verbose else logging.INFO

    # Create root logger config
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatter
    if json_logs:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Create context filter
    context_filter = ContextFilter(context)

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    root_logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        root_logger.addHandler(file_handler)

    # Set third-party loggers to WARNING to reduce noise
    for name in ["urllib3", "httpx", "httpcore", "gitlab"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    return context_filter


class LogContext:
    """Context manager for adding temporary logging context.

    Example:
        with LogContext(review_id="abc123", pr_number=42):
            logger.info("Processing review")  # Includes review_id and pr_number
    """

    def __init__(self, filter_instance: ContextFilter | None = None, **kwargs: Any) -> None:
        """Initialize the log context.

        Args:
            filter_instance: The ContextFilter to modify
            **kwargs: Context fields to add
        """
        self._filter = filter_instance
        self._new_context = kwargs
        self._old_values: dict[str, Any] = {}

    def __enter__(self) -> "LogContext":
        """Enter the context and add fields."""
        if self._filter:
            # Save old values and update
            for key in self._new_context:
                if key in self._filter._context:
                    self._old_values[key] = self._filter._context[key]
            self._filter.update_context(**self._new_context)
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit the context and restore old values."""
        if self._filter:
            # Remove new keys and restore old values
            for key in self._new_context:
                if key in self._old_values:
                    self._filter._context[key] = self._old_values[key]
                else:
                    self._filter._context.pop(key, None)
