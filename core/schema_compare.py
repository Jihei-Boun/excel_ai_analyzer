"""스키마·메타 요청 (행 수·컬럼 목록·공통 컬럼·dtype/결측) 규칙 경로."""

from __future__ import annotations

import re

import pandas as pd

from core.prompt_intent import detect_aggregate_op, expects_plot
from core.text_normalize import normalize_text

_SCHEMA_SIGNAL_PHRASES = (
    "행수",
    "열수",
    "컬럼목록",
    "컬럼리스트",
    "열목록",
    "컬럼명",
    "공통컬럼",
    "공통으로있는컬럼",
    "데이터타입",
    "결측치",
    "결측",
    "스키마",
    "구조",
    "dtype",
    "null",
    "missing",
    "columnlist",
    "colnames",
    "commoncolumns",
)

# 스키마가 아닌 분석 요청으로 보이는 표현 (컬럼별 집계 등)
_NON_SCHEMA_MARKERS = (
    "컬럼별",
    "별로",
    "합계",
    "합산",
    "총합",
    "평균",
    "차트",
    "그래프",
    "매출",
    "금액",
    "뽑아",
    "나열",
)


def is_schema_request(prompt: str) -> bool:
    """행·열·컬럼 구조/메타 질문인지 판별한다. 집계·차트는 제외."""
    if not prompt or not str(prompt).strip():
        return False
    if expects_plot(prompt):
        return False
    if detect_aggregate_op(prompt) is not None:
        return False

    normalized = normalize_text(prompt)
    compact = re.sub(r"\s+", "", normalized)

    if _looks_like_groupby_row_count(compact):
        return False

    if any(phrase in compact for phrase in _SCHEMA_SIGNAL_PHRASES):
        return True

    # "각 시트의 행과 컬럼을 비교"처럼 신호 구가 약해도 비교+구조 단서면 인정
    has_compare = any(k in compact for k in ("비교", "알려", "보여", "어때", "무엇"))
    has_struct = any(k in compact for k in ("행", "열", "컬럼", "시트", "파일", "컬럼명"))
    if has_compare and has_struct and not any(m in compact for m in _NON_SCHEMA_MARKERS):
        if "행" in compact and ("컬럼" in compact or "열" in compact):
            return True
    return False


def schema_kind(prompt: str) -> str:
    """스키마 하위 유형: compare | common | dtypes | compare(기본)."""
    compact = re.sub(r"\s+", "", normalize_text(prompt))
    if any(k in compact for k in ("공통컬럼", "공통으로있는컬럼", "commoncolumns")):
        return "common"
    if any(
        k in compact
        for k in ("데이터타입", "결측", "결측치", "dtype", "null", "missing")
    ):
        return "dtypes"
    return "compare"


def build_schema_outcome(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str = "파일",
) -> tuple[str, pd.DataFrame | None]:
    """스키마 요청에 대한 (reply, dataframe)을 만든다."""
    if not named_frames:
        return f"비교할 {unit_label}이(가) 없습니다.", None

    kind = schema_kind(prompt)
    if kind == "common":
        return _common_columns_result(named_frames, unit_label=unit_label)
    if kind == "dtypes":
        return _dtypes_result(named_frames, unit_label=unit_label)
    return _compare_result(named_frames, unit_label=unit_label)


def build_schema_compare_table(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str = "파일",
) -> pd.DataFrame:
    """이름 | 행 수 | 열 수 | 컬럼 목록 비교 표."""
    rows: list[dict] = []
    for name, frame in named_frames:
        cols = [str(c) for c in frame.columns]
        rows.append(
            {
                unit_label: name,
                "행 수": int(len(frame)),
                "열 수": int(len(frame.columns)),
                "컬럼 목록": ", ".join(cols),
            }
        )
    return pd.DataFrame(rows)


def _compare_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
) -> tuple[str, pd.DataFrame]:
    table = build_schema_compare_table(named_frames, unit_label=unit_label)
    if len(named_frames) == 1:
        name = named_frames[0][0]
        reply = (
            f"`{name}` 구조: "
            f"{int(table.iloc[0]['행 수'])}행 × {int(table.iloc[0]['열 수'])}열"
        )
        table = table.drop(columns=[unit_label], errors="ignore")
    else:
        reply = f"{unit_label}별 행 수·컬럼 목록 비교 ({len(named_frames)}개)"
    return reply, table


def _common_columns_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
) -> tuple[str, pd.DataFrame]:
    if len(named_frames) == 1:
        cols = [str(c) for c in named_frames[0][1].columns]
        table = pd.DataFrame({"컬럼": cols})
        return f"`{named_frames[0][0]}` 컬럼 {len(cols)}개", table

    col_sets = [set(str(c) for c in frame.columns) for _, frame in named_frames]
    common = set.intersection(*col_sets) if col_sets else set()
    # 첫 프레임 컬럼 순서 유지
    ordered = [str(c) for c in named_frames[0][1].columns if str(c) in common]
    only_by_unit: list[dict] = []
    for name, frame in named_frames:
        unique = sorted(set(str(c) for c in frame.columns) - common)
        only_by_unit.append(
            {
                unit_label: name,
                "고유 컬럼 수": len(unique),
                "고유 컬럼": ", ".join(unique) if unique else "(없음)",
            }
        )

    common_table = pd.DataFrame({"공통 컬럼": ordered})
    reply = (
        f"{len(named_frames)}개 {unit_label} 공통 컬럼 {len(ordered)}개"
        + (f": {', '.join(ordered)}" if ordered else "")
    )
    # 공통 목록 + 단위별 고유 컬럼을 한 표로 보기 어렵다면 공통 표만 반환
    # 고유 정보는 reply에 짧게 덧붙임
    extras = []
    for row in only_by_unit:
        if row["고유 컬럼 수"]:
            extras.append(f"{row[unit_label]}만: {row['고유 컬럼']}")
    if extras:
        reply = reply + " · " + " / ".join(extras)
    return reply, common_table if ordered else pd.DataFrame(
        {unit_label: [r[unit_label] for r in only_by_unit], "고유 컬럼": [r["고유 컬럼"] for r in only_by_unit]}
    )


def _dtypes_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
) -> tuple[str, pd.DataFrame]:
    parts: list[pd.DataFrame] = []
    for name, frame in named_frames:
        rows = []
        for col in frame.columns:
            series = frame[col]
            null_count = int(series.isna().sum())
            rows.append(
                {
                    unit_label: name,
                    "컬럼": str(col),
                    "데이터 타입": str(series.dtype),
                    "결측치": null_count,
                }
            )
        parts.append(pd.DataFrame(rows))
    table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(named_frames) == 1:
        # 단일일 때 단위 컬럼 생략
        table = table.drop(columns=[unit_label], errors="ignore")
        reply = f"`{named_frames[0][0]}` 컬럼별 데이터 타입·결측치"
    else:
        reply = f"{unit_label}별 컬럼 데이터 타입·결측치 ({len(named_frames)}개)"
    return reply, table


def _looks_like_groupby_row_count(compact: str) -> bool:
    """'범주형 컬럼별 행 개수'처럼 그룹 집계로 보이는 경우."""
    if "컬럼별" in compact or "별로" in compact:
        if any(k in compact for k in ("행개수", "개수", "건수", "카운트", "count")):
            return True
    return False
