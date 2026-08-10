"""Phase 10: residual composition fixes — nested high-level, compare metrics, grain, filters."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_plan_builder import build_planner_column_inventory
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan
from core.schema.row_classify import classify_rows


def test_nested_find_items_in_steps_expands() -> None:
    df = pd.DataFrame({"재고수량": [1, 5], "안전재고": [3, 2], "상품코드": ["A", "B"]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "operation": "find_items",
                    "numeric_filters": [
                        {"left_column": "재고수량", "op": "lt", "right_column": "안전재고"}
                    ],
                    "output_columns": ["상품코드", "재고수량", "안전재고"],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    assert any(s.op == "filter_rows" for s in plan.steps)
    filt = next(s for s in plan.steps if s.op == "filter_rows")
    assert any(f.get("right_column") == "안전재고" for f in filt.payload["numeric_filters"])


def test_nested_group_comparison_in_steps_expands() -> None:
    df = pd.DataFrame({"지역": ["서울", "부산"], "매출": [10, 20]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "operation": "group_comparison",
                    "group_column": "지역",
                    "groups": ["서울", "부산"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    ops = [s.op for s in plan.steps]
    assert "aggregate" in ops
    assert "compare_groups" in ops
    cmp = next(s for s in plan.steps if s.op == "compare_groups")
    assert cmp.payload["metrics"] == ["매출"]


def test_compare_groups_dict_metrics_sanitized() -> None:
    df = pd.DataFrame({"지역": ["서울", "부산", "서울"], "매출": [1, 2, 3]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["지역"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {
                    "op": "compare_groups",
                    "group_column": "지역",
                    "groups": ["서울", "부산"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
            ]
        },
        available_columns=list(df.columns),
    )
    cmp = next(s for s in plan.steps if s.op == "compare_groups")
    assert cmp.payload["metrics"] == ["매출"]
    report = validate_analysis_plan(
        plan,
        classify_rows(df, dimension_columns=["지역"]),
        user_prompt="서울과 부산 매출 비교해줘",
    )
    assert report.ok, [i.message for i in report.errors]


def test_nested_rate_vs_mean_expands_filter() -> None:
    df = pd.DataFrame(
        {
            "항목": ["A", "B"],
            "집행계_합계": [10, 90],
            "실행예산_합계": [100, 100],
        }
    )
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "operation": "aggregate",
                    "group_by": ["항목"],
                    "metrics": [
                        {"column": "집행계_합계", "fn": "sum"},
                        {"column": "실행예산_합계", "fn": "sum"},
                    ],
                },
                {
                    "operation": "rate_vs_mean",
                    "numerator": "집행계_합계",
                    "denominator": "실행예산_합계",
                    "relation": "above",
                },
            ]
        },
        available_columns=list(df.columns),
    )
    assert any(s.op == "filter_vs_mean" for s in plan.steps)


def test_embedded_gt_zero_numeric_filter() -> None:
    df = pd.DataFrame({"수_량": [0, 10, 5]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "numeric_filters": [{"column": "수_량", "value": ">0"}],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    filt = plan.steps[0].payload["numeric_filters"][0]
    assert filt["op"] == "gt"
    assert filt["value"] == 0.0


def test_column_filter_gt_zero_promoted() -> None:
    df = pd.DataFrame({"수_량": [0, 10]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "column_filters": [{"column": "수_량", "values": [">0"]}],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    assert plan.steps[0].payload["column_filters"] == []
    assert plan.steps[0].payload["numeric_filters"][0]["op"] == "gt"


def test_entity_ranking_missing_aggregate_flagged() -> None:
    df = pd.DataFrame({"상품": ["A", "A", "B"], "매출": [1, 2, 9]})
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "sort", "by": ["매출"], "ascending": [False]},
                {"op": "limit", "n": 5},
            ],
            "criteria_note": "매출 상위 5개 상품",
        },
        available_columns=list(df.columns),
    )
    report = validate_analysis_plan(
        plan,
        classify_rows(df, dimension_columns=["상품"]),
        user_prompt="매출 상위 5개 상품을 알려줘",
    )
    assert not report.ok
    assert any(i.code == "entity_ranking_missing_aggregate" for i in report.errors)


def test_inventory_unique_ratio_grain_hint() -> None:
    df = pd.DataFrame(
        {
            "상품": ["A"] * 5 + ["B"] * 5 + ["C"] * 2,
            "order_id": list(range(1, 13)),
            "매출": list(range(1, 13)),
        }
    )
    inv = build_planner_column_inventory(df)
    by_name = {e["name"]: e for e in inv}
    assert "unique_ratio" in by_name["상품"]
    assert by_name["상품"]["unique_ratio"] < 0.5
    assert by_name["상품"].get("grain_hint") == "repeated_entity_candidate"
    assert by_name["order_id"].get("grain_hint") == "row_id_like"


def test_stockout_filter_vs_mean_rejected_for_threshold() -> None:
    df = pd.DataFrame(
        {"상품코드": ["P1", "P2"], "재고수량": [1, 0], "안전재고": [5, 2]}
    )
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "filter_vs_mean", "column": "재고수량", "relation": "below"},
            ]
        },
        available_columns=list(df.columns),
    )
    report = validate_analysis_plan(
        plan,
        classify_rows(df, dimension_columns=["상품코드"]),
        user_prompt="품절 위험 상품을 알려줘",
    )
    assert not report.ok
    assert any(i.code == "column_vs_column_misclassified" for i in report.errors)
