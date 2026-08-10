"""고수준 analysis operation → 원자 steps 컴파일."""

from __future__ import annotations

from typing import Any

from core.analysis.analysis_plan_sanitize import _sanitize_numeric_filters


def _compile_high_level(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """고수준 operation을 원자 steps로 펼친다."""
    operation = str(data.get("operation") or "").strip()
    if operation in {"top_n_difference", "rank_difference", "difference_topn"}:
        return _compile_top_n_difference(data, columns)
    if operation in {"group_comparison", "compare_groups", "execution_rate_compare"}:
        return _compile_group_comparison(data, columns)
    if operation in {"correlation", "correlation_analysis"}:
        return _compile_correlation(data, columns)
    if operation in {"find_items", "item_filter", "condition_select"}:
        # max/min ranking → sort → limit
        extremum = _maybe_redirect_extremum_filter(data, columns)
        if extremum:
            return extremum
        # mean(...) value → filter_vs_mean 로 승격
        redirected = _maybe_redirect_mean_filter(data, columns)
        if redirected:
            return redirected
        return _compile_find_items(data, columns)
    if operation in {"rate_vs_mean", "execution_rate_vs_mean"}:
        return _compile_rate_vs_mean(data, columns)
    if operation in {"filter_vs_mean", "above_mean", "below_mean"}:
        return _compile_filter_vs_mean(data, columns)
    if operation in {"top_n_per_group", "top_per_group", "rank_per_group"}:
        return _compile_top_n_per_group(data, columns)
    if operation in {
        "split_by_difference",
        "increase_decrease_split",
        "budget_change_split",
    }:
        return _compile_split_by_difference(data, columns)
    if operation in {"aggregate", "groupby", "group_aggregate"}:
        return _compile_aggregate(data, columns)
    return {}


def _metric_alias_candidates(name: str) -> list[str]:
    """매출액_합계 / amount_sum 같은 별칭에서 원 컬럼 후보를 만든다."""
    import re

    text = str(name or "").strip()
    if not text:
        return []
    candidates = [text]
    stripped = re.sub(
        r"(_?(합계|합|평균|총계|sum|total|mean|avg|count|min|max))$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if stripped and stripped != text:
        candidates.append(stripped)
    # 한글 접미 공백형
    for suffix in (" 합계", " 평균", "합계", "평균"):
        if text.endswith(suffix) and len(text) > len(suffix):
            candidates.append(text[: -len(suffix)].strip())
    # unique preserve
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _resolve_metric_column(name: object, columns: set[str]) -> str | None:
    from core.analysis.analysis_plan_sanitize import _resolve_column

    raw = str(name or "").strip()
    if not raw:
        return None
    direct = _resolve_column(raw, columns)
    if direct:
        return direct
    for cand in _metric_alias_candidates(raw):
        resolved = _resolve_column(cand, columns)
        if resolved:
            return resolved
    return None


def _parse_metric_specs(
    metrics: Any,
    columns: set[str],
    *,
    default_fn: str | None = None,
    require_fn: bool = True,
) -> list[dict[str, str]]:
    """다양한 LLM metric 형태를 [{column, fn}]으로 정규화.

    require_fn=True이면 fn 누락 항목은 버린다 (silent sum 금지).
    """
    from core.analysis.ops_filters import AGGREGATE_FNS

    aliases = {
        "avg": "mean",
        "average": "mean",
        "med": "median",
        "n": "count",
        "cnt": "count",
        "total": "sum",
    }
    raw_list: list[Any]
    if isinstance(metrics, dict):
        # {"매출액": "sum"} 또는 {"column":..,"fn":..}
        if "column" in metrics or "name" in metrics or "fn" in metrics or "agg" in metrics:
            raw_list = [metrics]
        else:
            raw_list = [{k: v} for k, v in metrics.items()]
    elif isinstance(metrics, list):
        raw_list = metrics
    else:
        raw_list = []

    out: list[dict[str, str]] = []
    for metric in raw_list:
        col = ""
        fn = ""
        if isinstance(metric, str):
            col = metric
            fn = default_fn or ""
        elif isinstance(metric, dict):
            if "column" in metric or "name" in metric:
                col = str(metric.get("column") or metric.get("name") or "")
                fn = str(metric.get("fn") or metric.get("agg") or default_fn or "")
            elif len(metric) == 1:
                key, val = next(iter(metric.items()))
                # {"실행예산_합계": "sum"} or {"column": "x"} malformed
                if str(key) in {"column", "name", "fn", "agg"}:
                    continue
                col = str(key)
                fn = str(val or default_fn or "")
            else:
                col = str(metric.get("column") or metric.get("name") or "")
                fn = str(metric.get("fn") or metric.get("agg") or default_fn or "")
        else:
            continue
        resolved = _resolve_metric_column(col, columns)
        if not resolved:
            continue
        fn_norm = aliases.get(fn.lower().strip(), fn.lower().strip()) if fn else ""
        if require_fn and not fn_norm:
            continue
        if fn_norm and fn_norm not in AGGREGATE_FNS:
            continue
        if not fn_norm:
            continue
        out.append({"column": resolved, "fn": fn_norm})
    return out


def _looks_like_mean_intent(data: dict[str, Any]) -> bool:
    blob = " ".join(
        str(data.get(k) or "")
        for k in ("rate_name", "criteria_note", "explanation", "operation")
    ).lower()
    return any(tok in blob for tok in ("평균", "mean", "average", "avg"))


def _maybe_redirect_extremum_filter(
    data: dict[str, Any], columns: set[str]
) -> dict[str, Any]:
    """find_items with op=max/min → global ranking sort→limit."""
    from core.analysis.analysis_plan_sanitize import _resolve_column
    from core.profile_loader import preferred_columns_present

    raw = data.get("numeric_filters") or data.get("conditions") or []
    if not isinstance(raw, list) or not raw:
        return {}
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        op = str(spec.get("op") or spec.get("operator") or "").lower().strip()
        if op not in {"max", "min", "argmax", "argmin"}:
            continue
        col = _resolve_column(
            spec.get("column")
            or spec.get("left_column")
            or spec.get("value_column")
            or "",
            columns,
        )
        if not col:
            continue
        ascending = op in {"min", "argmin"}
        label_prefs = preferred_columns_present(columns)
        out_cols: list[str] = []
        seen: set[str] = set()
        for c in [
            *label_prefs,
            col,
            *[str(x) for x in (data.get("output_columns") or []) if str(x) in columns],
        ]:
            if c in columns and c not in seen:
                out_cols.append(c)
                seen.add(c)
        try:
            n = int(data.get("n") or data.get("limit") or 1)
        except (TypeError, ValueError):
            n = 1
        return {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "drop_blank_dimensions": True,
                },
                {"op": "sort", "by": [col], "ascending": [ascending]},
                {"op": "limit", "n": max(1, min(100, n))},
                {"op": "select_columns", "columns": out_cols},
            ],
            "criteria_note": str(
                data.get("criteria_note")
                or f"{col} 기준 {'하위' if ascending else '상위'} {n}개"
            ),
            "output_columns": out_cols,
            "interpret": bool(data.get("interpret", False)),
        }
    return {}


