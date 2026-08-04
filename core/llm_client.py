"""Ollama chat API — 구조화 JSON 응답용."""

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
    timeout: int = 300,
) -> dict[str, Any]:
    """Ollama chat API로 JSON 객체 응답을 받는다."""
    content = _chat_raw(
        prompt,
        system=system,
        base_url=base_url,
        model=model,
        timeout=timeout,
        format_json=True,
    )
    return _extract_json_object(content)


def chat_text(
    prompt: str,
    *,
    system: str,
    base_url: str,
    model: str,
    timeout: int = 300,
) -> str:
    """Ollama chat API로 일반 텍스트 응답을 받는다."""
    return _chat_raw(
        prompt,
        system=system,
        base_url=base_url,
        model=model,
        timeout=timeout,
        format_json=False,
    ).strip()


def _chat_raw(
    prompt: str,
    *,
    system: str,
    base_url: str,
    model: str,
    timeout: int,
    format_json: bool,
) -> str:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    if format_json:
        payload["format"] = "json"

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return str(response.json()["message"]["content"] or "")


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
