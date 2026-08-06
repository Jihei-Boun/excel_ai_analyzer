"""채팅 분석용 구조화 계획 — 원자 step 파이프라인."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_ANALYSIS_OPS = frozenset(
    {
        "annotate_row_types",
        "filter_rows",
        "select_columns",
        "derive_column",
        "sort",
        "limit",
        "drop_columns",
        "aggregate",
        "ratio_of_aggregates",
        "compare_groups",
        "distribution_summary",
        "correlation",
        "filter_vs_mean",
        "top_per_group",
    }
)

SUPPORTED_DERIVE_EXPRS = frozenset(
    {"diff", "abs", "abs_diff", "ratio", "percent_ratio", "sign_label"}
)

ROW_TYPES = frozenset({"detail", "subtotal", "total", "footer", "blank"})

META_COLUMNS = ("_row_type", "_row_type_confidence", "_row_type_reasons")

HIGH_LEVEL_OPERATIONS = frozenset(
    {
        "top_n_difference",
        "rank_difference",
        "difference_topn",
        "group_comparison",
        "compare_groups",
        "execution_rate_compare",
        "correlation",
        "correlation_analysis",
        "find_items",
        "item_filter",
        "condition_select",
        "rate_vs_mean",
        "execution_rate_vs_mean",
        "top_n_per_group",
        "top_per_group",
        "rank_per_group",
        "split_by_difference",
        "increase_decrease_split",
        "budget_change_split",
    }
)


@dataclass
class AnalysisStep:
    op: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, **dict(self.payload)}


@dataclass
class AnalysisPlan:
    """허용된 원자 연산만 담는 채팅 분석 계획."""

    steps: list[AnalysisStep] = field(default_factory=list)
    criteria_note: str = ""
    dimension_columns: list[str] = field(default_factory=list)
    output_columns: list[str] = field(default_factory=list)
    interpret: bool = False
    footer_labels: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "criteria_note": self.criteria_note,
            "dimension_columns": list(self.dimension_columns),
            "output_columns": list(self.output_columns),
            "interpret": self.interpret,
            "footer_labels": list(self.footer_labels),
        }

    @property
    def limit_n(self) -> int | None:
        for step in reversed(self.steps):
            if step.op == "limit":
                try:
                    return int(step.payload.get("n", 0)) or None
                except (TypeError, ValueError):
                    return None
        return None

    @property
    def sort_spec(self) -> tuple[list[str], list[bool]] | None:
        for step in reversed(self.steps):
            if step.op != "sort":
                continue
            by = step.payload.get("by") or []
            if isinstance(by, str):
                by = [by]
            ascending = step.payload.get("ascending", False)
            if isinstance(ascending, bool):
                ascending = [ascending] * len(by)
            elif not isinstance(ascending, list):
                ascending = [bool(ascending)] * len(by)
            return [str(x) for x in by], [bool(x) for x in ascending]
        return None

    @property
    def derive_specs(self) -> list[tuple[str, str, list[str]]]:
        """(name, expr_kind, operands) 목록."""
        out: list[tuple[str, str, list[str]]] = []
        for step in self.steps:
            if step.op == "derive_column":
                name = str(step.payload.get("name") or "")
                expr = step.payload.get("expr") or {}
                if not name or not isinstance(expr, dict) or not expr:
                    continue
                kind = next(iter(expr.keys()))
                operands = expr.get(kind) or []
                if isinstance(operands, str):
                    operands = [operands]
                out.append((name, str(kind), [str(x) for x in operands]))
            elif step.op == "ratio_of_aggregates":
                name = str(step.payload.get("name") or "비율")
                num = str(step.payload.get("numerator") or "")
                den = str(step.payload.get("denominator") or "")
                if name and num and den:
                    out.append((name, "ratio", [num, den]))
        return out

    @property
    def filters_to_detail_only(self) -> bool:
        for step in self.steps:
            if step.op != "filter_rows":
                continue
            include = step.payload.get("include_row_types") or []
            if include == ["detail"] or set(include) == {"detail"}:
                return True
        return False

    @property
    def uses_aggregate_ops(self) -> bool:
        return any(
            s.op
            in {
                "aggregate",
                "ratio_of_aggregates",
                "compare_groups",
                "distribution_summary",
                "correlation",
                "filter_vs_mean",
                "top_per_group",
            }
            for s in self.steps
        )


def analysis_plan_from_dict(
    data: dict[str, Any],
    *,
    available_columns: list[str],
) -> AnalysisPlan:
    """LLM JSON을 sanitize·컴파일하여 AnalysisPlan으로 만든다."""
    if not isinstance(data, dict):
        raise ValueError("분석 계획이 객체가 아닙니다.")

    columns = {str(c) for c in available_columns}
    compiled = _compile_high_level(data, columns)
    raw_steps = compiled.get("steps") or data.get("steps") or []
    if not isinstance(raw_steps, list):
        raise ValueError("steps는 배열이어야 합니다.")

    steps: list[AnalysisStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        step = _sanitize_step(item, columns)
        if step is not None:
            steps.append(step)

    if not steps:
        raise ValueError("실행 가능한 분석 step이 없습니다.")

    dim_cols = [
        str(c)
        for c in (compiled.get("dimension_columns") or data.get("dimension_columns") or [])
        if str(c) in columns
    ]
    out_cols = [
        str(c)
        for c in (compiled.get("output_columns") or data.get("output_columns") or [])
        if str(c) in columns or _is_derived_name(str(c), steps)
    ]
    # select에 없는 출력 컬럼이 있으면 마지막에 select 보강
    if out_cols and not any(s.op == "select_columns" for s in steps):
        steps.append(AnalysisStep("select_columns", {"columns": out_cols}))

    note = str(
        compiled.get("criteria_note")
        or data.get("criteria_note")
        or data.get("explanation")
        or ""
    ).strip()

    interpret = bool(
        compiled.get("interpret")
        if "interpret" in compiled
        else data.get("interpret", False)
    )
    # 비교·상관·항목탐색 고수준은 기본 해석 ON.
    # rate_vs_mean은 표 요청이 많아 기본 OFF (data/compiled에서 명시 가능).
    if str(data.get("operation") or "") in {
        "group_comparison",
        "compare_groups",
        "execution_rate_compare",
        "correlation",
        "correlation_analysis",
        "find_items",
        "item_filter",
        "condition_select",
        "split_by_difference",
        "increase_decrease_split",
        "budget_change_split",
    }:
        interpret = True if "interpret" not in data else interpret

    return AnalysisPlan(
        steps=steps,
        criteria_note=note,
        dimension_columns=dim_cols,
        output_columns=out_cols,
        interpret=interpret,
        raw=dict(data),
    )


def _is_derived_name(name: str, steps: list[AnalysisStep]) -> bool:
    for s in steps:
        if s.op == "derive_column" and str(s.payload.get("name") or "") == name:
            return True
        if s.op == "ratio_of_aggregates" and str(s.payload.get("name") or "") == name:
            return True
    return False


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

    label_prefs = [
        c
        for c in ("비목분류", "비용명_2", "비용명", "항목명", "항목")
        if c in columns
    ]
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
    """계획 vs 실행(또는 left−right) 차이로 증가/감소 전체를 유지한다.

    ``top_n_difference``와 달리 limit으로 자르지 않는다.
    """
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

    diff_name = str(data.get("diff_name") or "차이").strip() or "차이"
    label_name = str(data.get("label_name") or "구분").strip() or "구분"

    label_prefs = [
        c
        for c in ("비목분류", "비용명_2", "비용명", "항목명", "항목")
        if c in columns
    ]
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

    note = str(
        data.get("criteria_note")
        or (
            f"{diff_name} = {left} − {right}. "
            f"{label_name}=증가/감소/동일. 세부행 전체(상위 N 절단 없음)."
        )
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
        {
            "op": "sort",
            "by": [diff_name],
            "ascending": [False],
        },
        {"op": "select_columns", "columns": out_cols},
    ]
    return {
        "steps": steps,
        "criteria_note": note,
        "dimension_columns": [c for c in ("비용명_2", "비용명") if c in columns][:1],
        "output_columns": out_cols,
        "interpret": interpret,
    }


def _compile_rate_vs_mean(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """행단위 비율 → 분모 0 제외 → 평균 대비 필터 → 최소 열."""
    numerator = str(data.get("numerator") or data.get("executed_column") or "")
    denominator = str(data.get("denominator") or data.get("budget_column") or "")
    if numerator not in columns or denominator not in columns:
        return {}

    rate_name = str(data.get("rate_name") or "집행률").strip() or "집행률"
    relation = str(data.get("relation") or data.get("compare") or "below").lower()
    if any(tok in relation for tok in ("above", "높", "이상", "초과", "gt")):
        relation = "above"
    else:
        relation = "below"

    label_prefs = [
        c
        for c in ("비용명", "비용명_2", "비목분류", "항목명", "항목")
        if c in columns
    ]
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
        "dimension_columns": [c for c in ("비용명_2", "비용명") if c in columns][:1],
        "output_columns": out_cols,
        "interpret": interpret,
    }

def _compile_find_items(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """세부 항목 조건 탐색: 수치 필터 → (선택) 비중 파생 → 필요 열만 선택 → 정렬."""
    numeric_filters = _sanitize_numeric_filters(
        {"numeric_filters": data.get("numeric_filters") or data.get("conditions") or []},
        columns,
    )
    if not numeric_filters:
        return {}

    label_prefs = [
        c
        for c in (
            "비목분류",
            "비용명_2",
            "비용명",
            "항목명",
            "항목",
        )
        if c in columns
    ]
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
        related_hints = (
            "집행계_합계",
            "집행계_이월집행",
            "집행계_당해집행",
            "실행예산_이월예산",
            "실행예산_당해예산",
            "예산잔액_합계",
            "당년도집행",
            "당해누계",
            "가집행금액",
        )
        related = [c for c in related_hints if c in columns and c not in metric_cols]
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
    rate_name = str(data.get("rate_name") or "집행률")
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


def _sanitize_step(item: dict[str, Any], columns: set[str]) -> AnalysisStep | None:
    op = str(item.get("op") or item.get("operation") or "").strip()
    if op not in SUPPORTED_ANALYSIS_OPS:
        return None

    if op == "annotate_row_types":
        return AnalysisStep(op, {})

    if op == "filter_rows":
        include = item.get("include_row_types") or item.get("include") or ["detail"]
        if isinstance(include, str):
            include = [include]
        include = [str(x) for x in include if str(x) in ROW_TYPES]
        if not include:
            include = ["detail"]
        dim_cols = [
            str(c)
            for c in (item.get("dimension_columns") or [])
            if str(c) in columns
        ]
        column_filters = _sanitize_column_filters(item, columns)
        # 단일 column/values 단축형
        if not column_filters and item.get("column") and item.get("values") is not None:
            column_filters = _sanitize_column_filters(
                {
                    "column_filters": [
                        {"column": item.get("column"), "values": item.get("values")}
                    ]
                },
                columns,
            )
        numeric_filters = _sanitize_numeric_filters(item, columns)
        return AnalysisStep(
            op,
            {
                "include_row_types": include,
                "drop_blank_dimensions": bool(item.get("drop_blank_dimensions", True)),
                "exclude_uncertain": bool(item.get("exclude_uncertain", False)),
                "dimension_columns": dim_cols,
                "column_filters": column_filters,
                "numeric_filters": numeric_filters,
            },
        )

    if op == "select_columns":
        cols = item.get("columns")
        if cols is None and isinstance(item.get("column_renames"), dict):
            cols = list(item["column_renames"].keys())
        if isinstance(cols, str):
            cols = [cols]
        cols = [str(c) for c in (cols or [])]
        renames = item.get("renames") or item.get("column_renames") or {}
        if not isinstance(renames, dict):
            renames = {}
        return AnalysisStep(
            op,
            {
                "columns": cols,
                "renames": {str(k): str(v) for k, v in renames.items() if str(k)},
            },
        )

    if op == "derive_column":
        name = str(item.get("name") or "").strip()
        expr = item.get("expr") or item.get("formula")
        if isinstance(expr, dict) and "type" in expr and len(expr) > 1:
            # {type: ratio, numerator, denominator} → {ratio: [num, den]}
            kind = str(expr.get("type"))
            if kind in SUPPORTED_DERIVE_EXPRS:
                if kind == "ratio":
                    expr = {
                        "ratio": [
                            str(expr.get("numerator") or ""),
                            str(expr.get("denominator") or ""),
                        ]
                    }
                elif kind == "abs":
                    expr = {"abs": [str(expr.get("column") or expr.get("operand") or "")]}
                else:
                    left = str(expr.get("left") or expr.get("numerator") or "")
                    right = str(expr.get("right") or expr.get("denominator") or "")
                    expr = {kind: [left, right]}
        if not name or not isinstance(expr, dict) or not expr:
            return None
        kind = str(next(iter(expr.keys())))
        if kind not in SUPPORTED_DERIVE_EXPRS:
            return None
        operands = expr.get(kind) or []
        if isinstance(operands, str):
            operands = [operands]
        operands = [str(x) for x in operands]
        if kind == "abs" and len(operands) != 1:
            return None
        if kind == "sign_label" and len(operands) not in {1, 2}:
            return None
        if kind in {"diff", "abs_diff", "ratio", "percent_ratio"} and len(operands) != 2:
            return None
        # sign_label 1피연산자는 직전 파생열(차이)일 수 있어 columns 소속을 강제하지 않는다.
        if kind == "sign_label" and len(operands) == 1:
            return AnalysisStep(op, {"name": name, "expr": {kind: operands}})
        if any(opnd not in columns for opnd in operands):
            return None
        return AnalysisStep(op, {"name": name, "expr": {kind: operands}})

    if op == "sort":
        by = item.get("by") or item.get("columns") or []
        if isinstance(by, str):
            by = [by]
        by = [str(x) for x in by]
        if not by:
            return None
        ascending = item.get("ascending", False)
        if isinstance(ascending, bool):
            ascending = [ascending] * len(by)
        elif isinstance(ascending, list):
            ascending = [bool(x) for x in ascending]
            while len(ascending) < len(by):
                ascending.append(False)
            ascending = ascending[: len(by)]
        else:
            ascending = [bool(ascending)] * len(by)
        return AnalysisStep(op, {"by": by, "ascending": ascending})

    if op == "limit":
        try:
            n = max(1, min(100, int(item.get("n") or item.get("limit") or 5)))
        except (TypeError, ValueError):
            n = 5
        return AnalysisStep(op, {"n": n})

    if op == "drop_columns":
        cols = item.get("columns") or []
        if isinstance(cols, str):
            cols = [cols]
        return AnalysisStep(op, {"columns": [str(c) for c in cols]})

    if op == "aggregate":
        group_by = item.get("group_by") or item.get("group_column") or []
        if isinstance(group_by, str):
            group_by = [group_by]
        group_by = [str(c) for c in group_by if str(c) in columns]
        if not group_by:
            return None
        metrics = item.get("metrics") or []
        if not isinstance(metrics, list) or not metrics:
            return None
        include_groups = item.get("include_groups") or item.get("groups") or []
        if isinstance(include_groups, str):
            include_groups = [include_groups]
        prefer = item.get("prefer_subtotals")
        if prefer is None:
            prefer = True
        return AnalysisStep(
            op,
            {
                "group_by": group_by,
                "metrics": metrics,
                "prefer_subtotals": bool(prefer),
                "include_groups": [str(g) for g in include_groups if str(g).strip()],
            },
        )

    if op == "ratio_of_aggregates":
        name = str(item.get("name") or "비율").strip() or "비율"
        formula = item.get("formula") if isinstance(item.get("formula"), dict) else {}
        numerator = str(
            item.get("numerator") or formula.get("numerator") or ""
        )
        denominator = str(
            item.get("denominator") or formula.get("denominator") or ""
        )
        if not numerator or not denominator:
            return None
        # 피연산자는 원본 또는 이전 aggregate 결과 컬럼일 수 있음 — 이름은 유지
        return AnalysisStep(
            op,
            {"name": name, "numerator": numerator, "denominator": denominator},
        )

    if op == "compare_groups":
        group_column = str(item.get("group_column") or "")
        if not group_column:
            return None
        metrics = item.get("metrics") or []
        if isinstance(metrics, str):
            metrics = [metrics]
        groups = item.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        rate_columns = item.get("rate_columns") or []
        if isinstance(rate_columns, str):
            rate_columns = [rate_columns]
        return AnalysisStep(
            op,
            {
                "group_column": group_column,
                "metrics": [str(m) for m in metrics],
                "groups": [str(g) for g in groups if str(g).strip()],
                "rate_columns": [str(c) for c in rate_columns],
            },
        )

    if op == "distribution_summary":
        budget = str(item.get("budget_column") or "")
        executed = str(item.get("executed_column") or "")
        if not budget or not executed:
            return None
        return AnalysisStep(
            op,
            {
                "group_column": str(item.get("group_column") or "") or None,
                "item_column": str(item.get("item_column") or "") or None,
                "budget_column": budget,
                "executed_column": executed,
                "group_value": str(item.get("group_value") or "") or None,
                "zero_threshold": float(item.get("zero_threshold") or 0.0),
            },
        )

    if op == "correlation":
        x_col = str(item.get("x_column") or item.get("column_x") or "")
        y_col = str(item.get("y_column") or item.get("column_y") or "")
        value_cols = item.get("value_columns") or item.get("columns") or []
        if isinstance(value_cols, str):
            value_cols = [value_cols]
        if (not x_col or x_col not in columns) and isinstance(value_cols, list):
            valid = [str(c) for c in value_cols if str(c) in columns]
            if len(valid) >= 2:
                x_col, y_col = valid[0], valid[1]
        if x_col not in columns or y_col not in columns:
            return None
        label_col = str(item.get("label_column") or item.get("item_column") or "")
        methods = item.get("methods") or ["pearson", "spearman"]
        if isinstance(methods, str):
            methods = [methods]
        return AnalysisStep(
            op,
            {
                "x_column": x_col,
                "y_column": y_col,
                "label_column": label_col if label_col in columns else None,
                "methods": [str(m) for m in methods if str(m).strip()]
                or ["pearson", "spearman"],
            },
        )

    if op == "filter_vs_mean":
        # 파생열(집행률 등)일 수 있어 원본 columns 소속은 강제하지 않는다.
        column = str(item.get("column") or item.get("name") or "").strip()
        if not column:
            return None
        relation = str(item.get("relation") or item.get("compare") or "below")
        return AnalysisStep(op, {"column": column, "relation": relation})

    if op == "top_per_group":
        group_col = str(
            item.get("group_column") or item.get("group_by") or item.get("by") or ""
        ).strip()
        value_col = str(
            item.get("value_column")
            or item.get("metric")
            or item.get("metric_column")
            or ""
        ).strip()
        if group_col not in columns or value_col not in columns:
            return None
        try:
            n = int(item.get("n") or item.get("top_n") or item.get("limit") or 1)
        except (TypeError, ValueError):
            n = 1
        ascending = item.get("ascending", False)
        if isinstance(ascending, list):
            ascending = bool(ascending[0]) if ascending else False
        else:
            ascending = bool(ascending)
        return AnalysisStep(
            op,
            {
                "group_column": group_col,
                "value_column": value_col,
                "n": max(1, min(50, n)),
                "ascending": ascending,
            },
        )

    return None


def _sanitize_column_filters(
    item: dict[str, Any],
    columns: set[str],
) -> list[dict[str, Any]]:
    raw = item.get("column_filters") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        col = str(spec.get("column") or "")
        if col not in columns:
            continue
        values = spec.get("values") or []
        if isinstance(values, str):
            values = [values]
        values = [str(v) for v in values if str(v).strip()]
        if values:
            out.append({"column": col, "values": values})
    return out


def _sanitize_numeric_filters(
    item: dict[str, Any],
    columns: set[str],
) -> list[dict[str, Any]]:
    from core.analysis_ops import NUMERIC_FILTER_OPS

    raw = item.get("numeric_filters") or item.get("conditions") or []
    if not isinstance(raw, list):
        return []
    op_aliases = {
        "==": "eq",
        "=": "eq",
        "!=": "ne",
        "<>": "ne",
        ">": "gt",
        ">=": "gte",
        "<": "lt",
        "<=": "lte",
        "eq": "eq",
        "ne": "ne",
        "gt": "gt",
        "gte": "gte",
        "lt": "lt",
        "lte": "lte",
        "equal": "eq",
        "greater": "gt",
        "less": "lt",
    }
    out: list[dict[str, Any]] = []
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        col = str(spec.get("column") or "")
        if col not in columns:
            continue
        raw_op = str(spec.get("op") or spec.get("operator") or "").strip().lower()
        op = op_aliases.get(raw_op, raw_op)
        if op not in NUMERIC_FILTER_OPS:
            continue
        try:
            value = float(spec.get("value"))
        except (TypeError, ValueError):
            continue
        out.append({"column": col, "op": op, "value": value})
    return out
