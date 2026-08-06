"""실행 결과 검증 — 잘못된 통합 파일 조용히 저장 방지."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.integrate.plan_types import ExecutionPlan, ValidationIssue, ValidationReport
from core.io.text_normalize import normalize_text


def validate_integrate_result(
    *,
    plan: ExecutionPlan,
    source_details: dict[str, pd.DataFrame],
    integrated_details: pd.DataFrame,
    integrated: pd.DataFrame,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if not plan.group_keys:
        issues.append(
            ValidationIssue("error", "missing_group_keys", "group_keys가 비어 있습니다.")
        )

    missing_keys = [k for k in plan.group_keys if k not in integrated_details.columns]
    if missing_keys:
        issues.append(
            ValidationIssue(
                "error",
                "missing_key_columns",
                f"통합 상세에 키 열이 없습니다: {missing_keys}",
            )
        )

    additive = [c for c, f in plan.aggregations.items() if f == "sum"]
    for col in additive:
        if col not in integrated_details.columns:
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_additive_column",
                    f"합산 열이 통합 상세에 없습니다: {col}",
                )
            )
            continue
        if col in plan.group_keys:
            issues.append(
                ValidationIssue(
                    "error",
                    "key_marked_additive",
                    f"키 열을 합산 대상으로 지정했습니다: {col}",
                )
            )

    # 입력 상세 합 = 통합 상세 합
    for col in additive:
        if col not in integrated_details.columns:
            continue
        input_sum = 0.0
        for frame in source_details.values():
            if col in frame.columns:
                input_sum += float(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())
        out_sum = float(
            pd.to_numeric(integrated_details[col], errors="coerce").fillna(0).sum()
        )
        if abs(input_sum - out_sum) > 0.5:
            issues.append(
                ValidationIssue(
                    "error",
                    "detail_sum_mismatch",
                    f"{col}: 입력 상세 합({input_sum:,.0f}) ≠ 통합 상세 합({out_sum:,.0f})",
                )
            )

    # 중복 키
    key_cols = [k for k in plan.group_keys if k in integrated_details.columns]
    if key_cols:
        dup = integrated_details.duplicated(subset=key_cols, keep=False)
        if bool(dup.any()):
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_keys",
                    f"통합 상세에 중복 키가 {int(dup.sum())}행 있습니다.",
                )
            )

    # 라벨 충돌 경고
    label_cols = [
        c
        for c in integrated_details.columns
        if c not in plan.group_keys and c not in additive
    ]
    issues.extend(_label_conflict_warnings(source_details, plan, label_cols))

    # 소계 / 합계 일치
    issues.extend(_validate_subtotals(integrated, plan, additive))
    issues.extend(_validate_non_additive_not_summed(plan, integrated_details))

    errors = [item for item in issues if item.level == "error"]
    return ValidationReport(ok=not errors, issues=issues)


def _label_conflict_warnings(
    source_details: dict[str, pd.DataFrame],
    plan: ExecutionPlan,
    label_cols: list[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    key_cols = [k for k in plan.group_keys]
    if not key_cols:
        return issues

    # primary key = last identifier-like among keys
    group_col = plan.group_display_column
    primary_candidates = [k for k in key_cols if k != group_col] or key_cols
    primary = primary_candidates[0]

    by_key: dict[str, dict[str, set[str]]] = {}
    for source, frame in source_details.items():
        if primary not in frame.columns:
            continue
        for _, row in frame.iterrows():
            key = str(row[primary])
            bucket = by_key.setdefault(key, {})
            for col in label_cols:
                if col not in frame.columns:
                    continue
                value = row[col]
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    continue
                text = str(value).strip()
                if not text:
                    continue
                bucket.setdefault(col, set()).add(text)

    conflicts = 0
    for key, cols in by_key.items():
        for col, values in cols.items():
            if len(values) > 1:
                conflicts += 1
                if conflicts <= 5:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "label_conflict",
                            f"키 {key}의 {col} 값이 파일마다 다릅니다: {sorted(values)}",
                        )
                    )
    if conflicts > 5:
        issues.append(
            ValidationIssue(
                "warning",
                "label_conflict_more",
                f"추가 라벨 충돌 {conflicts - 5}건",
            )
        )
    return issues


def _validate_subtotals(
    integrated: pd.DataFrame,
    plan: ExecutionPlan,
    additive: list[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    group_col = plan.group_display_column
    if not group_col or group_col not in integrated.columns:
        return issues

    has_subtotal = any(spec.type == "subtotal" for spec in plan.derived_rows)
    if not has_subtotal and plan.derived_rows:
        return issues

    current_details: list[dict[str, Any]] = []
    for _, row in integrated.iterrows():
        label = row.get(group_col)
        text = "" if label is None or (isinstance(label, float) and pd.isna(label)) else str(label)
        norm = normalize_text(" ".join(text.split()))
        if "소계" in norm or "subtotal" in norm:
            for col in additive:
                if col not in integrated.columns:
                    continue
                expected = sum(
                    float(pd.to_numeric(d.get(col), errors="coerce") or 0)
                    for d in current_details
                )
                actual = float(pd.to_numeric(row.get(col), errors="coerce") or 0)
                if abs(expected - actual) > 0.5:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "subtotal_mismatch",
                            f"소계 불일치 {col}: expected {expected:,.0f} got {actual:,.0f}",
                        )
                    )
            current_details = []
            continue
        if any(token in norm for token in ("합계", "총계", "total")) and not any(
            tok in norm for tok in ("소계",)
        ):
            # footer — skip accumulating
            current_details = []
            continue
        # blank group label means continuation of details
        detail_marker = None
        for key in plan.group_keys:
            if key == group_col:
                continue
            if key in integrated.columns and not _is_empty(row.get(key)):
                detail_marker = key
                break
        if detail_marker:
            current_details.append({col: row.get(col) for col in additive})
        elif text and not _is_empty(text):
            # category header without code — ignore
            pass

    return issues


def _validate_non_additive_not_summed(
    plan: ExecutionPlan,
    integrated_details: pd.DataFrame,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for col, func in plan.aggregations.items():
        if func != "sum":
            continue
        name = normalize_text(col)
        suspicious = any(
            token in name
            for token in ("비율", "율", "percent", "pct", "일자", "날짜", "date", "memo", "비고")
        )
        if suspicious:
            issues.append(
                ValidationIssue(
                    "warning",
                    "suspicious_additive",
                    f"합산하기 애매한 열을 sum으로 지정했습니다: {col}",
                )
            )
        if col in integrated_details.columns and not pd.api.types.is_numeric_dtype(
            integrated_details[col]
        ):
            coerced = pd.to_numeric(integrated_details[col], errors="coerce")
            if float(coerced.isna().mean()) > 0.5:
                issues.append(
                    ValidationIssue(
                        "error",
                        "non_numeric_additive",
                        f"숫자로 합산할 수 없는 열입니다: {col}",
                    )
                )
    return issues


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip() == ""
