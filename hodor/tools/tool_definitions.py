"""Tool definitions for LiteLLM."""

# Tool definitions following LiteLLM format
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_pr_metadata",
            "description": "Get PR title, description, author, timestamps, labels, and status",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                },
                "required": ["owner", "repo", "pr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_pr_files",
            "description": "List all changed files with addition/deletion stats. Essential for understanding PR scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                },
                "required": ["owner", "repo", "pr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_file_diff",
            "description": "Get detailed unified diff for a specific file. Use this to analyze actual code changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                    "file_path": {"type": "string", "description": "Path to the file"},
                },
                "required": ["owner", "repo", "pr_number", "file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_pr_commits",
            "description": "Get list of commits in the PR with messages and metadata",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                },
                "required": ["owner", "repo", "pr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_ci_status",
            "description": "Get CI/CD check status (passed/failed/pending) for the PR",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                },
                "required": ["owner", "repo", "pr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tests",
            "description": "Find test files related to a specific source file. Useful for checking test coverage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "file_path": {"type": "string", "description": "Source file path to find tests for"},
                },
                "required": ["owner", "repo", "file_path"],
            },
        },
    },
]
