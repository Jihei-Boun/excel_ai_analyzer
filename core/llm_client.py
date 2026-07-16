"""Ollama chat API 공통 유틸."""

from __future__ import annotations

import json
import re
from typing import Any

import requests


def chat_json(
    prompt: str,
    *,
    system: str,
    base_url: str,
    model: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """Ollama chat API로 JSON 응답을 받는다."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return _extract_json_object(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"LLM 응답에서 JSON을 찾지 못했습니다: {text[:200]!r}")

    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM JSON 응답이 객체가 아닙니다.")

    return data
