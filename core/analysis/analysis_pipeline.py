"""채팅 분석: LLM 계획 → 실행기 → 검증 파이프라인."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_interpret import interpret_analysis_result
from core.analysis.analysis_plan_builder import build_analysis_plan
from core.analysis.analysis_plan_types import AnalysisPlan
from core.analysis.analysis_plan_validate import (
    format_plan_validation_feedback,
    validate_analysis_plan,
)
from core.analysis.analysis_result_validate import (
    format_result_validation_feedback,
    validate_analysis_result,
    validation_info_messages,
    validation_warning_messages,
)
from core.analysis.analysis_plan_contract import (
    choose_retry_mode,
    composition_category_from_issues,
    normalize_plan_signature,
    plan_composition_category,
    planner_failure_reason,
)
from core.llm_client import chat_json, chat_text
from core.pai.pandasai_config import prepare_dataframe_for_ai
from core.common.plan_retry import RetryAttempt, run_plan_retries
from core.integrate.plan_types import ValidationReport
from core.schema.row_classify import classify_rows, infer_dimension_columns
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
    max_retries: int = 2,
    chat_json_fn: Callable[..., dict[str, Any]] | None = None,
    chat_text_fn: Callable[..., str] | None = None,
    exhaust_meta: dict[str, Any] | None = None,
) -> AnalysisPipelineResult | None:
    """성공 시 결과, 적용 불가/소진 시 None(폴백).

    흐름: Planner → Plan Validator → Executor → Result Validator → Interpreter
    max_retries=2 이면 최초 1회 + 재시도 2회 (총 3회).

    semantic_role_mismatch WARNING은 최초 attempt에서 soft retry 1회를 유발한다
    (자동 rewrite 금지 — Planner가 수정).
    """
    if not should_try_analysis_pipeline(df, wants_dataframe=True):
        return None

    json_fn = chat_json_fn or chat_json
    text_fn = chat_text_fn or chat_text
    prepared = prepare_dataframe_for_ai(df)
    dims = infer_dimension_columns(prepared)
    classified = classify_rows(
        prepared,
        dimension_columns=dims,
        footer_labels=footer_labels_for(profile_name=profile_name),
    )
    retry_log: list[dict[str, Any]] = []
    semantic_soft_retried = False
    seen_signatures: list[str] = []
    seen_composition_categories: list[str] = []
    duplicate_plan_count = 0
    repeated_failure_category: str | None = None
    repair_retry_success = 0
    regenerate_retry_success = 0
    last_retry_mode: str | None = None
    semantic_ambiguity = False

    def _attempt(
        attempt_index: int,
        previous_errors: list[str],
    ) -> RetryAttempt[AnalysisPipelineResult]:
        nonlocal semantic_soft_retried, duplicate_plan_count, repeated_failure_category
        nonlocal repair_retry_success, regenerate_retry_success, last_retry_mode
        nonlocal semantic_ambiguity
        # 동일 plan 반복 방지 힌트
        errors_for_planner = list(previous_errors or [])
        if attempt_index > 0 and seen_signatures:
            errors_for_planner = [
                *errors_for_planner,
                "Do not repeat the previous invalid plan unchanged. "
                "Change operation shape and/or required fields.",
            ]
        if repeated_failure_category:
            errors_for_planner = [
                *errors_for_planner,
                f"Repeated failure category: {repeated_failure_category}. "
                "Switch to a different composition "
                "(e.g. entity ranking = aggregate→sort→limit; "
                "rate ranking = ratio→sort→limit; group-wise = top_per_group).",
            ]
        try:
            plan = build_analysis_plan(
                prompt,
                prepared,
                base_url=base_url,
                model=model,
                classified_df=classified,
                profile_name=profile_name,
                previous_errors=errors_for_planner or None,
                chat_json_fn=json_fn,
            )
        except Exception as exc:  # noqa: BLE001 — LLM/sanitize 실패 시 폴백
            reason = planner_failure_reason(exc)
            retry_log.append(
                {
                    "attempt": attempt_index,
                    "failure_stage": "plan_build",
                    "planner_failure_reason": reason,
                    "validation_errors": [f"plan_build: {exc}"],
                    "validation_warnings": [],
                    "previous_plan": None,
                }
            )
            return RetryAttempt(ok=False, errors=[f"plan_build: {exc}"])

        plan_dict = plan.to_dict()
        signature = normalize_plan_signature(plan_dict)
        comp_cat = plan_composition_category(plan_dict)
        if signature and signature in seen_signatures:
            duplicate_plan_count += 1
            if comp_cat in seen_composition_categories:
                repeated_failure_category = comp_cat
            retry_log.append(
                {
                    "attempt": attempt_index,
                    "failure_stage": "duplicate_plan",
                    "planner_failure_reason": "duplicate_plan",
                    "composition_category": comp_cat,
                    "repeated_failure_category": repeated_failure_category,
                    "validation_errors": [
                        "duplicate_plan: regenerated the same invalid plan signature"
                    ],
                    "validation_warnings": [],
                    "previous_plan": plan_dict,
                    "plan_signature": signature,
                }
            )
            return RetryAttempt(
                ok=False,
                errors=[
                    *previous_errors,
                    "Duplicate plan detected. Do not repeat the previous invalid plan unchanged.",
                ],
            )
        if signature:
            seen_signatures.append(signature)

        # --- Plan-time validation (실행 전) ---
        plan_report = validate_analysis_plan(
            plan,
            classified,
            profile_name=profile_name,
            user_prompt=prompt,
        )
        if not plan_report.ok:
            codes = [i.code for i in plan_report.errors]
            fail_cat = composition_category_from_issues(codes) or comp_cat
            mode = choose_retry_mode(codes)
            last_retry_mode = mode
            feedback = format_plan_validation_feedback(
                plan_report,
                previous_plan=plan_dict,
                df=prepared,
                profile_name=profile_name,
                attempt=attempt_index,
                failure_stage="plan_validation",
                retry_mode=mode,
                failure_category=fail_cat,
            )
            if fail_cat in seen_composition_categories:
                repeated_failure_category = fail_cat
            seen_composition_categories.append(fail_cat)
            retry_log.append(
                {
                    "attempt": attempt_index,
                    "failure_stage": "plan_validation",
                    "composition_category": fail_cat,
                    "retry_mode": mode,
                    "repeated_failure_category": repeated_failure_category,
                    "validation_errors": [
                        f"{i.code}: {i.message}" for i in plan_report.errors
                    ],
                    "validation_warnings": [
                        f"{i.code}: {i.message}" for i in plan_report.warnings
                    ],
                    "previous_plan": plan_dict,
                    "plan_signature": signature,
                }
            )
            return RetryAttempt(ok=False, errors=feedback)

        try:
            result_df, exec_meta = execute_analysis_plan(classified, plan)
        except Exception as exc:  # noqa: BLE001
            retry_log.append(
                {
                    "attempt": attempt_index,
                    "failure_stage": "execute",
                    "validation_errors": [f"execute: {exc}"],
                    "validation_warnings": [],
                    "previous_plan": plan_dict,
                }
            )
            return RetryAttempt(ok=False, errors=[f"execute: {exc}"])

        # --- Result-time validation (실행 후) ---
        report = validate_analysis_result(
            result_df,
            plan,
            source_df=prepared,
            exec_meta=exec_meta,
            profile_name=profile_name,
            user_prompt=prompt,
        )
        if not report.ok:
            feedback = format_result_validation_feedback(
                report,
                previous_plan=plan_dict,
                attempt=attempt_index,
            )
            retry_log.append(
                {
                    "attempt": attempt_index,
                    "failure_stage": "result_validation",
                    "validation_errors": [
                        f"{i.code}: {i.message}" for i in report.errors
                    ],
                    "validation_warnings": [
                        f"{i.code}: {i.message}" for i in report.warnings
                    ],
                    "previous_plan": plan_dict,
                }
            )
            return RetryAttempt(ok=False, errors=feedback)

        # Semantic mismatch: rewrite 없이 Planner soft retry 1회
        # sibling ambiguity / role mismatch가 명확할 때만
        semantic_warnings = [
            i
            for i in report.warnings
            if i.code == "semantic_role_mismatch"
        ]
        if (
            semantic_warnings
            and not semantic_soft_retried
            and attempt_index < max_retries
            and _should_semantic_retry(semantic_warnings, prompt)
        ):
            semantic_soft_retried = True
            feedback = format_result_validation_feedback(
                report,
                previous_plan=plan_dict,
                attempt=attempt_index,
            )
            soft_msgs = [
                f"WARNING {i.code}: {i.message}" for i in semantic_warnings
            ]
            soft_msgs.append(
                "Semantic role mismatch / ambiguous sibling columns detected. "
                "Do NOT invent columns. Re-select the metric that best matches the "
                "user request using role_hints and criteria_note. "
                "Preserve the same operation composition "
                "(especially compare_groups / aggregate / ratio / filter_vs_mean steps); "
                "only change column choices if needed. "
                "Do not replace compare_groups with sort→limit."
            )
            retry_log.append(
                {
                    "attempt": attempt_index,
                    "failure_stage": "semantic_soft_retry",
                    "validation_errors": [],
                    "validation_warnings": soft_msgs,
                    "previous_plan": plan_dict,
                }
            )
            return RetryAttempt(ok=False, errors=feedback + soft_msgs)
        if semantic_warnings and not _should_semantic_retry(semantic_warnings, prompt):
            # Prompt does not disambiguate siblings — keep plan, record ambiguity.
            semantic_ambiguity = True
            warn_extra = [
                f"WARNING semantic_ambiguity: {i.message}" for i in semantic_warnings
            ]
            # fall through with warnings only
            report_warnings_extra = warn_extra
        else:
            report_warnings_extra = []

        warn_msgs = validation_warning_messages(report) + report_warnings_extra
        info_msgs = validation_info_messages(report)
        reply = _build_reply(result_df, plan, exec_meta)
        if plan.interpret:
            try:
                interpretation = interpret_analysis_result(
                    prompt,
                    result_df,
                    plan,
                    exec_meta=exec_meta,
                    validation_warnings=warn_msgs,
                    validation_infos=info_msgs,
                    base_url=base_url,
                    model=model,
                    chat_text_fn=text_fn,
                    profile_name=profile_name,
                )
                if interpretation:
                    reply = f"{reply}\n\n{interpretation}"
            except Exception as exc:  # noqa: BLE001 — 해석 실패해도 표는 반환
                exec_meta = {
                    **exec_meta,
                    "interpretation_error": str(exc),
                }

        obs = _planner_observability(
            plan,
            retry_log=retry_log,
            warn_msgs=warn_msgs,
            duplicate_plan_count=duplicate_plan_count,
            final_path="analysis_plan",
            repeated_failure_category=repeated_failure_category,
            repair_retry_success=repair_retry_success,
            regenerate_retry_success=regenerate_retry_success,
            last_retry_mode=last_retry_mode,
            semantic_ambiguity=semantic_ambiguity,
        )
        # Count recovery if a prior attempt failed and this one succeeded
        if attempt_index > 0 and last_retry_mode == "repair":
            repair_retry_success += 1
            obs["repair_retry_success"] = repair_retry_success
        elif attempt_index > 0 and last_retry_mode == "regenerate":
            regenerate_retry_success += 1
            obs["regenerate_retry_success"] = regenerate_retry_success
        elif attempt_index > 0:
            # mode unknown (e.g. execute/result) — count as regenerate bucket
            regenerate_retry_success += 1
            obs["regenerate_retry_success"] = regenerate_retry_success
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
                    "plan_validation": plan_report.summary_text(),
                    "aggregation": {"operation": "analysis_plan"},
                    "validation_warnings": warn_msgs,
                    "validation_infos": info_msgs,
                    "retry_log": list(retry_log),
                    **obs,
                    **exec_meta,
                },
            ),
        )

    outcome = run_plan_retries(max_retries=max_retries, attempt=_attempt)
    if outcome.value is not None:
        # 실패 이력도 성공 결과에 남김
        outcome.value.meta.setdefault("retry_log", retry_log)
        outcome.value.meta.setdefault("retry_count", len(retry_log))
        return outcome.value
    if exhaust_meta is not None:
        exhaust_meta.update(_classify_pipeline_exhaust(retry_log))
        exhaust_meta["retry_log"] = list(retry_log)
        exhaust_meta["duplicate_plan_count"] = duplicate_plan_count
        exhaust_meta.update(
            _planner_observability(
                None,
                retry_log=retry_log,
                warn_msgs=[],
                duplicate_plan_count=duplicate_plan_count,
                final_path="exhausted",
                repeated_failure_category=repeated_failure_category,
                repair_retry_success=repair_retry_success,
                regenerate_retry_success=regenerate_retry_success,
                last_retry_mode=last_retry_mode,
                semantic_ambiguity=semantic_ambiguity,
            )
        )
    return None


def _should_semantic_retry(warnings: list[Any], prompt: str) -> bool:
    """명확한 sibling/role mismatch일 때만 soft retry.

    질문이 period/target 단서 없이 모호하면 첫 plan을 유지(semantic_ambiguity).
    numerator/denominator role mismatch는 단서 없이도 soft retry 허용.
    """
    if not warnings:
        return False
    text = str(prompt or "").lower()
    has_cue = any(
        tok in text
        for tok in (
            "당년",
            "당해",
            "누적",
            "목표",
            "계획",
            "전년",
            "current",
            "ytd",
            "target",
            "prior",
            "actual",
            "효율",
            "집행률",
        )
    )
    for item in warnings:
        msg = str(getattr(item, "message", item) or "").lower()
        if "role" in msg and "candidate" in msg:
            return True
        if "numerator" in msg or "denominator" in msg:
            return True
        if "alternatives" in msg or "similarly named" in msg:
            return bool(has_cue)
    return False


def _planner_observability(
    plan: AnalysisPlan | None,
    *,
    retry_log: list[dict[str, Any]],
    warn_msgs: list[str],
    duplicate_plan_count: int,
    final_path: str,
    repeated_failure_category: str | None = None,
    repair_retry_success: int = 0,
    regenerate_retry_success: int = 0,
    last_retry_mode: str | None = None,
    semantic_ambiguity: bool = False,
) -> dict[str, Any]:
    selected_operations: list[str] = []
    selected_columns: list[str] = []
    if plan is not None:
        selected_operations = [s.op for s in plan.steps]
        raw = plan.raw or {}
        for key in ("numerator", "denominator", "group_column", "value_column"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                selected_columns.append(val)
        for step in plan.steps:
            payload = step.payload or {}
            for key in ("column", "left_column", "right_column", "numerator", "denominator"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    selected_columns.append(val)
            for metric in payload.get("metrics") or []:
                if isinstance(metric, dict) and metric.get("column"):
                    selected_columns.append(str(metric["column"]))
                elif isinstance(metric, str):
                    selected_columns.append(metric)
            for col in payload.get("group_by") or []:
                selected_columns.append(str(col))
            if step.op == "ratio_of_aggregates":
                name = str(payload.get("name") or "").strip()
                if name:
                    selected_columns.append(name)
    # unique preserve
    uniq_cols: list[str] = []
    seen: set[str] = set()
    for c in selected_columns:
        if c not in seen:
            seen.add(c)
            uniq_cols.append(c)
    failure_reasons = [
        str(r.get("planner_failure_reason") or r.get("failure_stage") or "")
        for r in retry_log
        if r.get("failure_stage")
    ]
    comp_cats = [
        str(r.get("composition_category") or "")
        for r in retry_log
        if r.get("composition_category")
    ]
    modes = [str(r.get("retry_mode") or "") for r in retry_log if r.get("retry_mode")]
    return {
        "selected_operations": selected_operations,
        "selected_columns": uniq_cols,
        "retry_count": len(retry_log),
        "semantic_warnings": [w for w in warn_msgs if "semantic" in w.lower()],
        "planner_failure_reason": failure_reasons[-1] if failure_reasons else None,
        "planner_failure_reasons": failure_reasons,
        "composition_categories": comp_cats,
        "repeated_failure_category": repeated_failure_category,
        "duplicate_plan_count": duplicate_plan_count,
        "final_path": final_path,
        "repair_retry_success": repair_retry_success,
        "regenerate_retry_success": regenerate_retry_success,
        "last_retry_mode": last_retry_mode or (modes[-1] if modes else None),
        "retry_modes": modes,
        "semantic_ambiguity": semantic_ambiguity,
    }


def _classify_pipeline_exhaust(retry_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Planner/validator 소진 원인을 fallback_reason으로 요약."""
    if not retry_log:
        return {"fallback_reason": "planner_generation_failed"}
    stages = [str(r.get("failure_stage") or "") for r in retry_log]
    reasons = [
        str(r.get("planner_failure_reason") or "")
        for r in retry_log
        if r.get("planner_failure_reason")
    ]
    if any(s == "plan_build" for s in stages) and all(
        s in {"plan_build", "duplicate_plan", ""} for s in stages
    ):
        return {
            "fallback_reason": "planner_generation_failed",
            "planner_failure_reason": reasons[-1] if reasons else "empty_plan",
        }
    if any(s == "duplicate_plan" for s in stages) and not any(
        s in {"execute", "result_validation", "plan_validation"} for s in stages
    ):
        return {
            "fallback_reason": "planner_generation_failed",
            "planner_failure_reason": "duplicate_plan",
        }
    if any(s == "execute" for s in stages):
        return {"fallback_reason": "execution_error"}
    if any(s == "result_validation" for s in stages):
        return {"fallback_reason": "result_validation_exhausted"}
    if any(s == "plan_validation" for s in stages):
        # unsupported aggregation 등
        joined = " ".join(
            " ".join(r.get("validation_errors") or []) for r in retry_log
        )
        if "unsupported_aggregation" in joined or "unsupported" in joined.lower():
            return {"fallback_reason": "unsupported_operation"}
        if "missing_aggregation_fn" in joined:
            return {
                "fallback_reason": "plan_validation_exhausted",
                "planner_failure_reason": "missing_required_field",
            }
        return {"fallback_reason": "plan_validation_exhausted"}
    if any(s == "semantic_soft_retry" for s in stages):
        return {"fallback_reason": "plan_validation_exhausted"}
    return {"fallback_reason": "plan_validation_exhausted"}


def run_analysis_pipeline(
    prompt: str,
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
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
        profile_name=profile_name,
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
