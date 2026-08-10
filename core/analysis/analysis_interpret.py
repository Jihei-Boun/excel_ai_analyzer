"""분석 결과 해석 — 검증된 계산 결과 설명 전용.

입력 계약 (Phase 4)
-------------------
Interpreter에는 다음만 전달한다.

* user question
* AnalysisPlan 요약 (criteria / ops — 원본 DF 없음)
* validated result rows (제한)
* result metadata (comparison, correlation, warnings 등)
* profile interpretation hints

원본 DataFrame·미검증 수치·추가 aggregation용 원본 행은 전달하지 않는다.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan
from core.llm_client import chat_text

_MAX_RESULT_ROWS = 40

_INTERPRETER_HARD_CONSTRAINTS = (
    "You are an interpretation module only. "
    "Do not recompute values. "
    "Do not derive new metrics unless they are explicitly included in the validated result. "
    "Do not aggregate, filter, or re-read source data. "
    "Do not invent numbers, percentages, differences, or ranks absent from the JSON. "
    "Do not choose a different numerator/denominator than shown in the result. "
    "Do not invent causal explanations beyond what the numbers support. "
    "Treat the provided validated result as the source of truth. "
    "If a value is missing or null, say it is unavailable — do not guess."
)


def build_interpreter_payload(
    prompt: str,
    result_df: pd.DataFrame,
    plan: AnalysisPlan,
    *,
    exec_meta: dict[str, Any] | None = None,
    validation_warnings: list[str] | None = None,
    validation_infos: list[str] | None = None,
) -> dict[str, Any]:
    """Interpreter LLM에 넘길 최소 JSON payload."""
    meta = exec_meta or {}
    sort_spec = plan.sort_spec
    sort_meta = None
    if sort_spec:
        by, ascending = sort_spec
        sort_meta = {"by": by, "ascending": ascending}

    plan_summary = {
        "criteria_note": plan.criteria_note,
        "operation": str((plan.raw or {}).get("operation") or "") or None,
        "steps": [s.op for s in plan.steps],
        "dimension_columns": list(plan.dimension_columns),
        "output_columns": list(plan.output_columns),
        "interpret": bool(plan.interpret),
    }
    # high-level fields useful for wording (not for recomputation)
    raw = plan.raw or {}
    for key in (
        "group_column",
        "groups",
        "rate_name",
        "numerator",
        "denominator",
        "x_column",
        "y_column",
    ):
        if key in raw and raw[key] not in (None, "", []):
            plan_summary[key] = raw[key]

    metadata: dict[str, Any] = {
        "row_count": int(len(result_df)) if result_df is not None else 0,
        "columns": [str(c) for c in (result_df.columns if result_df is not None else [])],
        "sort": sort_meta,
        "comparison": meta.get("comparison") or [],
        "structured": meta.get("structured") or [],
        "distribution": meta.get("distribution"),
        "correlation": meta.get("correlation"),
        "vs_mean": meta.get("vs_mean"),
        "aggregate_sources": meta.get("aggregate_sources") or {},
        "zero_denominator_groups": meta.get("zero_denominator_groups") or [],
        "warnings": list(meta.get("warnings") or []) + list(validation_warnings or []),
        "infos": list(validation_infos or []),
    }

    return {
        "question": prompt,
        "plan": plan_summary,
        "result": _df_to_records(result_df, max_rows=_MAX_RESULT_ROWS),
        "metadata": metadata,
    }


def interpret_analysis_result(
    prompt: str,
    result_df: pd.DataFrame,
    plan: AnalysisPlan,
    *,
    exec_meta: dict[str, Any] | None = None,
    validation_warnings: list[str] | None = None,
    validation_infos: list[str] | None = None,
    base_url: str,
    model: str,
    chat_text_fn: Callable[..., str] | None = None,
    profile_name: str | None = None,
) -> str:
    """검증된 계산 결과만 근거로 해석 문장을 생성한다."""
    from core.profile_loader import (
        interpret_guidance_for,
        interpret_system_prompt_for,
        interpret_user_prefix_for,
    )

    payload = build_interpreter_payload(
        prompt,
        result_df,
        plan,
        exec_meta=exec_meta,
        validation_warnings=validation_warnings,
        validation_infos=validation_infos,
    )

    system = interpret_system_prompt_for(profile_name=profile_name)
    system = f"{system} {_INTERPRETER_HARD_CONSTRAINTS}".strip()
    domain_guidance = interpret_guidance_for(profile_name=profile_name)
    if domain_guidance:
        system = f"{system} Domain wording hints (do not invent numbers): {domain_guidance}"

    prefix = interpret_user_prefix_for(profile_name=profile_name) or (
        "Explain the validated calculation results below. "
        "Do not recompute. Use only values present in the JSON."
    )
    user = (
        f"{prefix}\n\n"
        "Validated analysis payload (source of truth):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
    )

    fn = chat_text_fn or chat_text
    text = fn(
        user,
        system=system,
        base_url=base_url,
        model=model,
    )
    return (text or "").strip()


def _df_to_records(
    df: pd.DataFrame | None,
    *,
    max_rows: int = _MAX_RESULT_ROWS,
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    records: list[dict[str, Any]] = []
    for _, row in df.head(max_rows).iterrows():
        item: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                item[str(col)] = None
            elif hasattr(val, "item"):
                try:
                    item[str(col)] = val.item()
                except (ValueError, AttributeError):
                    item[str(col)] = val
            else:
                item[str(col)] = val
        records.append(item)
    return records
