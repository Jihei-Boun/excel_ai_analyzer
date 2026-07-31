"""프롬프트·DataFrame 컬럼 매칭."""

from __future__ import annotations

import re

import pandas as pd

from core.constants import (
    AMOUNT_COLUMN_HINTS,
    CODE_METRIC_ABS_MAX,
    CODE_METRIC_INT_RATIO,
    CODE_METRIC_NAME_HINTS,
    CODE_METRIC_SAMPLE_SIZE,
)
from core.excel_loader import find_merged_header_pair, merged_header_base
from core.text_normalize import normalize_text


def _mentioned_columns(df: pd.DataFrame, normalized_prompt: str) -> list[str]:
    """프롬프트에 이름만 등장하는 컬럼을 긴 이름 우선으로 고른다."""
    scored: list[tuple[int, str]] = []
    for column in df.columns:
        normalized = normalize_text(str(column))
        if len(normalized) >= 2 and normalized in normalized_prompt:
            scored.append((len(normalized), column))
    scored.sort(reverse=True)
    if not scored:
        return []
    best = scored[0][0]
    return [column for length, column in scored if length == best]


def _column_prompt_match_length(column: str, normalized_prompt: str) -> int:
    """컬럼명·세그먼트가 프롬프트에 있으면 매칭 길이를 반환한다."""
    generic_parts = {"합계", "합", "계", "평균", "total", "sum", "avg", "mean"}
    col_norm = normalize_text(column)
    if len(col_norm) >= 2 and col_norm in normalized_prompt:
        return len(col_norm)

    parts = [p for p in re.split(r"[_\s]+", str(column)) if p]
    best = 0
    for part in parts:
        part_norm = normalize_text(part)
        if len(part_norm) < 2 or part_norm in generic_parts:
            continue
        if part_norm in normalized_prompt:
            best = max(best, len(part_norm))

    base = normalize_text(merged_header_base(str(column)))
    if len(base) >= 2 and base not in generic_parts and base in normalized_prompt:
        best = max(best, len(base))
    return best


def _is_amount_metric_column(name: str) -> bool:
    normalized = normalize_text(str(name))
    return any(hint in normalized for hint in AMOUNT_COLUMN_HINTS)


def looks_like_code_metric_column(df: pd.DataFrame, column: object) -> bool:
    """비용명(121, 201)처럼 코드성 수치 컬럼인지 판별한다."""
    name = str(column)
    if not pd.api.types.is_numeric_dtype(df[column]):
        return False
    norm = normalize_text(name)
    base = normalize_text(merged_header_base(name))
    name_looks_code = any(
        hint in norm or hint == base for hint in CODE_METRIC_NAME_HINTS
    )
    if not name_looks_code:
        return False
    sample = (
        pd.to_numeric(df[column], errors="coerce")
        .dropna()
        .head(CODE_METRIC_SAMPLE_SIZE)
    )
    if sample.empty:
        return True
    ints = sample.map(
        lambda v: float(v).is_integer() and abs(float(v)) < CODE_METRIC_ABS_MAX
    )
    return bool(ints.mean() > CODE_METRIC_INT_RATIO)


_looks_like_code_metric_column = looks_like_code_metric_column


def resolve_metric_column(df: pd.DataFrame, wanted: str) -> str | None:
    """파일마다 컬럼명이 달라도 같은 수치 열을 찾는다.

    동명이 여러 개면 첫 일치 후보를 반환한다. 합계열 우선 rewrite는 하지 않는다.
    (복합 지표 후보는 schema_hints로 LLM에 힌트한다.)
    """
    if wanted in df.columns:
        return wanted
    target = normalize_text(str(wanted))
    for column in df.columns:
        if normalize_text(str(column)) == target:
            return column
    for column in df.columns:
        norm = normalize_text(str(column))
        if target and (target in norm or norm in target):
            coerced = pd.to_numeric(df[column], errors="coerce")
            if coerced.notna().any() or pd.api.types.is_numeric_dtype(df[column]):
                return column
    return None


_resolve_metric_column = resolve_metric_column


def _metric_column_preference(column: str) -> tuple[int, int]:
    """스키마 힌트 정렬용 — 합계·금액열 우선 점수."""
    norm = normalize_text(column)
    total_rank = 1 if (norm.endswith("합계") or norm.endswith("_합계")) else 0
    amount_rank = 1 if _is_amount_metric_column(column) else 0
    return (total_rank, amount_rank)


def _is_explicit_groupby_prompt(prompt: str) -> bool:
    normalized = normalize_text(prompt)
    return (
        "별" in prompt
        or "그룹" in prompt
        or "groupby" in normalized
        or "groupby" in prompt.lower()
    )


def find_mentioned_column(df: pd.DataFrame, prompt: str) -> str | None:
    """프롬프트에 언급된 컬럼명을 긴 이름 우선으로 고른다."""
    mentioned = _mentioned_columns(df, normalize_text(prompt))
    return mentioned[0] if mentioned else None


