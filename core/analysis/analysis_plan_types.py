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
    {"diff", "abs", "abs_diff", "ratio", "percent_ratio", "sign_label", "sum", "mean"}
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
    # 모든 step과 결과 검증이 끝난 뒤 최종 표의 행·열을 바꾼다.
    transpose: bool = False
    transpose_label_column: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "steps": [step.to_dict() for step in self.steps],
            "criteria_note": self.criteria_note,
            "dimension_columns": list(self.dimension_columns),
            "output_columns": list(self.output_columns),
            "interpret": self.interpret,
            "footer_labels": list(self.footer_labels),
        }
        if self.transpose:
            out["transpose"] = True
            if self.transpose_label_column:
                out["transpose_label_column"] = self.transpose_label_column
        return out

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
    profile_name: str | None = None,
) -> AnalysisPlan:
    """LLM JSON을 sanitize·컴파일하여 AnalysisPlan으로 만든다."""
    from core.profile_loader import use_profile

    if not isinstance(data, dict):
        raise ValueError("분석 계획이 객체가 아닙니다.")

    with use_profile(profile_name):
        return _analysis_plan_from_dict_inner(data, available_columns=available_columns)


def _analysis_plan_from_dict_inner(
    data: dict[str, Any],
    *,
    available_columns: list[str],
) -> AnalysisPlan:
    from core.analysis.analysis_plan_compile import _compile_high_level, expand_steps_high_level_ops
    from core.analysis.analysis_plan_sanitize import _sanitize_step

    columns = {str(c) for c in available_columns}
    compiled = _compile_high_level(data, columns)
    raw_steps = compiled.get("steps") or data.get("steps") or []
    if not isinstance(raw_steps, list):
        raise ValueError("steps는 배열이어야 합니다.")
    if not raw_steps:
        raw_steps = _top_level_derive_steps(data)
    raw_steps, transpose, transpose_label = _split_transpose(data, raw_steps)
    if transpose and not raw_steps and not compiled.get("steps"):
        # 표 전체 전치: 원본 행을 그대로 두고 마지막에 바꾼다.
        raw_steps = [{"op": "annotate_row_types"}]

    # LLM often nests high-level forms inside steps[] with key `operation`
    # (e.g. steps:[{operation:find_items,...}]). Expand those generically.
    raw_steps = expand_steps_high_level_ops(raw_steps, columns)

    steps: list[AnalysisStep] = []
    known_columns = set(columns)
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        step = _sanitize_step(item, known_columns)
        if step is not None:
            steps.append(step)
            if step.op == "derive_column":
                derived = str(step.payload.get("name") or "").strip()
                if derived:
                    known_columns.add(derived)
            elif step.op == "ratio_of_aggregates":
                derived = str(step.payload.get("name") or "").strip()
                if derived:
                    known_columns.add(derived)

    if not steps:
        op = str(data.get("operation") or compiled.get("operation") or "").strip()
        hint = ""
        if op in {"aggregate", "groupby", "group_aggregate"}:
            hint = (
                " For operation=aggregate include group_by ([] for one overall row) and "
                "metrics:[{column, fn}] with fn in sum|mean|median|min|max|count."
            )
        elif op in {"find_items", "item_filter", "condition_select"}:
            hint = (
                " For operation=find_items provide numeric_filters as "
                "[{column,op,value}] or [{left_column,op,right_column}]."
            )
        raise ValueError("실행 가능한 분석 step이 없습니다." + hint)

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
        transpose=transpose,
        transpose_label_column=transpose_label,
    )


def _top_level_derive_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    """``{"operation":"derive_column", name, expr, select_columns?, column_filters?}`` 꼴을 펼친다.

    행마다 열을 계산하는 요청이라 상세 행만 남긴 뒤 계산하고, 열 선택이 있으면 붙인다.
    """
    if str(data.get("operation") or "").strip() != "derive_column":
        return []
    derive = {"op": "derive_column", "name": data.get("name"), "expr": data.get("expr")}
    filter_step: dict[str, Any] = {"op": "filter_rows", "include_row_types": ["detail"]}
    if data.get("column_filters"):
        filter_step["column_filters"] = data.get("column_filters")
    steps: list[dict[str, Any]] = [{"op": "annotate_row_types"}, filter_step, derive]
    selected = data.get("select_columns") or data.get("output_columns")
    if isinstance(selected, list) and selected:
        cols = [str(c) for c in selected]
        name = str(data.get("name") or "").strip()
        if name and name not in cols:
            cols.append(name)
        steps.append({"op": "select_columns", "columns": cols})
    return steps


_TRANSPOSE_OPS = frozenset({"transpose", "swap_axes", "pivot_axes"})


def _split_transpose(
    data: dict[str, Any],
    raw_steps: list[Any],
) -> tuple[list[Any], bool, str]:
    """transpose step(마지막만 허용)과 최상위 transpose 플래그를 떼어 낸다."""
    flag = data.get("transpose")
    transpose = flag is True or str(flag).strip().lower() in {"true", "1", "yes"}
    label = str(data.get("transpose_label_column") or "").strip()
    kept: list[Any] = []
    for index, item in enumerate(raw_steps):
        op = ""
        if isinstance(item, dict):
            op = str(item.get("op") or item.get("operation") or "").strip().lower()
        if op not in _TRANSPOSE_OPS:
            kept.append(item)
            continue
        if index != len(raw_steps) - 1:
            raise ValueError(
                "transpose must be the LAST step (it swaps rows and columns of the final table)."
            )
        transpose = True
        label = str(item.get("label_column") or label).strip()
    if str(data.get("operation") or "").strip().lower() in _TRANSPOSE_OPS:
        transpose = True
    return kept, transpose, label


def _is_derived_name(name: str, steps: list[AnalysisStep]) -> bool:
    for s in steps:
        if s.op == "derive_column" and str(s.payload.get("name") or "") == name:
            return True
        if s.op == "ratio_of_aggregates" and str(s.payload.get("name") or "") == name:
            return True
    return False
