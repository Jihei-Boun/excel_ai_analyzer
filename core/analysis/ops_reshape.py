"""표 모양 바꾸기 연산 (행·열 전치)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.schema.row_classify import META_COLUMNS_SET
from core.summary.summary_utils import cell_text

TRANSPOSE_HEADER = "항목"


def transpose_table(
    df: pd.DataFrame,
    *,
    label_column: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """행과 열을 바꾼다.

    라벨 열(지정이 없으면 첫 번째 비숫자 열)의 값이 새 열 이름이 되고,
    나머지 열 이름은 첫 열 ``항목``의 값이 된다. 라벨이 비거나 겹치면
    행 번호나 ``(2)`` 꼬리표로 구분한다. 값은 바꾸지 않는다.
    """
    work = df.drop(columns=[c for c in df.columns if c in META_COLUMNS_SET], errors="ignore")
    work = work.reset_index(drop=True)
    label = _pick_label_column(work, label_column)

    if label is None:
        labels = [str(i + 1) for i in range(len(work))]
        values = work
    else:
        labels = [cell_text(v).strip() for v in work[label]]
        values = work.drop(columns=[label])
    unique_labels = _unique_labels(labels)

    out = values.T.reset_index()
    out.columns = [TRANSPOSE_HEADER, *unique_labels]
    out[TRANSPOSE_HEADER] = out[TRANSPOSE_HEADER].astype(str)
    meta = {
        "label_column": label,
        "source_shape": [int(work.shape[0]), int(work.shape[1])],
        "result_shape": [int(out.shape[0]), int(out.shape[1])],
    }
    return out, meta


def _pick_label_column(df: pd.DataFrame, requested: str | None) -> str | None:
    """지정 열, 없으면 값이 가장 덜 겹치는 비숫자 열(같으면 앞 열)."""
    if requested and requested in df.columns:
        return requested
    best: str | None = None
    best_ratio = -1.0
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        texts = [cell_text(v).strip() for v in df[col]]
        filled = [t for t in texts if t]
        ratio = len(set(filled)) / len(texts) if texts else 0.0
        if ratio > best_ratio:
            best, best_ratio = col, ratio
    return best


def _unique_labels(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for index, text in enumerate(labels):
        base = text or str(index + 1)
        count = seen.get(base, 0) + 1
        seen[base] = count
        out.append(base if count == 1 else f"{base} ({count})")
    return out
