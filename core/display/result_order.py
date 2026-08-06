"""결과 행 순서 복원."""
from __future__ import annotations

import pandas as pd

from core.io.text_normalize import normalize_text

_SORT_REQUEST_TOKENS = (
    "정렬",
    "내림차순",
    "오름차순",
    "상위",
    "하위",
    "큰순",
    "작은순",
    "높은순",
    "낮은순",
    "순서대로정렬",
    "sort",
    "orderby",
    "descending",
    "ascending",
)

def wants_explicit_sort(prompt: str) -> bool:
    """사용자가 정렬·순위 변경을 요청했는지."""
    import re

    from core.io.text_normalize import normalize_text

    if not prompt or not str(prompt).strip():
        return False
    compact = re.sub(r"\s+", "", normalize_text(prompt)).lower()
    return any(token in compact for token in _SORT_REQUEST_TOKENS)


def restore_source_row_order(
    result: pd.DataFrame,
    source_df: pd.DataFrame | None,
    *,
    prompt: str = "",
) -> pd.DataFrame:
    """결과 행을 원본(미리보기) 등장 순서로 되돌린다.

    PandasAI가 가나다순 등으로 재정렬한 경우를 보정한다.
    사용자가 정렬을 요청했거나 매칭 키가 없으면 그대로 둔다.
    """
    from collections import defaultdict, deque

    if (
        result is None
        or result.empty
        or source_df is None
        or source_df.empty
        or wants_explicit_sort(prompt)
    ):
        return result

    key_cols = _order_key_columns(result, source_df)
    if not key_cols:
        return result

    source_keys = _row_key_series(source_df, key_cols)
    result_keys = _row_key_series(result, key_cols)
    positions: dict[str, deque[int]] = defaultdict(deque)
    for index, key in enumerate(source_keys):
        positions[key].append(index)

    order: list[int] = []
    for key in result_keys:
        bucket = positions.get(key)
        if not bucket:
            return result
        order.append(bucket.popleft())

    out = result.copy()
    out["_src_order"] = order
    if out["_src_order"].is_monotonic_increasing:
        return result
    return (
        out.sort_values("_src_order", kind="stable")
        .drop(columns=["_src_order"])
        .reset_index(drop=True)
    )


def _order_key_columns(result: pd.DataFrame, source_df: pd.DataFrame) -> list[str]:
    shared = [str(c) for c in source_df.columns if c in result.columns]
    if not shared:
        return []

    # 식별에 유리한 문자/코드 컬럼을 우선
    preferred: list[str] = []
    numeric_shared: list[str] = []
    for col in shared:
        series = source_df[col]
        name = str(col)
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(
            series
        ):
            # 코드성 숫자도 키로 쓸 수 있음
            compact = name.replace(" ", "").replace("_", "").lower()
            if any(token in compact for token in ("코드", "code", "번호", "id")):
                preferred.append(name)
            else:
                numeric_shared.append(name)
            continue
        preferred.append(name)

    keys = preferred or numeric_shared
    # 너무 많은 키는 과적합·부동소수 불일치 위험 → 앞쪽 식별 컬럼 위주
    return keys[:3]


def _row_key_series(df: pd.DataFrame, key_cols: list[str]) -> pd.Series:
    parts = []
    for col in key_cols:
        series = df[col]
        if pd.api.types.is_numeric_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce")
            text = numeric.map(
                lambda value: (
                    ""
                    if pd.isna(value)
                    else str(int(value))
                    if float(value).is_integer()
                    else f"{float(value):.6g}"
                )
            )
        else:
            text = series.map(lambda value: "" if pd.isna(value) else str(value).strip())
        parts.append(text.astype(str))
    if len(parts) == 1:
        return parts[0]
    joined = parts[0]
    for part in parts[1:]:
        joined = joined + "\0" + part
    return joined

