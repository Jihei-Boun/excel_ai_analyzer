"""분석용 스키마 힌트 — 컬럼 의미를 LLM에 힌트로만 전달한다 (강제 rewrite 금지)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from core.column_match import _is_amount_metric_column
from core.excel_loader import merged_header_base
from core.pandasai_config import (
    _is_blank,
    _is_hierarchical_column,
    is_total_label,
)
from core.text_normalize import normalize_text


def prepare_analysis_frame(raw_df: pd.DataFrame) -> pd.DataFrame:
    """원본을 보존한 채 분석용 복사본을 만든다.

    hierarchical 분류 forward-fill과 코드 열 문자열화는 이 복사본에만 적용된다.
    """
    from core.pandasai_config import prepare_dataframe_for_ai

    return prepare_dataframe_for_ai(raw_df, stringify_codes=True)


def build_schema_hints(raw_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """원본 DataFrame을 보고 컬럼별 추정 역할을 만든다."""
    if raw_df is None or raw_df.empty:
        return {}

    hints: dict[str, dict[str, Any]] = {}
    for column in raw_df.columns:
        name = str(column)
        series = raw_df[column]
        entry: dict[str, Any] = {"source_preserved": True}

        if _is_hierarchical_column(series):
            entry["role"] = "hierarchical_category"
            entry["fill_strategy"] = "forward_fill_for_analysis_only"
            entry["note"] = (
                "원본에는 병합칸으로 빈 값이 있을 수 있습니다. "
                "분석용 복사본에서만 상위 분류로 채웠습니다."
            )
        elif _looks_like_category(series, name):
            entry["role"] = "category"
        elif _is_amount_metric_column(name) and _is_numeric_series(series):
            entry["role"] = "amount_metric"
        elif _is_numeric_series(series):
            entry["role"] = "numeric"

        if any(is_total_label(v) for v in series.head(50).tolist()):
            entry["contains_total_labels"] = True

        if len(entry) > 1:
            hints[name] = entry

    for parent, children in _compound_metric_groups(raw_df).items():
        total_cols = [
            c
            for c in children
            if normalize_text(c).endswith("합계") or normalize_text(c).endswith("_합계")
        ]
        hints[f"__metric_group__{parent}"] = {
            "role": "compound_metric_group",
            "parent": parent,
            "columns": children,
            "total_candidates": total_cols,
            "note": (
                f"'{parent}'만 언급되면 하위 열 중 의미를 확인하세요. "
                + (
                    f"합계 후보: {', '.join(total_cols)}."
                    if total_cols
                    else "합계 접미사 열이 없으면 사용자 의에 맞는 하위 열을 고르세요."
                )
            ),
            "source_preserved": True,
        }
    return hints


def format_schema_hints_for_prompt(
    raw_df: pd.DataFrame,
    hints: dict[str, dict[str, Any]] | None = None,
) -> str:
    """LLM 프롬프트에 넣을 스키마 힌트 텍스트."""
    hints = hints if hints is not None else build_schema_hints(raw_df)
    if not hints:
        return ""

    lines = [
        "스키마 힌트 (추정이며 강제 규칙이 아닙니다. 사용자 요청에 맞게 선택하세요):",
        f"- 제공 DataFrame은 분석용 복사본입니다. 원본 미리보기 값은 변경되지 않았습니다.",
        f"- 컬럼: {', '.join(str(c) for c in raw_df.columns)}",
    ]

    hierarchical = [
        name
        for name, meta in hints.items()
        if meta.get("role") == "hierarchical_category"
    ]
    if hierarchical:
        from core.profile_loader import guardrail_hints_for

        ghints = guardrail_hints_for()
        fill_ex = ghints.get("fill_example", "상위 분류값")
        primary = hierarchical[0]
        lines.append(
            "- 계층형 분류(분석용으로만 forward-fill됨): " + ", ".join(hierarchical)
        )
        lines.append(
            f"- 중요: 원본 미리보기의 빈 {primary}는 결측이 아닙니다. "
            f"엑셀 병합칸이므로 '{fill_ex}' 아래 빈 행도 같은 {primary}로 "
            "이미 채워진 분석용 DataFrame을 사용하세요. "
            "빈 문자열/NaN을 별도 그룹으로 피벗하지 마세요."
        )

    total_cols = [
        name
        for name, meta in hints.items()
        if meta.get("contains_total_labels") and not name.startswith("__")
    ]
    if total_cols:
        lines.append(
            "- 소계/합계 라벨이 포함된 열: "
            + ", ".join(total_cols)
            + " → 집계 전에 해당 행 제외를 검토하세요."
        )

    for code_col, name_col in _code_name_pairs(raw_df):
        lines.append(
            f"- 코드/명칭 쌍: '{code_col}'(코드) + '{name_col}'(명칭). "
            f"피벗·교차·그룹 축에는 명칭 열 '{name_col}'을 우선하세요. "
            f"코드 숫자(121, 121.0 등)를 컬럼명처럼 조회하지 마세요."
        )

    for name, meta in hints.items():
        if not name.startswith("__metric_group__"):
            continue
        parent = meta.get("parent")
        cols = ", ".join(meta.get("columns") or [])
        note = meta.get("note") or ""
        lines.append(f"- 복합 지표 '{parent}': {cols}. {note}")

    from core.profile_loader import footer_labels_for, guardrail_hints_for

    ghints = guardrail_hints_for()
    group_ex = ghints.get("group_col", "분류")
    footers = footer_labels_for()
    footer_note = "·".join(footers) if footers else ghints.get("footer_examples", "")
    footer_line = (
        f"- 피벗 전 소계·합계·총계 라벨 행은 제외하세요. "
        f"하단 요약 라벨({footer_note}) 행도 제외를 검토하세요."
        if footer_note
        else "- 피벗 전 소계·합계·총계 라벨 행은 제외하세요."
    )
    lines.extend(
        [
            "- 피벗 키 조합이 유일하면 pivot 또는 pivot_table 사용 가능.",
            "- 중복 가능성이 있거나 유일성이 확인되지 않으면 "
            "pivot_table을 쓰고 aggfunc를 명시하세요. "
            "aggfunc='sum'은 금액 열이고 합산이 분명할 때만 사용하세요.",
            "- 교차 피벗 시 요청에서 먼저 언급된 축을 행(index), "
            "다음에 언급된 축을 열(columns)로 두세요. "
            "행마다 값이 1개만 있는 대각선 표가 되면 축이 뒤바뀐 것입니다.",
            footer_line,
            "- 행 축(index)에는 빈 분류값이 없어야 합니다. "
            "분석용 복사본의 forward-fill된 분류 열을 사용하세요.",
            f"- reset_index() 뒤에는 행 축 컬럼명(예: {group_ex})을 유지하세요. "
            "왼쪽 열이 비거나 숫자만 있으면 잘못된 피벗입니다.",
            "- 컬럼명을 임의로 다른 이름으로 rewrite하지 마세요.",
        ]
    )
    return "\n".join(lines)


def _code_name_pairs(df: pd.DataFrame) -> list[tuple[str, str]]:
    from core.excel_loader import find_merged_header_pair, merged_header_base

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for column in df.columns:
        base = merged_header_base(str(column))
        if base in seen:
            continue
        pair = find_merged_header_pair(df.columns, base)
        if not pair:
            continue
        seen.add(base)
        pairs.append(pair)
    return pairs


def _compound_metric_groups(df: pd.DataFrame) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for column in df.columns:
        name = str(column)
        if "_" not in name:
            continue
        if not _is_amount_metric_column(name):
            continue
        if not _is_numeric_series(df[column]):
            continue
        parent = merged_header_base(name)
        if normalize_text(parent) == normalize_text(name):
            continue
        groups[parent].append(name)
    return {parent: cols for parent, cols in groups.items() if len(cols) >= 2}


def _is_numeric_series(series: pd.Series) -> bool:
    if pd.api.types.is_numeric_dtype(series):
        return True
    coerced = pd.to_numeric(series, errors="coerce")
    return bool(coerced.notna().any())


def _looks_like_category(series: pd.Series, name: str) -> bool:
    if _is_numeric_series(series) and pd.api.types.is_numeric_dtype(series):
        return False
    norm = normalize_text(name)
    if any(token in norm for token in ("분류", "구분", "카테고리", "지역", "부서")):
        return True
    sample = [v for v in series.head(30).tolist() if not _is_blank(v)]
    if not sample:
        return False
    textish = sum(1 for v in sample if isinstance(v, str))
    return textish >= max(1, len(sample) // 2)