def _maybe_redirect_mean_filter(
    data: dict[str, Any], columns: set[str]
) -> dict[str, Any]:
    """find_items value에 mean(...)가 있으면 filter_vs_mean으로 변환."""
    import re

    raw = data.get("numeric_filters") or data.get("conditions") or []
    if not isinstance(raw, list) or not raw:
        return {}
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        value = str(spec.get("value") or "").strip()
        col = str(spec.get("column") or spec.get("left_column") or "").strip()
        if not value:
            continue
        mean_match = re.match(
            r"^(?:mean|avg|average|평균)\s*\(\s*([^)]+)\s*\)$",
            value,
            flags=re.IGNORECASE,
        )
        target = col
        if mean_match:
            inner = mean_match.group(1).strip()
            if inner and inner.lower() not in {"x", "col", "column"}:
                target = inner
        elif value.lower() in {"mean", "avg", "average", "평균"}:
            pass
        else:
            continue
        from core.analysis.analysis_plan_sanitize import _resolve_column

        resolved = _resolve_column(target, columns)
        if not resolved:
            continue
        op = str(spec.get("op") or spec.get("operator") or "gt").lower()
        relation = "above"
        if op in {"lt", "lte", "<", "<=", "below"}:
            relation = "below"
        return _compile_filter_vs_mean(
            {
                **data,
                "column": resolved,
                "relation": relation,
            },
            columns,
        )
    return {}


