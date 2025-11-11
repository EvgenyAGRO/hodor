"""File classification and emoji annotation for PR reviews."""

from typing import Literal

FileCategory = Literal[
    "auth",
    "payment",
    "database",
    "api",
    "business_logic",
    "test",
    "docs",
    "ui",
    "config",
    "refactor",
    "deletion",
    "security",
    "performance",
]


# Emoji mapping for file categories
EMOJI_MAP = {
    "auth": "🔥",  # Critical: Authentication/security
    "payment": "🔥",  # Critical: Payment processing
    "database": "🔥",  # Critical: Database/migrations
    "security": "🔒",  # Security-related changes
    "api": "⚠️",  # High risk: API endpoints
    "business_logic": "⚠️",  # High risk: Business logic
    "test": "🧪",  # Test files
    "docs": "📚",  # Documentation
    "ui": "🎨",  # UI/Frontend
    "performance": "⚡",  # Performance-related
    "config": "🔧",  # Configuration
    "refactor": "♻️",  # Refactoring
    "deletion": "🗑️",  # Deleted code
}


# Priority order for sorting (higher number = higher priority)
PRIORITY = {
    "auth": 10,
    "payment": 10,
    "database": 10,
    "security": 9,
    "api": 8,
    "business_logic": 7,
    "performance": 6,
    "test": 5,
    "ui": 4,
    "config": 3,
    "refactor": 2,
    "deletion": 2,
    "docs": 1,
}


def classify_file(file_path: str, patch: str | None = None) -> FileCategory:
    """
    Classify a file based on its path and optionally its diff content.

    Args:
        file_path: Path to the file
        patch: Optional diff patch content

    Returns:
        FileCategory enum value
    """
    path_lower = file_path.lower()

    # Critical: Authentication
    if any(keyword in path_lower for keyword in ["auth", "login", "session", "jwt", "oauth", "password"]):
        return "auth"

    # Critical: Payments
    if any(keyword in path_lower for keyword in ["payment", "billing", "stripe", "paypal", "checkout", "transaction"]):
        return "payment"

    # Critical: Database
    if any(keyword in path_lower for keyword in ["migration", "schema"]) or file_path.endswith(".sql"):
        return "database"

    # Security
    if any(
        keyword in path_lower for keyword in ["security", "crypto", "encrypt", "sanitize", "escape", "cors", "csrf"]
    ):
        return "security"

    # Tests
    if (
        any(keyword in path_lower for keyword in ["test", "spec", "__tests__"])
        or file_path.startswith("tests/")
        or any(
            file_path.endswith(ext) for ext in [".test.js", ".test.ts", ".spec.js", ".spec.ts", "_test.py", "_test.go"]
        )
    ):
        return "test"

    # Documentation
    if file_path.endswith((".md", ".txt", ".rst", ".adoc")) or "docs/" in path_lower or "documentation/" in path_lower:
        return "docs"

    # UI/Frontend
    if (
        any(file_path.endswith(ext) for ext in [".css", ".scss", ".sass", ".less", ".jsx", ".tsx", ".vue", ".svelte"])
        or "component" in path_lower
        or "styles/" in path_lower
        or "ui/" in path_lower
    ):
        return "ui"

    # Configuration
    if (
        file_path in ["package.json", "requirements.txt", "Gemfile", "Cargo.toml", "go.mod", "pom.xml", ".env.example"]
        or file_path.startswith(".github/")
        or any(keyword in path_lower for keyword in ["config", "dockerfile", ".yml", ".yaml", ".toml"])
    ):
        return "config"

    # API endpoints
    if any(keyword in path_lower for keyword in ["api", "endpoint", "route", "controller", "handler"]):
        return "api"

    # Check patch content if available
    if patch:
        # Pure deletion
        if is_deletion(patch):
            return "deletion"

        # Refactoring (lots of moves/renames, no logic changes)
        if is_pure_refactor(patch):
            return "refactor"

        # Performance-related
        if contains_performance_keywords(patch):
            return "performance"

    # Default to business logic
    return "business_logic"


def is_deletion(patch: str) -> bool:
    """Check if patch is primarily deletions."""
    if not patch:
        return False

    lines = patch.split("\n")
    additions = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))

    # Consider it a deletion if >80% of changes are deletions
    total = additions + deletions
    return total > 0 and (deletions / total) > 0.8


def is_pure_refactor(patch: str) -> bool:
    """
    Heuristic to detect pure refactoring (renames, moves, formatting).
    Not perfect, but catches common patterns.
    """
    if not patch:
        return False

    # Look for signs of refactoring
    refactor_keywords = [
        "rename",
        "move",
        "extract",
        "inline",
        "formatting",
        "whitespace",
    ]

    patch_lower = patch.lower()
    return any(keyword in patch_lower for keyword in refactor_keywords)


def contains_performance_keywords(patch: str) -> bool:
    """Check if patch contains performance-related changes."""
    if not patch:
        return False

    performance_keywords = [
        "cache",
        "optimize",
        "performance",
        "slow",
        "speed",
        "query",
        "index",
        "async",
        "parallel",
        "lazy",
        "memoize",
    ]

    patch_lower = patch.lower()
    return any(keyword in patch_lower for keyword in performance_keywords)


def get_emoji_for_file(file_path: str, patch: str | None = None) -> str:
    """
    Get emoji annotation for a file.

    Args:
        file_path: Path to the file
        patch: Optional diff patch content

    Returns:
        Emoji string
    """
    category = classify_file(file_path, patch)
    return EMOJI_MAP.get(category, "📝")


def get_priority_for_file(file_path: str, patch: str | None = None) -> int:
    """
    Get priority score for a file (for sorting).

    Args:
        file_path: Path to the file
        patch: Optional diff patch content

    Returns:
        Priority score (higher = more important)
    """
    category = classify_file(file_path, patch)
    return PRIORITY.get(category, 0)


def annotate_files(files: list[dict]) -> list[dict]:
    """
    Add emoji annotations and categories to a list of files.

    Args:
        files: List of file dictionaries with 'filename' and optionally 'patch'

    Returns:
        Annotated file list with 'emoji', 'category', and 'priority' fields
    """
    annotated = []

    for file in files:
        filename = file.get("filename", "")
        patch = file.get("patch")

        category = classify_file(filename, patch)
        emoji = EMOJI_MAP.get(category, "📝")
        priority = PRIORITY.get(category, 0)

        annotated.append(
            {
                **file,
                "emoji": emoji,
                "category": category,
                "priority": priority,
            }
        )

    # Sort by priority (descending)
    annotated.sort(key=lambda f: f["priority"], reverse=True)

    return annotated
