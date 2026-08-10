"""AnalysisPlan 실행 후(result-time) 검증.

Plan-time(analysis_plan_validate)과 분리한다.

Severity
--------
* ERROR  — retry / fallback 유발 (``report.ok == False``)
* WARNING — Interpreter metadata; ``semantic_role_mismatch``는 pipeline soft retry 1회
* INFO    — 진단용 메타

Structural vs Semantic
----------------------
* Structural: 강하게 검사 (컬럼·그룹·범위·정렬·분모 0 등)
* Semantic: profile role mismatch는 WARNING (자동 rewrite 금지)

Semantic mismatch 설계 (강제 구현 최소화)
-----------------------------------------
요청 개념과 선택된 컬럼 role이 어긋날 수 있으면 warning + 후보 role만 제시한다.
예: selected=당년도집행, related candidates=집행계_합계.
Validator는 답을 고치지 않는다. Pipeline이 soft retry로 Planner에 피드백한다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan
from core.integrate.plan_types import ValidationIssue, ValidationReport
from core.schema.row_classify import ROW_TYPE_COL, classify_rows, infer_dimension_columns
from core.summary.summary_utils import cell_text


def validate_analysis_result(
    result: pd.DataFrame | None,
    plan: AnalysisPlan,
    *,
    source_df: pd.DataFrame | None = None,
    exec_meta: dict[str, Any] | None = None,
    profile_name: str | None = None,
    user_prompt: str | None = None,
) -> ValidationReport:
    """실행 결과가 AnalysisPlan 기대 구조를 만족하는지 검사한다."""
    issues: list[ValidationIssue] = []
    meta = dict(exec_meta or {})

    if result is None:
        return ValidationReport(
            ok=False,
            issues=[ValidationIssue("error", "empty_result", "결과가 없습니다.")],
        )

    if not isinstance(result, pd.DataFrame):
        return ValidationReport(
            ok=False,
            issues=[
                ValidationIssue(
                    "error",
                    "non_dataframe_result",
                    f"결과가 DataFrame이 아닙니다: {type(result).__name__}",
                )
            ],
        )

    issues.append(
        ValidationIssue(
            "info",
            "result_row_count",
            f"result row_count={len(result)}",
        )
    )

    if result.empty:
        issues.append(
            ValidationIssue(
                "error",
                "empty_dataframe",
                "결과 DataFrame이 비어 있습니다 (0행).",
            )
        )

    issues.extend(_check_numeric_integrity(result))
    issues.extend(_check_output_columns(result, plan))
    issues.extend(_check_limit_contract(result, plan))
    issues.extend(_check_group_presence(result, plan))
    issues.extend(_check_dimension_quality(result, plan))
    issues.extend(_check_summary_rows(result, plan))
    issues.extend(_check_sort_contract(result, plan))
    issues.extend(_check_operation_contracts(result, plan, meta))
    issues.extend(_check_ratio_denominator_scope(result, plan, meta))
    issues.extend(
        _check_semantic_role_mismatch(
            plan,
            profile_name=profile_name,
            user_prompt=user_prompt,
            source_df=source_df,
        )
    )

    if source_df is not None and not result.empty:
        issues.extend(_check_derive_sample(result, plan))

    # runtime meta warnings
    for w in meta.get("warnings") or []:
        text = str(w)
        if "denominator" in text.lower() or "분모" in text:
            continue  # handled in ratio scope
        issues.append(
            ValidationIssue("warning", "executor_warning", text)
        )

    errors = [i for i in issues if i.level == "error"]
    return ValidationReport(ok=not errors, issues=issues)


def format_result_validation_feedback(
    report: ValidationReport,
    *,
    previous_plan: dict[str, Any] | None = None,
    attempt: int | None = None,
) -> list[str]:
    """Result validation 실패 → Planner feedback (plan 실패와 구분)."""
    import json

    lines: list[str] = [
        "Failure stage: result_validation",
        "The plan executed, but the produced result is invalid because:",
    ]
    if attempt is not None:
        lines.insert(0, f"Attempt: {attempt}")
    if previous_plan:
        try:
            compact = json.dumps(previous_plan, ensure_ascii=False, default=str)
            if len(compact) > 1200:
                compact = compact[:1200] + "…"
            lines.append(f"Previous plan: {compact}")
        except Exception:  # noqa: BLE001
            lines.append("Previous plan: (unserializable)")

    lines.append("Validation errors:")
    idx = 1
    for issue in report.issues:
        if issue.level not in {"error", "warning"}:
            continue
        prefix = "ERROR" if issue.level == "error" else "WARNING"
        lines.append(f"{idx}. [{prefix}/{issue.code}] {issue.message}")
        idx += 1
    lines.append(
        "Generate a corrected AnalysisPlan that produces a valid result. "
        "Do not invent columns. Do not ask the interpreter to recompute."
    )
    return lines


def validation_error_messages(report: ValidationReport) -> list[str]:
    return [f"{item.code}: {item.message}" for item in report.errors]


def validation_warning_messages(report: ValidationReport) -> list[str]:
    return [f"{item.code}: {item.message}" for item in report.warnings]


def validation_info_messages(report: ValidationReport) -> list[str]:
    return [
        f"{item.code}: {item.message}"
        for item in report.issues
        if item.level == "info"
    ]


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def _check_numeric_integrity(result: pd.DataFrame) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if result.empty:
        return issues
    numeric_cols = [
        c
        for c in result.columns
        if pd.api.types.is_numeric_dtype(result[c])
        or pd.to_numeric(result[c], errors="coerce").notna().any()
    ]
    if not numeric_cols:
        return issues
    all_nan = True
    has_inf = False
    for col in numeric_cols:
        series = pd.to_numeric(result[col], errors="coerce")
        if series.notna().any():
            all_nan = False
        if bool(((series == np.inf) | (series == -np.inf)).any()):
            has_inf = True
    if all_nan:
        issues.append(
            ValidationIssue(
                "error",
                "all_nan_result",
                "숫자 결과 컬럼이 모두 NaN입니다.",
            )
        )
    if has_inf:
        issues.append(
            ValidationIssue(
                "error",
                "inf_result",
                "결과에 Inf가 포함되어 있습니다 (분모 0 등).",
            )
        )
    return issues


def _check_output_columns(result: pd.DataFrame, plan: AnalysisPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    required_ops = {s.op for s in plan.steps}
    # plan.output_columns — structural: missing required outputs are errors for key ops
    for col in plan.output_columns:
        if col in result.columns:
            continue
        level = "error" if required_ops & {
            "aggregate",
            "ratio_of_aggregates",
            "compare_groups",
            "correlation",
            "top_per_group",
        } else "warning"
        # derived names may be renamed — soft if select happened
        if any(s.op == "select_columns" for s in plan.steps):
            level = "warning"
        issues.append(
            ValidationIssue(
                level,
                "missing_output_column",
                f"출력 컬럼 `{col}`이 결과에 없습니다.",
            )
        )
    return issues


def _check_limit_contract(result: pd.DataFrame, plan: AnalysisPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    limit_n = plan.limit_n
    if limit_n is None:
        return issues
    if len(result) > limit_n:
        issues.append(
            ValidationIssue(
                "error",
                "limit_exceeded",
                f"요청 상위 {limit_n}개를 초과한 {len(result)}행입니다.",
            )
        )
    elif 0 < len(result) < limit_n:
        issues.append(
            ValidationIssue(
                "warning",
                "limit_underfilled",
                (
                    f"요청 top {limit_n}보다 적은 {len(result)}행만 반환되었습니다 "
                    "(필터·데이터 부족 가능)."
                ),
            )
        )
    return issues


def _check_group_presence(result: pd.DataFrame, plan: AnalysisPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw = plan.raw or {}
    groups = raw.get("groups") or []
    group_col = str(raw.get("group_column") or "")
    if not groups or not group_col or group_col not in result.columns or result.empty:
        return issues
    if isinstance(groups, str):
        groups = [groups]
    present = {str(v).strip() for v in result[group_col].dropna().unique().tolist()}
    missing: list[str] = []
    for g in groups:
        text = str(g).strip()
        if text in present:
            continue
        if any(text in p or p in text for p in present if p):
            continue
        missing.append(text)
    if not missing:
        return issues
    # 명시 요청 그룹 → error; 일부만이면 여전히 error (Phase 3 정책과 동일)
    issues.append(
        ValidationIssue(
            "error",
            "result_missing_groups",
            f"결과에서 요청 그룹이 누락되었습니다: {missing}.",
        )
    )
    return issues


def _check_dimension_quality(result: pd.DataFrame, plan: AnalysisPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    dims = [c for c in plan.dimension_columns if c in result.columns]
    if not dims:
        dims = [
            c for c in infer_dimension_columns(result) if c in result.columns
        ][:1]
    if not dims or result.empty:
        return issues
    primary = dims[0]
    blank_ratio = float(result[primary].map(lambda v: not bool(cell_text(v))).mean())
    if blank_ratio >= 0.99:
        issues.append(
            ValidationIssue(
                "error",
                "blank_dimensions",
                f"항목 컬럼 `{primary}`이 모두 비어 있습니다.",
            )
        )
    elif blank_ratio > 0:
        issues.append(
            ValidationIssue(
                "warning",
                "partial_blank_dimensions",
                f"항목 컬럼 `{primary}`에 빈 값이 {blank_ratio:.0%} 있습니다.",
            )
        )
    dup = result.duplicated(subset=dims, keep=False)
    if bool(dup.any()):
        issues.append(
            ValidationIssue(
                "warning",
                "duplicate_dimensions",
                f"동일 항목이 {int(dup.sum())}행 중복됩니다.",
            )
        )
    return issues


def _check_summary_rows(result: pd.DataFrame, plan: AnalysisPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if plan.filters_to_detail_only and not plan.uses_aggregate_ops and not result.empty:
        classified = classify_rows(
            result,
            dimension_columns=plan.dimension_columns or None,
            footer_labels=plan.footer_labels,
        )
        bad = classified[ROW_TYPE_COL].isin(["subtotal", "total", "footer"])
        if bool(bad.any()):
            issues.append(
                ValidationIssue(
                    "error",
                    "summary_rows_mixed",
                    f"소계·합계·footer 행이 {int(bad.sum())}개 결과에 섞여 있습니다.",
                )
            )
            issues.append(
                ValidationIssue(
                    "info",
                    "excluded_summary_hint",
                    f"summary_rows_present={int(bad.sum())}",
                )
            )
    return issues


def _check_sort_contract(result: pd.DataFrame, plan: AnalysisPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    sort_spec = plan.sort_spec
    if not sort_spec or result.empty:
        return issues
    by, ascending = sort_spec
    by = [c for c in by if c in result.columns]
    if not by:
        issues.append(
            ValidationIssue(
                "error",
                "sort_column_missing_in_result",
                "정렬 대상 컬럼이 결과에 없습니다.",
            )
        )
        return issues
    expected = result.sort_values(by, ascending=ascending[: len(by)], kind="mergesort")
    if not result[by].reset_index(drop=True).equals(expected[by].reset_index(drop=True)):
        issues.append(
            ValidationIssue(
                "error",
                "sort_mismatch",
                f"정렬 기준 {by}이 계획과 일치하지 않습니다.",
            )
        )
    return issues


def _check_operation_contracts(
    result: pd.DataFrame,
    plan: AnalysisPlan,
    meta: dict[str, Any],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ops = {s.op for s in plan.steps}
    raw = plan.raw or {}

    if "aggregate" in ops and not result.empty:
        group_cols = []
        for step in plan.steps:
            if step.op == "aggregate":
                group_cols.extend(str(c) for c in (step.payload.get("group_by") or []))
        for g in group_cols:
            if g and g not in result.columns:
                issues.append(
                    ValidationIssue(
                        "error",
                        "aggregate_missing_group_column",
                        f"aggregate 결과에 group column `{g}`이 없습니다.",
                    )
                )
        # metric presence: at least one numeric or planned metric
        metric_names: list[str] = []
        for step in plan.steps:
            if step.op != "aggregate":
                continue
            for m in step.payload.get("metrics") or []:
                if isinstance(m, str):
                    metric_names.append(m)
                elif isinstance(m, dict):
                    metric_names.append(str(m.get("column") or m.get("name") or ""))
        for name in metric_names:
            if name and name not in result.columns:
                issues.append(
                    ValidationIssue(
                        "error",
                        "aggregate_missing_metric",
                        f"aggregate metric `{name}`이 결과에 없습니다.",
                    )
                )

    if "ratio_of_aggregates" in ops and not result.empty:
        for step in plan.steps:
            if step.op != "ratio_of_aggregates":
                continue
            name = str(step.payload.get("name") or "비율")
            if name not in result.columns:
                issues.append(
                    ValidationIssue(
                        "error",
                        "ratio_output_missing",
                        f"ratio output `{name}`이 결과에 없습니다.",
                    )
                )
                continue
            series = pd.to_numeric(result[name], errors="coerce")
            finite = series.dropna()
            if not finite.empty and bool(((finite == np.inf) | (finite == -np.inf)).any()):
                issues.append(
                    ValidationIssue(
                        "error",
                        "ratio_non_finite",
                        f"ratio `{name}`에 비유한 값이 있습니다.",
                    )
                )

    if "compare_groups" in ops and not result.empty:
        group_col = str(raw.get("group_column") or "")
        for step in plan.steps:
            if step.op == "compare_groups" and step.payload.get("group_column"):
                group_col = str(step.payload.get("group_column"))
        if group_col and group_col not in result.columns:
            issues.append(
                ValidationIssue(
                    "error",
                    "compare_missing_group_column",
                    f"compare_groups 결과에 `{group_col}`이 없습니다.",
                )
            )
        metrics = []
        for step in plan.steps:
            if step.op == "compare_groups":
                metrics.extend(str(m) for m in (step.payload.get("metrics") or []))
        present_metric = any(m in result.columns for m in metrics if m)
        if metrics and not present_metric:
            issues.append(
                ValidationIssue(
                    "error",
                    "compare_missing_metric",
                    "compare_groups 비교 metric이 결과에 없습니다.",
                )
            )

    if "correlation" in ops:
        corr = meta.get("correlation") or {}
        has_coeff = any(
            corr.get(k) is not None for k in ("pearson_r", "spearman_rho", "r", "rho")
        )
        # also allow correlation columns in result
        corr_cols = [
            c
            for c in result.columns
            if any(tok in str(c).lower() for tok in ("pearson", "spearman", "상관", "corr"))
        ]
        if not has_coeff and not corr_cols and result.empty:
            issues.append(
                ValidationIssue(
                    "error",
                    "correlation_missing",
                    "correlation coefficient가 결과에 없습니다.",
                )
            )
        for key in ("pearson_r", "spearman_rho", "r", "rho"):
            val = corr.get(key)
            if val is None:
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                issues.append(
                    ValidationIssue(
                        "error",
                        "correlation_non_numeric",
                        f"correlation `{key}`이 숫자가 아닙니다.",
                    )
                )
                continue
            if num < -1.0 - 1e-9 or num > 1.0 + 1e-9:
                issues.append(
                    ValidationIssue(
                        "error",
                        "correlation_out_of_range",
                        f"correlation `{key}`={num} is outside [-1, 1].",
                    )
                )

    if "distribution_summary" in ops and not result.empty:
        if len(result) < 1:
            issues.append(
                ValidationIssue(
                    "error",
                    "distribution_empty",
                    "distribution_summary 결과가 비어 있습니다.",
                )
            )

    if "top_per_group" in ops and not result.empty:
        for step in plan.steps:
            if step.op != "top_per_group":
                continue
            value_col = str(step.payload.get("value_column") or "")
            group_col = str(step.payload.get("group_column") or "")
            try:
                n = int(step.payload.get("n") or 1)
            except (TypeError, ValueError):
                n = 1
            if value_col and value_col not in result.columns:
                issues.append(
                    ValidationIssue(
                        "error",
                        "top_per_group_missing_value",
                        f"top_per_group value column `{value_col}`이 결과에 없습니다.",
                    )
                )
            if group_col and group_col in result.columns:
                counts = result[group_col].map(lambda v: cell_text(v)).value_counts()
                over = counts[counts > n]
                if not over.empty:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "top_per_group_exceeds_n",
                            f"일부 그룹이 n={n}을 초과합니다: {over.to_dict()}",
                        )
                    )
            if ROW_TYPE_COL in result.columns:
                bad = result[ROW_TYPE_COL].isin(["subtotal", "total", "footer"])
                if bool(bad.any()):
                    issues.append(
                        ValidationIssue(
                            "error",
                            "ranking_includes_summary_rows",
                            "ranking 결과에 subtotal/total/footer가 포함되어 있습니다.",
                        )
                    )

    return issues


def _check_ratio_denominator_scope(
    result: pd.DataFrame,
    plan: AnalysisPlan,
    meta: dict[str, Any],
) -> list[ValidationIssue]:
    """필터·그룹 범위에서 분모 0인 ratio를 검사한다.

    정책:
    * zero_denominator_groups 메타 기록
    * 요청 핵심 그룹(plan.raw.groups)에 포함되면 ERROR
    * 그 외는 WARNING
    """
    issues: list[ValidationIssue] = []
    ratio_steps = [s for s in plan.steps if s.op == "ratio_of_aggregates"]
    if not ratio_steps or result.empty:
        # still honor executor meta
        zero_groups = list(meta.get("zero_denominator_groups") or [])
        if meta.get("denominator_zero") and not zero_groups:
            issues.append(
                ValidationIssue(
                    "error",
                    "denominator_zero_runtime",
                    "비율 계산에서 denominator zero가 발생했습니다.",
                )
            )
        return issues

    raw = plan.raw or {}
    requested = raw.get("groups") or []
    if isinstance(requested, str):
        requested = [requested]
    requested_norm = {str(g).strip() for g in requested if str(g).strip()}

    group_col = str(raw.get("group_column") or "")
    for step in plan.steps:
        if step.op == "compare_groups" and step.payload.get("group_column"):
            group_col = str(step.payload.get("group_column"))
        if step.op == "aggregate":
            gb = step.payload.get("group_by") or []
            if gb and not group_col:
                group_col = str(gb[0])

    zero_groups: list[str] = list(meta.get("zero_denominator_groups") or [])

    for step in ratio_steps:
        name = str(step.payload.get("name") or "비율")
        den = str(step.payload.get("denominator") or "")
        if name not in result.columns:
            continue
        ratio = pd.to_numeric(result[name], errors="coerce")
        # NaN ratio with zero/null denominator column if present
        den_zero_mask = ratio.isna()
        if den and den in result.columns:
            den_vals = pd.to_numeric(result[den], errors="coerce")
            den_zero_mask = den_vals.fillna(0).abs() == 0

        if not bool(den_zero_mask.any()):
            continue

        if group_col and group_col in result.columns:
            for val in result.loc[den_zero_mask, group_col].tolist():
                text = str(val).strip()
                if text and text not in zero_groups:
                    zero_groups.append(text)
        else:
            zero_groups.append("__row__")

    if zero_groups:
        meta["zero_denominator_groups"] = zero_groups
        issues.append(
            ValidationIssue(
                "info",
                "zero_denominator_groups",
                f"zero_denominator_groups={zero_groups}",
            )
        )

        required_hit = [
            g
            for g in zero_groups
            if g in requested_norm
            or any(g in r or r in g for r in requested_norm if r)
        ]
        if required_hit or (requested_norm and not group_col):
            issues.append(
                ValidationIssue(
                    "error",
                    "denominator_zero_required_group",
                    (
                        "Denominator is zero for required group(s) in the filtered/aggregated "
                        f"scope: {required_hit or zero_groups}. "
                        "Ratio is undefined for these groups."
                    ),
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    "warning",
                    "denominator_zero_optional_group",
                    (
                        "Denominator is zero for some groups in the result scope: "
                        f"{zero_groups}. Marked invalid for those rows."
                    ),
                )
            )

    if meta.get("denominator_zero") and not zero_groups:
        issues.append(
            ValidationIssue(
                "error",
                "denominator_zero_runtime",
                "비율 계산에서 denominator zero가 발생했습니다.",
            )
        )
    return issues


def _check_semantic_role_mismatch(
    plan: AnalysisPlan,
    *,
    profile_name: str | None,
    user_prompt: str | None,
    source_df: pd.DataFrame | None = None,
) -> list[ValidationIssue]:
    """Semantic mismatch는 WARNING만. 자동 rewrite 금지.

    1) profile role/prefs 후보와 선택 컬럼 불일치
    2) 스키마에 유사 이름 수치 컬럼이 여러 개인데 요청이 모호한 경우
    """
    issues: list[ValidationIssue] = []
    raw = plan.raw or {}
    selected = _selected_metric_columns(plan)
    num = str(raw.get("numerator") or "")
    den = str(raw.get("denominator") or "")
    if num and num not in selected:
        selected.append(num)
    if den and den not in selected:
        selected.append(den)

    try:
        from core.profile_loader import column_prefs_for, roles_for
    except Exception:  # noqa: BLE001
        roles = {}
        prefs = {}
    else:
        roles = roles_for(profile_name=profile_name)
        prefs = column_prefs_for(profile_name=profile_name)

    default_num = [str(x) for x in (prefs.get("default_numerator") or ())]
    current_num = [str(x) for x in (prefs.get("current_numerator") or ())]
    role_num = [str(x) for x in (roles.get("metric_numerator") or ())]
    role_den = [str(x) for x in (roles.get("metric_denominator") or ())]

    if num and current_num and default_num:
        if num in current_num and num not in default_num:
            alts = [c for c in default_num if c != num][:4]
            if alts:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "semantic_role_mismatch",
                        (
                            "The selected column is valid numerically, but its semantic role "
                            "may not fully match a cumulative/total execution concept. "
                            f"Selected: `{num}`. Related candidates: {', '.join(alts)}. "
                            "Review the column roles and regenerate the plan if appropriate."
                        ),
                    )
                )

    if den and role_den and den not in role_den and any(role_den):
        issues.append(
            ValidationIssue(
                "warning",
                "semantic_role_mismatch",
                (
                    f"Selected denominator `{den}` is executable but not in profile "
                    f"denominator_candidate roles. Related candidates: "
                    f"{', '.join(role_den[:4])}."
                ),
            )
        )

    if num and role_num and num not in role_num and num not in current_num and role_num:
        issues.append(
            ValidationIssue(
                "warning",
                "semantic_role_mismatch",
                (
                    f"Selected numerator `{num}` is executable but not in profile "
                    f"numerator_candidate roles. Related candidates: "
                    f"{', '.join(role_num[:4])}."
                ),
            )
        )

    # Schema-level ambiguity (profile roles가 비어 있어도 동작)
    if source_df is not None and selected:
        issues.extend(
            _ambiguous_sibling_column_warnings(
                selected,
                source_df=source_df,
                user_prompt=user_prompt,
            )
        )
    return issues


def _selected_metric_columns(plan: AnalysisPlan) -> list[str]:
    found: list[str] = []
    raw = plan.raw or {}
    for key in (
        "numerator",
        "denominator",
        "value_column",
        "x_column",
        "y_column",
        "left",
        "right",
    ):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            found.append(val)
    for step in plan.steps:
        payload = step.payload or {}
        if step.op == "aggregate":
            for metric in payload.get("metrics") or []:
                if isinstance(metric, dict):
                    col = str(metric.get("column") or "")
                    if col:
                        found.append(col)
                elif isinstance(metric, str):
                    found.append(metric)
        for key in ("column", "value_column", "numerator", "denominator", "left", "right"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                found.append(val)
    # unique preserve order
    out: list[str] = []
    seen: set[str] = set()
    for c in found:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _ambiguous_sibling_column_warnings(
    selected: list[str],
    *,
    source_df: pd.DataFrame,
    user_prompt: str | None,
) -> list[ValidationIssue]:
    """동일 어간을 공유하는 수치 컬럼이 여럿이면 soft warning."""
    from core.io.text_normalize import normalize_text

    issues: list[ValidationIssue] = []
    numeric_cols = [
        str(c)
        for c in source_df.columns
        if not str(c).startswith("_")
        and (
            pd.api.types.is_numeric_dtype(source_df[c])
            or pd.to_numeric(source_df[c], errors="coerce").notna().any()
        )
    ]
    prompt_norm = normalize_text(user_prompt or "")

    for col in selected:
        if col not in numeric_cols:
            continue
        stem = _metric_stem(col)
        if len(stem) < 2:
            continue
        siblings = [
            c
            for c in numeric_cols
            if c != col and stem in normalize_text(c)
        ]
        if len(siblings) < 1:
            continue
        # 요청이 특정 sibling을 명시하면 skip
        if any(normalize_text(s) in prompt_norm for s in siblings):
            continue
        if normalize_text(col) in prompt_norm and not any(
            normalize_text(s) in prompt_norm for s in siblings
        ):
            # 사용자가 선택 컬럼을 직접 말했으면 skip
            continue
        issues.append(
            ValidationIssue(
                "warning",
                "semantic_role_mismatch",
                (
                    f"Selected metric `{col}` has similarly named numeric alternatives "
                    f"in the schema: {', '.join(siblings[:4])}. "
                    "If the user request is ambiguous about which metric to use, "
                    "regenerate the plan with the best-matching column and mention "
                    "the choice in criteria_note. Do not invent columns."
                ),
            )
        )
    return issues


def _metric_stem(name: str) -> str:
    from core.io.text_normalize import normalize_text

    text = normalize_text(name)
    for prefix in (
        "당년도",
        "당해",
        "누적",
        "목표",
        "계획",
        "실행",
        "현재",
        "전년",
        "예상",
        "current",
        "ytd",
        "target",
        "plan",
        "actual",
    ):
        pref = normalize_text(prefix)
        if text.startswith(pref) and len(text) > len(pref):
            return text[len(pref) :]
    return text


def _check_derive_sample(
    result: pd.DataFrame,
    plan: AnalysisPlan,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for name, kind, operands in plan.derive_specs:
        if name not in result.columns:
            continue
        if kind not in {"diff", "abs_diff"}:
            continue
        if len(operands) != 2:
            continue
        left_c, right_c = operands
        if left_c not in result.columns or right_c not in result.columns:
            continue
        sample = result.head(min(5, len(result)))
        left = pd.to_numeric(sample[left_c], errors="coerce")
        right = pd.to_numeric(sample[right_c], errors="coerce")
        expected = (left - right).abs() if kind == "abs_diff" else (left - right)
        actual = pd.to_numeric(sample[name], errors="coerce")
        mismatch = ~((expected - actual).abs() < 1e-6) & expected.notna() & actual.notna()
        if bool(mismatch.any()):
            issues.append(
                ValidationIssue(
                    "error",
                    "derive_mismatch",
                    f"`{name}` ({kind}) 재계산이 결과와 일치하지 않습니다.",
                )
            )
    return issues
