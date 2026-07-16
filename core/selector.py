"""1단계: 데이터 선택 (비목 구간 우선 + PandasAI + 휴리스틱 폴백)."""

from __future__ import annotations

import re

import pandas as pd

from core.pandasai_config import chat


def run_selection(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
) -> tuple[pd.DataFrame, str]:
    """조건에 맞는 DataFrame을 선택한다.

    예실대비표의 비목(연구활동비 등)은 헤더~소계 전 구간이므로
    비목 구간 선택을 먼저 시도한다.
    """
    section = _section_selection(df, prompt)
    if section is not None and not section.empty:
        names = sorted({str(x) for x in section.get("비목섹션", pd.Series(dtype=str)).unique() if str(x).strip()})
        label = ", ".join(names) if names else "비목"
        return (
            section.reset_index(drop=True),
            f"비목 구간 선택({label}): {len(section):,}행 · 헤더부터 소계 전까지",
        )

    query = (
        "다음 요청에 맞게 행/열을 필터링하거나 선택한 DataFrame만 반환하세요.\n"
        "이 데이터는 예실대비표입니다. '비목섹션' 컬럼이 있으면 그 값으로 "
        "비목 구간(연구활동비 등) 전체를 필터하세요. "
        "비목분류에 값이 없어도 같은 비목섹션이면 모두 포함해야 합니다.\n"
        "문자열 검색은 str.contains(..., na=False, regex=False)를 사용하세요.\n"
        "결과 변수명은 result 로 두고 DataFrame을 반환하세요.\n"
        f"요청: {prompt}"
    )

    try:
        result, summary = chat(
            df,
            query,
            base_url=base_url,
            model=model,
            output_type="dataframe",
        )
        if isinstance(result, pd.DataFrame):
            # AI가 헤더 1행만 골랐으면 비목 구간으로 확장
            expanded = _expand_partial_section_result(df, result, prompt)
            if expanded is not None and len(expanded) > len(result):
                return (
                    expanded.reset_index(drop=True),
                    f"비목 구간으로 확장: {len(expanded):,}행 (AI {len(result)}행 → 구간 전체)",
                )
            return result.reset_index(drop=True), summary
    except Exception as primary_error:
        fallback = _heuristic_selection(df, prompt)
        if fallback is not None and not fallback.empty:
            return (
                fallback.reset_index(drop=True),
                f"휴리스틱 선택 결과: {len(fallback):,}행 (AI 실패 후 폴백: {primary_error})",
            )
        raise

    fallback = _heuristic_selection(df, prompt)
    if fallback is not None and not fallback.empty:
        return (
            fallback.reset_index(drop=True),
            f"휴리스틱 선택 결과: {len(fallback):,}행",
        )

    raise ValueError(
        f"선택 결과가 DataFrame이 아닙니다: {type(result).__name__} — {result!r}"
    )


def _section_selection(df: pd.DataFrame, prompt: str) -> pd.DataFrame | None:
    """비목 헤더~소계 전 구간을 선택한다."""
    if "비목섹션" not in df.columns:
        return None

    keywords = _extract_keywords(prompt)
    if not keywords:
        return None

    # '소계만' 요청은 구간 선택이 아님
    if keywords and all(k.replace(" ", "") in {"소계", "합계"} for k in keywords):
        return None

    known = {
        str(v).strip()
        for v in df["비목섹션"].dropna().unique()
        if str(v).strip() and str(v).strip() not in {"소계", "합계"}
    }
    if not known:
        return None

    matched_sections: list[str] = []
    for keyword in keywords:
        for section in known:
            if keyword == section or keyword in section or section in keyword:
                matched_sections.append(section)

    # 중복 제거, 순서 유지
    matched_sections = list(dict.fromkeys(matched_sections))
    if not matched_sections:
        return None

    mask = df["비목섹션"].isin(matched_sections)
    filtered = df.loc[mask]
    return filtered if not filtered.empty else None


def _expand_partial_section_result(
    df: pd.DataFrame,
    result: pd.DataFrame,
    prompt: str,
) -> pd.DataFrame | None:
    """AI가 비목 헤더만 고른 경우 같은 비목섹션 전체로 확장한다."""
    if "비목섹션" not in df.columns or result.empty:
        return None
    section = _section_selection(df, prompt)
    if section is None or section.empty:
        return None
    # result가 section의 부분집합(또는 헤더만)이면 확장
    if len(result) >= len(section):
        return None
    return section


def _heuristic_selection(df: pd.DataFrame, prompt: str) -> pd.DataFrame | None:
    """간단한 키워드·조건 요청을 pandas로 직접 필터한다."""
    text = prompt.strip()
    if not text:
        return None

    section = _section_selection(df, prompt)
    if section is not None and not section.empty:
        return section

    numeric_match = re.search(
        r"([가-힣A-Za-z0-9_]+?)(?:이|가|은|는)?\s+"
        r"([\d,]+)\s*(만)?\s*원?\s*(이상|이하|초과|미만)",
        text,
    )
    if numeric_match:
        col_hint, number, man, op = numeric_match.groups()
        col = _find_column(df, col_hint)
        if col is not None and pd.api.types.is_numeric_dtype(df[col]):
            value = float(number.replace(",", ""))
            if man:
                value *= 10_000
            series = df[col]
            if op == "이상":
                return df.loc[series >= value]
            if op == "이하":
                return df.loc[series <= value]
            if op == "초과":
                return df.loc[series > value]
            if op == "미만":
                return df.loc[series < value]

    keywords = _extract_keywords(text)
    if not keywords:
        return None

    text_cols = [
        c
        for c in df.columns
        if c != "비목섹션"
        and (pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object)
    ]
    if not text_cols:
        return None

    mask = pd.Series(False, index=df.index)
    for keyword in keywords:
        for col in text_cols:
            mask = mask | df[col].astype(str).str.contains(
                keyword, case=False, na=False, regex=False
            )

    filtered = df.loc[mask]
    return filtered if not filtered.empty else None


def _extract_keywords(prompt: str) -> list[str]:
    cleaned = prompt
    for noise in (
        "보여줘",
        "뽑아줘",
        "추출",
        "리스트",
        "만",
        "행",
        "열",
        "항목",
        "데이터",
        "표시",
        "검색",
        "필터",
        "해줘",
        "해주세요",
        "주세요",
        "으로",
        "로",
        "을",
        "를",
        "과",
        "와",
        "이랑",
        "그리고",
        "또는",
    ):
        cleaned = cleaned.replace(noise, " ")
    parts = [p.strip() for p in re.split(r"[\s,|/]+", cleaned) if len(p.strip()) >= 2]
    stop = {"있는", "없는", "모든", "전체", "해당", "관련"}
    return [p for p in parts if p not in stop]


def _find_column(df: pd.DataFrame, hint: str) -> str | None:
    for col in df.columns:
        if hint in str(col):
            return col
    return None
