"""Phase 8: Planner contract, compile resilience, required fn, duplicate signature."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_plan_contract import (
    normalize_plan_signature,
    planner_failure_reason,
)
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan
from core.analysis.analysis_executor import execute_analysis_plan
from core.schema.row_classify import classify_rows


def test_aggregate_metric_alias_and_kv_shape() -> None:
    plan = analysis_plan_from_dict(
        {
            "operation": "aggregate",
            "group_by": ["카테고리"],
            "metrics": [{"매출액_합계": "sum"}],
        },
        available_columns=["카테고리", "매출액"],
    )
    agg = next(s for s in plan.steps if s.op == "aggregate")
    assert agg.payload["metrics"] == [{"column": "매출액", "fn": "sum"}]


def test_aggregate_missing_fn_rejected() -> None:
    df = pd.DataFrame({"부서": ["A"], "연봉": [1]})
    try:
        analysis_plan_from_dict(
            {
                "steps": [
                    {
                        "op": "aggregate",
                        "group_by": ["부서"],
                        "metrics": [{"column": "연봉"}],
                    }
                ]
            },
            available_columns=list(df.columns),
        )
        raised = False
    except ValueError:
        raised = True
    # sanitize drops metrics without fn → empty plan OR validation error
    if not raised:
        plan = analysis_plan_from_dict(
            {
                "steps": [
                    {
                        "op": "annotate_row_types",
                    },
                    {
                        "op": "aggregate",
                        "group_by": ["부서"],
                        "metrics": [{"column": "연봉", "fn": "sum"}],
                    },
                ]
            },
            available_columns=list(df.columns),
        )
        # explicit missing fn path via validate
        bad = analysis_plan_from_dict(
            {
                "steps": [
                    {
                        "op": "aggregate",
                        "group_by": ["부서"],
                        "metrics": [{"column": "연봉", "fn": "sum"}],
                    }
                ]
            },
            available_columns=list(df.columns),
        )
        bad.steps[0].payload["metrics"] = [{"column": "연봉"}]
        report = validate_analysis_plan(bad, classify_rows(df, dimension_columns=["부서"]))
        assert not report.ok
        assert any(i.code == "missing_aggregation_fn" for i in report.errors)
    else:
        assert raised


def test_group_mean_via_fake_count_denominator() -> None:
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["A", "B"],
            "numerator": "연봉",
            "denominator": "count",
            "rate_name": "부서별 평균 연봉",
            "criteria_note": "평균 연봉",
        },
        available_columns=["부서", "연봉"],
    )
    agg = next(s for s in plan.steps if s.op == "aggregate")
    assert agg.payload["metrics"] == [{"column": "연봉", "fn": "mean"}]


def test_find_items_mean_redirects_to_filter_vs_mean() -> None:
    plan = analysis_plan_from_dict(
        {
            "operation": "find_items",
            "numeric_filters": [
                {"column": "temperature", "op": "gt", "value": "mean(temperature)"}
            ],
        },
        available_columns=["device_id", "temperature"],
    )
    assert any(s.op == "filter_vs_mean" for s in plan.steps)


def test_plan_signature_duplicate_detection() -> None:
    a = {"operation": "aggregate", "group_by": ["x"], "metrics": [{"column": "y", "fn": "sum"}]}
    b = {"metrics": [{"fn": "sum", "column": "y"}], "group_by": ["x"], "operation": "aggregate"}
    assert normalize_plan_signature(a) == normalize_plan_signature(b)


def test_planner_failure_reason_codes() -> None:
    assert (
        planner_failure_reason(
            "실행 가능한 분석 step이 없습니다. For operation=aggregate include group_by"
        )
        == "wrong_operation_shape"
    )
