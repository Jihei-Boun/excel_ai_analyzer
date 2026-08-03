"""스키마·메타 요청 (행 수·컬럼 목록·공통 컬럼·dtype/결측·타입 분류) 규칙 경로.

컬럼 의미 추정은 규칙으로 답하지 않고 LLM 경로로 보낸다.
"""

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
    # 타입 분류
    "숫자컬럼",
    "문자컬럼",
    "숫자형컬럼",
    "문자형컬럼",
    "수치형컬럼",
    "날짜형컬럼",
    "날짜컬럼",
    "컬럼을구분",
    "컬럼구분",
    "타입구분",
    "형구분",
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
    "뽑아",
    "나열",
)

_TYPE_GROUP_PHRASES = (
    "숫자컬럼",
    "문자컬럼",
    "숫자형컬럼",
    "문자형컬럼",
    "수치형컬럼",
    "날짜형컬럼",
    "날짜컬럼",
    "컬럼을구분",
    "컬럼구분",
    "타입구분",
    "형구분",
    "숫자와문자",
    "숫자문자",
)

_MEANING_PHRASES = (
    "컬럼의미",
    "컬럼설명",
    "의미인지",
    "의미추측",
    "추측해서설명",
    "의미를설명",
    "무엇을의미",
    "어떤의미",
    "컬럼의도",
    "columnmeaning",
    "의미알려",
)

def _column_meaning_rules(
    *, use_budget_profile: bool = False
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """컬럼 의미 규칙. YAML(profiles/)에서 로드한다."""
    from core.profile_loader import load_meaning_rules

    return load_meaning_rules(use_budget_profile=use_budget_profile)


def is_column_meaning_request(prompt: str) -> bool:
    """컬럼 의미 추정·설명 요청인지. 규칙 스키마가 아니라 LLM으로 보낸다."""
    if not prompt or not str(prompt).strip():
        return False
    compact = re.sub(r"\s+", "", normalize_text(prompt))
    return _is_meaning_request(compact)


def is_schema_request(prompt: str) -> bool:
    """행·열·컬럼 구조/메타 질문인지 판별한다. 집계·차트·의미 추정은 제외."""
    if not prompt or not str(prompt).strip():
        return False
    if expects_plot(prompt):
        return False
    if detect_aggregate_op(prompt) is not None:
        return False

    # 결측 '행' 필터 요청은 스키마(컬럼별 결측 개수)가 아님
    from core.value_filter import is_missing_rows_request

    if is_missing_rows_request(prompt):
        return False

    normalized = normalize_text(prompt)
    compact = re.sub(r"\s+", "", normalized)

    # 컬럼 의미 설명은 LLM 경로
    if _is_meaning_request(compact):
        return False

    if _looks_like_groupby_row_count(compact):
        return False

    if schema_kind(prompt) == "type_groups":
        return True

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
    """스키마 하위 유형: type_groups | common | dtypes | compare."""
    compact = re.sub(r"\s+", "", normalize_text(prompt))

    from core.value_filter import is_missing_rows_request

    if is_missing_rows_request(prompt):
        return "compare"

    if _is_type_group_request(compact):
        return "type_groups"
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
    use_budget_profile: bool = False,
) -> tuple[str, pd.DataFrame | None]:
    """스키마 요청에 대한 (reply, dataframe)을 만든다."""
    del use_budget_profile  # 의미 추정 규칙 경로 제거 후 미사용 (호환용 인자)
    if not named_frames:
        return f"비교할 {unit_label}이(가) 없습니다.", None

    kind = schema_kind(prompt)
    if kind == "common":
        return _common_columns_result(named_frames, unit_label=unit_label)
    if kind == "dtypes":
        return _dtypes_result(named_frames, unit_label=unit_label)
    if kind == "type_groups":
        return _type_groups_result(named_frames, unit_label=unit_label)
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


def classify_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    """컬럼을 숫자형/문자형/날짜형/기타로 분류한다."""
    groups: dict[str, list[str]] = {
        "numeric": [],
        "string": [],
        "datetime": [],
        "other": [],
    }
    for col in df.columns:
        kind = _column_type_kind(df[col])
        groups[kind].append(str(col))
    return groups


def estimate_column_meaning(
    column: str,
    series: pd.Series | None = None,
    *,
    use_budget_profile: bool = False,
) -> str:
    """컬럼명(·샘플)으로 의미를 추정한다."""
    compact = re.sub(r"[\s_\-]+", "", str(column)).lower()
    for hints, meaning in _column_meaning_rules(use_budget_profile=use_budget_profile):
        for hint in hints:
            hint_compact = re.sub(r"[\s_\-]+", "", hint).lower()
            if hint_compact and hint_compact in compact:
                return meaning

    if series is not None:
        if pd.api.types.is_datetime64_any_dtype(series):
            return "날짜/시간 값"
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(
            series
        ):
            return "수치 값 (용도는 컬럼명만으로 특정하기 어려움)"
        if pd.api.types.is_bool_dtype(series):
            return "예/아니오 또는 참/거짓 플래그"

    return "용도를 컬럼명만으로 특정하기 어려워 추가 확인이 필요합니다"


