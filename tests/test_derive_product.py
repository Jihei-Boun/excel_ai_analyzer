"""Row-wise product: the executor can multiply columns on each row.

The planner prompt does not offer it yet. One added prompt line fixed two
multiplication questions but changed unrelated first plans with
qwen2.5-coder:32b (heldout4: two more silent wrong answers), so the line waits
for a model comparison.
"""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan
from core.analysis.plan_explain import explain_plan


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "품목": ["A", "B", "C", "소계"],
            "수량": [3, 5, None, 8],
            "단가": [1_000, 250, 400, None],
            "메모": ["x", "y", "z", None],
        }
    )


def _run(expr: dict, name: str | None = "금액", extra: list[dict] | None = None):
    df = _df()
    derive = {"op": "derive_column", "expr": expr}
    if name is not None:
        derive["name"] = name
    steps = [{"op": "annotate_row_types"}, {"op": "filter_rows", "include_row_types": ["detail"]}, derive]
    plan = analysis_plan_from_dict({"steps": steps + (extra or [])}, available_columns=list(df.columns))
    report = validate_analysis_plan(plan, df, profile_name="generic")
    return plan, report, df


def test_product_multiplies_each_row_and_keeps_blanks_blank() -> None:
    plan, report, df = _run({"product": ["수량", "단가"]})
    assert report.ok, [i.message for i in report.issues]
    out, _ = execute_analysis_plan(df, plan)
    assert list(out["금액"][:2]) == [3_000, 1_250]
    assert pd.isna(out.loc[2, "금액"])  # a blank quantity is not treated as 1


def test_product_then_group_total() -> None:
    plan, report, df = _run(
        {"product": ["수량", "단가"]},
        extra=[{"op": "aggregate", "group_by": [], "metrics": [{"column": "금액", "fn": "sum"}]}],
    )
    assert report.ok, [i.message for i in report.issues]
    out, _ = execute_analysis_plan(df, plan)
    assert float(out["금액"].iloc[0]) == 4_250


def test_multiply_aliases_and_default_name() -> None:
    for alias in ("multiply", "mul", "times"):
        plan, report, _ = _run({alias: ["수량", "단가"]}, name=None)
        assert report.ok
        step = next(s for s in plan.steps if s.op == "derive_column")
        assert step.payload == {"name": "수량×단가", "expr": {"product": ["수량", "단가"]}}


def test_product_needs_numeric_operands_and_two_of_them() -> None:
    _, report, _ = _run({"product": ["수량", "메모"]})
    assert "non_numeric_derive_operand" in {i.code for i in report.errors}
    plan, _, _ = _run({"product": ["수량"]})
    assert not any(s.op == "derive_column" for s in plan.steps)


def test_product_is_explained() -> None:
    plan, _, _ = _run({"product": ["수량", "단가"]})
    assert explain_plan(plan) == "행마다 금액 = 수량 × 단가"
