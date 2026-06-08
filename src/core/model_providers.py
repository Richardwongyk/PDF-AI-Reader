"""Model provider helpers for LiteLLM-compatible model names."""

from __future__ import annotations

_MODEL_ALIASES = {
    "deepseek-chat": "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-chat": "deepseek/deepseek-v4-flash",
}

_MODEL_DISPLAY_NAMES = {
    "deepseek/deepseek-v4-flash": "DeepSeek v4 Flash",
    "deepseek/deepseek-v4-pro": "DeepSeek v4 Pro",
    "openai/gpt-4o": "GPT-4o",
    "qwen/qwen-plus": "Qwen Plus",
    "glm-4": "GLM-4",
    "moonshot-v1-8k": "Moonshot v1 8K",
}


def normalize_litellm_model(model: str | None) -> str:
    """Return a LiteLLM model id with provider prefix when it can be inferred."""
    value = str(model or "").strip()
    lowered = value.lower()
    if lowered in _MODEL_ALIASES:
        return _MODEL_ALIASES[lowered]
    if not value or "/" in value:
        return value
    if lowered.startswith("deepseek"):
        return f"deepseek/{value}"
    return value


def display_model_name(model: str | None) -> str:
    """Return a concise user-facing model name."""
    normalized = normalize_litellm_model(model)
    if normalized in _MODEL_DISPLAY_NAMES:
        return _MODEL_DISPLAY_NAMES[normalized]
    if "/" in normalized:
        return normalized.split("/", 1)[1]
    return normalized or "Mock"


def provider_family(model: str | None) -> str:
    """Return the provider family used for sharing API keys across model variants."""
    value = normalize_litellm_model(model)
    if "/" in value:
        return value.split("/", 1)[0]
    return value.split("-", 1)[0] if value else ""
