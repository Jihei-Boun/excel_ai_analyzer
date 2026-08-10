"""LLM 채팅 분석 계획 생성."""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan, analysis_plan_from_dict
from core.analysis.analysis_column_prefs import apply_safety_column_normalization
from core.llm_client import chat_json
from core.profile_loader import active_profile, roles_for
from core.schema.row_classify import ROW_TYPE_COL, classification_summary, row_type_distribution
from core.integrate.schema_infer import semantic_hints_text

# 도메인 비의존 범용 계획 프롬프트
_GENERIC_PLAN_SYSTEM = (
    "You are a planning module for a generic Excel analyzer. "
    "Given a pandas DataFrame inventory and optional row-type distribution, "
    "produce ONE JSON analysis plan for a deterministic executor. "
    "Do NOT write pandas code. Do NOT invent columns that are not in the inventory. "
    "Prefer a pipeline of atomic steps. Allowed ops: "
    "annotate_row_types, filter_rows, select_columns, derive_column, sort, limit, "
    "drop_columns, aggregate, ratio_of_aggregates, compare_groups, "
    "distribution_summary, correlation, filter_vs_mean, top_per_group. "
    "filter_rows may include include_row_types, column_filters "
    "[{column, values}] for label membership, and numeric_filters either "
    "[{column, op, value}] for scalar compares OR "
    "[{left_column, op, right_column}] for column-vs-column compares "
    "(op: eq|ne|gt|gte|lt|lte). Example: stock below safety stock → "
    "numeric_filters:[{left_column: stock_qty, op: lt, right_column: safety_stock}]. "
    "aggregate: group_by, metrics[{column, fn}], prefer_subtotals(bool), include_groups. "
    "fn MUST be one of sum|mean|median|min|max|count (avg→mean). "
    "prefer_subtotals applies to sum only; mean/count/min/max/median always use detail rows. "
    "Never double-count subtotals with details. "
    "RANKING / TOP-N (상위 N, 가장 큰/높은/낮은, top N): do NOT invent a special op. "
    "Use atomic steps: (optional metric derive or aggregate/ratio) → sort → limit. "
    "Examples: "
    "(1) top products by sales: aggregate by product sum sales → sort desc → limit N; "
    "(2) top rates: derive or ratio_of_aggregates → sort by rate desc → limit N; "
    "(3) largest remaining amount: filter detail → sort by remaining desc → limit 1. "
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
    "For vague risk/shortage concepts (e.g. stockout risk), prefer comparing "
    "related numeric columns from inventory (qty vs safety/min/reorder) via "
    "column-vs-column numeric_filters — do not invent business formulas not "
    "supported by columns. "
    "For rate vs mean comparisons: operation='rate_vs_mean' with numerator, "
    "denominator, relation; exclude denominator==0; interpret=false for table-only. "
    "For per-group top/bottom item: operation='top_n_per_group' with group_column, "
    "value_column, n, ascending. Filter to detail rows only. "
    "For splitting increases vs decreases between two numeric columns: "
    "operation='split_by_difference' with left, right; keep ALL detail rows. "
    "For comparing categories with a rate: prefer operation='group_comparison' "
    "with group_column, groups, numerator, denominator, rate_name, "
    "prefer_subtotals=true when useful. "
    "When several similarly named metric columns exist (e.g. current/ytd/target sales), "
    "pick the one that best matches the user wording; if the request is ambiguous, "
    "prefer the most general cumulative/total-like metric and note the choice in "
    "criteria_note. Never invent a column. "
    "Always exclude non-detail rows when the user asks for item rankings: "
    "annotate_row_types then filter_rows with include_row_types=['detail'] "
    "and drop_blank_dimensions=true. "
    "Semantic role_hints are OPTIONAL hints only — never invent columns from them. "
    "You may return compact high-level forms: "
    "operation='top_n_difference', operation='group_comparison', "
    "operation='correlation', operation='find_items', operation='rate_vs_mean', "
    "operation='top_n_per_group', or operation='split_by_difference'. "
    "Return ONLY a JSON object."
)

_MAX_SAMPLE_VALUES = 6


def _plan_system_prompt(
    *,
    profile_name: str | None = None,
) -> str:
    from core.profile_loader import plan_language_note_for

    profile = active_profile(
        profile_name=profile_name,
    )
    guidance = str(profile.get("plan_guidance") or "").strip()
    lang_note = plan_language_note_for(profile_name=profile_name)
    parts = [_GENERIC_PLAN_SYSTEM]
    if lang_note:
        parts.append(lang_note)
    if guidance:
        parts.append(f"Domain guidance (hints only, not hard rules): {guidance}")
    return " ".join(parts)


def build_planner_column_inventory(
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
    max_samples: int = _MAX_SAMPLE_VALUES,
) -> list[dict[str, Any]]:
    """Planner용 컬럼 inventory (sample 제한, role_hints는 강제 아님)."""
    roles = roles_for(profile_name=profile_name)
    role_index: dict[str, list[str]] = {}
    role_map = {
        "group_columns": "group_candidate",
        "label_columns": "label_candidate",
        "metric_numerator": "numerator_candidate",
        "metric_denominator": "denominator_candidate",
        "metric_remaining": "remaining_candidate",
        "key_metrics": "key_metric",
    }
    for role_key, hint in role_map.items():
        for col in roles.get(role_key) or ():
            role_index.setdefault(str(col), []).append(hint)

    columns: list[dict[str, Any]] = []
    for col in df.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        series = df[col]
        non_null = series.dropna()
        null_ratio = float(series.isna().mean()) if len(series) else 0.0
        is_num = pd.api.types.is_numeric_dtype(series) or _mostly_numeric(non_null)
        is_dt = pd.api.types.is_datetime64_any_dtype(series)
        if is_dt:
            dtype = "datetime"
        elif is_num:
            dtype = "numeric"
        else:
            dtype = "categorical"

        sample = [_jsonable(v) for v in non_null.head(max_samples).tolist()]
        entry: dict[str, Any] = {
            "name": name,
            "dtype": dtype,
            "null_ratio": round(null_ratio, 4),
            "unique_count": int(non_null.nunique()),
            "sample_values": sample,
        }
        hints = role_index.get(name) or []
        if hints:
            entry["role_hints"] = hints
        columns.append(entry)
    return columns


