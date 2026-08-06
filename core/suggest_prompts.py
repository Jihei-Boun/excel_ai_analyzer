"""업로드 DataFrame 컬럼 기반 추천 질문 생성."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from core.column_match import looks_like_code_metric_column
from core.constants import CHAT_EXAMPLE_LIMIT
from core.profile_loader import column_hints_for, load_profile
from core.text_normalize import normalize_text


_FALLBACK_GENERIC = (
    "파일을 요약해줘",
    "각 컬럼의 데이터 타입과 결측치 개수를 알려줘",
    "첫 번째 숫자형 컬럼의 합계를 구해줘",
    "범주형 컬럼별 행 개수를 표로 보여줘",
)

_FALLBACK_MULTI = (
    "각 파일의 행 수와 컬럼 목록을 비교해줘",
    "두 파일에서 공통으로 있는 컬럼을 알려줘",
    "각 파일의 숫자형 컬럼 합계를 표로 비교해줘",
    "두 파일을 공통 키로 병합한 결과를 보여줘",
)

_FALLBACK_MULTI_SHEET = (
    "각 시트의 행 수와 컬럼 목록을 비교해줘",
    "시트별로 숫자형 컬럼 합계를 표로 비교해줘",
    "시트 간 공통 컬럼을 알려줘",
    "파일을 요약해줘",
)

_MAX_CATEGORY_CARDINALITY = 40


def suggest_example_prompts(
    df: pd.DataFrame | None,
    *,
    profile_name: str | None = None,
    multi_file: bool = False,
    multi_sheet: bool = False,
    limit: int = CHAT_EXAMPLE_LIMIT,
) -> list[str]:
    """모드·컬럼에 맞춰 채팅 예시 프롬프트를 만든다.

    도메인 프로필에 suggested_prompts가 있으면 우선한다.
    일반(generic)에서는 컬럼 기반 동적 질문을 앞에 두고 부족분을 고정 문구로 채운다.
    """
    from core.profile_loader import resolve_profile_name

    limit = max(1, int(limit))
    name = resolve_profile_name(
        profile_name=profile_name,
    )
    if name != "generic":
        return list(
            _profile_prompts(name, multi_file=multi_file, multi_sheet=multi_sheet)
        )[:limit]

    fallback = _fallback_prompts(multi_file=multi_file, multi_sheet=multi_sheet)
    if df is None or df.empty or len(df.columns) == 0:
        return list(fallback)[:limit]

    dynamic = _dynamic_prompts(df)
    return _merge_unique(dynamic, fallback, limit=limit)


def _profile_prompts(
    profile_name: str,
    *,
    multi_file: bool = False,
    multi_sheet: bool = False,
) -> tuple[str, ...]:
    profile = load_profile(profile_name)
    if multi_sheet:
        sheet = profile.get("suggested_prompts_multi_sheet") or ()
        if sheet:
            return tuple(str(p) for p in sheet)
    if multi_file:
        multi = profile.get("suggested_prompts_multi_file") or ()
        if multi:
            return tuple(str(p) for p in multi)
    prompts = profile.get("suggested_prompts") or ()
    if prompts:
        return tuple(str(p) for p in prompts)
    if profile_name == "budget":
        return (
            "파일을 요약해줘",
            "실행예산 합계를 알려줘",
            "비목분류별 집행계 합계를 표로 보여줘",
            "비용명이 121인 데이터만 보여줘",
        )
    return _FALLBACK_GENERIC


def _budget_prompts(*, multi_file: bool = False) -> tuple[str, ...]:
    return _profile_prompts("budget", multi_file=multi_file)


def _fallback_prompts(*, multi_file: bool, multi_sheet: bool) -> tuple[str, ...]:
    if multi_sheet:
        profile = load_profile("generic")
        sheet_prompts = profile.get("suggested_prompts_multi_sheet")
        if sheet_prompts:
            return tuple(str(p) for p in sheet_prompts)
        return _FALLBACK_MULTI_SHEET
    if multi_file:
        profile = load_profile("generic")
        file_prompts = profile.get("suggested_prompts_multi_file")
        if file_prompts:
            return tuple(str(p) for p in file_prompts)
        return _FALLBACK_MULTI
    profile = load_profile("generic")
    prompts = profile.get("suggested_prompts")
    if prompts:
        return tuple(str(p) for p in prompts)
    return _FALLBACK_GENERIC


def _dynamic_prompts(df: pd.DataFrame) -> list[str]:
    prompts: list[str] = ["파일을 요약해줘"]
    cat_cols = _category_columns(df)
    num_cols = _numeric_metric_columns(df)

    if cat_cols:
        prompts.append(f"{cat_cols[0]}별 행 개수를 표로 보여줘")
    if num_cols:
        prompts.append(f"{num_cols[0]} 합계를 구해줘")
    if cat_cols and num_cols:
        prompts.append(f"{cat_cols[0]}별 {num_cols[0]} 합계를 보여줘")

    prompts.append("각 컬럼의 데이터 타입과 결측치 개수를 알려줘")
    return prompts


def _category_columns(df: pd.DataFrame) -> list[str]:
    result: list[str] = []
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_numeric_dtype(series) and not looks_like_code_metric_column(
            df, col
        ):
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        nunique = int(series.nunique(dropna=True))
        if nunique < 2 or nunique > _MAX_CATEGORY_CARDINALITY:
            continue
        result.append(str(col))
    return result


def _numeric_metric_columns(df: pd.DataFrame) -> list[str]:
    amount_first: list[str] = []
    others: list[str] = []
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        if looks_like_code_metric_column(df, col):
            continue
        name = str(col)
        if _looks_amount_name(name):
            amount_first.append(name)
        else:
            others.append(name)
    return amount_first + others


def _looks_amount_name(name: str) -> bool:
    normalized = normalize_text(name)
    return any(hint in normalized for hint in column_hints_for()["amount_column_hints"])


def _merge_unique(
    primary: Sequence[str],
    secondary: Sequence[str],
    *,
    limit: int,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for prompt in list(primary) + list(secondary):
        text = str(prompt).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out
