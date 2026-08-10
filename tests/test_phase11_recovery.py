"""Phase 11: aggregate output alias rewrite + retry repair mode."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_plan_contract import choose_retry_mode, retry_invariant_message
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import (
    format_plan_validation_feedback,
    validate_analysis_plan,
)
from core.schema.row_classify import classify_rows


def test_aggregate_output_alias_sort_select() -> None:
    df = pd.DataFrame({"상품": ["A", "A", "B"], "매출": [1, 2, 9]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {"op": "sort", "by": ["매출_합계"], "ascending": [False]},
                {"op": "limit", "n": 5},
                {"op": "select_columns", "columns": ["상품", "매출_합계"]},
            ]
        },
        available_columns=list(df.columns),
    )
    sort = next(s for s in plan.steps if s.op == "sort")
    assert sort.payload["by"] == ["매출"]
    sel = next(s for s in plan.steps if s.op == "select_columns")
    assert "매출" in sel.payload["columns"]
    assert "매출_합계" not in sel.payload["columns"]
    report = validate_analysis_plan(
        plan,
        classify_rows(df, dimension_columns=["상품"]),
        user_prompt="매출 상위 5개 상품을 알려줘",
    )
    assert report.ok, [i.message for i in report.errors]


def test_aggregate_output_alias_english_sum() -> None:
    df = pd.DataFrame({"customer_id": ["c1", "c1", "c2"], "order_amount": [10, 20, 5]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["customer_id"],
                    "metrics": [{"column": "order_amount", "fn": "sum"}],
                },
                {"op": "sort", "by": ["order_amount_sum"], "ascending": [False]},
                {"op": "limit", "n": 5},
            ]
        },
        available_columns=list(df.columns),
    )
    assert next(s for s in plan.steps if s.op == "sort").payload["by"] == ["order_amount"]


def test_compare_groups_mean_alias() -> None:
    df = pd.DataFrame({"부서": ["A부서", "B부서", "A부서"], "성과점수": [1.0, 2.0, 3.0]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "성과점수", "fn": "mean"}],
                },
                {
                    "op": "compare_groups",
                    "group_column": "부서",
                    "groups": ["A부서", "B부서"],
                    "metrics": ["성과점수_mean"],
                },
            ]
        },
        available_columns=list(df.columns),
    )
    cmp = next(s for s in plan.steps if s.op == "compare_groups")
    assert cmp.payload["metrics"] == ["성과점수"]
    report = validate_analysis_plan(plan, classify_rows(df, dimension_columns=["부서"]))
    assert report.ok, [i.message for i in report.errors]


def test_choose_retry_mode_repair_vs_regenerate() -> None:
    assert choose_retry_mode(["missing_sort_column", "missing_select_column"]) == "repair"
    assert choose_retry_mode(["entity_ranking_missing_aggregate"]) == "regenerate"
    assert "X_합계" in retry_invariant_message(["missing_sort_column"])


def test_repair_feedback_contains_mode_and_invariant() -> None:
    df = pd.DataFrame({"상품": ["A"], "매출": [1]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {"op": "sort", "by": ["매출"], "ascending": [False]},
            ]
        },
        available_columns=list(df.columns),
    )
    # force a missing sort target after sanitize
    sort = next(s for s in plan.steps if s.op == "sort")
    sort.payload["by"] = ["없는컬럼"]
    report = validate_analysis_plan(plan, classify_rows(df, dimension_columns=["상품"]))
    assert not report.ok
    feedback = format_plan_validation_feedback(
        report, previous_plan=plan.to_dict(), attempt=1, failure_category="aggregate_output_alias"
    )
    joined = "\n".join(feedback)
    assert "retry_mode:" in joined
    assert "Failure category:" in joined
    assert "Invariant:" in joined
