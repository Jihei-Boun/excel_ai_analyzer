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
    }
)

SUPPORTED_DERIVE_EXPRS = frozenset({"diff", "abs", "abs_diff", "ratio"})

ROW_TYPES = frozenset({"detail", "subtotal", "total", "footer", "blank"})

META_COLUMNS = ("_row_type", "_row_type_confidence", "_row_type_reasons")


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
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "criteria_note": self.criteria_note,
            "dimension_columns": list(self.dimension_columns),
            "output_columns": list(self.output_columns),
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
            if step.op != "derive_column":
                continue
            name = str(step.payload.get("name") or "")
            expr = step.payload.get("expr") or {}
            if not name or not isinstance(expr, dict) or not expr:
                continue
            kind = next(iter(expr.keys()))
            operands = expr.get(kind) or []
            if isinstance(operands, str):
                operands = [operands]
            out.append((name, str(kind), [str(x) for x in operands]))
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

    return AnalysisPlan(
        steps=steps,
        criteria_note=note,
        dimension_columns=dim_cols,
        output_columns=out_cols,
        raw=dict(data),
    )


def _is_derived_name(name: str, steps: list[AnalysisStep]) -> bool:
    return any(
        s.op == "derive_column" and str(s.payload.get("name") or "") == name for s in steps
    )


def _compile_high_level(data: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """top_n_difference 등 고수준 operation을 원자 steps로 펼친다."""
    operation = str(data.get("operation") or "").strip()
    if operation not in {"top_n_difference", "rank_difference", "difference_topn"}:
        return {}

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
    # 중복 제거, 존재하는 것만
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
    }


def _sanitize_step(item: dict[str, Any], columns: set[str]) -> AnalysisStep | None:
    op = str(item.get("op") or "").strip()
    if op not in SUPPORTED_ANALYSIS_OPS:
        return None

    if op == "annotate_row_types":
        return AnalysisStep(op, {})

    if op == "filter_rows":
        include = item.get("include_row_types") or ["detail"]
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
        return AnalysisStep(
            op,
            {
                "include_row_types": include,
                "drop_blank_dimensions": bool(item.get("drop_blank_dimensions", True)),
                "exclude_uncertain": bool(item.get("exclude_uncertain", False)),
                "dimension_columns": dim_cols,
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
        # 파생 컬럼명은 inventory에 없을 수 있음 — 실행 시 존재 검사
        return AnalysisStep(
            op,
            {
                "columns": cols,
                "renames": {str(k): str(v) for k, v in renames.items() if str(k)},
            },
        )

    if op == "derive_column":
        name = str(item.get("name") or "").strip()
        expr = item.get("expr")
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
        if kind in {"diff", "abs_diff", "ratio"} and len(operands) != 2:
            return None
        if any(opnd not in columns and not opnd for opnd in operands):
            # 피연산자는 기존 컬럼이어야 함
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

    return None