def _compile_filter_vs_mean(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    from core.analysis.analysis_plan_sanitize import _resolve_column
    from core.profile_loader import preferred_columns_present

    col = _resolve_column(
        data.get("column") or data.get("value_column") or data.get("metric") or "",
        columns,
    )
    if not col:
        return {}
    relation = str(data.get("relation") or data.get("compare") or "above").lower()
    if any(tok in relation for tok in ("below", "낮", "이하", "미만", "lt")):
        relation = "below"
    else:
        relation = "above"
    label_prefs = preferred_columns_present(columns)
    out_cols: list[str] = []
    seen: set[str] = set()
    for c in [
        *label_prefs,
        col,
        *[str(x) for x in (data.get("output_columns") or []) if str(x) in columns],
    ]:
        if c in columns and c not in seen:
            out_cols.append(c)
            seen.add(c)
    return {
        "steps": [
            {"op": "annotate_row_types"},
            {
                "op": "filter_rows",
                "include_row_types": ["detail"],
                "drop_blank_dimensions": True,
            },
            {"op": "filter_vs_mean", "column": col, "relation": relation},
            {
                "op": "sort",
                "by": [col],
                "ascending": [relation == "below"],
            },
            {"op": "select_columns", "columns": out_cols},
        ],
        "criteria_note": str(
            data.get("criteria_note")
            or f"{col}이(가) 산술평균보다 {'높은' if relation == 'above' else '낮은'} 행"
        ),
        "output_columns": out_cols,
        "interpret": bool(data.get("interpret", False)),
    }


def _compile_aggregate(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """고수준 aggregate → annotate/filter/aggregate 원자 steps."""
    from core.analysis.analysis_plan_sanitize import _resolve_columns

    group_by = data.get("group_by") or data.get("group_column") or data.get(
        "dimension_columns"
    ) or []
    if isinstance(group_by, str):
        group_by = [group_by]
    group_by = _resolve_columns([str(c) for c in group_by], columns)
    if not group_by:
        return {}

    resolved_metrics = _parse_metric_specs(
        data.get("metrics") or data.get("aggregations") or [],
        columns,
        require_fn=True,
    )
    if not resolved_metrics:
        single = (
            data.get("value_column")
            or data.get("metric")
            or data.get("metric_column")
            or data.get("column")
        )
        fn = str(data.get("fn") or data.get("agg") or "").lower().strip()
        col = _resolve_metric_column(single or "", columns)
        if col and fn:
            resolved_metrics = _parse_metric_specs(
                [{"column": col, "fn": fn}],
                columns,
                require_fn=True,
            )
    if not resolved_metrics:
        return {}

    prefer = data.get("prefer_subtotals")
    if prefer is None:
        prefer = True
    include_groups = data.get("include_groups") or data.get("groups") or []
    if isinstance(include_groups, str):
        include_groups = [include_groups]

    metric_names = [m["column"] for m in resolved_metrics]
    out_cols: list[str] = []
    seen: set[str] = set()
    for col in [*group_by, *metric_names]:
        if col not in seen:
            out_cols.append(col)
            seen.add(col)
    for col in data.get("output_columns") or []:
        name = str(col)
        resolved = _resolve_metric_column(name, columns)
        if resolved and resolved not in seen:
            out_cols.append(resolved)
            seen.add(resolved)

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": ["detail"],
            "drop_blank_dimensions": True,
        },
        {
            "op": "aggregate",
            "group_by": group_by,
            "metrics": resolved_metrics,
            "prefer_subtotals": bool(prefer),
            "include_groups": [str(g) for g in include_groups if str(g).strip()],
        },
        {"op": "select_columns", "columns": out_cols},
    ]
    note = str(data.get("criteria_note") or "").strip()
    interpret = bool(data.get("interpret", False))
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": group_by,
        "output_columns": out_cols,
        "interpret": interpret,
    }

def _compile_top_n_per_group(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """그룹마다 값 기준 상위/하위 n행 → 정렬 → 최소 열."""
    from core.profile_loader import preferred_columns_present

    group_col = str(
        data.get("group_column") or data.get("group_by") or data.get("by") or ""
    ).strip()
    value_col = str(
        data.get("value_column")
        or data.get("metric")
        or data.get("metric_column")
        or ""
    ).strip()
    if not value_col:
        sort_by = data.get("sort_by") or data.get("by_value")
        if isinstance(sort_by, list) and sort_by:
            value_col = str(sort_by[0]).strip()
        elif isinstance(sort_by, str):
            value_col = sort_by.strip()
    if group_col not in columns or value_col not in columns:
        return {}

    try:
        n = int(data.get("n") or data.get("top_n") or data.get("limit") or 1)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(50, n))

    ascending = data.get("ascending", False)
    if isinstance(ascending, list):
        ascending = bool(ascending[0]) if ascending else False
    else:
        ascending = bool(ascending)
    order = str(data.get("order") or data.get("direction") or "").lower()
    if any(tok in order for tok in ("asc", "small", "min", "낮", "작")):
        ascending = True
    if any(tok in order for tok in ("desc", "large", "max", "높", "큰")):
        ascending = False

    label_prefs = preferred_columns_present(columns)
    explicit = [
        str(c)
        for c in (data.get("output_columns") or data.get("select_columns") or [])
        if str(c) in columns
    ]
    out_cols: list[str] = []
    seen: set[str] = set()
    for col in [*label_prefs, value_col, *explicit]:
        if col in columns and col not in seen:
            out_cols.append(col)
            seen.add(col)
    if not explicit:
        keep = set(label_prefs) | {value_col, group_col}
        out_cols = [c for c in out_cols if c in keep]

    note = str(
        data.get("criteria_note")
        or (
            f"{group_col}별로 {value_col} "
            f"{'하위' if ascending else '상위'} {n}개 항목"
        )
    )
    if "interpret" in data:
        interpret = bool(data.get("interpret"))
    else:
        interpret = False

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": ["detail"],
            "drop_blank_dimensions": True,
            "exclude_uncertain": False,
        },
        {
            "op": "top_per_group",
            "group_column": group_col,
            "value_column": value_col,
            "n": n,
            "ascending": ascending,
        },
        {
            "op": "sort",
            "by": [value_col],
            "ascending": [ascending],
        },
        {"op": "select_columns", "columns": out_cols},
    ]
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": [group_col],
        "output_columns": out_cols,
        "interpret": interpret,
    }


