"""LLM 채팅 분석 계획 생성."""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan, analysis_plan_from_dict
from core.analysis.analysis_column_prefs import apply_analysis_column_prefs
from core.llm_client import chat_json
from core.profile_loader import active_profile
from core.schema.row_classify import classification_summary
from core.integrate.schema_infer import build_frame_inventory, semantic_hints_text

# 도메인 비의존 범용 계획 프롬프트
_GENERIC_PLAN_SYSTEM = (
    "You are a planning module for a generic Excel analyzer. "
    "Given a pandas DataFrame inventory and optional row-type distribution, "
    "produce ONE JSON analysis plan for a deterministic executor. "
    "Do NOT write pandas code. Do NOT invent columns that are not in the inventory. "
    "Prefer a pipeline of atomic steps. Allowed ops: "
    "annotate_row_types, filter_rows, select_columns, derive_column, sort, limit, "
    "drop_columns, aggregate, ratio_of_aggregates, compare_groups, "
    "distribution_summary, correlation, filter_vs_mean. "
    "filter_rows may include include_row_types, column_filters "
    "[{column, values}] for label membership, and numeric_filters "
    "[{column, op, value}] where op is eq|ne|gt|gte|lt|lte. "
    "aggregate: group_by, metrics[{column, fn}], prefer_subtotals(bool), include_groups. "
    "When prefer_subtotals=true, use trustworthy subtotal rows if present; "
    "otherwise sum detail rows. Never double-count subtotals with details. "
    "ratio_of_aggregates: name, numerator, denominator — compute sum-level ratio "
    "(NOT the mean of row ratios). Apply after aggregate. "
    "compare_groups: group_column, groups, metrics, rate_columns. "
    "distribution_summary: denominator_column, numerator_column "
    "(aliases: budget_column, executed_column), optional group_column/group_value. "
    "correlation: x_column, y_column, optional label_column, methods "
    "— row-level Pearson/Spearman on detail rows (NOT a ratio, NOT group aggregate). "
    "filter_vs_mean: column, relation(below|above) — keep rows vs arithmetic mean. "
    "For ranking by 'largest difference' / '차이가 큰', use abs_diff and descending sort, "
    "and set criteria_note explaining absolute difference. "
    "For directional comparisons use signed diffs matching the user wording. "
    "For '상관' / '상관관계' / 'correlation' / Pearson / Spearman: "
    "MUST use operation='correlation' with x_column, y_column, optional label_column, "
    "interpret=true. Never answer correlation with group_comparison or "
    "ratio_of_aggregates. Zero denominator is not correlation. "
    "For finding items by numeric conditions (많다/없는/0/=0/>0): "
    "MUST use operation='find_items' with numeric_filters, sort_by, "
    "output_columns (ONLY labels + condition columns + 1-2 related metrics — "
    "NEVER return all source columns), interpret=true when 의미/설명 asked. "
    "For rate vs mean comparisons: operation='rate_vs_mean' with numerator, "
    "denominator, relation; exclude denominator==0; interpret=false for table-only. "
    "For per-group top/bottom item: operation='top_n_per_group' with group_column, "
    "value_column, n, ascending. Filter to detail rows only. "
    "For splitting increases vs decreases between two numeric columns: "
    "operation='split_by_difference' with left, right; keep ALL detail rows. "
    "For comparing categories with a rate: prefer operation='group_comparison' "
    "with group_column, groups, numerator, denominator, rate_name, "
    "prefer_subtotals=true when useful. "
    "Always exclude non-detail rows when the user asks for item rankings: "
    "annotate_row_types then filter_rows with include_row_types=['detail'] "
    "and drop_blank_dimensions=true. "
    "You may return compact high-level forms: "
    "operation='top_n_difference', operation='group_comparison', "
    "operation='correlation', operation='find_items', operation='rate_vs_mean', "
    "operation='top_n_per_group', or operation='split_by_difference'. "
    "Return ONLY a JSON object."
)


def _plan_system_prompt(
    *,
    profile_name: str | None = None,
) -> str:
    profile = active_profile(
        profile_name=profile_name,
    )
    guidance = str(profile.get("plan_guidance") or "").strip()
    if not guidance:
        return _GENERIC_PLAN_SYSTEM
    return f"{_GENERIC_PLAN_SYSTEM} Domain guidance: {guidance}"


def build_analysis_plan(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    classified_df: pd.DataFrame | None = None,
    profile_name: str | None = None,
    previous_errors: list[str] | None = None,
    chat_json_fn: Callable[..., dict[str, Any]] = chat_json,
) -> AnalysisPlan:
    """사용자 요청 + 스키마 inventory → 원자 step 분석 계획."""
    inventory = build_frame_inventory("current", df)
    row_summary = classification_summary(classified_df if classified_df is not None else df)
    columns = [str(c) for c in df.columns]

    system = _plan_system_prompt(
        profile_name=profile_name,
    )

    user_parts = [
        f"User request:\n{prompt}",
        f"Available columns:\n{json.dumps(columns, ensure_ascii=False)}",
        f"Inventory:\n{json.dumps(inventory, ensure_ascii=False, indent=2)}",
        f"Row type summary:\n{json.dumps(row_summary, ensure_ascii=False, indent=2)}",
        (
            "Return JSON with either:\n"
            "1) steps[], criteria_note, dimension_columns, output_columns, interpret(bool)\n"
            "2) operation=top_n_difference with dimension_columns, value_columns, "
            "difference_mode (absolute|signed), sort, limit, exclude_rows, criteria_note\n"
            "3) operation=group_comparison with group_column, groups, "
            "numerator, denominator, rate_name, prefer_subtotals, criteria_note, interpret\n"
            "4) operation=correlation with x_column, y_column, label_column, "
            "methods, criteria_note, interpret\n"
            "5) operation=find_items with numeric_filters[{column,op,value}], "
            "sort_by, output_columns (minimal), criteria_note, interpret\n"
            "6) operation=rate_vs_mean with numerator, denominator, relation "
            "(below|above), rate_name, output_columns (minimal), interpret\n"
            "7) operation=top_n_per_group with group_column, value_column, n, "
            "ascending, output_columns (minimal), interpret\n"
            "8) operation=split_by_difference with left, right, diff_name, "
            "label_name, output_columns, interpret=true (NO limit)"
        ),
    ]
    hint = semantic_hints_text(
        profile_name=profile_name,
    )
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
    category_labels: list[str] = []
    profile = active_profile(
        profile_name=profile_name,
    )
    label_cols = list(profile.get("group_columns") or ()) + list(
        profile.get("preferred_labels") or ()
    )
    seen_cols: set[str] = set()
    for col in label_cols:
        if col in seen_cols or col not in df.columns:
            continue
        seen_cols.add(col)
        for val in df[col].dropna().unique().tolist():
            text = str(val).strip()
            if text and text not in category_labels:
                category_labels.append(text)
    data = apply_analysis_column_prefs(
        prompt,
        data,
        columns,
        profile_name=profile_name,
        category_labels=category_labels,
    )
    plan = analysis_plan_from_dict(
        data, available_columns=columns, profile_name=profile_name
    )
    plan.footer_labels = [str(x) for x in (profile.get("footer_labels") or ())]
    return plan
