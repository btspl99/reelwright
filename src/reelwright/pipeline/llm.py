"""A small OpenAI-compatible chat client (works with OpenAI, OpenRouter, Ollama, vLLM, LM Studio, ...)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..config import Settings


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("The model did not return valid JSON.")


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=180)
        self.sleep = sleep
        self.retries = retries

    def _post(self, payload: dict) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return self.client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)

    def complete_json(self, system: str, user: str, max_tokens: int = 2500) -> Any:
        payload: dict = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        last_error = "unknown error"
        for attempt in range(self.retries):
            try:
                response = self._post(payload)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                self.sleep(min(2**attempt, 8))
                continue
            if response.status_code == 400 and "response_format" in payload:
                payload.pop("response_format")  # some servers do not support JSON mode
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                self.sleep(min(2**attempt, 8))
                continue
            if response.status_code >= 400:
                raise LLMError(f"LLM request failed: HTTP {response.status_code} {response.text[:200]}")
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError("Unexpected LLM response shape.") from exc
            return extract_json(content or "")
        raise LLMError(f"LLM request failed after retries: {last_error}")


def get_llm(settings: Settings) -> LLMClient | None:
    if not settings.llm_base_url or not settings.llm_model:
        return None
    return LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