def _compile_split_by_difference(
    data: dict[str, Any], columns: set[str]
) -> dict[str, Any]:
    """계획 vs 실행(또는 left−right) 차이로 증가/감소를 유지한다.

    ``top_n_difference``와 달리 limit으로 자르지 않는다.
    ``direction``이 up/down이면 해당 부호 행만 남긴다.
    """
    from core.profile_loader import (
        default_diff_name,
        default_split_label_name,
        detail_label_columns_present,
        preferred_columns_present,
    )

    values = [str(c) for c in (data.get("value_columns") or []) if str(c) in columns]
    left = str(
        data.get("left") or data.get("after") or data.get("executed_column") or ""
    )
    right = str(
        data.get("right") or data.get("before") or data.get("planned_column") or ""
    )
    if left not in columns or right not in columns:
        if len(values) >= 2:
            left, right = values[0], values[1]
        else:
            return {}
    if left not in columns or right not in columns or left == right:
        return {}

    diff_name = str(data.get("diff_name") or default_diff_name()).strip() or "차이"
    label_name = (
        str(data.get("label_name") or default_split_label_name()).strip() or "구분"
    )
    direction = str(data.get("direction") or "both").strip().lower()
    if direction not in {"up", "down", "both"}:
        direction = "both"

    label_prefs = preferred_columns_present(columns)
    explicit = [
        str(c)
        for c in (data.get("output_columns") or data.get("select_columns") or [])
        if str(c) in columns or str(c) in {diff_name, label_name}
    ]
    out_cols: list[str] = []
    seen: set[str] = set()
    for col in [*label_prefs, right, left, diff_name, label_name, *explicit]:
        if col in seen:
            continue
        if col in columns or col in {diff_name, label_name}:
            out_cols.append(col)
            seen.add(col)
    if not explicit:
        keep = set(label_prefs) | {left, right, diff_name, label_name}
        out_cols = [c for c in out_cols if c in keep]

    if direction == "up":
        scope = f"{label_name}=증가 항목만"
    elif direction == "down":
        scope = f"{label_name}=감소 항목만"
    else:
        scope = f"{label_name}=증가/감소/동일. 세부행 전체(상위 N 절단 없음)"
    note = str(
        data.get("criteria_note")
        or (f"{diff_name} = {left} − {right}. {scope}.")
    )
    interpret = bool(data.get("interpret", True))

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": ["detail"],
            "drop_blank_dimensions": True,
            "exclude_uncertain": False,
        },
        {
            "op": "derive_column",
            "name": diff_name,
            "expr": {"diff": [left, right]},
        },
        {
            "op": "derive_column",
            "name": label_name,
            "expr": {"sign_label": [left, right]},
        },
    ]
    if direction == "up":
        steps.append(
            {
                "op": "filter_rows",
                "include_row_types": ["detail"],
                "drop_blank_dimensions": False,
                "column_filters": [{"column": label_name, "values": ["증가"]}],
            }
        )
    elif direction == "down":
        steps.append(
            {
                "op": "filter_rows",
                "include_row_types": ["detail"],
                "drop_blank_dimensions": False,
                "column_filters": [{"column": label_name, "values": ["감소"]}],
            }
        )
    steps.extend(
        [
            {
                "op": "sort",
                "by": [diff_name],
                "ascending": [False],
            },
            {"op": "select_columns", "columns": out_cols},
        ]
    )
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": detail_label_columns_present(columns)[:1],
        "output_columns": out_cols,
        "interpret": interpret,
    }