def _is_meaning_request(compact: str) -> bool:
    if "컬럼" not in compact:
        return False
    if any(p in compact for p in _MEANING_PHRASES):
        return True
    # 의미·추측이 분명한 경우
    if any(k in compact for k in ("의미", "추측", "해석", "용도")):
        if any(
            k in compact
            for k in ("타입", "결측", "dtype", "숫자", "문자", "구분", "비교")
        ):
            return False
        return True
    # '설명'만 있을 때는 구조/목록 질문과 구분
    if "설명" in compact:
        if any(
            k in compact
            for k in (
                "목록",
                "리스트",
                "행수",
                "열수",
                "비교",
                "타입",
                "결측",
                "숫자",
                "문자",
                "구분",
            )
        ):
            return False
        return True
    return False


def _is_type_group_request(compact: str) -> bool:
    if any(p in compact for p in _TYPE_GROUP_PHRASES):
        return True
    has_num = any(k in compact for k in ("숫자", "수치", "numeric", "number"))
    has_str = any(k in compact for k in ("문자", "문자열", "텍스트", "string", "text"))
    has_date = any(k in compact for k in ("날짜", "일자", "datetime", "date"))
    if "컬럼" in compact and (
        (has_num and has_str)
        or (has_num and has_date)
        or (has_str and has_date)
        or ("구분" in compact and (has_num or has_str or has_date))
    ):
        return True
    return False


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
    extras = []
    for row in only_by_unit:
        if row["고유 컬럼 수"]:
            extras.append(f"{row[unit_label]}만: {row['고유 컬럼']}")
    if extras:
        reply = reply + " · " + " / ".join(extras)
    return reply, common_table if ordered else pd.DataFrame(
        {
            unit_label: [r[unit_label] for r in only_by_unit],
            "고유 컬럼": [r["고유 컬럼"] for r in only_by_unit],
        }
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
        table = table.drop(columns=[unit_label], errors="ignore")
        reply = f"`{named_frames[0][0]}` 컬럼별 데이터 타입·결측치"
    else:
        reply = f"{unit_label}별 컬럼 데이터 타입·결측치 ({len(named_frames)}개)"
    return reply, table


def _type_groups_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
) -> tuple[str, pd.DataFrame | None]:
    """숫자/문자/날짜 컬럼을 구분해 마크다운으로 반환한다."""
    if len(named_frames) == 1:
        _name, frame = named_frames[0]
        groups = classify_columns(frame)
        reply = _format_type_groups_markdown(groups)
        # 마크다운 목록이 주 응답 — 표 중복 표시는 생략
        return reply, None

    parts: list[str] = [
        f"선택된 {unit_label} {len(named_frames)}개의 컬럼 타입을 구분했습니다.",
        "",
    ]
    rows: list[dict] = []
    for name, frame in named_frames:
        groups = classify_columns(frame)
        parts.append(f"### `{name}`")
        parts.append("")
        parts.append(_format_type_groups_markdown(groups))
        parts.append("")
        for kind, label in (
            ("numeric", "숫자형"),
            ("string", "문자형"),
            ("datetime", "날짜형"),
            ("other", "기타"),
        ):
            for col in groups[kind]:
                rows.append({unit_label: name, "컬럼": col, "유형": label})
    table = pd.DataFrame(rows) if rows else None
    return "\n".join(parts).rstrip(), table


def _type_groups_table(groups: dict[str, list[str]]) -> pd.DataFrame:
    label_map = {
        "numeric": "숫자형",
        "string": "문자형",
        "datetime": "날짜형",
        "other": "기타",
    }
    rows: list[dict] = []
    for kind, label in label_map.items():
        for col in groups.get(kind, []):
            rows.append({"컬럼": col, "유형": label})
    return pd.DataFrame(rows)


def _format_type_groups_markdown(groups: dict[str, list[str]]) -> str:
    sections = [
        ("numeric", "숫자형 컬럼"),
        ("string", "문자형 컬럼"),
        ("datetime", "날짜형 컬럼"),
        ("other", "기타 컬럼"),
    ]
    lines: list[str] = []
    for key, title in sections:
        cols = groups.get(key) or []
        if not cols:
            continue
        lines.append(f"**{title}**")
        lines.extend(f"- {col}" for col in cols)
        lines.append("")
    return "\n".join(lines).rstrip() if lines else "분류할 컬럼이 없습니다."


def _column_type_kind(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "other"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if (
        pd.api.types.is_string_dtype(series)
        or pd.api.types.is_object_dtype(series)
        or str(series.dtype) == "category"
        or str(series.dtype) == "string"
    ):
        return "string"
    return "other"


def _looks_like_groupby_row_count(compact: str) -> bool:
    """'범주형 컬럼별 행 개수'처럼 그룹 집계로 보이는 경우."""
    if "컬럼별" in compact or "별로" in compact:
        if any(k in compact for k in ("행개수", "개수", "건수", "카운트", "count")):
            return True
    return False
