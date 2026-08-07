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
        return _compile_find_items(data, columns)
    if operation in {"rate_vs_mean", "execution_rate_vs_mean"}:
        return _compile_rate_vs_mean(data, columns)
    if operation in {"top_n_per_group", "top_per_group", "rank_per_group"}:
        return _compile_top_n_per_group(data, columns)
    if operation in {
        "split_by_difference",
        "increase_decrease_split",
        "budget_change_split",
    }:
        return _compile_split_by_difference(data, columns)
    return {}


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
    """세부 항목 조건 탐색: 수치 필터 → (선택) 비중 파생 → 필요 열만 선택 → 정렬."""
    from core.profile_loader import (
        preferred_columns_present,
        related_metric_columns_present,
    )

    numeric_filters = _sanitize_numeric_filters(
        {"numeric_filters": data.get("numeric_filters") or data.get("conditions") or []},
        columns,
    )
    if not numeric_filters:
        return {}

    label_prefs = preferred_columns_present(columns)
    metric_cols = [str(f["column"]) for f in numeric_filters]

    numerator = str(data.get("numerator") or "")
    denominator = str(data.get("denominator") or "")
    rate_name = str(data.get("rate_name") or data.get("ratio_name") or "").strip()
    # rate_name이 명시된 경우만 비중 열을 만든다 (오탐 방지)
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
        # condition_related_metrics 보조 (없으면 find_related만)
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
    if isinstance(sort_by, str):
        sort_by = [sort_by]
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
                    sort_by = [str(filt["column"])]
                    break
            if not sort_by:
                sort_by = [str(numeric_filters[0]["column"])]

    ascending = data.get("ascending", False)
    if isinstance(ascending, bool):
        ascending = [ascending] * len(sort_by)
    elif isinstance(ascending, list):
        ascending = [bool(x) for x in ascending]
        while len(ascending) < len(sort_by):
            ascending.append(False)
        ascending = ascending[: len(sort_by)]
    else:
        ascending = [False] * len(sort_by)

    steps: list[dict[str, Any]] = [
        {"op": "annotate_row_types"},
        {
            "op": "filter_rows",
            "include_row_types": ["detail"],
            "drop_blank_dimensions": True,
            "exclude_uncertain": False,
            "numeric_filters": numeric_filters,
        },
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
    steps.extend(
        [
            {"op": "sort", "by": sort_by, "ascending": ascending},
            {"op": "select_columns", "columns": out_cols},
        ]
    )
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
    dims = [str(c) for c in (data.get("dimension_columns") or []) if str(c) in columns]
    values = [str(c) for c in (data.get("value_columns") or []) if str(c) in columns]
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
    group_col = str(
        data.get("group_column")
        or (data.get("group_by") or [None])[0]
        or ""
    )
    if group_col not in columns:
        # dimension_columns 첫 열 시도
        dims = [str(c) for c in (data.get("dimension_columns") or []) if str(c) in columns]
        group_col = dims[0] if dims else ""
    if not group_col or group_col not in columns:
        return {}

    groups = data.get("groups") or data.get("include_groups") or []
    if isinstance(groups, str):
        groups = [groups]
    groups = [str(g) for g in groups if str(g).strip()]

    metrics_raw = data.get("metrics") or data.get("value_columns") or []
    metric_cols: list[str] = []
    metric_specs: list[dict[str, str]] = []
    if isinstance(metrics_raw, list):
        for item in metrics_raw:
            if isinstance(item, str) and item in columns:
                metric_cols.append(item)
                metric_specs.append({"column": item, "fn": "sum"})
            elif isinstance(item, dict):
                col = str(item.get("column") or item.get("name") or "")
                if col in columns:
                    metric_cols.append(col)
                    metric_specs.append(
                        {"column": col, "fn": str(item.get("fn") or "sum")}
                    )

    numerator = str(data.get("numerator") or "")
    denominator = str(data.get("denominator") or "")
    from core.profile_loader import default_rate_name

    rate_name = str(data.get("rate_name") or default_rate_name())
    if numerator in columns and denominator in columns:
        for col in (denominator, numerator):
            if col not in metric_cols:
                metric_cols.append(col)
                metric_specs.append({"column": col, "fn": "sum"})

    if len(metric_specs) < 1:
        return {}

    prefer = data.get("prefer_subtotals")
    if prefer is None:
        prefer = True

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
    if numerator in columns and denominator in columns:
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

    steps.append(
        {
            "op": "compare_groups",
            "group_column": group_col,
            "groups": groups,
            "metrics": metric_cols,
            "rate_columns": [rate_name] if numerator and denominator else [],
        }
    )

    out_cols = [group_col, *metric_cols]
    note = str(
        data.get("criteria_note")
        or f"{group_col} 그룹별 집계·비율 비교"
    )
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": [group_col],
        "output_columns": out_cols,
        "interpret": True,
    }


