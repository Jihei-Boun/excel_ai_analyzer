"""LLM 채팅 분석 계획 생성."""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis_plan_types import AnalysisPlan, analysis_plan_from_dict
from core.analysis_column_prefs import apply_analysis_column_prefs
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
        "distribution_summary: budget_column, executed_column, optional group_column/group_value. "
        "correlation: x_column, y_column, optional label_column, methods "
        "— row-level Pearson/Spearman on detail rows (NOT a ratio, NOT group aggregate). "
        "filter_vs_mean: column, relation(below|above) — keep rows vs arithmetic mean. "
        "For ranking by 'largest difference' / '차이가 큰', use abs_diff and descending sort, "
        "and set criteria_note explaining absolute difference. "
        "For '초과' / '더 많이 집행' use directional diff (e.g. executed - planned). "
        "For '부족' / '미집행' use the opposite direction. "
        "For '상관' / '상관관계' / 'correlation' / Pearson / Spearman: "
        "MUST use operation='correlation' with x_column, y_column, label_column "
        "(detail item name like 비용명), interpret=true. "
        "Never answer correlation with group_comparison, ratio_of_aggregates, "
        "or 가집행_대비_집행율 style ratios. Zero denominator is not correlation. "
        "For finding items by numeric conditions (많다/없는/0/=0/>0) and explaining: "
        "MUST use operation='find_items' with numeric_filters, sort_by, "
        "output_columns (ONLY labels + condition columns + 1-2 related metrics — "
        "NEVER return all source columns), interpret=true when 의미/설명 asked. "
        "Example: 이월예산 많은데 당해집행 없는 항목 → "
        "numeric_filters=[{실행예산_이월예산,gt,0},{집행계_당해집행,eq,0}], "
        "sort_by=[실행예산_이월예산] descending, "
        "output_columns=[비목분류,비용명_2,비용명,실행예산_이월예산,집행계_당해집행,"
        "집행계_합계,집행계_이월집행]. "
        "For 비용명별/항목별 집행률 then 평균보다 낮은/높은 항목 only: "
        "MUST use operation='rate_vs_mean' with numerator, denominator, relation, "
        "interpret=false when user asks for a table. "
        "Default: numerator=집행계_합계, denominator=실행예산_합계. "
        "Exclude denominator==0 from rate and mean. "
        "Do NOT use group_comparison or 계획예산 for this. "
        "For 비목분류별/그룹별 가장 잔액(또는 금액)이 큰/작은 항목 하나씩: "
        "MUST use operation='top_n_per_group' with group_column, value_column, n=1, "
        "ascending=false for 큰/최대, ascending=true for 작/최소. "
        "Default value_column=예산잔액_합계 (use 예산잔액_당해잔액 only if 당해/당년 asked). "
        "Filter to detail rows only. output_columns=labels+value only. "
        "Do NOT answer with group_comparison aggregates or a full item list. "
        "For comparing categories / execution efficiency between groups / 해석: "
        "prefer operation='group_comparison' with group_column, groups, "
        "numerator, denominator, rate_name, prefer_subtotals=true, interpret=true. "
        "Default execution rate columns when present: "
        "numerator=집행계_합계 (NOT 당년도집행), denominator=실행예산_합계 (NOT 계획예산). "
        "Only use 당년도집행/당년도예산/계획예산 when the user explicitly asks for "
        "당년·당해·올해 기준. "
        "Do NOT answer efficiency comparisons with top_n_difference on detail rows. "
        "Always exclude non-detail rows when the user asks for item rankings: "
        "annotate_row_types then filter_rows with include_row_types=['detail'] "
        "and drop_blank_dimensions=true. "
        "You may return compact high-level forms: "
        "operation='top_n_difference', operation='group_comparison', "
        "operation='correlation', operation='find_items', operation='rate_vs_mean', "
        "or operation='top_n_per_group'. "
        "Return ONLY a JSON object."
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
            "ascending, output_columns (minimal), interpret"
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
    data = apply_analysis_column_prefs(prompt, data, columns)
    return analysis_plan_from_dict(data, available_columns=columns)
