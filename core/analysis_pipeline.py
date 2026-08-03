"""채팅 분석: LLM 계획 → 실행기 → 검증 파이프라인."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from core.analysis_executor import execute_analysis_plan
from core.analysis_plan_builder import build_analysis_plan
from core.analysis_plan_types import AnalysisPlan
from core.analysis_validate import validate_analysis_result, validation_error_messages
from core.llm_client import chat_json
from core.pandasai_config import prepare_dataframe_for_ai
from core.plan_types import ValidationReport
from core.row_classify import classify_rows, infer_dimension_columns


@dataclass
class AnalysisPipelineResult:
    dataframe: pd.DataFrame
    reply: str
    plan: AnalysisPlan
    validation: ValidationReport
    meta: dict[str, Any] = field(default_factory=dict)


def should_try_analysis_pipeline(df: pd.DataFrame, *, wants_dataframe: bool) -> bool:
    """질문별 정규식 없이, 범용 게이트만 사용한다."""
    if not wants_dataframe or df is None or df.empty:
        return False
    numeric_hits = 0
    for col in df.columns:
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.notna().any() or pd.api.types.is_numeric_dtype(df[col]):
            numeric_hits += 1
            if numeric_hits >= 1:
                return True
    return False


def try_analysis_pipeline(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    max_retries: int = 2,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
) -> AnalysisPipelineResult | None:
    """성공 시 결과, 적용 불가/소진 시 None(폴백)."""
    if not should_try_analysis_pipeline(df, wants_dataframe=True):
        return None

    json_fn = chat_json_fn or chat_json
    prepared = prepare_dataframe_for_ai(df)
    dims = infer_dimension_columns(prepared)
    classified = classify_rows(prepared, dimension_columns=dims)

    previous_errors: list[str] = []
    last_exc: Exception | None = None

    for _attempt in range(max_retries + 1):
        try:
            plan = build_analysis_plan(
                prompt,
                prepared,
                base_url=base_url,
                model=model,
                classified_df=classified,
                use_budget_profile=use_budget_profile,
                previous_errors=previous_errors or None,
                chat_json_fn=json_fn,
            )
        except Exception as exc:  # noqa: BLE001 — LLM/sanitize 실패 시 폴백
            last_exc = exc
            previous_errors = [f"plan_build: {exc}"]
            continue

        try:
            result_df, exec_meta = execute_analysis_plan(classified, plan)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            previous_errors = [f"execute: {exc}"]
            continue

        report = validate_analysis_result(result_df, plan, source_df=prepared)
        if report.ok:
            reply = _build_reply(result_df, plan)
            return AnalysisPipelineResult(
                dataframe=result_df,
                reply=reply,
                plan=plan,
                validation=report,
                meta={
                    "analysis_plan": plan.to_dict(),
                    "analysis_validation": report.summary_text(),
                    "aggregation": {"operation": "analysis_plan"},
                    **exec_meta,
                },
            )

        previous_errors = validation_error_messages(report)
        last_exc = ValueError(report.summary_text())

    _ = last_exc
    return None


def run_analysis_pipeline(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    use_budget_profile: bool = False,
    max_retries: int = 2,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
) -> AnalysisPipelineResult:
    """실패 시 예외. 테스트용."""
    result = try_analysis_pipeline(
        prompt,
        df,
        base_url=base_url,
        model=model,
        use_budget_profile=use_budget_profile,
        max_retries=max_retries,
        chat_json_fn=chat_json_fn,
    )
    if result is None:
        raise RuntimeError("분석 계획 파이프라인이 결과를 만들지 못했습니다.")
    return result


def _build_reply(result: pd.DataFrame, plan: AnalysisPlan) -> str:
    parts = [f"분석 계획 결과: {len(result):,}행"]
    if plan.criteria_note:
        parts.append(plan.criteria_note)
    return " · ".join(parts)
