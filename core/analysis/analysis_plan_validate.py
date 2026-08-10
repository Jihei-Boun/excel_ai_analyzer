"""AnalysisPlan 실행 전(plan-time) 검증.

원칙:
- 잘못된 plan을 Python이 고쳐쓰지 않는다.
- 오류·후보(hint)만 Planner feedback로 넘긴다.
- 의미를 바꾸는 자동 보정은 하지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from core.analysis.analysis_plan_types import (
    META_COLUMNS,
    AnalysisPlan,
    AnalysisStep,
)
from core.integrate.plan_types import ValidationIssue, ValidationReport
from core.schema.row_classify import ROW_TYPE_COL

_NUMERIC_AGGS = frozenset({"sum", "mean", "median", "avg", "min", "max", "std", "var"})
_SUPPORTED_FILTER_OPS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "==", "!=", ">", ">=", "<", "<=", "between"}
)
_MAX_LIMIT_N = 10_000
_AGG_LIKE_OPS = frozenset(
    {
        "aggregate",
        "ratio_of_aggregates",
        "compare_groups",
        "distribution_summary",
        "top_per_group",
        "filter_vs_mean",
    }
)


def validate_analysis_plan(
    plan: AnalysisPlan,
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
) -> ValidationReport:
    """실행 전 AnalysisPlan이 스키마·의존성에 맞는지 검사한다."""
    issues: list[ValidationIssue] = []
    if plan is None or not plan.steps:
        return ValidationReport(
            ok=False,
            issues=[ValidationIssue("error", "empty_plan", "실행 가능한 분석 step이 없습니다.")],
        )
    if df is None or df.empty:
        return ValidationReport(
            ok=False,
            issues=[ValidationIssue("error", "empty_source", "분석 대상 DataFrame이 비어 있습니다.")],
        )

    available = {str(c) for c in df.columns}
    known = set(available)
    known.update(META_COLUMNS)

    # 고수준 raw 필드도 함께 검사 (sanitize가 일부만 남긴 경우 대비)
    issues.extend(_validate_raw_high_level(plan, df, available, profile_name=profile_name))

    row_types_present = _row_types_present(df)
    saw_annotate = False
    include_types_active: set[str] | None = None

    for index, step in enumerate(plan.steps):
        op = step.op
        payload = step.payload or {}

        if op == "annotate_row_types":
            saw_annotate = True
            continue

        if op == "filter_rows":
            issues.extend(_validate_filter_rows(step, df, known, available))
            include = payload.get("include_row_types") or []
            if include:
                include_types_active = {str(x) for x in include}
            continue

        if op == "select_columns":
            issues.extend(_validate_select_columns(step, known))
            cols = [str(c) for c in (payload.get("columns") or []) if str(c)]
            if cols:
                # select 이후 사용 가능 컬럼 축소 (+ 파생/메타는 유지)
                known = {c for c in known if c in cols or c in META_COLUMNS or c.startswith("_")}
                for c in cols:
                    known.add(c)
            renames = payload.get("renames") or {}
            if isinstance(renames, dict):
                for src, dst in renames.items():
                    if str(src) in known:
                        known.add(str(dst))
            continue

        if op == "drop_columns":
            for col in payload.get("columns") or []:
                known.discard(str(col))
            continue

        if op == "derive_column":
            issues.extend(_validate_derive(step, known, df))
            name = str(payload.get("name") or "").strip()
            if name:
                known.add(name)
            continue

        if op == "aggregate":
            issues.extend(
                _validate_aggregate(
                    step,
                    known,
                    df,
                    available,
                    saw_annotate=saw_annotate,
                    include_types=include_types_active,
                    row_types_present=row_types_present,
                )
            )
            # aggregate 결과 컬럼 추정
            for metric in payload.get("metrics") or []:
                col = _metric_column(metric)
                if col:
                    known.add(col)
            for g in payload.get("group_by") or []:
                known.add(str(g))
            continue

        if op == "ratio_of_aggregates":
            issues.extend(
                _validate_ratio(
                    step,
                    known,
                    df,
                    available,
                    profile_name=profile_name,
                )
            )
            name = str(payload.get("name") or "").strip()
            if name:
                known.add(name)
            continue

        if op == "compare_groups":
            issues.extend(
                _validate_compare_groups(
                    step,
                    known,
                    df,
                    available,
                    saw_annotate=saw_annotate,
                    include_types=include_types_active,
                    row_types_present=row_types_present,
                )
            )
            continue

        if op == "correlation":
            issues.extend(_validate_correlation(step, known, df))
            continue

        if op == "distribution_summary":
            issues.extend(_validate_distribution(step, known, df, available))
            continue

        if op == "filter_vs_mean":
            issues.extend(_validate_filter_vs_mean(step, known, df))
            continue

        if op == "top_per_group":
            issues.extend(
                _validate_top_per_group(
                    step,
                    known,
                    df,
                    saw_annotate=saw_annotate,
                    include_types=include_types_active,
                    row_types_present=row_types_present,
                )
            )
            continue

        if op == "sort":
            issues.extend(_validate_sort(step, known, index, plan.steps))
            continue

        if op == "limit":
            issues.extend(_validate_limit(step))
            continue

    # 집계류인데 detail+subtotal 동시 선택
    issues.extend(
        _validate_row_mix_for_plan(
            plan,
            df,
            saw_annotate=saw_annotate,
            include_types=include_types_active,
            row_types_present=row_types_present,
        )
    )
    # Phase 9: operation composition / dependency rules
    issues.extend(_validate_plan_composition(plan, known_final=known, available=available))

    errors = [i for i in issues if i.level == "error"]
    return ValidationReport(ok=not errors, issues=issues)


def _validate_plan_composition(
    plan: AnalysisPlan,
    *,
    known_final: set[str],
    available: set[str],
) -> list[ValidationIssue]:
    """원자 op 조합·의존성 오류. 정답 plan을 만들지 않고 feedback만 낸다."""
    del known_final
    issues: list[ValidationIssue] = []
    ops = [s.op for s in plan.steps]
    has = set(ops)

    # track produced columns in order
    produced: set[str] = set(available)
    produced.update(META_COLUMNS)
    ratio_names: list[str] = []
    saw_aggregate = False
    saw_ratio = False
    saw_top_per_group = False
    saw_limit = False
    saw_sort = False
    saw_compare = False

    for step in plan.steps:
        op = step.op
        payload = step.payload or {}
        if op == "aggregate":
            saw_aggregate = True
            for metric in payload.get("metrics") or []:
                col = _metric_column(metric)
                if col:
                    produced.add(col)
            for g in payload.get("group_by") or []:
                produced.add(str(g))
        elif op == "ratio_of_aggregates":
            saw_ratio = True
            name = str(payload.get("name") or "").strip()
            if not name:
                issues.append(
                    ValidationIssue(
                        "error",
                        "missing_ratio_name",
                        (
                            "ratio_of_aggregates requires an explicit `name` output. "
                            "Later sort/compare must reference that name."
                        ),
                    )
                )
            else:
                ratio_names.append(name)
                produced.add(name)
        elif op == "derive_column":
            name = str(payload.get("name") or "").strip()
            if name:
                produced.add(name)
        elif op == "top_per_group":
            saw_top_per_group = True
        elif op == "limit":
            saw_limit = True
        elif op == "sort":
            saw_sort = True
            for col in payload.get("by") or []:
                name = str(col)
                if name not in produced:
                    if _looks_like_rate_name(name):
                        issues.append(
                            ValidationIssue(
                                "error",
                                "missing_ratio_before_sort",
                                (
                                    f"Sorting by rate-like column `{name}` requires a prior "
                                    "ratio_of_aggregates (or derive) that creates it. "
                                    "For rate ranking use: aggregate → ratio_of_aggregates → sort → limit."
                                ),
                            )
                        )
                    else:
                        issues.append(
                            ValidationIssue(
                                "error",
                                "missing_metric_before_sort",
                                (
                                    f"The plan sorts by `{name}`, but no previous step creates `{name}`. "
                                    "Aggregate/derive/ratio first, or sort an existing column."
                                ),
                            )
                        )
        elif op == "compare_groups":
            saw_compare = True
            for metric in payload.get("metrics") or []:
                col = str(metric)
                if not col:
                    continue
                if col not in produced:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "compare_before_metric",
                            (
                                f"compare_groups metric `{col}` is not available yet. "
                                "Create it with aggregate and/or ratio_of_aggregates before compare_groups."
                            ),
                        )
                    )
            for rate in payload.get("rate_columns") or []:
                col = str(rate)
                if col and col not in produced:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "compare_before_metric",
                            (
                                f"compare_groups rate_column `{col}` is not available yet. "
                                "Add ratio_of_aggregates with name=`{col}` before compare_groups."
                            ),
                        )
                    )

    # top_per_group misuse for global ranking
    if saw_top_per_group and saw_limit:
        issues.append(
            ValidationIssue(
                "error",
                "misused_top_per_group",
                (
                    "top_per_group requires a group column and is only appropriate for "
                    "ranking within each group. For a single global ranking, use a metric "
                    "followed by sort and limit — do not combine top_per_group with limit."
                ),
            )
        )

    # Intent signals from planner criteria / high-level fields (not auto-filled note alone)
    note = str(plan.criteria_note or "")
    raw = plan.raw or {}
    op_field = str(raw.get("operation") or "")
    rate_name_field = str(raw.get("rate_name") or "")
    intent_blob = " ".join(
        [
            note,
            str(raw.get("criteria_note") or ""),
            rate_name_field,
            op_field if _looks_like_rate_request(op_field) else "",
        ]
    )
    rate_outputs = {c for c in produced if _looks_like_rate_name(c)}
    if (
        (_looks_like_rate_request(intent_blob) or bool(rate_name_field.strip()))
        and not saw_ratio
        and not rate_outputs
    ):
        # allow if a rate-named column already exists in the source inventory
        if not any(_looks_like_rate_name(c) for c in available):
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_ratio_composition",
                    (
                        "The request appears to need a rate/ratio, but the plan has no "
                        "ratio_of_aggregates (or derive ratio) step. Typical composition: "
                        "aggregate → ratio_of_aggregates(name=...) → (sort → limit | compare_groups)."
                    ),
                )
            )

    # ranking incomplete: sort without limit when criteria suggests top-N
    if (
        saw_sort
        and not saw_limit
        and not saw_top_per_group
        and _looks_like_topn_request(intent_blob)
    ):
        issues.append(
            ValidationIssue(
                "error",
                "global_ranking_missing_limit",
                (
                    "Global ranking needs sort → limit. "
                    "A sort without limit does not produce a top-N result."
                ),
            )
        )

    # top_per_group used when request looks like a single global ranking
    if (
        saw_top_per_group
        and not saw_compare
        and _looks_like_topn_request(intent_blob)
        and not _looks_like_groupwise_ranking(intent_blob)
    ):
        issues.append(
            ValidationIssue(
                "error",
                "misused_top_per_group",
                (
                    "top_per_group is only appropriate for ranking within each group. "
                    "For a single global ranking, use a metric followed by sort and limit."
                ),
            )
        )

    # filter_vs_mean alone used for max ranking
    if (
        "filter_vs_mean" in has
        and saw_sort
        and not saw_limit
        and not saw_aggregate
        and _looks_like_extremum_request(intent_blob)
    ):
        issues.append(
            ValidationIssue(
                "error",
                "global_ranking_misclassified",
                (
                    "Finding the largest/smallest value is a global ranking: "
                    "sort → limit. Do not use filter_vs_mean to find a max/min."
                ),
            )
        )

    del saw_aggregate
    return issues


def _looks_like_groupwise_ranking(blob: str) -> bool:
    text = str(blob or "").lower()
    if "별" in text or "각각" in text or "마다" in text:
        return True
    return any(
        tok in text
        for tok in (
            "each ",
            " per ",
            "within each",
            "group-wise",
            "groupwise",
        )
    )


def _looks_like_rate_name(name: str) -> bool:
    text = str(name or "").lower()
    return any(
        tok in text
        for tok in ("률", "비율", "rate", "ratio", "attainment", "execution")
    )


def _looks_like_rate_request(blob: str) -> bool:
    text = str(blob or "").lower()
    return any(
        tok in text
        for tok in (
            "률",
            "비율",
            "대비",
            "rate",
            "ratio",
            "목표 대비",
            "예산 대비",
            "실적률",
            "집행률",
        )
    )


def _looks_like_topn_request(blob: str) -> bool:
    text = str(blob or "").lower()
    if any(tok in text for tok in ("상위", "하위", "top", "bottom")):
        return True
    if re.search(r"\b\d+\s*(개|명|건|items?)\b", text):
        return True
    return False


def _looks_like_extremum_request(blob: str) -> bool:
    text = str(blob or "").lower()
    return any(
        tok in text
        for tok in ("가장 큰", "가장 높은", "가장 작은", "가장 낮은", "max", "min", "largest", "highest")
    )


def format_plan_validation_feedback(
    report: ValidationReport,
    *,
    previous_plan: dict[str, Any] | None = None,
    df: pd.DataFrame | None = None,
    profile_name: str | None = None,
    attempt: int | None = None,
    failure_stage: str = "plan_validation",
) -> list[str]:
    """Planner 재시도용 feedback 문자열. 답을 강제하지 않고 후보만 제시."""
    lines: list[str] = []
    if attempt is not None:
        lines.append(f"Attempt: {attempt}")
    lines.append(f"Failure stage: {failure_stage}")
    if failure_stage == "plan_validation":
        lines.append("The plan cannot be executed because:")
    else:
        lines.append("The plan executed, but the produced result is invalid because:")

    if previous_plan:
        import json

        try:
            compact = json.dumps(previous_plan, ensure_ascii=False, default=str)
            if len(compact) > 1200:
                compact = compact[:1200] + "…"
            lines.append(f"Previous plan: {compact}")
        except Exception:  # noqa: BLE001
            lines.append("Previous plan: (unserializable)")

    lines.append("Validation errors:")
    for i, issue in enumerate(report.issues, start=1):
        if issue.level != "error" and issue.level != "warning":
            continue
        prefix = "ERROR" if issue.level == "error" else "WARNING"
        lines.append(f"{i}. [{prefix}/{issue.code}] {issue.message}")

    # Composition-specific guidance (hints only — do not force a full answer plan)
    codes = {i.code for i in report.errors}
    if codes & {
        "misused_top_per_group",
        "missing_group_column",
        "global_ranking_missing_limit",
        "global_ranking_misclassified",
    }:
        lines.append(
            "Composition hint: global ranking = metric → sort → limit; "
            "group-wise ranking = metric → top_per_group(group, value, n)."
        )
    if codes & {
        "missing_ratio_composition",
        "missing_ratio_before_sort",
        "missing_ratio_name",
        "missing_numerator",
        "missing_denominator",
    }:
        lines.append(
            "Composition hint: rate/ratio needs ratio_of_aggregates with an explicit "
            "`name`, then sort/compare may reference that same name."
        )
    if codes & {"missing_metric_before_sort", "compare_before_metric"}:
        lines.append(
            "Composition hint: create the metric (aggregate and/or ratio_of_aggregates) "
            "before sort or compare_groups."
        )

    # 컬럼 후보 힌트 (강제 지시 금지)
    if df is not None and not df.empty:
        candidates = suggest_column_candidates(df, profile_name=profile_name)
        if candidates:
            lines.append(
                "Available column candidates (hints only — choose based on the user request):"
            )
            for role, cols in candidates.items():
                if cols:
                    lines.append(f"- {role}: {', '.join(cols[:6])}")

    lines.append("Generate a corrected AnalysisPlan. Do not invent columns.")
    return lines


def suggest_column_candidates(
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
) -> dict[str, list[str]]:
    """프로필 role + 실제 존재하는 컬럼만 후보로 반환 (강제 아님)."""
    from core.profile_loader import roles_for

    roles = roles_for(profile_name=profile_name)
    present = {str(c) for c in df.columns}
    out: dict[str, list[str]] = {}
    mapping = {
        "group_like": "group_columns",
        "label_like": "label_columns",
        "numerator_like": "metric_numerator",
        "denominator_like": "metric_denominator",
        "remaining_like": "metric_remaining",
    }
    for label, key in mapping.items():
        cols = [c for c in (roles.get(key) or ()) if c in present]
        if cols:
            out[label] = cols
    # 숫자형 일반 후보
    numeric = [
        str(c)
        for c in df.columns
        if _is_numeric_compatible(df[c]) and str(c) not in META_COLUMNS
    ]
    if numeric:
        out.setdefault("numeric_columns", numeric[:12])
    return out


def validation_error_messages(report: ValidationReport) -> list[str]:
    return [f"{item.code}: {item.message}" for item in report.errors]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row_types_present(df: pd.DataFrame) -> set[str]:
    if ROW_TYPE_COL not in df.columns:
        return set()
    return {str(x) for x in df[ROW_TYPE_COL].dropna().unique().tolist()}


def _is_numeric_compatible(series: pd.Series) -> bool:
    if pd.api.types.is_numeric_dtype(series):
        return True
    coerced = pd.to_numeric(series, errors="coerce")
    non_null = series.dropna()
    if non_null.empty:
        return False
    return float(coerced.notna().sum()) / max(len(non_null), 1) >= 0.5


def _column_exists(name: str, known: set[str], available: set[str]) -> bool:
    return name in known or name in available or name in META_COLUMNS


def _closest_candidates(name: str, columns: set[str], *, limit: int = 5) -> list[str]:
    if not name:
        return []
    target = re.sub(r"\s+", "", name).lower()
    scored: list[tuple[int, str]] = []
    for col in columns:
        compact = re.sub(r"\s+", "", col).lower()
        score = 0
        if target == compact:
            score = 100
        elif target in compact or compact in target:
            score = 80
        else:
            # 공통 접두
            common = 0
            for a, b in zip(target, compact):
                if a != b:
                    break
                common += 1
            score = common * 5
        if score > 0:
            scored.append((score, col))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scored[:limit]]


def _missing_column_issue(code: str, name: str, available: set[str]) -> ValidationIssue:
    cands = _closest_candidates(name, available)
    msg = f"Column `{name}` does not exist."
    if cands:
        msg += f" Available candidates: {', '.join(cands)}."
    return ValidationIssue("error", code, msg)


def _metric_column(metric: Any) -> str | None:
    if isinstance(metric, str):
        return metric
    if isinstance(metric, dict):
        return str(metric.get("column") or metric.get("name") or "") or None
    return None


def _metric_fn(metric: Any) -> str:
    if isinstance(metric, dict):
        raw = metric.get("fn") if "fn" in metric else metric.get("agg")
        if raw is None or str(raw).strip() == "":
            return ""
        return str(raw).lower().strip()
    return ""



def _validate_raw_high_level(
    plan: AnalysisPlan,
    df: pd.DataFrame,
    available: set[str],
    *,
    profile_name: str | None,
) -> list[ValidationIssue]:
    del profile_name
    issues: list[ValidationIssue] = []
    raw = plan.raw or {}
    if not isinstance(raw, dict):
        return issues

    for key in ("group_column", "group_by"):
        val = raw.get(key)
        if isinstance(val, str) and val and val not in available:
            issues.append(_missing_column_issue("missing_group_column", val, available))
        elif isinstance(val, list):
            for item in val:
                if str(item) and str(item) not in available:
                    issues.append(
                        _missing_column_issue("missing_group_column", str(item), available)
                    )

    for key in ("numerator", "denominator", "x_column", "y_column", "value_column", "left", "right"):
        val = raw.get(key)
        if isinstance(val, str) and val and val not in available:
            # 파생 이름(비율 등)일 수 있어 고수준만 약한 검사 — 존재하지 않으면 error
            # rate_name은 제외
            issues.append(_missing_column_issue(f"missing_{key}", val, available))

    groups = raw.get("groups") or raw.get("include_groups")
    group_col = str(raw.get("group_column") or "")
    if groups and group_col and group_col in available:
        issues.extend(_validate_group_values(df, group_col, groups, required_all=True))

    return issues


def _validate_group_values(
    df: pd.DataFrame,
    group_column: str,
    groups: Any,
    *,
    required_all: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if isinstance(groups, str):
        groups = [groups]
    if not isinstance(groups, list) or not groups:
        return issues
    wanted = [str(g).strip() for g in groups if str(g).strip()]
    if not wanted:
        return issues

    series = df[group_column]
    present_text = {str(v).strip() for v in series.dropna().unique().tolist()}
    # 부분 일치도 허용 (라벨이 길 때)
    missing: list[str] = []
    for g in wanted:
        if g in present_text:
            continue
        if any(g in p or p in g for p in present_text if p):
            continue
        missing.append(g)

    if not missing:
        return issues

    sample = sorted(present_text)[:8]
    if required_all or len(missing) == len(wanted):
        issues.append(
            ValidationIssue(
                "error",
                "missing_group_value",
                (
                    f"Requested group value(s) {missing} not found in `{group_column}`. "
                    f"Sample values: {sample}."
                ),
            )
        )
    else:
        issues.append(
            ValidationIssue(
                "warning",
                "partial_group_value",
                (
                    f"Some requested groups {missing} are missing in `{group_column}`. "
                    f"Sample values: {sample}."
                ),
            )
        )
    return issues


def _validate_filter_rows(
    step: AnalysisStep,
    df: pd.DataFrame,
    known: set[str],
    available: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    payload = step.payload or {}

    for filt in payload.get("column_filters") or []:
        if not isinstance(filt, dict):
            continue
        col = str(filt.get("column") or "")
        if not col:
            continue
        if not _column_exists(col, known, available):
            issues.append(_missing_column_issue("missing_filter_column", col, available))
            continue
        values = filt.get("values")
        if values is None:
            continue
        if isinstance(values, (str, int, float)):
            values = [values]
        if not isinstance(values, list) or not values:
            continue
        series = df[col] if col in df.columns else None
        if series is None:
            continue
        present = {str(v).strip() for v in series.dropna().unique().tolist()}
        missing_vals = []
        for v in values:
            text = str(v).strip()
            if text in present:
                continue
            if any(text in p or p in text for p in present if p):
                continue
            missing_vals.append(text)
        if missing_vals and len(missing_vals) == len(values):
            issues.append(
                ValidationIssue(
                    "error",
                    "filter_no_matching_value",
                    (
                        f"Filter on `{col}` uses value(s) {missing_vals} that do not appear "
                        f"in the data. This filter would yield 0 rows. "
                        f"Sample values: {sorted(present)[:8]}."
                    ),
                )
            )
        elif missing_vals:
            issues.append(
                ValidationIssue(
                    "warning",
                    "filter_partial_value",
                    f"Filter on `{col}` includes unknown value(s) {missing_vals}.",
                )
            )

    for filt in payload.get("numeric_filters") or []:
        if not isinstance(filt, dict):
            continue
        col = str(
            filt.get("left_column")
            or filt.get("column")
            or filt.get("left")
            or ""
        )
        right_col = str(
            filt.get("right_column")
            or filt.get("other_column")
            or filt.get("right")
            or ""
        )
        op = str(filt.get("op") or filt.get("operator") or "").lower()
        op = {
            "==": "eq",
            "!=": "ne",
            ">": "gt",
            ">=": "gte",
            "<": "lt",
            "<=": "lte",
        }.get(op, op)
        if col and not _column_exists(col, known, available):
            issues.append(_missing_column_issue("missing_numeric_filter_column", col, available))
            continue
        if right_col and not _column_exists(right_col, known, available):
            issues.append(
                _missing_column_issue("missing_numeric_filter_column", right_col, available)
            )
            continue
        if op and op not in _SUPPORTED_FILTER_OPS:
            issues.append(
                ValidationIssue(
                    "error",
                    "unsupported_filter_op",
                    f"Unsupported filter operator `{op}` on `{col}`.",
                )
            )
            continue
        if col and col in df.columns and not _is_numeric_compatible(df[col]):
            issues.append(
                ValidationIssue(
                    "error",
                    "non_numeric_filter_column",
                    f"Numeric filter on non-numeric column `{col}`.",
                )
            )
        if right_col and right_col in df.columns and not _is_numeric_compatible(df[right_col]):
            issues.append(
                ValidationIssue(
                    "error",
                    "non_numeric_filter_column",
                    f"Numeric filter on non-numeric column `{right_col}`.",
                )
            )
        if right_col:
            # column-vs-column: scalar value not required
            continue
        value = filt.get("value", filt.get("values"))
        if op == "between":
            low = filt.get("min", filt.get("low"))
            high = filt.get("max", filt.get("high"))
            if value is not None and isinstance(value, (list, tuple)) and len(value) == 2:
                low, high = value[0], value[1]
            try:
                if low is not None and high is not None and float(low) > float(high):
                    issues.append(
                        ValidationIssue(
                            "error",
                            "invalid_between_range",
                            f"Invalid between range on `{col}`: min ({low}) > max ({high}).",
                        )
                    )
            except (TypeError, ValueError):
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_between_value",
                        f"Between filter on `{col}` has non-numeric bounds.",
                    )
                )
        elif value is not None and not isinstance(value, (int, float, list)):
            # 문자열 숫자가 아닌 경우
            try:
                float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                issues.append(
                    ValidationIssue(
                        "error",
                        "non_numeric_filter_value",
                        f"Numeric comparison on `{col}` uses non-numeric value `{value}`.",
                    )
                )

    return issues


def _validate_select_columns(step: AnalysisStep, known: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    cols = step.payload.get("columns") or []
    missing = [str(c) for c in cols if str(c) and str(c) not in known]
    for name in missing:
        issues.append(
            ValidationIssue(
                "error",
                "missing_select_column",
                f"select_columns references unknown column `{name}`.",
            )
        )
    return issues


def _validate_derive(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    expr = step.payload.get("expr") or {}
    if not isinstance(expr, dict) or not expr:
        issues.append(
            ValidationIssue("error", "invalid_derive_expr", "derive_column has empty expr.")
        )
        return issues
    kind = str(next(iter(expr.keys())))
    operands = expr.get(kind) or []
    if isinstance(operands, str):
        operands = [operands]
    for opnd in operands:
        name = str(opnd)
        if not name:
            continue
        if name not in known:
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_derive_dependency",
                    f"derive_column `{step.payload.get('name')}` depends on missing column `{name}`.",
                )
            )
        elif name in df.columns and kind in {"ratio", "percent_ratio", "diff", "abs_diff"}:
            if not _is_numeric_compatible(df[name]):
                issues.append(
                    ValidationIssue(
                        "error",
                        "non_numeric_derive_operand",
                        f"derive `{kind}` operand `{name}` is not numeric-compatible.",
                    )
                )
    return issues


def _validate_aggregate(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
    available: set[str],
    *,
    saw_annotate: bool,
    include_types: set[str] | None,
    row_types_present: set[str],
) -> list[ValidationIssue]:
    del saw_annotate, include_types, row_types_present
    from core.analysis.ops_filters import AGGREGATE_FNS

    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    for g in payload.get("group_by") or []:
        if str(g) not in known and str(g) not in available:
            issues.append(_missing_column_issue("missing_group_by", str(g), available))
    metrics = payload.get("metrics") or []
    if not metrics:
        issues.append(
            ValidationIssue("error", "aggregate_no_metrics", "aggregate has no metrics.")
        )
    for metric in metrics:
        col = _metric_column(metric)
        fn_raw = _metric_fn(metric)
        if not fn_raw:
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_aggregation_fn",
                    f"aggregate metric `{col or '?'}` missing required fn "
                    f"(sum|mean|median|min|max|count). Do not default to sum.",
                )
            )
            continue
        fn_aliases = {"avg": "mean", "average": "mean", "med": "median", "n": "count"}
        fn = fn_aliases.get(fn_raw, fn_raw)
        if fn not in AGGREGATE_FNS:
            issues.append(
                ValidationIssue(
                    "error",
                    "unsupported_aggregation",
                    f"Unsupported aggregation `{fn_raw}`. "
                    f"Allowed: {', '.join(sorted(AGGREGATE_FNS))}.",
                )
            )
            continue
        if not col:
            continue
        if col not in known and col not in available:
            issues.append(_missing_column_issue("missing_metric_column", col, available))
            continue
        if fn in _NUMERIC_AGGS and col in df.columns and not _is_numeric_compatible(df[col]):
            issues.append(
                ValidationIssue(
                    "error",
                    "non_numeric_aggregate",
                    f"Cannot apply `{fn}` to non-numeric column `{col}`.",
                )
            )
    groups = payload.get("include_groups") or []
    group_by = payload.get("group_by") or []
    if groups and group_by:
        issues.extend(
            _validate_group_values(df, str(group_by[0]), groups, required_all=True)
        )
    return issues


def _validate_ratio(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
    available: set[str],
    *,
    profile_name: str | None,
) -> list[ValidationIssue]:
    del profile_name
    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    num = str(payload.get("numerator") or "")
    den = str(payload.get("denominator") or "")
    if not num:
        issues.append(
            ValidationIssue("error", "missing_numerator", "ratio_of_aggregates missing numerator.")
        )
    elif num not in known and num not in available:
        issues.append(_missing_column_issue("missing_numerator", num, available))
    elif num in df.columns and not _is_numeric_compatible(df[num]):
        issues.append(
            ValidationIssue(
                "error",
                "non_numeric_numerator",
                f"numerator `{num}` is not numeric-compatible.",
            )
        )

    if not den:
        issues.append(
            ValidationIssue(
                "error", "missing_denominator", "ratio_of_aggregates missing denominator."
            )
        )
    elif den not in known and den not in available:
        issues.append(_missing_column_issue("missing_denominator", den, available))
    elif den in df.columns and not _is_numeric_compatible(df[den]):
        issues.append(
            ValidationIssue(
                "error",
                "non_numeric_denominator",
                f"denominator `{den}` is not numeric-compatible.",
            )
        )

    if num and den and num == den:
        issues.append(
            ValidationIssue(
                "warning",
                "numerator_equals_denominator",
                (
                    f"numerator and denominator are the same column `{num}`. "
                    "Confirm this matches the user request."
                ),
            )
        )

    if den and den in df.columns and _is_numeric_compatible(df[den]):
        series = pd.to_numeric(df[den], errors="coerce")
        if series.notna().any() and float(series.fillna(0).abs().sum()) == 0.0:
            issues.append(
                ValidationIssue(
                    "error",
                    "denominator_all_zero",
                    f"denominator `{den}` is entirely zero (or null→0); ratio is undefined.",
                )
            )

    name = str(payload.get("name") or "").strip()
    if not name:
        issues.append(
            ValidationIssue(
                "error",
                "missing_ratio_name",
                (
                    "ratio_of_aggregates requires an explicit `name` so later "
                    "sort/compare can reference the rate column."
                ),
            )
        )

    return issues


def _validate_compare_groups(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
    available: set[str],
    *,
    saw_annotate: bool,
    include_types: set[str] | None,
    row_types_present: set[str],
) -> list[ValidationIssue]:
    del saw_annotate, include_types, row_types_present
    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    group_col = str(payload.get("group_column") or "")
    if not group_col:
        issues.append(
            ValidationIssue("error", "missing_group_column", "compare_groups needs group_column.")
        )
    elif group_col not in known and group_col not in available:
        issues.append(_missing_column_issue("missing_group_column", group_col, available))

    groups = payload.get("groups") or []
    if group_col and groups and group_col in df.columns:
        # 사용자가 명시한 그룹이 plan에 있으면 전부 모두 존재해야 함
        issues.extend(_validate_group_values(df, group_col, groups, required_all=True))
    elif not groups:
        issues.append(
            ValidationIssue(
                "warning",
                "compare_groups_empty",
                "compare_groups has no explicit groups list.",
            )
        )

    for metric in payload.get("metrics") or []:
        col = str(metric)
        if col and col not in known and col not in available:
            issues.append(
                ValidationIssue(
                    "error",
                    "compare_before_metric",
                    (
                        f"compare_groups metric `{col}` is not yet available. "
                        "Create it with aggregate and/or ratio_of_aggregates before compare_groups."
                    ),
                )
            )
    for rate in payload.get("rate_columns") or []:
        col = str(rate)
        if col and col not in known and col not in available:
            issues.append(
                ValidationIssue(
                    "error",
                    "compare_before_metric",
                    (
                        f"compare_groups rate_column `{col}` is not yet available. "
                        "Add ratio_of_aggregates with an explicit name before compare_groups."
                    ),
                )
            )
    return issues


def _validate_correlation(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    for key in ("x_column", "y_column"):
        col = str(payload.get(key) or "")
        if not col:
            issues.append(
                ValidationIssue("error", f"missing_{key}", f"correlation missing `{key}`.")
            )
            continue
        if col not in known:
            issues.append(
                ValidationIssue(
                    "error",
                    f"missing_{key}",
                    f"correlation `{key}` `{col}` does not exist.",
                )
            )
            continue
        if col in df.columns and not _is_numeric_compatible(df[col]):
            issues.append(
                ValidationIssue(
                    "error",
                    "non_numeric_correlation",
                    f"correlation column `{col}` is not numeric-compatible.",
                )
            )
    return issues


def _validate_distribution(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
    available: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    for key in ("numerator_column", "denominator_column"):
        col = str(payload.get(key) or "")
        if not col:
            issues.append(
                ValidationIssue("error", f"missing_{key}", f"distribution_summary missing {key}.")
            )
        elif col not in known and col not in available:
            issues.append(_missing_column_issue(f"missing_{key}", col, available))
        elif col in df.columns and not _is_numeric_compatible(df[col]):
            issues.append(
                ValidationIssue(
                    "error",
                    "non_numeric_distribution",
                    f"distribution_summary `{key}` `{col}` is not numeric-compatible.",
                )
            )
    return issues


def _validate_filter_vs_mean(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    col = str(step.payload.get("column") or "")
    if not col:
        issues.append(
            ValidationIssue("error", "missing_vs_mean_column", "filter_vs_mean missing column.")
        )
    elif col not in known:
        issues.append(
            ValidationIssue(
                "error",
                "missing_vs_mean_column",
                f"filter_vs_mean column `{col}` does not exist yet.",
            )
        )
    elif col in df.columns and not _is_numeric_compatible(df[col]):
        issues.append(
            ValidationIssue(
                "error",
                "non_numeric_vs_mean",
                f"filter_vs_mean column `{col}` is not numeric-compatible.",
            )
        )
    return issues


def _validate_top_per_group(
    step: AnalysisStep,
    known: set[str],
    df: pd.DataFrame,
    *,
    saw_annotate: bool,
    include_types: set[str] | None,
    row_types_present: set[str],
) -> list[ValidationIssue]:
    del saw_annotate
    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    for key in ("group_column", "value_column"):
        col = str(payload.get(key) or "")
        if not col:
            issues.append(
                ValidationIssue("error", f"missing_{key}", f"top_per_group missing {key}.")
            )
        elif col not in known:
            issues.append(
                ValidationIssue(
                    "error",
                    f"missing_{key}",
                    f"top_per_group `{key}` `{col}` does not exist.",
                )
            )
    try:
        n = int(payload.get("n") or 1)
        if n <= 0:
            issues.append(
                ValidationIssue("error", "invalid_top_n", f"top_per_group n must be > 0 (got {n}).")
            )
        elif n > _MAX_LIMIT_N:
            issues.append(
                ValidationIssue(
                    "error",
                    "top_n_too_large",
                    f"top_per_group n={n} exceeds {_MAX_LIMIT_N}.",
                )
            )
    except (TypeError, ValueError):
        issues.append(
            ValidationIssue("error", "invalid_top_n", "top_per_group n is not an integer.")
        )

    # ranking에 footer/subtotal 포함 위험
    if include_types and include_types & {"subtotal", "total", "footer"}:
        issues.append(
            ValidationIssue(
                "error",
                "ranking_includes_summary_rows",
                (
                    "top_per_group / ranking includes subtotal/total/footer row types, "
                    "which may distort ranking. Choose detail rows explicitly."
                ),
            )
        )
    elif not include_types and row_types_present & {"subtotal", "total", "footer"}:
        issues.append(
            ValidationIssue(
                "warning",
                "ranking_may_include_summary_rows",
                (
                    "Row types include summary rows and no explicit detail filter was set "
                    "before top_per_group. Prefer include_row_types=['detail']."
                ),
            )
        )
    return issues


def _validate_sort(
    step: AnalysisStep,
    known: set[str],
    index: int,
    steps: list[AnalysisStep],
) -> list[ValidationIssue]:
    del index, steps
    issues: list[ValidationIssue] = []
    payload = step.payload or {}
    by = payload.get("by") or []
    if isinstance(by, str):
        by = [by]
    if not by:
        issues.append(ValidationIssue("error", "sort_missing_target", "sort has no target columns."))
        return issues
    ascending = payload.get("ascending", False)
    if isinstance(ascending, list) and any(
        not isinstance(x, (bool, int)) for x in ascending
    ):
        issues.append(
            ValidationIssue(
                "error",
                "invalid_sort_ascending",
                "sort ascending values must be boolean.",
            )
        )
    for col in by:
        name = str(col)
        if name not in known:
            issues.append(
                ValidationIssue(
                    "error",
                    "missing_sort_column",
                    (
                        f"sort target `{name}` does not exist yet. "
                        "Derive or aggregate it before sorting."
                    ),
                )
            )
    return issues


def _validate_limit(step: AnalysisStep) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        n = int(step.payload.get("n") or 0)
    except (TypeError, ValueError):
        return [
            ValidationIssue("error", "invalid_limit", "limit n is not an integer."),
        ]
    if n <= 0:
        issues.append(
            ValidationIssue("error", "invalid_limit", f"limit n must be > 0 (got {n}).")
        )
    elif n > _MAX_LIMIT_N:
        issues.append(
            ValidationIssue(
                "error",
                "limit_too_large",
                f"limit n={n} exceeds {_MAX_LIMIT_N}.",
            )
        )
    return issues


def _validate_row_mix_for_plan(
    plan: AnalysisPlan,
    df: pd.DataFrame,
    *,
    saw_annotate: bool,
    include_types: set[str] | None,
    row_types_present: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not any(s.op in _AGG_LIKE_OPS for s in plan.steps):
        return issues

    if include_types and ("detail" in include_types) and (
        include_types & {"subtotal", "total"}
    ):
        issues.append(
            ValidationIssue(
                "error",
                "detail_subtotal_double_count",
                (
                    "The selected rows include both detail and subtotal rows, "
                    "which may double-count the aggregation. "
                    "Choose one row level explicitly."
                ),
            )
        )
        return issues

    # annotate/filter 없이 혼합 row type이 있으면 경고/에러
    mixed = row_types_present & {"detail", "subtotal"}
    if len(mixed) >= 2 and (include_types is None or not include_types):
        # prefer_subtotals만 켠 aggregate는 실행기가 처리 — warning
        prefer_sub = any(
            s.op == "aggregate" and bool(s.payload.get("prefer_subtotals"))
            for s in plan.steps
        )
        level = "warning" if prefer_sub or saw_annotate else "error"
        issues.append(
            ValidationIssue(
                level,
                "possible_double_count",
                (
                    "Source rows include both detail and subtotal levels. "
                    "Without an explicit row-type filter, aggregation may double-count. "
                    "Choose one row level explicitly (e.g. include_row_types=['detail'] "
                    "or prefer_subtotals with subtotal-only)."
                ),
            )
        )
    return issues