def build_planner_row_context(classified_df: pd.DataFrame | None, df: pd.DataFrame) -> dict[str, Any]:
    """row level 분포만 요약 (전체 행 데이터는 넣지 않음)."""
    frame = classified_df if classified_df is not None else df
    if frame is None or frame.empty:
        return {"row_types": {}, "levels_detected": []}
    if ROW_TYPE_COL in frame.columns:
        dist = row_type_distribution(frame)
    else:
        dist = {}
    levels = [k for k, v in dist.items() if int(v) > 0]
    return {
        "row_types": {str(k): int(v) for k, v in dist.items()},
        "levels_detected": levels,
        "note": (
            "When aggregating, choose one row level explicitly to avoid double-counting "
            "(detail vs subtotal/total/footer)."
        ),
    }


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
    """사용자 요청 + 스키마 inventory → 원자 step 분석 계획.

    Phase 5: safety 컬럼명 정규화만 적용. 의미 rewrite 없음.
    """
    column_inventory = build_planner_column_inventory(df, profile_name=profile_name)
    row_context = build_planner_row_context(classified_df, df)
    row_summary = classification_summary(classified_df if classified_df is not None else df)
    columns = [str(c) for c in df.columns if not str(c).startswith("_")]

    system = _plan_system_prompt(
        profile_name=profile_name,
    )

    user_parts = [
        f"User request:\n{prompt}",
        f"Available columns:\n{json.dumps(columns, ensure_ascii=False)}",
        (
            "Column inventory (samples are limited; role_hints are optional semantic hints "
            "only — never invent columns):\n"
            f"{json.dumps(column_inventory, ensure_ascii=False, indent=2)}"
        ),
        f"Row levels:\n{json.dumps(row_context, ensure_ascii=False, indent=2)}",
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
            "5) operation=find_items with numeric_filters as "
            "[{column,op,value}] OR [{left_column,op,right_column}] "
            "(column-vs-column; do NOT put a column name in value), "
            "sort_by, output_columns (minimal), criteria_note, interpret\n"
            "6) operation=rate_vs_mean with numerator, denominator, relation "
            "(below|above), rate_name, output_columns (minimal), interpret\n"
            "7) operation=top_n_per_group with group_column, value_column, n, "
            "ascending, output_columns (minimal), interpret\n"
            "8) operation=split_by_difference with left, right, diff_name, "
            "label_name, output_columns, interpret=true (NO limit)\n"
            "9) operation=aggregate with group_by (or dimension_columns), "
            "metrics[{column,fn}] where fn is sum|mean|median|min|max|count "
            "(required — never omit metrics)"
        ),
    ]
    hint = semantic_hints_text(
        profile_name=profile_name,
    )
    if hint:
        user_parts.append(hint)
    role_hint_text = _role_semantic_hint_text(df, profile_name=profile_name)
    if role_hint_text:
        user_parts.append(role_hint_text)
    if previous_errors:
        user_parts.append(
            "Previous plan failed validation. Fix these issues and regenerate "
            "a corrected AnalysisPlan. Do NOT invent columns. "
            "Hints list candidates only — do not treat them as mandatory answers:\n"
            + "\n".join(f"- {err}" for err in previous_errors)
        )

    data = chat_json_fn(
        "\n\n".join(user_parts),
        system=system,
        base_url=base_url,
        model=model,
    )
    if not isinstance(data, dict):
        raise ValueError("분석 계획이 객체가 아닙니다.")

    data = apply_safety_column_normalization(data, columns)

    profile = active_profile(
        profile_name=profile_name,
    )
    plan = analysis_plan_from_dict(
        data, available_columns=columns, profile_name=profile_name
    )
    plan.footer_labels = [str(x) for x in (profile.get("footer_labels") or ())]
    return plan


def _role_semantic_hint_text(
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
) -> str:
    roles = roles_for(profile_name=profile_name)
    present = {str(c) for c in df.columns}
    lines: list[str] = []
    for col in roles.get("metric_denominator") or ():
        if col in present:
            lines.append(f"- `{col}` may represent a budget/planned amount (denominator candidate).")
    for col in roles.get("metric_numerator") or ():
        if col in present:
            lines.append(f"- `{col}` may represent an executed/spent amount (numerator candidate).")
    for col in roles.get("group_columns") or ():
        if col in present:
            lines.append(f"- `{col}` may be a category/group column.")
    if not lines:
        return ""
    return (
        "Optional semantic hints (do NOT hardcode; use only if they fit the request "
        "and actual columns):\n" + "\n".join(lines[:12])
    )


def _mostly_numeric(series: pd.Series) -> bool:
    if series.empty:
        return False
    coerced = pd.to_numeric(series, errors="coerce")
    return float(coerced.notna().mean()) >= 0.5


def _jsonable(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
