"""선택 vs 연산 의도 분류."""

from __future__ import annotations

from core.llm_client import chat_json

SYSTEM = """당신은 엑셀 데이터 분석 의도 분류기입니다.
사용자 요청이 아래 중 무엇인지 판단하세요.

- selection: 특정 행/열/조건으로 데이터를 필터링·선택
- operation: 합계, 평균, 그룹화, 피벗, 정렬 등 연산·집계

반드시 JSON만 반환하세요: {"intent": "selection" | "operation", "reason": "간단한 이유"}
"""


def classify_intent(prompt: str, *, base_url: str, model: str) -> str:
    result = chat_json(
        prompt,
        system=SYSTEM,
        base_url=base_url,
        model=model,
    )
    intent = str(result.get("intent", "selection")).strip().lower()
    if intent not in {"selection", "operation"}:
        return _fallback_intent(prompt)
    return intent


def _fallback_intent(prompt: str) -> str:
    operation_keywords = ("합계", "평균", "총", "집계", "그룹", "피벗", "정렬", "count", "sum", "mean", "group")
    lowered = prompt.lower()
    if any(keyword in lowered for keyword in operation_keywords):
        return "operation"
    return "selection"
