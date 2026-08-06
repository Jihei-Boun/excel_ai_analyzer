"""DataFrame prep for PandasAI analysis."""

from __future__ import annotations

import re

import pandas as pd

_TOTAL_LABEL_RE = re.compile(
    r"^(?:소\s*계|합\s*계|총\s*계|sub\s*total|grand\s*total|total)$",
    flags=re.IGNORECASE,
)


def prepare_dataframe_for_ai(
    df: pd.DataFrame,
    *,
    stringify_codes: bool = False,
) -> pd.DataFrame:
    """분석용 복사본을 만든다. 원본 DataFrame은 변경하지 않는다.

    hierarchical 분류(그룹 → 빈 상세 → 소계)가 감지된 열만 forward-fill한다.
    stringify_codes=True이면 코드성 수치 열(비용명 121 등)을 문자열로 바꾼다.
    """
    out = df.copy().reset_index(drop=True)
    for col in out.columns:
        if _is_hierarchical_column(out[col]):
            out[col] = _fill_hierarchical_labels(out[col])

    if stringify_codes:
        out = _stringify_code_metric_columns(out)

    # string dtype을 object로 맞춰 LLM 생성 코드 호환성 향상
    for col in out.columns:
        if str(out[col].dtype) == "string":
            out[col] = out[col].fillna("").astype(object)
    return out


def _stringify_code_metric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """비용명 코드처럼 보이는 수치 열을 분석용 문자열로 변환한다."""
    from core.schema.column_match import looks_like_code_metric_column

    out = df
    for column in list(out.columns):
        if not looks_like_code_metric_column(out, column):
            continue
        out = out.copy()
        out[column] = out[column].map(_format_code_cell).astype(object)
    return out


def _format_code_cell(value: object) -> object:
    if _is_blank(value):
        return ""
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric):
        number = float(numeric)
        if number.is_integer():
            return str(int(number))
        return str(number)
    return str(value).strip()


def exclude_total_rows(df: pd.DataFrame) -> pd.DataFrame:
    """합계·소계·총계 행을 제거한다 (모든 문자열 컬럼 검사)."""
    if df is None or df.empty:
        return df

    exclude_mask = pd.Series(False, index=df.index)
    for column in df.columns:
        if pd.api.types.is_numeric_dtype(df[column]):
            continue
        exclude_mask |= df[column].map(is_total_label)

    if not exclude_mask.any():
        return df
    return df.loc[~exclude_mask].reset_index(drop=True)


def sum_metric_excluding_totals(df: pd.DataFrame, metric_col: str) -> float | None:
    """소계/합계 행을 제외하고 수치 컬럼 합계를 구한다."""
    if df is None or df.empty or metric_col not in df.columns:
        return None

    work = exclude_total_rows(prepare_dataframe_for_ai(df))
    if work.empty:
        return None

    total = pd.to_numeric(work[metric_col], errors="coerce").sum(skipna=True)
    if pd.isna(total):
        return None
    return float(total)


def _is_hierarchical_column(series: pd.Series) -> bool:
    """`그룹명 → 빈 행들 → 소계/합계` 패턴인지 확인한다."""
    if pd.api.types.is_numeric_dtype(series):
        return False

    values = series.tolist()
    for index, value in enumerate(values):
        if _is_blank(value) or _is_total_label(value):
            continue

        has_blank_detail = False
        for following in values[index + 1 :]:
            if _is_blank(following):
                has_blank_detail = True
                continue
            if _is_total_label(following):
                if has_blank_detail:
                    return True
                break
            break
    return False


def _fill_hierarchical_labels(series: pd.Series) -> pd.Series:
    filled: list[object] = []
    current_group: object | None = None

    for value in series.tolist():
        if _is_total_label(value):
            filled.append(value)
            current_group = None
        elif _is_blank(value):
            filled.append(current_group if current_group is not None else value)
        else:
            filled.append(value)
            current_group = value

    return pd.Series(filled, index=series.index, dtype="string")


def is_total_label(value: object) -> bool:
    """합계·소계·총계 등 집계 행 라벨인지 확인한다."""
    if _is_blank(value):
        return False
    return bool(_TOTAL_LABEL_RE.fullmatch(str(value).strip()))


def _is_total_label(value: object) -> bool:
    return is_total_label(value)


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if bool(pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    # 엑셀 병합칸이 빈 문자열로 들어오는 경우가 많아 공백도 blank로 본다.
    if isinstance(value, str) and not value.strip():
        return True
    return False
