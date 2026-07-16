"""자연어 요청을 PandasAI로 실행하는 범용 분석 진입점."""

from __future__ import annotations

import re

import pandas as pd

from core.pandasai_config import chat, prepare_dataframe_for_ai


def run_analysis(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
) -> tuple[object, str]:
    """DataFrame과 사용자 요청을 PandasAI에 전달해 결과를 반환한다."""
    if not prompt.strip():
        raise ValueError("분석 요청을 입력해 주세요.")

    query = (
        "사용자의 요청을 현재 DataFrame의 실제 컬럼명과 데이터 타입에 맞춰 "
        "pandas 연산으로 수행하세요.\n"
        "필터링, 정렬, 집계, 그룹화, 피벗, 통계 등 요청의 종류를 스스로 판단하세요.\n"
        "특정 컬럼이나 데이터 형식을 가정하지 마세요.\n"
        "반복된 상위 분류 값은 원본의 빈 상세 행을 분석용으로 채운 값이므로 "
        "같은 분류의 모든 행을 필터링할 때 사용하세요.\n"
        "'리스트', '목록', '표', '보여줘' 요청은 반드시 DataFrame으로 반환하세요.\n"
        "단일 계산만 숫자나 문자열로 반환하세요.\n"
        f"사용자 요청: {prompt}"
    )
    output_type = "dataframe" if _expects_dataframe(prompt) else None
    try:
        return chat(
            df,
            query,
            base_url=base_url,
            model=model,
            output_type=output_type,
        )
    except RuntimeError:
        if output_type == "dataframe":
            fallback = _filter_by_mentioned_value(df, prompt)
            if fallback is not None:
                return (
                    fallback,
                    f"데이터 값 일치 결과: {len(fallback):,}행",
                )
        raise


def _expects_dataframe(prompt: str) -> bool:
    """표 형태 결과를 요구하는 표현인지 판별한다."""
    lowered = prompt.lower()
    table_keywords = (
        "리스트",
        "목록",
        "표",
        "보여",
        "출력",
        "조회",
        "검색",
        "필터",
        "추출",
        "행",
        "열",
        "상위",
        "하위",
        "정렬",
        "list",
        "table",
        "show",
        "filter",
        "rows",
        "columns",
    )
    return any(keyword in lowered for keyword in table_keywords)


def _filter_by_mentioned_value(
    df: pd.DataFrame,
    prompt: str,
) -> pd.DataFrame | None:
    """요청에 명시된 실제 셀 값으로 행을 찾는 범용 폴백."""
    prepared = prepare_dataframe_for_ai(df)
    normalized_prompt = _normalize_text(prompt)
    matches: list[tuple[int, str, object]] = []

    for column in prepared.columns:
        series = prepared[column]
        if not (pd.api.types.is_string_dtype(series) or series.dtype == object):
            continue
        for value in series.dropna().unique():
            text = str(value).strip()
            normalized = _normalize_text(text)
            if len(normalized) >= 2 and normalized in normalized_prompt:
                matches.append((len(normalized), column, value))

    if not matches:
        return None

    longest = max(length for length, _, _ in matches)
    mask = pd.Series(False, index=prepared.index)
    for length, column, value in matches:
        if length == longest:
            mask |= prepared[column] == value

    result = prepared.loc[mask]
    return result.reset_index(drop=True) if not result.empty else None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()