def _compile_rate_vs_mean(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """행단위 비율 → 분모 0 제외 → 평균 대비 필터 → 최소 열."""
    from core.profile_loader import (
        default_rate_name,
        detail_label_columns_present,
        preferred_columns_present,
    )

    numerator = str(data.get("numerator") or data.get("executed_column") or "")
    denominator = str(data.get("denominator") or data.get("budget_column") or "")
    if numerator not in columns or denominator not in columns:
        return {}

    rate_name = str(data.get("rate_name") or default_rate_name()).strip() or "비율"
    relation = str(data.get("relation") or data.get("compare") or "below").lower()
    if any(tok in relation for tok in ("above", "높", "이상", "초과", "gt")):
        relation = "above"
    else:
        relation = "below"

    label_prefs = preferred_columns_present(columns)
    # 비율 표는 세부 라벨을 앞에 두는 편이 읽기 쉽다
    detail_labels = detail_label_columns_present(columns)
    if detail_labels:
        label_prefs = list(
            dict.fromkeys([*detail_labels, *[c for c in label_prefs if c not in detail_labels]])
        )
    explicit = [
        str(c)
        for c in (data.get("output_columns") or data.get("select_columns") or [])
        if str(c) in columns or str(c) == rate_name
    ]
    out_cols: list[str] = []
    seen: set[str] = set()
    for col in [*label_prefs, denominator, numerator, rate_name, *explicit]:
        if col == rate_name or col in columns:
            if col not in seen:
                out_cols.append(col)
                seen.add(col)
    # 가독성: 과도한 열 제거 (라벨+분모+분자+비율 위주)
    if not explicit:
        keep_max = set(label_prefs) | {denominator, numerator, rate_name}
        out_cols = [c for c in out_cols if c in keep_max]

    sort_asc = relation == "below"
    note = str(
        data.get("criteria_note")
        or (
            f"{rate_name} = {numerator} ÷ {denominator} "
            f"(분모 0 제외). 산술평균보다 "
            f"{'낮은' if relation == 'below' else '높은'} 항목만 표시."
        )
    )
    # 표 요청은 기본 해석 OFF
    if "interpret" in data:
        interpret = bool(data.get("interpret"))
    else:
        interpret = False

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": ["detail"],
            "drop_blank_dimensions": True,
            "exclude_uncertain": False,
            "numeric_filters": [
                {"column": denominator, "op": "gt", "value": 0},
            ],
        },
        {
            "op": "derive_column",
            "name": rate_name,
            "expr": {"ratio": [numerator, denominator]},
        },
        {
            "op": "filter_vs_mean",
            "column": rate_name,
            "relation": relation,
        },
        {
            "op": "sort",
            "by": [rate_name],
            "ascending": [sort_asc],
        },
        {"op": "select_columns", "columns": out_cols},
    ]
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": detail_label_columns_present(columns)[:1],
        "output_columns": out_cols,
        "interpret": interpret,
    }

