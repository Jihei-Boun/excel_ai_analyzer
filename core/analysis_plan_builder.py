"""LLM 채팅 분석 계획 생성."""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis_plan_types import AnalysisPlan, analysis_plan_from_dict
from core.llm_client import chat_json
from core.row_classify import classification_summary
from core.schema_infer import build_frame_inventory, semantic_hints_text


def build_analysis_plan(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    classified_df: pd.DataFrame | None = None,
    use_budget_profile: bool = False,
    previous_errors: list[str] | None = None,
    chat_json_fn: Callable[..., dict[str, Any]] = chat_json,
) -> AnalysisPlan:
    """사용자 요청 + 스키마 inventory → 원자 step 분석 계획."""
    inventory = build_frame_inventory("current", df)
    row_summary = classification_summary(classified_df if classified_df is not None else df)
    columns = [str(c) for c in df.columns]

    system = (
        "You are a planning module for a generic Excel analyzer. "
        "Given a pandas DataFrame inventory and optional row-type distribution, "
        "produce ONE JSON analysis plan for a deterministic executor. "
        "Do NOT write pandas code. Do NOT invent columns that are not in the inventory. "
        "Prefer a pipeline of atomic steps. Allowed ops: "
        "annotate_row_types, filter_rows, select_columns, derive_column, sort, limit, drop_columns. "
        "derive_column expr allowlist: diff, abs, abs_diff, ratio — operands must be real column names. "
        "For ranking by 'largest difference' / '차이가 큰', use abs_diff and descending sort, "
        "and set criteria_note explaining absolute difference. "
        "For '초과' / '더 많이 집행' use directional diff (e.g. executed - planned). "
        "For '부족' / '미집행' use the opposite direction. "
        "Always exclude non-detail rows when the user asks for items/entries: "
        "annotate_row_types then filter_rows with include_row_types=['detail'] "
        "and drop_blank_dimensions=true. "
        "You may also return a compact high-level form with operation='top_n_difference' "
        "plus dimension_columns, value_columns, difference_mode, sort, limit, exclude_rows; "
        "the engine will compile it to atomic steps. "
        "Return ONLY a JSON object."
    )

    user_parts = [
        f"User request:\n{prompt}",
        f"Available columns:\n{json.dumps(columns, ensure_ascii=False)}",
        f"Inventory:\n{json.dumps(inventory, ensure_ascii=False, indent=2)}",
        f"Row type summary:\n{json.dumps(row_summary, ensure_ascii=False, indent=2)}",
        (
            "Return JSON with either:\n"
            "1) steps[], criteria_note, dimension_columns, output_columns\n"
            "2) or operation=top_n_difference with dimension_columns, value_columns, "
            "difference_mode (absolute|signed), sort, limit, exclude_rows, criteria_note"
        ),
    ]
    hint = semantic_hints_text(use_budget_profile=use_budget_profile)
    if hint:
        user_parts.append(hint)
    if previous_errors:
        user_parts.append(
            "Previous plan failed validation. Fix these issues:\n"
            + "\n".join(f"- {err}" for err in previous_errors)
        )

    data = chat_json_fn(
        "\n\n".join(user_parts),
        system=system,
        base_url=base_url,
        model=model,
    )
    return analysis_plan_from_dict(data, available_columns=columns)
