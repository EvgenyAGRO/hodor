"""OpenHands SDK client adapter for Hodor.

This module provides a clean interface to OpenHands SDK, handling:
- LLM configuration and model selection
- API key management (with backward compatibility)
- Agent creation with appropriate tool presets
- Model name normalization
"""

from dataclasses import dataclass
import logging
import os
import shutil
from typing import Any

from openhands.sdk import LLM
from openhands.tools.preset.default import get_default_agent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelMetadata:
    """Describes the normalized model string plus its capabilities."""

    raw: str
    normalized: str
    supports_reasoning: bool
    default_reasoning_effort: str


@dataclass(frozen=True)
class ModelRule:
    """Rules that customize parsing for specific model families."""

    identifiers: tuple[str, ...]
    provider: str | None = None
    use_responses_endpoint: bool | None = None
    supports_reasoning: bool = False
    default_reasoning_effort: str = "none"

    def matches(self, model: str) -> bool:
        model_lower = model.lower()
        return any(identifier in model_lower for identifier in self.identifiers)


# Ordered from most specific → least specific so substring matches work reliably.
MODEL_RULES: tuple[ModelRule, ...] = (
    # Mini reasoning models should stick to the standard completion endpoint.
    ModelRule(
        identifiers=("gpt-5-mini",),
        provider="openai",
        use_responses_endpoint=False,
        supports_reasoning=True,
        default_reasoning_effort="medium",
    ),
    ModelRule(
        identifiers=("o3-mini",),
        provider="openai",
        use_responses_endpoint=False,
        supports_reasoning=True,
        default_reasoning_effort="medium",
    ),
    # Full GPT-5 models opt into the responses endpoint automatically.
    ModelRule(
        identifiers=("gpt-5",),
        provider="openai",
        use_responses_endpoint=True,
        supports_reasoning=True,
        default_reasoning_effort="medium",
    ),
    # Other OpenAI reasoning families.
    ModelRule(
        identifiers=("o3",),
        provider="openai",
        use_responses_endpoint=False,
        supports_reasoning=True,
        default_reasoning_effort="medium",
    ),
    ModelRule(
        identifiers=("o1",),
        provider="openai",
        use_responses_endpoint=False,
        supports_reasoning=True,
        default_reasoning_effort="medium",
    ),
)


def describe_model(model: str) -> ModelMetadata:
    """Return normalized model name plus capability flags."""

    cleaned_model = model.strip()
    if not cleaned_model:
        raise ValueError("Model name must be provided")

    rule = _match_model_rule(cleaned_model)
    normalized = _normalize_model_path(cleaned_model, rule)
    supports_reasoning = rule.supports_reasoning if rule else False
    default_reasoning_effort = rule.default_reasoning_effort if rule else "none"

    return ModelMetadata(
        raw=cleaned_model,
        normalized=normalized,
        supports_reasoning=supports_reasoning,
        default_reasoning_effort=default_reasoning_effort,
    )


def _match_model_rule(model: str) -> ModelRule | None:
    for rule in MODEL_RULES:
        if rule.matches(model):
            return rule
    return None


def _normalize_model_path(model: str, rule: ModelRule | None) -> str:
    segments = [segment for segment in model.split("/") if segment]

    if rule and rule.provider:
        segments = _ensure_provider_segment(segments, rule.provider)

    if rule and rule.use_responses_endpoint is not None:
        segments = _ensure_responses_segment(segments, rule.use_responses_endpoint)

    return "/".join(segments)


def _ensure_provider_segment(segments: list[str], provider: str) -> list[str]:
    normalized = list(segments)
    if not normalized:
        return [provider]
    if normalized[0].lower() == provider.lower():
        normalized[0] = provider
        return normalized
    return [provider, *normalized]


def _ensure_responses_segment(segments: list[str], use_responses: bool) -> list[str]:
    normalized = list(segments)
    has_responses_segment = len(normalized) > 1 and normalized[1].lower() == "responses"
    if use_responses and not has_responses_segment:
        insert_index = 1 if normalized else 0
        normalized.insert(insert_index, "responses")
    elif not use_responses and has_responses_segment:
        normalized.pop(1)
    return normalized


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

    metadata = describe_model(model)
    normalized_model = metadata.normalized

    # Build LLM config
    llm_config: dict[str, Any] = {
        "model": normalized_model,
        "api_key": api_key,
        "usage_id": "hodor_agent",  # Identifies this LLM instance for usage tracking
        "drop_params": True,  # Drop unsupported API parameters automatically
    }

    # Disable encrypted reasoning for all OpenAI reasoning models
    # The "include" parameter is for encrypted reasoning, which causes issues
    # with GPT-5-mini and other models that don't support it
    if metadata.supports_reasoning:
        llm_config["enable_encrypted_reasoning"] = False

    # Add base URL if provided
    if base_url:
        llm_config["base_url"] = base_url

    # Handle temperature
    thinking_active = reasoning_effort is not None or metadata.supports_reasoning

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
        # Default to the capability-aware reasoning mode to keep behaviour predictable.
        # Non-reasoning models explicitly set "none" because OpenHands defaults to "high".
        llm_config["reasoning_effort"] = metadata.default_reasoning_effort

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

    # Set terminal dimensions dynamically based on actual terminal size
    # These environment variables are inherited by the subprocess terminal
    term_size = shutil.get_terminal_size(fallback=(200, 50))
    os.environ.setdefault("COLUMNS", str(term_size.columns))
    os.environ.setdefault("LINES", str(term_size.lines))

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
