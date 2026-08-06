"""분석 결과 해석 — 제공된 수치만으로 자연어 설명."""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis_plan_types import AnalysisPlan
from core.llm_client import chat_text


def interpret_analysis_result(
    prompt: str,
    result_df: pd.DataFrame,
    plan: AnalysisPlan,
    *,
    exec_meta: dict[str, Any] | None = None,
    base_url: str,
    model: str,
    chat_text_fn: Callable[..., str] | None = None,
    profile_name: str | None = None,
) -> str:
    """계산 결과 JSON만 근거로 해석 문장을 생성한다."""
    from core.profile_loader import interpret_guidance_for

    meta = exec_meta or {}
    payload = {
        "user_request": prompt,
        "criteria_note": plan.criteria_note,
        "table": _df_to_records(result_df),
        "comparison": meta.get("comparison") or [],
        "structured": meta.get("structured") or [],
        "distribution": meta.get("distribution"),
        "correlation": meta.get("correlation"),
        "warnings": meta.get("warnings") or [],
        "aggregate_sources": meta.get("aggregate_sources") or {},
    }

    system = (
        "당신은 표 계산 결과를 해석하는 분석가입니다. "
        "반드시 한국어만 사용하세요. 중국어·영어·기타 언어로 쓰지 마세요. "
        "제공된 JSON에 없는 수치·항목·비율을 만들지 마세요. "
        "상관관계와 원인을 구분하세요. "
        "한국어로 간결히 작성하세요. "
        "correlation이면 1) 전체 상관 2) 분포·양수 표본 3) 결론 순으로, "
        "그 외에는 가능하면 1) 전체 비교 2) 그룹별 특징 3) 결론 순으로 쓰세요."
    )
    domain_guidance = interpret_guidance_for(profile_name=profile_name)
    if domain_guidance:
        system = f"{system} {domain_guidance}"
    user = (
        "다음 계산 결과만 근거로 사용자 요청에 답하세요.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    fn = chat_text_fn or chat_text
    text = fn(
        user,
        system=system,
        base_url=base_url,
        model=model,
    )
    return (text or "").strip()


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    records: list[dict[str, Any]] = []
    for _, row in df.head(80).iterrows():
        item: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                item[str(col)] = None
            elif hasattr(val, "item"):
                try:
                    item[str(col)] = val.item()
                except (ValueError, AttributeError):
                    item[str(col)] = val
            else:
                item[str(col)] = val
        records.append(item)
    return records
