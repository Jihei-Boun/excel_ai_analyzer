"""2단계: 연산 (PandasAI + 간단 폴백)."""

from __future__ import annotations

import re

import pandas as pd

from core.pandasai_config import chat


def run_operation(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
) -> tuple[object, str]:
    """PandasAI로 합계·평균·그룹화 등 연산을 수행한다."""
    query = (
        "다음 요청에 맞게 합계, 평균, 그룹화, 피벗, 정렬 등 연산을 수행하세요.\n"
        "수치 연산은 숫자형 컬럼에만 적용하세요.\n"
        f"요청: {prompt}"
    )
    try:
        result, summary = chat(
            df,
            query,
            base_url=base_url,
            model=model,
        )
        return result, summary
    except Exception as primary_error:
        fallback = _heuristic_operation(df, prompt)
        if fallback is not None:
            value, summary = fallback
            return value, f"{summary} (AI 실패 후 폴백: {primary_error})"
        raise


def _heuristic_operation(df: pd.DataFrame, prompt: str) -> tuple[object, str] | None:
    text = prompt.strip()
    numeric_cols = list(df.select_dtypes(include="number").columns)
    if not numeric_cols:
        return None

    target = numeric_cols[0]
    for col in numeric_cols:
        if col in text:
            target = col
            break
    else:
        for col in numeric_cols:
            if any(token and token in col for token in re.findall(r"[가-힣A-Za-z0-9_]+", text)):
                target = col
                break

    series = df[target]
    if any(k in text for k in ("합계", "총합", "sum", "총")):
        value = float(series.sum())
        return value, f"{target} 합계: {value:,.0f}"
    if any(k in text for k in ("평균", "mean", "avg")):
        value = float(series.mean())
        return value, f"{target} 평균: {value:,.0f}"
    if any(k in text for k in ("최댓값", "최대", "max")):
        value = float(series.max())
        return value, f"{target} 최댓값: {value:,.0f}"
    if any(k in text for k in ("최솟값", "최소", "min")):
        value = float(series.min())
        return value, f"{target} 최솟값: {value:,.0f}"
    return None
