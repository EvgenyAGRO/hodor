"""Retry utilities for transient failure handling.

Provides decorators for retrying operations that may fail due to transient
network issues, rate limits, or temporary service unavailability.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

# Type variable for generic function signatures
F = TypeVar("F", bound=Callable[..., Any])


# Common transient exceptions
class TransientError(Exception):
    """Base class for transient errors that should be retried."""
    pass


class RateLimitError(TransientError):
    """Raised when API rate limits are hit."""
    pass


class NetworkError(TransientError):
    """Raised on network-related failures."""
    pass


class RetryTimeoutError(TransientError):
    """Raised when an operation times out during retry attempts."""
    pass


# Retry configuration constants
DEFAULT_MAX_ATTEMPTS = 3
API_MAX_ATTEMPTS = 3
GIT_MAX_ATTEMPTS = 3
NETWORK_MAX_ATTEMPTS = 3

# Wait time bounds (seconds)
MIN_WAIT = 1
MAX_WAIT = 30
API_MIN_WAIT = 2
API_MAX_WAIT = 60


def retry_api(
    max_attempts: int = API_MAX_ATTEMPTS,
    min_wait: float = API_MIN_WAIT,
    max_wait: float = API_MAX_WAIT,
) -> Callable[[F], F]:
    """Decorator for retrying API calls with exponential backoff.

    Handles rate limits and transient API errors. Uses random exponential
    backoff to avoid thundering herd problems.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)

    Returns:
        Decorated function with retry logic

    Example:
        @retry_api()
        def call_gitlab_api():
            ...
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            @retry(
                stop=stop_after_attempt(max_attempts),
                wait=wait_random_exponential(multiplier=1, min=min_wait, max=max_wait),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            def inner() -> Any:
                return func(*args, **kwargs)

            try:
                return inner()
            except RetryError as e:
                # Re-raise the original exception
                raise e.last_attempt.exception() from e

        return wrapper  # type: ignore
    return decorator


def retry_network(
    max_attempts: int = NETWORK_MAX_ATTEMPTS,
    min_wait: float = MIN_WAIT,
    max_wait: float = MAX_WAIT,
) -> Callable[[F], F]:
    """Decorator for retrying network operations.

    Handles connection errors, timeouts, and other network-related failures.
    Uses exponential backoff with jitter.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)

    Returns:
        Decorated function with retry logic
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            @retry(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            def inner() -> Any:
                return func(*args, **kwargs)

            try:
                return inner()
            except RetryError as e:
                raise e.last_attempt.exception() from e

        return wrapper  # type: ignore
    return decorator


def retry_git(
    max_attempts: int = GIT_MAX_ATTEMPTS,
    min_wait: float = MIN_WAIT,
    max_wait: float = MAX_WAIT,
) -> Callable[[F], F]:
    """Decorator for retrying git operations.

    Handles transient git failures like network issues during clone/fetch.

    Args:
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)

    Returns:
        Decorated function with retry logic
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            @retry(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            def inner() -> Any:
                return func(*args, **kwargs)

            try:
                return inner()
            except RetryError as e:
                raise e.last_attempt.exception() from e

        return wrapper  # type: ignore
    return decorator


def is_rate_limit_error(exception: BaseException) -> bool:
    """Check if an exception indicates a rate limit error.

    Args:
        exception: The exception to check

    Returns:
        True if the exception indicates rate limiting
    """
    error_str = str(exception).lower()
    rate_limit_indicators = [
        "rate limit",
        "too many requests",
        "429",
        "quota exceeded",
        "throttl",
    ]
    return any(indicator in error_str for indicator in rate_limit_indicators)


def is_transient_error(exception: BaseException) -> bool:
    """Check if an exception is likely transient and worth retrying.

    Args:
        exception: The exception to check

    Returns:
        True if the error appears transient
    """
    if isinstance(exception, TransientError):
        return True

    error_str = str(exception).lower()
    transient_indicators = [
        "connection",
        "timeout",
        "temporarily",
        "unavailable",
        "503",
        "502",
        "504",
        "reset by peer",
        "broken pipe",
        "network",
    ]
    return any(indicator in error_str for indicator in transient_indicators)
