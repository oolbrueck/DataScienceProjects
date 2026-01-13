from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

import requests


class LlmClient(Protocol):
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


@dataclass
class OpenAIChatConfig:
    api_key: Optional[str] = None
    base_url: str = "https://api.openai.com/v1/chat/completions"
    model: str = "gpt-4o-mini"
    timeout_s: int = 60
    temperature: float = 0.0


class OpenAIChatClient:
    def __init__(self, config: Optional[OpenAIChatConfig] = None) -> None:
        cfg = config or OpenAIChatConfig()
        cfg.api_key = cfg.api_key or os.getenv("OPENAI_API_KEY")
        cfg.base_url = os.getenv("OPENAI_BASE_URL", cfg.base_url)
        cfg.model = os.getenv("OPENAI_MODEL", cfg.model)

        if not cfg.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAIChatClient")

        self.config = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            }
        )

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful JSON classifier. Respond with JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        resp = self.session.post(
            self.config.base_url,
            data=json.dumps(payload),
            timeout=self.config.timeout_s,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM API response missing choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError("LLM API response missing content")
        return content


@dataclass
class StaticLlmClient:
    response: str

    def generate(self, prompt: str) -> str:
        _ = prompt
        return self.response


@dataclass
class SequenceLlmClient:
    responses: list[str]

    def generate(self, prompt: str) -> str:
        _ = prompt
        if not self.responses:
            raise RuntimeError("SequenceLlmClient ran out of responses")
        return self.responses.pop(0)
