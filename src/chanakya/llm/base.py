"""LLM provider contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    kind = "error"


class LLMAuthError(LLMError):
    kind = "auth_error"


class LLMRateLimited(LLMError):
    kind = "rate_limited"


class LLMTimeout(LLMError):
    kind = "timeout"


class BudgetExceeded(LLMError):
    kind = "budget_blocked"


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    tokens_estimated: bool = False
    latency_ms: int = 0
    done_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    mode: str

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        json_schema: dict | None = None,
        think: str | bool | None = None,
    ) -> LLMResponse: ...

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...

    def list_models(self) -> list[str]: ...


def estimate_tokens(text: str) -> int:
    """Conservative estimate (≈4 chars/token for English; Indic scripts use more
    tokens, so we round up). Used only for pre-call budget checks or when a
    provider returns no usage metadata."""
    return max(1, int(len(text) / 3.5) + 4)
