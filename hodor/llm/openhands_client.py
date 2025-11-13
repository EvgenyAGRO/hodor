"""OpenHands SDK client adapter for Hodor.

This module provides a clean interface to OpenHands SDK, handling:
- LLM configuration and model selection
- API key management (with backward compatibility)
- Agent creation with appropriate tool presets
- Model name normalization
"""

import logging
import os
from typing import Any

from openhands.sdk import LLM
from openhands.tools.preset.default import get_default_agent

logger = logging.getLogger(__name__)


def get_api_key() -> str:
    """Get LLM API key from environment variables.

    Checks in order of precedence:
    1. LLM_API_KEY (OpenHands standard)
    2. ANTHROPIC_API_KEY (backward compatibility)
    3. OPENAI_API_KEY (backward compatibility)

    Returns:
        API key string

    Raises:
        RuntimeError: If no API key is found
    """
    api_key = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("No LLM API key found. Please set one of: LLM_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY")

    return api_key


def normalize_model_name(model: str) -> str:
    """Normalize model names for consistency.

    Handles special cases:
    - gpt-5/o3-mini: Add OpenAI responses prefix
    - Preserves provider prefixes (anthropic/, openai/, etc.)

    Args:
        model: Raw model name from user

    Returns:
        Normalized model name
    """
    model_lower = model.lower()

    # Handle GPT-5 and o3-mini special cases (reasoning models)
    if "gpt-5" in model_lower or "o3-mini" in model_lower:
        # These are reasoning models that need the responses endpoint
        if not model.startswith("openai/"):
            return f"openai/responses/{model}"

    return model


def supports_reasoning(model: str) -> bool:
    """Check if a model supports reasoning/thinking mode.

    Args:
        model: Model name

    Returns:
        True if model supports extended thinking
    """
    model_lower = model.lower()

    # OpenAI reasoning models that use thinking by default
    if "gpt-5" in model_lower or "o3" in model_lower or "o1" in model_lower:
        return True

    # NOTE: Claude models (Sonnet, Opus) CAN use extended thinking, but only
    # when explicitly requested via --reasoning-effort flag. They don't use
    # thinking by default because it makes reviews extremely slow (10-15min).
    # Removed Claude from auto-detection to avoid unintended extended thinking.

    return False


def create_hodor_agent(
    model: str,
    api_key: str | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    base_url: str | None = None,
    verbose: bool = False,
    llm_overrides: dict[str, Any] | None = None,
    skills: list[dict] | None = None,
) -> Any:
    """Create an OpenHands agent configured for Hodor PR reviews.

    Args:
        model: LLM model name (e.g., "anthropic/claude-sonnet-4-5")
        api_key: LLM API key (if None, reads from environment)
        temperature: Sampling temperature (if None, auto-selected based on model)
        reasoning_effort: For reasoning models: "low", "medium", or "high"
        base_url: Custom LLM base URL (optional)
        verbose: Enable verbose logging
        llm_overrides: Additional LLM parameters to pass through
        skills: Repository skills to inject into agent context (from discover_skills())

    Returns:
        Configured OpenHands Agent instance
    """
    # Get API key
    if api_key is None:
        api_key = get_api_key()

    # Normalize model name
    normalized_model = normalize_model_name(model)

    # Build LLM config
    llm_config: dict[str, Any] = {
        "model": normalized_model,
        "api_key": api_key,
        "usage_id": "hodor_agent",  # Identifies this LLM instance for usage tracking
        "drop_params": True,  # Drop unsupported API parameters automatically
    }

    # Add base URL if provided
    if base_url:
        llm_config["base_url"] = base_url

    # Handle temperature
    thinking_active = reasoning_effort is not None or supports_reasoning(normalized_model)

    if temperature is not None:
        llm_config["temperature"] = temperature
    elif thinking_active:
        # Reasoning models require temperature 1.0
        llm_config["temperature"] = 1.0
    else:
        # Default to deterministic for non-reasoning models
        llm_config["temperature"] = 0.0

    # Handle reasoning effort
    if reasoning_effort:
        # User explicitly requested extended thinking
        llm_config["reasoning_effort"] = reasoning_effort
    else:
        # IMPORTANT: OpenHands SDK defaults to reasoning_effort="high"!
        # We must explicitly set "none" to disable extended thinking.
        # Without this, all models will use slow extended thinking mode.
        llm_config["reasoning_effort"] = "none"

    # Apply any user overrides
    if llm_overrides:
        llm_config.update(llm_overrides)

    # Configure logging
    if verbose:
        logging.getLogger("openhands").setLevel(logging.DEBUG)
        logger.info(f"Creating OpenHands agent with model: {normalized_model}")
        logger.info(f"LLM config: {llm_config}")
    else:
        logging.getLogger("openhands").setLevel(logging.WARNING)

    # Create LLM instance
    llm = LLM(**llm_config)

    # Create agent with custom tools optimized for automated code reviews
    # Use subprocess terminal instead of tmux to avoid "command too long" errors
    # that occur when environment has large variables (DIRENV_DIFF, LS_COLORS, etc.)
    from openhands.sdk.agent.agent import Agent
    from openhands.sdk.context.agent_context import AgentContext
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.sdk.context import Skill
    from openhands.sdk.tool.spec import Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.glob import GlobTool
    from openhands.tools.grep import GrepTool
    from openhands.tools.planning_file_editor import PlanningFileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    # Set wider terminal dimensions for better output formatting in OpenHands
    # These environment variables are inherited by the subprocess terminal
    os.environ.setdefault("COLUMNS", "200")
    os.environ.setdefault("LINES", "50")

    tools = [
        Tool(name=TerminalTool.name, params={"terminal_type": "subprocess"}),  # Bash commands
        Tool(name=GrepTool.name),  # Efficient code search via ripgrep
        Tool(name=GlobTool.name),  # Pattern-based file finding
        Tool(name=PlanningFileEditorTool.name),  # Read-optimized file editor for reviews
        Tool(name=FileEditorTool.name),  # Full file editor (if modifications needed)
        Tool(name=TaskTrackerTool.name),  # Task tracking
    ]

    if verbose:
        logger.info(
            f"Configured {len(tools)} tools: terminal, grep, glob, planning_file_editor, file_editor, task_tracker"
        )

    # Create condenser for context management
    condenser = LLMSummarizingCondenser(
        llm=llm.model_copy(update={"usage_id": "condenser"}), max_size=80, keep_first=4
    )

    # Build agent context with repository skills if provided
    context = None
    if skills:
        skill_objects = []
        for skill in skills:
            skill_objects.append(
                Skill(
                    name=skill["name"],
                    content=skill["content"],
                    trigger=skill.get("trigger"),  # Always None for repo skills (always active)
                )
            )
        context = AgentContext(skills=skill_objects)

        if verbose:
            skill_names = ", ".join([s["name"] for s in skills])
            logger.info(f"Injecting {len(skill_objects)} skill(s) into agent context: {skill_names}")

    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": True},  # Always use CLI mode for PR reviews
        condenser=condenser,
        context=context,  # Inject repository skills
    )

    return agent
