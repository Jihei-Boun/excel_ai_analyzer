"""분석 계획 실행 결과 가드레일."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.analysis.analysis_plan_types import AnalysisPlan
from core.integrate.plan_types import ValidationIssue, ValidationReport
from core.schema.row_classify import ROW_TYPE_COL, classify_rows, infer_dimension_columns
from core.summary.summary_utils import cell_text


def validate_analysis_result(
    result: pd.DataFrame,
    plan: AnalysisPlan,
    *,
    source_df: pd.DataFrame | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if result is None:
        return ValidationReport(
            ok=False,
            issues=[ValidationIssue("error", "empty_result", "결과가 없습니다.")],
        )

    # limit
    limit_n = plan.limit_n
    if limit_n is not None:
        if len(result) > limit_n:
            issues.append(
                ValidationIssue(
                    "error",
                    "limit_exceeded",
                    f"요청 상위 {limit_n}개를 초과한 {len(result)}행입니다.",
                )
            )

    # output columns
    for col in plan.output_columns:
        if col not in result.columns:
            # rename 되었을 수 있음 — soft warning
            issues.append(
                ValidationIssue(
                    "warning",
                    "missing_output_column",
                    f"출력 컬럼 `{col}`이 결과에 없습니다.",
                )
            )

    # dimension emptiness
    dims = [c for c in plan.dimension_columns if c in result.columns]
    if not dims:
        dims = [
            c
            for c in infer_dimension_columns(result)
            if c in result.columns
        ][:1]
    if dims and not result.empty:
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

    # summary rows mixed in when detail-only filter was requested
    # (집계·비교 결과 표는 그룹 요약이므로 이 검사를 건너뛴다)
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

    # sort order
    sort_spec = plan.sort_spec
    if sort_spec and not result.empty:
        by, ascending = sort_spec
        by = [c for c in by if c in result.columns]
        if by:
            expected = result.sort_values(by, ascending=ascending[: len(by)], kind="mergesort")
            if not result[by].reset_index(drop=True).equals(
                expected[by].reset_index(drop=True)
            ):
                issues.append(
                    ValidationIssue(
                        "error",
                        "sort_mismatch",
                        f"정렬 기준 {by}이 계획과 일치하지 않습니다.",
                    )
                )

    # derive recomputation sample
    if source_df is not None and not result.empty:
        issues.extend(_check_derive_sample(result, plan, source_df))

    errors = [i for i in issues if i.level == "error"]
    return ValidationReport(ok=not errors, issues=issues)


def _check_derive_sample(
    result: pd.DataFrame,
    plan: AnalysisPlan,
    source_df: pd.DataFrame,
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
        # NaN 허용 비교
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


def validation_error_messages(report: ValidationReport) -> list[str]:
    return [f"{item.code}: {item.message}" for item in report.errors]
