"""Deterministic offline provider for tests, demos and dry runs.

Two behaviours:
* ``scripted`` — returns queued responses verbatim (used to test invalid JSON,
  timeouts, hallucinations, prompt injection handling).
* ``heuristic`` — builds a plausible, *clearly simulated* answer from the
  structured input block (candidate capabilities and rule hints). It never
  invents values that are not in the input.

Token usage is estimated from text length so budget logic is exercised.
"""

from __future__ import annotations

import json
import re
from collections import deque

from .base import ChatMessage, LLMError, LLMResponse, LLMTimeout, estimate_tokens

_DATA = re.compile(r"<tender_data>\s*(\{.*?\})\s*</tender_data>", re.S)


class FakeProvider:
    mode = "fake"

    def __init__(self, scripted: list[str | Exception] | None = None) -> None:
        self.queue: deque[str | Exception] = deque(scripted or [])
        self.calls: list[dict] = []

    def chat(self, messages: list[ChatMessage], *, model: str, max_tokens: int, temperature: float = 0.0,
             json_schema: dict | None = None, think=None) -> LLMResponse:
        user = messages[-1].content
        self.calls.append({"model": model, "max_tokens": max_tokens, "prompt_chars": sum(len(m.content) for m in messages)})
        if self.queue:
            item = self.queue.popleft()
            if isinstance(item, Exception):
                raise item
            content = item
        else:
            content = self._heuristic(user, max_tokens)
        return LLMResponse(content=content, model=model,
                           input_tokens=sum(estimate_tokens(m.content) for m in messages),
                           output_tokens=min(max_tokens, estimate_tokens(content)), tokens_estimated=True, latency_ms=1)

    def _heuristic(self, user: str, max_tokens: int) -> str:
        m = _DATA.search(user)
        data = json.loads(m.group(1)) if m else {}
        cands = data.get("candidate_capabilities") or []
        hint = float(data.get("rule_hint") or 0)
        cap_ids = [c["id"] if isinstance(c, dict) else c for c in cands][:2]
        if "triage" in user.lower()[:400] or data.get("task") == "triage":
            relevant = "yes" if hint >= 0.3 else ("unclear" if hint >= 0.15 else "no")
            return json.dumps({"relevant": relevant, "capability_ids": cap_ids if relevant != "no" else [],
                               "opportunity_type": data.get("opportunity_type") or "Unknown",
                               "reason": "simulated triage from rule hints", "confidence": round(min(0.9, 0.4 + hint), 2)})
        fit = "existing_offering" if hint >= 0.5 else ("custom_build" if hint >= 0.25 else "not_fit")
        return json.dumps({
            "scope_summary": (data.get("title") or "")[:200],
            "deliverables": [],
            "explicit_technologies": [],
            "hardware_involved": None,
            "capability_ids": cap_ids,
            "fit": fit,
            "requirements": [],
            "consortium_allowed": None,
            "estimated_value_inr": None,
            "contract_duration": None,
            "risks": ["simulated analysis — no model was called"],
            "missing_information": ["tender documents not reviewed"],
            "recommended_next_action": "Download the tender document and review scope and eligibility",
            "confidence": 0.3,
        })

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise LLMError("fake provider has no embeddings")

    def list_models(self) -> list[str]:
        return ["fake-fast", "fake-deep"]


def timeout_error() -> Exception:
    return LLMTimeout("simulated timeout")