_ALL_NUMERIC_PHRASES = (
    "숫자형컬럼",
    "수치형컬럼",
    "숫자컬럼",
    "수치컬럼",
    "모든숫자형",
    "전체숫자형",
    "numericcolumn",
    "numericcolumns",
    "allnumeric",
)


def wants_all_numeric_metrics(prompt: str) -> bool:
    """구체 컬럼명 없이 '숫자형 컬럼 합계'처럼 전 수치 컬럼을 요청했는지."""
    if not prompt:
        return False
    compact = normalize_text(prompt)
    return any(phrase in compact for phrase in _ALL_NUMERIC_PHRASES)


def list_numeric_metric_columns(df: pd.DataFrame) -> list[str]:
    """집계에 쓸 수치 컬럼 목록 (datetime·코드성 컬럼 제외)."""
    if df is None or df.empty:
        return []
    selected: list[str] = []
    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            continue
        coerced = pd.to_numeric(df[column], errors="coerce")
        if not coerced.notna().any() and not pd.api.types.is_numeric_dtype(df[column]):
            continue
        if looks_like_code_metric_column(df, column):
            continue
        selected.append(column)
    return selected


def find_mentioned_numeric_column(df: pd.DataFrame, prompt: str) -> str | None:
    """프롬프트에 언급된 수치형 컬럼을 하나 찾는다."""
    columns = find_mentioned_numeric_columns(df, prompt)
    return columns[0] if columns else None


def find_mentioned_numeric_columns(df: pd.DataFrame, prompt: str) -> list[str]:
    """프롬프트에 언급된 수치형 컬럼을 모두 찾는다 (금액 컬럼·합계열 우선).

    '숫자형 컬럼'처럼 전체를 요청하면 언급된 이름이 없을 때 전 수치 컬럼을 반환한다.
    """
    normalized_prompt = normalize_text(prompt)
    group_col = find_groupby_column(df, prompt)
    group_norms = set()
    if group_col:
        group_norms.add(normalize_text(str(group_col)))
        group_norms.add(normalize_text(merged_header_base(str(group_col))))

    scored: list[tuple[int, int, str]] = []
    for column in df.columns:
        coerced = pd.to_numeric(df[column], errors="coerce")
        if not coerced.notna().any() and not pd.api.types.is_numeric_dtype(df[column]):
            continue
        col_name = str(column)
        col_norm = normalize_text(col_name)
        if col_norm in group_norms or normalize_text(merged_header_base(col_name)) in group_norms:
            continue

        match_len = _column_prompt_match_length(col_name, normalized_prompt)
        if match_len <= 0:
            continue

        amount_bonus = 100 if _is_amount_metric_column(col_name) else 0
        total_bonus = 0
        wants_total = any(k in normalized_prompt for k in ("합계", "합을", "합산", "의합", "총합"))
        is_total_col = col_norm.endswith("합계") or col_norm.endswith("_합계")
        if wants_total and is_total_col:
            total_bonus = 50
        code_penalty = -120 if looks_like_code_metric_column(df, column) else 0
        scored.append((amount_bonus + total_bonus + code_penalty, match_len, column))

    if not scored:
        if wants_all_numeric_metrics(prompt):
            return [
                col
                for col in list_numeric_metric_columns(df)
                if normalize_text(str(col)) not in group_norms
                and normalize_text(merged_header_base(str(col))) not in group_norms
            ]
        return []

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score = scored[0][0]
    top = [item for item in scored if item[0] == best_score]
    top.sort(key=lambda item: item[1], reverse=True)

    selected: list[str] = []
    for _score, _match_len, column in top:
        base = normalize_text(merged_header_base(str(column)))
        if any(base == normalize_text(merged_header_base(prev)) for prev in selected):
            continue
        selected.append(column)
    return selected


def find_groupby_column(df: pd.DataFrame, prompt: str) -> str | None:
    """'비용명별', '비목분류 별로'처럼 그룹 기준 컬럼을 찾는다."""
    if df is None or df.empty or not prompt:
        return None

    match = re.search(r"([0-9A-Za-z가-힣]+)\s*별(?:로)?", prompt)
    if not match:
        return None

    key = match.group(1)
    return _resolve_axis_column(df, key)


def _resolve_axis_column(df: pd.DataFrame, key: str) -> str | None:
    """축/그룹 키를 실제 컬럼명으로 해석한다. 병합 헤더는 명칭(오른쪽)을 우선한다."""
    key_norm = normalize_text(key)
    if len(key_norm) < 2:
        return None

    pair = find_merged_header_pair(df.columns, key)
    if pair:
        return pair[1]

    exact: list[str] = []
    partial: list[tuple[int, str]] = []
    for column in df.columns:
        name = str(column)
        norm = normalize_text(name)
        base = normalize_text(merged_header_base(name))
        if norm == key_norm or base == key_norm:
            exact.append(name)
        elif key_norm in norm or key_norm in base:
            partial.append((len(norm), name))

    if exact:
        exact.sort(
            key=lambda col: (
                0 if pd.api.types.is_numeric_dtype(df[col]) else 1,
                len(str(col)),
            ),
            reverse=True,
        )
        return exact[0]
    if partial:
        partial.sort(reverse=True)
        return partial[0][1]
    return None

