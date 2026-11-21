"""Tiny LLM client over plain HTTP (no SDKs needed).

Providers:
  anthropic : Anthropic Messages API
  openai    : any OpenAI-compatible /chat/completions endpoint
              (OpenAI, Groq, Together, local Ollama via http://localhost:11434/v1 ...)
  mock      : no network; callers fall back to their rule-based logic
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from .config import settings

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4o-mini",
}


class LLMUnavailable(RuntimeError):
    pass


def is_enabled() -> bool:
    return settings.llm_provider in ("anthropic", "openai")


def complete(system: str, user: str, max_tokens: int = 800, temperature: float = 0.2) -> str:
    provider = settings.llm_provider
    model = settings.llm_model or DEFAULT_MODELS.get(provider, "")

    if provider == "anthropic":
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])

    if provider == "openai":
        base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"content-type": "application/json"}
        if settings.llm_api_key:
            headers["authorization"] = f"Bearer {settings.llm_api_key}"
        r = requests.post(
            f"{base}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    raise LLMUnavailable(f"LLM provider '{provider}' is not a network provider")


def complete_json(system: str, user: str, max_tokens: int = 800) -> dict[str, Any]:
    """Ask for JSON and parse the first {...} block in the answer."""
    text = complete(system + "\nAnswer with a single JSON object only, no prose.", user, max_tokens)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in LLM answer: {text[:200]}")
    return json.loads(match.group(0))