def _compile_find_items(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """세부 항목 조건 탐색: 수치/라벨 필터 → 필요 열만 선택 → 정렬."""
    from core.analysis.analysis_plan_sanitize import _resolve_column
    from core.profile_loader import (
        preferred_columns_present,
        related_metric_columns_present,
    )

    # categorical equality가 numeric_filters에 들어온 경우 column_filters로 승격
    column_filters: list[dict[str, Any]] = []
    raw_numeric = data.get("numeric_filters") or data.get("conditions") or []
    kept_numeric: list[Any] = []
    if isinstance(raw_numeric, list):
        for spec in raw_numeric:
            if not isinstance(spec, dict):
                continue
            col = _resolve_column(
                spec.get("column") or spec.get("left_column") or "", columns
            )
            op = str(spec.get("op") or spec.get("operator") or "").lower()
            val = spec.get("value")
            right = _resolve_column(
                spec.get("right_column") or spec.get("other_column") or "", columns
            )
            if (
                col
                and col in columns
                and not right
                and op in {"eq", "==", "=", "ne", "!=", "<>"}
                and val is not None
                and str(val).strip()
                and not _value_looks_numeric(val)
            ):
                column_filters.append({"column": col, "values": [str(val)]})
            else:
                kept_numeric.append(spec)

    for spec in data.get("column_filters") or []:
        if isinstance(spec, dict):
            column_filters.append(spec)

    numeric_filters = _sanitize_numeric_filters(
        {"numeric_filters": kept_numeric},
        columns,
    )
    if not numeric_filters and not column_filters:
        return {}

    label_prefs = preferred_columns_present(columns)
    metric_cols: list[str] = []
    for filt in numeric_filters:
        for key in ("column", "left_column", "right_column"):
            col = str(filt.get(key) or "")
            if col and col not in metric_cols:
                metric_cols.append(col)
    for filt in column_filters:
        col = str(filt.get("column") or "")
        if col and col not in metric_cols:
            metric_cols.append(col)

    numerator = str(data.get("numerator") or "")
    denominator = str(data.get("denominator") or "")
    rate_name = str(data.get("rate_name") or data.get("ratio_name") or "").strip()
    derive_ratio = bool(
        rate_name and numerator in columns and denominator in columns
    )

    explicit_out = [
        str(c)
        for c in (data.get("output_columns") or data.get("select_columns") or [])
        if str(c) in columns or (derive_ratio and str(c) == rate_name)
    ]
    if explicit_out:
        out_cols = []
        seen: set[str] = set()
        for col in [*label_prefs, *metric_cols, *explicit_out]:
            if (col in columns or (derive_ratio and col == rate_name)) and col not in seen:
                out_cols.append(col)
                seen.add(col)
    else:
        related = [
            c
            for c in related_metric_columns_present(columns)
            if c not in metric_cols
        ]
        if not related:
            related = [
                c
                for c in related_metric_columns_present(
                    columns, key="condition_related_metrics"
                )
                if c not in metric_cols
            ]
        out_cols = []
        seen = set()
        related_kept = 0
        for col in [*label_prefs, *metric_cols, *related]:
            if col in seen or col not in columns:
                continue
            if col in related:
                if related_kept >= 2:
                    continue
                related_kept += 1
            out_cols.append(col)
            seen.add(col)

    if derive_ratio:
        for col in (numerator, denominator, rate_name):
            if col and col not in out_cols:
                out_cols.append(col)

    sort_by = data.get("sort_by") or data.get("sort") or []
    if isinstance(sort_by, dict):
        sort_by = [sort_by.get("column") or sort_by.get("by") or ""]
    if isinstance(sort_by, str):
        # "temperature desc" 형태
        sort_by = [sort_by.split()[0]] if sort_by.strip() else []
    sort_by = [
        str(c)
        for c in sort_by
        if str(c) in columns or (derive_ratio and str(c) == rate_name)
    ]
    if not sort_by:
        if derive_ratio and rate_name:
            sort_by = [rate_name]
        else:
            for filt in numeric_filters:
                if str(filt.get("op")) in {"gt", "gte"}:
                    sort_by = [str(filt.get("column") or filt.get("left_column") or "")]
                    sort_by = [c for c in sort_by if c]
                    break
            if not sort_by and numeric_filters:
                first = numeric_filters[0]
                sort_by = [
                    str(first.get("column") or first.get("left_column") or "")
                ]
                sort_by = [c for c in sort_by if c]

    ascending = data.get("ascending", False)
    if isinstance(ascending, bool):
        ascending = [ascending] * max(len(sort_by), 1)
    elif isinstance(ascending, list):
        ascending = [bool(x) for x in ascending]
        while len(ascending) < len(sort_by):
            ascending.append(False)
        ascending = ascending[: len(sort_by)]
    else:
        ascending = [False] * max(len(sort_by), 1)

    filter_payload: dict[str, Any] = {
        "include_row_types": ["detail"],
        "drop_blank_dimensions": True,
        "exclude_uncertain": False,
    }
    if numeric_filters:
        filter_payload["numeric_filters"] = numeric_filters
    if column_filters:
        filter_payload["column_filters"] = column_filters

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {"op": "filter_rows", **filter_payload},
    ]
    if derive_ratio:
        steps.append(
            {
                "op": "derive_column",
                "name": rate_name,
                "expr": {"percent_ratio": [numerator, denominator]},
            }
        )
        steps.append(
            {
                "op": "filter_rows",
                "include_row_types": ["detail"],
                "drop_blank_dimensions": False,
                "numeric_filters": [
                    {"column": denominator, "op": "gt", "value": 0},
                ],
            }
        )
    if sort_by:
        steps.append({"op": "sort", "by": sort_by, "ascending": ascending[: len(sort_by)]})
    steps.append({"op": "select_columns", "columns": out_cols})
    note = str(
        data.get("criteria_note")
        or (
            f"조건에 맞는 세부 항목만 추리고 {rate_name}={numerator}÷{denominator}(%)를 표시했습니다."
            if derive_ratio
            else "조건에 맞는 세부 항목만 추리고 관련 열만 표시했습니다."
        )
    )
    interpret = True if "interpret" not in data else bool(data.get("interpret"))
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": label_prefs[:1],
        "output_columns": out_cols,
        "interpret": interpret,
    }


