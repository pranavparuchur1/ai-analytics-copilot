"""Client for an OpenAI-compatible chat endpoint (the inference-gateway by default)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from analytics_copilot.config import get_settings


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


class LLM(Protocol):
    model: str

    def complete(self, messages: list[dict], *, max_tokens: int = 512,
                 temperature: float = 0.0) -> LLMResponse: ...


class LLMError(Exception):
    pass


class GatewayLLM:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout_s: float = 120.0, retries: int = 2):
        s = get_settings()
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        self.model = model or s.llm_model
        self.retries = retries
        self._client = httpx.Client(
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key or s.llm_api_key}"})

    def complete(self, messages: list[dict], *, max_tokens: int = 512,
                 temperature: float = 0.0) -> LLMResponse:
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature}
        t0 = time.perf_counter()
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._client.post(f"{self.base_url}/chat/completions", json=body)
                if r.status_code in (429, 502, 503) and attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                usage = data.get("usage") or {}
                return LLMResponse(
                    text=data["choices"][0]["message"]["content"] or "",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_ms=(time.perf_counter() - t0) * 1000)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                last = e
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM request failed: {last}")


class ScriptedLLM:
    """Test double: returns canned replies in order and records the prompts it was sent."""

    def __init__(self, replies: list[str], model: str = "scripted"):
        self.replies = list(replies)
        self.model = model
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], *, max_tokens: int = 512,
                 temperature: float = 0.0) -> LLMResponse:
        self.calls.append(messages)
        if not self.replies:
            raise LLMError("ScriptedLLM ran out of replies")
        text = self.replies.pop(0)
        return LLMResponse(text, sum(len(m["content"]) // 4 for m in messages),
                           len(text) // 4, 1.0)
