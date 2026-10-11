"""Ollama provider for both Ollama Cloud and local/self-hosted Ollama.

Facts checked against docs.ollama.com on 2026-10-10:
- Cloud API base ``https://ollama.com/api`` with ``Authorization: Bearer
  $OLLAMA_API_KEY``; local base ``http://localhost:11434/api`` with no auth.
- ``POST /api/chat`` (``stream: false``) returns ``prompt_eval_count``,
  ``prompt_eval_cached_count``, ``eval_count`` and durations in nanoseconds —
  we record these as actual usage.
- Structured outputs (``format`` JSON schema): the docs state Ollama Cloud does
  not currently support them. In cloud mode we therefore do NOT rely on
  ``format``; JSON is requested in the prompt and validated by Pydantic in
  ``pipeline.analyze`` with bounded retries.
- ``think`` controls reasoning output on thinking models (e.g. gpt-oss accepts
  low/medium/high); thinking tokens are billed as output, so we default to low.
- Embeddings: ``POST /api/embed`` (``input`` str|list) → ``embeddings``.
The API key is read from the environment at call time and never logged.
"""

from __future__ import annotations

import time

import httpx

from ..config import LLMSettings
from ..security import redact
from .base import ChatMessage, LLMAuthError, LLMError, LLMRateLimited, LLMResponse, LLMTimeout, estimate_tokens


class OllamaProvider:
    def __init__(self, settings: LLMSettings, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep) -> None:
        if settings.mode not in ("cloud", "local"):
            raise ValueError("OllamaProvider requires llm.mode cloud or local")
        self.settings = settings
        self.mode = settings.mode
        self._api_key = api_key
        if self.mode == "cloud" and not api_key:
            raise LLMAuthError(f"llm.mode=cloud but environment variable {settings.api_key_env} is not set")
        base = settings.cloud_base_url if self.mode == "cloud" else settings.local_base_url
        headers = {"Content-Type": "application/json"}
        if self.mode == "cloud":
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(base_url=base.rstrip("/"), headers=headers,
                                    timeout=httpx.Timeout(settings.request_timeout_s, connect=15.0), transport=transport)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: dict) -> dict:
        attempts = self.settings.max_retries + 1
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = self._client.post(path, json=payload)
            except httpx.TimeoutException as exc:
                last = LLMTimeout(f"timeout calling {path}")
                if attempt < attempts - 1:
                    self._sleep(2 ** attempt)
                    continue
                raise last from exc
            except httpx.TransportError as exc:
                last = LLMError(redact(f"transport error: {exc}", [self._api_key]))
                if attempt < attempts - 1:
                    self._sleep(2 ** attempt)
                    continue
                raise last from exc
            if resp.status_code in (401, 403):
                raise LLMAuthError(f"Ollama rejected the credentials (HTTP {resp.status_code})")
            if resp.status_code == 429:
                last = LLMRateLimited("Ollama rate limit / concurrency limit reached")
                if attempt < attempts - 1:
                    retry_after = resp.headers.get("retry-after")
                    self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else 5 * (attempt + 1))
                    continue
                raise last
            if resp.status_code >= 500:
                last = LLMError(f"Ollama server error HTTP {resp.status_code}")
                if attempt < attempts - 1:
                    self._sleep(2 ** attempt)
                    continue
                raise last
            if resp.status_code >= 400:
                body = redact(resp.text[:300], [self._api_key])
                raise LLMError(f"Ollama HTTP {resp.status_code}: {body}")
            return resp.json()
        raise last or LLMError("unknown Ollama error")

    def _use_schema(self) -> bool:
        pref = self.settings.send_format_schema
        if pref == "always":
            return True
        if pref == "never":
            return False
        return self.mode == "local"

    def chat(self, messages: list[ChatMessage], *, model: str, max_tokens: int, temperature: float = 0.0,
             json_schema: dict | None = None, think: str | bool | None = None) -> LLMResponse:
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_schema is not None and self._use_schema():
            payload["format"] = json_schema
        if think is not None:
            payload["think"] = think
        t0 = time.monotonic()
        data = self._post("/api/chat", payload)
        latency = int((time.monotonic() - t0) * 1000)
        content = (data.get("message") or {}).get("content", "") or ""
        in_tok, out_tok = data.get("prompt_eval_count"), data.get("eval_count")
        estimated = in_tok is None or out_tok is None
        if in_tok is None:
            in_tok = sum(estimate_tokens(m.content) for m in messages)
        if out_tok is None:
            out_tok = estimate_tokens(content + ((data.get("message") or {}).get("thinking") or ""))
        return LLMResponse(content=content, model=data.get("model", model), input_tokens=int(in_tok),
                           output_tokens=int(out_tok), cached_tokens=int(data.get("prompt_eval_cached_count") or 0),
                           tokens_estimated=estimated, latency_ms=latency, done_reason=data.get("done_reason"),
                           raw={k: v for k, v in data.items() if k != "message"})

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        data = self._post("/api/embed", {"model": model, "input": texts})
        return data.get("embeddings") or []

    def list_models(self) -> list[str]:
        resp = self._client.get("/api/tags")
        if resp.status_code in (401, 403):
            raise LLMAuthError("Ollama rejected the credentials")
        resp.raise_for_status()
        return sorted(m.get("name") or m.get("model") for m in resp.json().get("models", []))
