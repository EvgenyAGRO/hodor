"""Hodor - AI-powered code review agent that finds bugs and security issues."""

from .agent import review_pr
from .cli import main

__version__ = "0.1.0"
__all__ = ["review_pr", "main"]
