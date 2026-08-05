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
) -> str:
    """계산 결과 JSON만 근거로 해석 문장을 생성한다."""
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
        "correlation 메타가 있으면: 상관계수(r≈0이면 무상관)와 "
        "양수 표본 희소성(둘다_양수 행이 적음)을 중심으로 쓰고, "
        "가집행 대비 집행률·0% 비율 해석으로 바꾸지 마세요. "
        "둘다 양수인 소수 행의 강한 상관을 전체 결론으로 단정하지 마세요. "
        "항목 탐색(이월예산·당해집행 0 등)이면: 조건 정의, "
        "완전 미집행 vs 이월집행만 있는 유형, 규모상 우선 점검 항목, "
        "잔액/불용 리스크를 짧게 설명하세요. "
        "집행률이 낮다는 사실만으로 비효율·문제라고 단정하지 마세요. "
        "한국어로 간결히 작성하세요. "
        "correlation이면 1) 전체 상관 2) 분포·양수 표본 3) 결론 순으로, "
        "그 외에는 가능하면 1) 전체 비교 2) 그룹별 특징 3) 결론 순으로 쓰세요."
    )
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
    for _, row in df.head(50).iterrows():
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