def _value_looks_numeric(value: object) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False




def _compile_correlation(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """세부 행 기준 두 수치 열 상관분석 계획."""
    x_col = str(data.get("x_column") or data.get("column_x") or "")
    y_col = str(data.get("y_column") or data.get("column_y") or "")
    value_cols = data.get("value_columns") or data.get("columns") or []
    if isinstance(value_cols, str):
        value_cols = [value_cols]
    value_cols = [str(c) for c in value_cols if str(c) in columns]
    if (not x_col or x_col not in columns) and len(value_cols) >= 2:
        x_col, y_col = value_cols[0], value_cols[1]
    if x_col not in columns or y_col not in columns:
        return {}

    label_col = str(data.get("label_column") or data.get("item_column") or "")
    if label_col not in columns:
        dims = [str(c) for c in (data.get("dimension_columns") or []) if str(c) in columns]
        label_col = dims[-1] if dims else ""

    methods = data.get("methods") or ["pearson", "spearman"]
    if isinstance(methods, str):
        methods = [methods]
    methods = [str(m) for m in methods if str(m).strip()]

    drop_blank = True
    exclude = data.get("exclude_rows") if isinstance(data.get("exclude_rows"), dict) else {}
    if "blank_dimensions" in exclude:
        drop_blank = bool(exclude.get("blank_dimensions"))

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": ["detail"],
            "drop_blank_dimensions": drop_blank,
            "exclude_uncertain": False,
        },
        {
            "op": "correlation",
            "x_column": x_col,
            "y_column": y_col,
            "label_column": label_col or None,
            "methods": methods or ["pearson", "spearman"],
        },
    ]
    note = str(
        data.get("criteria_note")
        or f"세부 행 기준 {x_col}와 {y_col}의 상관계수(Pearson/Spearman)를 계산했습니다."
    )
    interpret = True if "interpret" not in data else bool(data.get("interpret"))
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": [],
        "output_columns": ["지표", "값"],
        "interpret": interpret,
    }


def _compile_top_n_difference(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    from core.analysis.analysis_plan_sanitize import _resolve_column, _resolve_columns
    from core.profile_loader import preferred_columns_present

    dims = _resolve_columns(
        [str(c) for c in (data.get("dimension_columns") or [])],
        columns,
    )
    values = _resolve_columns(
        [str(c) for c in (data.get("value_columns") or [])],
        columns,
    )
    if not values:
        single = _resolve_column(
            data.get("value_column")
            or data.get("metric")
            or data.get("metric_column")
            or "",
            columns,
        )
        if single:
            values = [single]

    # 단일 metric top-N: difference가 아니라 sort → limit
    if len(values) == 1:
        value_col = values[0]
        sort_dir = str(data.get("sort") or data.get("order") or "descending").lower()
        ascending = sort_dir in {"ascending", "asc", "오름차순"}
        try:
            limit_n = max(1, min(100, int(data.get("limit") or data.get("n") or 5)))
        except (TypeError, ValueError):
            limit_n = 5
        label_prefs = preferred_columns_present(columns)
        out_cols: list[str] = []
        seen: set[str] = set()
        for col in [*dims, *label_prefs, value_col]:
            if col in columns and col not in seen:
                out_cols.append(col)
                seen.add(col)
        note = str(
            data.get("criteria_note")
            or f"{value_col} 기준 {'하위' if ascending else '상위'} {limit_n}개"
        )
        return {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "drop_blank_dimensions": True,
                    "exclude_uncertain": False,
                },
                {"op": "sort", "by": [value_col], "ascending": [ascending]},
                {"op": "limit", "n": limit_n},
                {"op": "select_columns", "columns": out_cols},
            ],
            "criteria_note": note,
            "dimension_columns": dims,
            "output_columns": out_cols,
            "interpret": bool(data.get("interpret", False)),
        }

    if len(values) < 2:
        return {}

    mode = str(data.get("difference_mode") or "absolute").lower()
    sort_dir = str(data.get("sort") or "descending").lower()
    ascending = sort_dir in {"ascending", "asc", "오름차순"}
    try:
        limit_n = max(1, min(100, int(data.get("limit") or 5)))
    except (TypeError, ValueError):
        limit_n = 5

    exclude = data.get("exclude_rows") if isinstance(data.get("exclude_rows"), dict) else {}
    include_types = ["detail"]
    drop_blank = bool(exclude.get("blank_dimensions", True))

    left, right = values[0], values[1]
    if mode in {"absolute", "abs", "절댓값", "절대"}:
        expr = {"abs_diff": [left, right]}
        note = "차이의 절댓값을 기준으로 내림차순 정렬했습니다."
        if ascending:
            note = "차이의 절댓값을 기준으로 오름차순 정렬했습니다."
    else:
        expr = {"diff": [left, right]}
        note = f"{left}−{right} 차이를 기준으로 정렬했습니다."

    diff_name = "차이"
    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": include_types,
            "drop_blank_dimensions": drop_blank,
            "exclude_uncertain": False,
        },
        {"op": "derive_column", "name": diff_name, "expr": expr},
        {"op": "sort", "by": [diff_name], "ascending": [ascending]},
        {"op": "limit", "n": limit_n},
    ]
    out_cols = [*dims, *values, diff_name]
    seen: set[str] = set()
    ordered: list[str] = []
    for col in out_cols:
        if col in seen:
            continue
        if col in columns or col == diff_name:
            ordered.append(col)
            seen.add(col)
    steps.append({"op": "select_columns", "columns": ordered})

    if data.get("criteria_note"):
        note = str(data["criteria_note"])

    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": dims,
        "output_columns": ordered,
        "interpret": False,
    }


