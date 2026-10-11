"""Cost estimation and spending safeguards.

Every call is checked *before* it is made (worst case = estimated input + full
output budget) against per-run cost, per-day cost and per-run call limits. When
a limit would be exceeded the call is refused with ``BudgetExceeded``; the
caller records the opportunity as ``skipped_budget`` and deterministic work
continues. Skipped analysis is never reported as completed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import BudgetSettings, ModelPrice
from .base import BudgetExceeded


def price_for(model: str, pricing: dict[str, ModelPrice]) -> ModelPrice | None:
    if model in pricing:
        return pricing[model]
    family = model.split(":")[0]
    for name, price in pricing.items():
        if name.split(":")[0] == family and ":" not in name:
            return price
    return pricing.get(family)


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_tokens: int,
             pricing: dict[str, ModelPrice], mode: str) -> float:
    if mode in ("local", "fake", "disabled"):
        return 0.0  # local inference: infrastructure cost, not per-token
    p = price_for(model, pricing)
    if p is None:
        return 0.0
    uncached = max(0, input_tokens - cached_tokens)
    cached_rate = p.cached_input if p.cached_input is not None else p.input
    return (uncached * p.input + cached_tokens * cached_rate + output_tokens * p.output) / 1_000_000


@dataclass
class BudgetGuard:
    settings: BudgetSettings
    pricing: dict[str, ModelPrice]
    mode: str
    spent_today_before_run: float = 0.0
    run_cost: float = 0.0
    calls: int = 0
    deep_calls: int = 0
    blocked: int = 0
    blocked_reasons: list[str] = field(default_factory=list)
    unpriced_models: set[str] = field(default_factory=set)

    @property
    def exhausted(self) -> bool:
        return self.calls >= self.settings.max_calls_per_run or self.run_cost >= self.settings.max_cost_per_run_usd

    def check(self, model: str, est_input_tokens: int, max_output_tokens: int, purpose: str) -> float:
        if self.mode in ("cloud",) and price_for(model, self.pricing) is None:
            self.unpriced_models.add(model)
        if self.calls + 1 > self.settings.max_calls_per_run:
            self._block(f"max_calls_per_run ({self.settings.max_calls_per_run}) reached")
        if purpose == "deep" and self.deep_calls + 1 > self.settings.max_deep_analyses_per_run:
            self._block(f"max_deep_analyses_per_run ({self.settings.max_deep_analyses_per_run}) reached")
        if est_input_tokens > self.settings.max_input_tokens_per_call:
            self._block(f"input {est_input_tokens} tokens exceeds max_input_tokens_per_call")
        worst = cost_usd(model, est_input_tokens, max_output_tokens, 0, self.pricing, self.mode)
        if self.run_cost + worst > self.settings.max_cost_per_run_usd:
            self._block(f"max_cost_per_run_usd ({self.settings.max_cost_per_run_usd}) would be exceeded")
        if self.spent_today_before_run + self.run_cost + worst > self.settings.max_cost_per_day_usd:
            self._block(f"max_cost_per_day_usd ({self.settings.max_cost_per_day_usd}) would be exceeded")
        return worst

    def _block(self, reason: str) -> None:
        self.blocked += 1
        if reason not in self.blocked_reasons:
            self.blocked_reasons.append(reason)
        raise BudgetExceeded(reason)

    def record(self, model: str, input_tokens: int, output_tokens: int, cached_tokens: int, purpose: str) -> float:
        c = cost_usd(model, input_tokens, output_tokens, cached_tokens, self.pricing, self.mode)
        self.run_cost += c
        self.calls += 1
        if purpose == "deep":
            self.deep_calls += 1
        return c
