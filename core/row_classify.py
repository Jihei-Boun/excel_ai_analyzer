"""공통 행 역할 분류 — detail/subtotal/total/footer/blank."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

import pandas as pd

from core.constants import AMOUNT_COLUMN_HINTS, ITEM_COLUMN_HINTS
from core.summary_utils import cell_text, compact, is_excluded_summary_label, is_grand_total_label
from core.text_normalize import normalize_text

ROW_TYPE_COL = "_row_type"
ROW_CONF_COL = "_row_type_confidence"
ROW_REASONS_COL = "_row_type_reasons"
META_COLUMNS_SET = frozenset({ROW_TYPE_COL, ROW_CONF_COL, ROW_REASONS_COL})


def classify_rows(
    df: pd.DataFrame,
    *,
    dimension_columns: list[str] | None = None,
    summary_row_labels: Iterable[str] | None = None,
    footer_labels: Iterable[str] | None = None,
) -> pd.DataFrame:
    """행별 역할 메타 컬럼을 부여한 복사본을 반환한다. 불확실해도 삭제하지 않는다.

    footer_labels는 프로필에서 주입한다. 기본값은 빈 튜플(도메인 footer 가정 없음).
    """
    if df is None or df.empty:
        return df

    work = df.copy()
    dims = dimension_columns or infer_dimension_columns(work)
    amount_cols = infer_amount_columns(work)
    extra_labels = {normalize_text(x) for x in (summary_row_labels or []) if x}
    footer_set = {compact(str(x)) for x in (footer_labels or []) if x}

    types: list[str] = []
    confs: list[str] = []
    reasons: list[str] = []
    n = len(work)
    for pos, (_, row) in enumerate(work.iterrows()):
        row_type, conf, reason = _classify_one(
            row,
            dims=dims,
            amount_cols=amount_cols,
            position=pos,
            n_rows=n,
            extra_labels=extra_labels,
            footer_set=footer_set,
        )
        types.append(row_type)
        confs.append(conf)
        reasons.append("|".join(reason))

    work[ROW_TYPE_COL] = types
    work[ROW_CONF_COL] = confs
    work[ROW_REASONS_COL] = reasons
    return work


def infer_dimension_columns(df: pd.DataFrame) -> list[str]:
    """라벨/항목 후보 열을 고른다. 특정 파일 컬럼명에 고정하지 않는다."""
    preferred: list[str] = []
    hint_norms = [normalize_text(h) for h in ITEM_COLUMN_HINTS]
    for col in df.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        if pd.api.types.is_numeric_dtype(df[col]) and _looks_like_amount_name(name):
            continue
        norm = normalize_text(name)
        if any(h and h in norm for h in hint_norms):
            preferred.append(name)
    if preferred:
        return preferred[:4]

    text_cols: list[str] = []
    for col in df.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        series = df[col]
        if pd.api.types.is_numeric_dtype(series) and _looks_like_amount_name(name):
            continue
        if pd.api.types.is_string_dtype(series) or series.dtype == object:
            text_cols.append(name)
    return text_cols[:4]


def infer_amount_columns(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for col in df.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        coerced = pd.to_numeric(df[col], errors="coerce")
        if not coerced.notna().any() and not pd.api.types.is_numeric_dtype(df[col]):
            continue
        if _looks_like_amount_name(name) or pd.api.types.is_numeric_dtype(df[col]):
            # 코드처럼 보이는 작은 정수열은 제외
            if _looks_like_code_series(df[col]):
                continue
            cols.append(name)
    return cols


def row_type_distribution(df: pd.DataFrame) -> dict[str, int]:
    if df is None or df.empty or ROW_TYPE_COL not in df.columns:
        return {}
    return dict(Counter(str(x) for x in df[ROW_TYPE_COL].tolist()))


def _classify_one(
    row: pd.Series,
    *,
    dims: list[str],
    amount_cols: list[str],
    position: int,
    n_rows: int,
    extra_labels: set[str],
    footer_set: set[str] | None = None,
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    dim_texts = [cell_text(row[c]) for c in dims if c in row.index]
    non_empty_dims = [t for t in dim_texts if t]
    all_dims_blank = bool(dims) and not non_empty_dims
    # 소계/합계 라벨은 dimension 후보 밖(예: 비목분류)에 있을 수 있다.
    label_texts = _label_texts_from_row(row, amount_cols=amount_cols)
    footers = footer_set or set()

    label_hit = None
    for text in label_texts:
        if is_grand_total_label(text) or (
            is_excluded_summary_label(text) and _is_total_like(text)
        ):
            label_hit = "total"
            reasons.append(f"label_total:{text}")
            break
        if _is_subtotal_like(text):
            label_hit = "subtotal"
            reasons.append(f"label_subtotal:{text}")
            break
        if _is_footer_like(text, footer_set=footers):
            label_hit = "footer"
            reasons.append(f"label_footer:{text}")
            break
        if normalize_text(text) in extra_labels:
            label_hit = "subtotal"
            reasons.append(f"schema_summary_label:{text}")
            break

    amount_filled = 0
    for col in amount_cols:
        if col not in row.index:
            continue
        val = pd.to_numeric(row[col], errors="coerce")
        if pd.notna(val):
            amount_filled += 1
    amounts_only = all_dims_blank and amount_filled > 0
    if amounts_only:
        reasons.append("amounts_without_dimension")

    near_bottom = n_rows > 0 and position >= max(0, n_rows - 3)
    if near_bottom and (all_dims_blank or label_hit in {"total", "footer"}):
        reasons.append("near_bottom")

    # 소계/합계/footer 라벨은 금액 유무와 관계없이 우선한다.
    if label_hit == "total":
        return "total", "high", reasons
    if label_hit == "subtotal":
        return "subtotal", "high", reasons
    if label_hit == "footer":
        return "footer", "high", reasons

    # blank: 차원·금액 모두 비어 있음
    if all_dims_blank and amount_filled == 0:
        return "blank", "high", reasons + ["empty_row"]

    if amounts_only:
        # 라벨 없이 금액만 → footer/total 후보. 하단이면 footer, 아니면 blank에 가깝게
        if near_bottom:
            return "footer", "medium", reasons
        return "blank", "medium", reasons

    if not non_empty_dims:
        return "blank", "medium", reasons + ["blank_dimensions"]

    # 상세로 보이지만 하단에 고액만 있으면 low confidence
    if near_bottom and amount_filled >= max(1, len(amount_cols) // 2):
        return "detail", "low", reasons + ["detail_near_bottom"]

    return "detail", "high", reasons or ["default_detail"]


def _label_texts_from_row(row: pd.Series, *, amount_cols: list[str]) -> list[str]:
    """라벨 판별용 텍스트. 금액·메타 열은 제외하고 등장 순서를 유지한다."""
    amount_set = set(amount_cols)
    texts: list[str] = []
    for col in row.index:
        name = str(col)
        if name.startswith("_") or name in amount_set:
            continue
        text = cell_text(row[col])
        if text:
            texts.append(text)
    return texts


def _looks_like_amount_name(name: str) -> bool:
    norm = normalize_text(name)
    return any(normalize_text(h) in norm for h in AMOUNT_COLUMN_HINTS)


def _looks_like_code_series(series: pd.Series) -> bool:
    non_null = series.dropna()
    if non_null.empty:
        return False
    coerced = pd.to_numeric(non_null, errors="coerce")
    if float(coerced.notna().mean()) < 0.8:
        return False
    vals = coerced.dropna()
    if vals.empty:
        return False
    if float((vals == vals.round()).mean()) < 0.9:
        return False
    return float(vals.abs().max()) < 10_000


def _is_subtotal_like(text: str) -> bool:
    compact_text = compact(text)
    norm = normalize_text(text)
    return compact_text in {"소계"} or norm in {"소계", "subtotal"} or "소계" in compact_text


def _is_total_like(text: str) -> bool:
    compact_text = compact(text)
    norm = normalize_text(text)
    if is_grand_total_label(text):
        return True
    return compact_text in {"합계", "총계"} or norm in {"합계", "총계", "total", "grandtotal"}


def _is_footer_like(text: str, *, footer_set: set[str] | None = None) -> bool:
    if not footer_set:
        return False
    return compact(text) in footer_set


def classification_summary(df: pd.DataFrame) -> dict[str, Any]:
    dist = row_type_distribution(df)
    return {
        "distribution": dist,
        "dimension_columns": infer_dimension_columns(
            df.drop(columns=[c for c in (ROW_TYPE_COL, ROW_CONF_COL, ROW_REASONS_COL) if c in df.columns], errors="ignore")
            if df is not None
            else pd.DataFrame()
        ),
    }