def _compile_group_comparison(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    from core.analysis.analysis_plan_sanitize import _resolve_column

    group_col = _resolve_column(
        data.get("group_column")
        or (data.get("group_by") or [None])[0]
        or "",
        columns,
    ) or ""
    if not group_col:
        dims = [
            _resolve_column(c, columns)
            for c in (data.get("dimension_columns") or [])
        ]
        dims = [c for c in dims if c]
        group_col = dims[0] if dims else ""
    if not group_col or group_col not in columns:
        return {}

    groups = data.get("groups") or data.get("include_groups") or []
    if isinstance(groups, str):
        groups = [groups]
    groups = [str(g) for g in groups if str(g).strip()]

    metric_specs = _parse_metric_specs(
        data.get("metrics") or data.get("value_columns") or [],
        columns,
        require_fn=True,
    )
    metric_cols = [m["column"] for m in metric_specs]

    numerator = _resolve_column(data.get("numerator") or "", columns) or ""
    den_raw = data.get("denominator")
    denominator = ""
    if den_raw is not None and str(den_raw).strip().lower() not in {
        "",
        "null",
        "none",
        "count",
        "n",
        "cnt",
    }:
        denominator = _resolve_column(den_raw, columns) or ""

    from core.profile_loader import default_rate_name

    rate_name = str(data.get("rate_name") or default_rate_name())

    # mean intent with fake denominator=count → aggregate(fn=mean)
    if numerator and not denominator and _looks_like_mean_intent(data):
        metric_specs = [{"column": numerator, "fn": "mean"}]
        metric_cols = [numerator]
    elif numerator and denominator and numerator in columns and denominator in columns:
        for col in (denominator, numerator):
            if col not in metric_cols:
                metric_cols.append(col)
                metric_specs.append({"column": col, "fn": "sum"})
    elif numerator and not denominator and numerator in columns:
        # 단일 metric 그룹 비교: 명시 fn 없으면 sum (비교용 합계)
        if numerator not in metric_cols:
            fn = "mean" if _looks_like_mean_intent(data) else "sum"
            metric_specs.append({"column": numerator, "fn": fn})
            metric_cols.append(numerator)

    if len(metric_specs) < 1:
        return {}

    prefer = data.get("prefer_subtotals")
    if prefer is None:
        prefer = all(m["fn"] == "sum" for m in metric_specs)

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "aggregate",
            "group_by": [group_col],
            "metrics": metric_specs,
            "prefer_subtotals": bool(prefer),
            "include_groups": groups,
        },
    ]
    rate_columns: list[str] = []
    if numerator and denominator and numerator in columns and denominator in columns:
        steps.append(
            {
                "op": "ratio_of_aggregates",
                "name": rate_name,
                "numerator": numerator,
                "denominator": denominator,
            }
        )
        if rate_name not in metric_cols:
            metric_cols.append(rate_name)
        rate_columns = [rate_name]

    steps.append(
        {
            "op": "compare_groups",
            "group_column": group_col,
            "groups": groups,
            "metrics": [c for c in metric_cols if c in columns or c == rate_name],
            "rate_columns": rate_columns,
        }
    )

    out_cols = [group_col, *metric_cols]
    if data.get("criteria_note"):
        note = str(data["criteria_note"])
    elif rate_columns:
        note = f"{group_col} 그룹 비율 비교"
    else:
        note = f"{group_col} 그룹 비교"
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": [group_col],
        "output_columns": out_cols,
        "interpret": True,
    }


