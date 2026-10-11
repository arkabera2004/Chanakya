"""LLM providers (Ollama cloud/local, fake) and budget controls."""

from __future__ import annotations

from ..config import Settings
from .base import (
    BudgetExceeded, ChatMessage, LLMAuthError, LLMError, LLMProvider, LLMRateLimited, LLMResponse, LLMTimeout,
    estimate_tokens,
)
from .budget import BudgetGuard, cost_usd, price_for
from .fake import FakeProvider


def build_provider(settings: Settings) -> LLMProvider | None:
    """Return the configured provider, or None when LLM analysis is disabled."""
    mode = settings.llm.mode
    if mode == "disabled":
        return None
    if mode == "fake":
        return FakeProvider()
    from .ollama import OllamaProvider

    key = settings.secret(settings.llm.api_key_env) if mode == "cloud" else None
    return OllamaProvider(settings.llm, api_key=key)


__all__ = [
    "BudgetExceeded", "BudgetGuard", "ChatMessage", "FakeProvider", "LLMAuthError", "LLMError", "LLMProvider",
    "LLMRateLimited", "LLMResponse", "LLMTimeout", "build_provider", "cost_usd", "estimate_tokens", "price_for",
]
