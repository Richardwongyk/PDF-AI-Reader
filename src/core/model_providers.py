"""Model provider helpers for LiteLLM-compatible model names."""

from __future__ import annotations


def normalize_litellm_model(model: str | None) -> str:
    """Return a LiteLLM model id with provider prefix when it can be inferred."""
    value = str(model or "").strip()
    if not value or "/" in value:
        return value
    lowered = value.lower()
    if lowered.startswith("deepseek"):
        return f"deepseek/{value}"
    return value


def provider_family(model: str | None) -> str:
    """Return the provider family used for sharing API keys across model variants."""
    value = normalize_litellm_model(model)
    if "/" in value:
        return value.split("/", 1)[0]
    return value.split("-", 1)[0] if value else ""
