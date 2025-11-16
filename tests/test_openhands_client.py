import pytest

pytest.importorskip("openhands.sdk", reason="OpenHands SDK is required to import openhands_client module")

from hodor.llm.openhands_client import describe_model


@pytest.mark.parametrize(
    "model,normalized,supports_reasoning,effort",
    [
        ("gpt-5", "openai/responses/gpt-5", True, "medium"),
        ("openai/gpt-5-2025-08-07", "openai/responses/gpt-5-2025-08-07", True, "medium"),
        ("gpt-5-mini", "openai/gpt-5-mini", True, "medium"),
        ("openai/responses/gpt-5-mini", "openai/gpt-5-mini", True, "medium"),
        ("o3-mini", "openai/o3-mini", True, "medium"),
        ("o1-preview", "openai/o1-preview", True, "medium"),
        ("anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5", False, "none"),
    ],
)
def test_describe_model_normalization(model, normalized, supports_reasoning, effort):
    metadata = describe_model(model)
    assert metadata.normalized == normalized
    assert metadata.supports_reasoning == supports_reasoning
    assert metadata.default_reasoning_effort == effort


def test_describe_model_requires_value():
    with pytest.raises(ValueError):
        describe_model("")
