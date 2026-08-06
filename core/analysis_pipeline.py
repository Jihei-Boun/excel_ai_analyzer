"""채팅 분석: LLM 계획 → 실행기 → 검증 파이프라인."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from core.analysis_executor import execute_analysis_plan
from core.analysis_interpret import interpret_analysis_result
from core.analysis_plan_builder import build_analysis_plan
from core.analysis_plan_types import AnalysisPlan
from core.analysis_validate import validate_analysis_result, validation_error_messages
from core.llm_client import chat_json, chat_text
from core.pandasai_config import prepare_dataframe_for_ai
from core.plan_retry import RetryAttempt, run_plan_retries
from core.plan_types import ValidationReport
from core.row_classify import classify_rows, infer_dimension_columns
from core.profile_loader import footer_labels_for


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
    profile_name: str | None = None,
    use_budget_profile: bool = False,
    max_retries: int = 2,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    chat_text_fn: Callable[..., str] | None = None,
) -> AnalysisPipelineResult | None:
    """성공 시 결과, 적용 불가/소진 시 None(폴백)."""
    if not should_try_analysis_pipeline(df, wants_dataframe=True):
        return None

    json_fn = chat_json_fn or chat_json
    text_fn = chat_text_fn or chat_text
    prepared = prepare_dataframe_for_ai(df)
    dims = infer_dimension_columns(prepared)
    classified = classify_rows(
        prepared,
        dimension_columns=dims,
        footer_labels=footer_labels_for(profile_name=profile_name, use_budget_profile=use_budget_profile),
    )

    def _attempt(
        _attempt_index: int,
        previous_errors: list[str],
    ) -> RetryAttempt[AnalysisPipelineResult]:
        try:
            plan = build_analysis_plan(
                prompt,
                prepared,
                base_url=base_url,
                model=model,
                classified_df=classified,
                profile_name=profile_name, use_budget_profile=use_budget_profile,
                previous_errors=previous_errors or None,
                chat_json_fn=json_fn,
            )
        except Exception as exc:  # noqa: BLE001 — LLM/sanitize 실패 시 폴백
            return RetryAttempt(ok=False, errors=[f"plan_build: {exc}"])

        try:
            result_df, exec_meta = execute_analysis_plan(classified, plan)
        except Exception as exc:  # noqa: BLE001
            return RetryAttempt(ok=False, errors=[f"execute: {exc}"])

        report = validate_analysis_result(result_df, plan, source_df=prepared)
        if not report.ok:
            return RetryAttempt(ok=False, errors=validation_error_messages(report))

        reply = _build_reply(result_df, plan, exec_meta)
        if plan.interpret:
            try:
                interpretation = interpret_analysis_result(
                    prompt,
                    result_df,
                    plan,
                    exec_meta=exec_meta,
                    base_url=base_url,
                    model=model,
                    chat_text_fn=text_fn,
                )
                if interpretation:
                    reply = f"{reply}\n\n{interpretation}"
            except Exception as exc:  # noqa: BLE001 — 해석 실패해도 표는 반환
                exec_meta = {
                    **exec_meta,
                    "interpretation_error": str(exc),
                }

        return RetryAttempt(
            ok=True,
            value=AnalysisPipelineResult(
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
            ),
        )

    outcome = run_plan_retries(max_retries=max_retries, attempt=_attempt)
    return outcome.value


def run_analysis_pipeline(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
    max_retries: int = 2,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    chat_text_fn: Callable[..., str] | None = None,
) -> AnalysisPipelineResult:
    """실패 시 예외. 테스트용."""
    result = try_analysis_pipeline(
        prompt,
        df,
        base_url=base_url,
        model=model,
        profile_name=profile_name, use_budget_profile=use_budget_profile,
        max_retries=max_retries,
        chat_json_fn=chat_json_fn,
        chat_text_fn=chat_text_fn,
    )
    if result is None:
        raise RuntimeError("분석 계획 파이프라인이 결과를 만들지 못했습니다.")
    return result


def _build_reply(
    result: pd.DataFrame,
    plan: AnalysisPlan,
    exec_meta: dict[str, Any] | None = None,
) -> str:
    parts = [f"분석 계획 결과: {len(result):,}행"]
    if plan.criteria_note:
        parts.append(plan.criteria_note)
    meta = exec_meta or {}
    corr = meta.get("correlation") or {}
    if corr:
        r = corr.get("pearson_r")
        rho = corr.get("spearman_rho")
        strength = corr.get("strength") or ""
        if r is not None:
            parts.append(f"Pearson r={float(r):+.2f}")
        if rho is not None:
            parts.append(f"Spearman ρ={float(rho):+.2f}")
        if strength:
            parts.append(strength)
        both_n = corr.get("both_positive_count")
        if both_n is not None:
            parts.append(f"둘 다 양수 {int(both_n)}행")
    vs_mean = meta.get("vs_mean") or {}
    if vs_mean and vs_mean.get("mean") is not None:
        mean = float(vs_mean["mean"])
        rel = str(vs_mean.get("relation") or "below")
        label = "미만" if rel.startswith("below") else "초과"
        parts.append(f"평균 {mean:.2%} {label}")
    comparisons = meta.get("comparison") or []
    if comparisons:
        first = comparisons[0]
        metric = first.get("metric")
        higher = first.get("higher_group")
        if first.get("diff_pp") is not None and higher and metric:
            parts.append(
                f"{higher}의 {metric}이 {float(first['diff_pp']):.2f}%p 더 높음"
            )
        elif higher and metric and first.get("diff") is not None:
            parts.append(f"{higher}의 {metric}이 더 큼 (차이 {float(first['diff']):,.4g})")
    warnings = meta.get("warnings") or []
    if warnings:
        parts.append(f"주의 {len(warnings)}건")
    return " · ".join(parts)
