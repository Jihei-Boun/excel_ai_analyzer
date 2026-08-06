"""결측 행 필터 요청·결과."""
from __future__ import annotations

import re

import pandas as pd

from core.io.text_normalize import normalize_text

def is_missing_rows_request(prompt: str) -> bool:
    """결측이 있는 '행'을 보여달라는 요청인지 판별한다.

    '컬럼별 결측치 개수' 같은 스키마 요약과 구분한다.
    """
    if not prompt or not str(prompt).strip():
        return False
    compact = re.sub(r"\s+", "", normalize_text(prompt)).lower()
    has_missing = any(
        token in compact
        for token in ("결측", "null", "missing", "nan", "비어있", "빈값", "누락")
    )
    if not has_missing:
        return False

    # 스키마/집계 요약으로 보이는 경우 제외
    if any(
        token in compact
        for token in (
            "개수",
            "갯수",
            "타입",
            "dtype",
            "컬럼별",
            "열별",
            "데이터타입",
            "행수",
            "열수",
        )
    ):
        return False

    # 행 단위 필터 의도
    if any(
        token in compact
        for token in ("행만", "행을", "행보여", "행알려", "로우", "rows", "row")
    ):
        return True
    if "행" in compact and any(
        token in compact
        for token in ("있는", "포함", "보여", "알려", "필터", "추출", "골라", "찾아")
    ):
        return True
    return False


def filter_missing_rows(df: pd.DataFrame) -> pd.DataFrame:
    """하나 이상의 결측 셀이 있는 행만 남긴다."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    mask = df.isna().any(axis=1)
    return df.loc[mask].reset_index(drop=True)


def build_missing_rows_outcome(
    df: pd.DataFrame,
    *,
    label: str = "현재 데이터",
) -> tuple[str, pd.DataFrame | None]:
    """결측 행 필터 결과 (reply, dataframe)."""
    if df is None or df.empty:
        return f"`{label}`에 표시할 데이터가 없습니다.", None

    filtered = filter_missing_rows(df)
    if filtered.empty:
        return f"`{label}`에서 결측값이 있는 행을 찾지 못했습니다.", None

    missing_cols = [
        str(col)
        for col in filtered.columns
        if bool(filtered[col].isna().any())
    ]
    col_note = ", ".join(f"`{c}`" for c in missing_cols[:8])
    more = f" 외 {len(missing_cols) - 8}개" if len(missing_cols) > 8 else ""
    reply = (
        f"결측값이 있는 행 {len(filtered):,}개 "
        f"(전체 {len(df):,}행 중)"
        + (f" · 관련 열: {col_note}{more}" if missing_cols else "")
    )
    return reply, filtered

