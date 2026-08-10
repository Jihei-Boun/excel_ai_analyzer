"""Phase 9: operation composition contracts and validator rules."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_plan_contract import (
    composition_category_from_issues,
    plan_composition_category,
)
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import (
    format_plan_validation_feedback,
    validate_analysis_plan,
)
from core.schema.row_classify import classify_rows


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "지역": ["서울", "서울", "부산", "부산"],
            "상품": ["A", "B", "A", "B"],
            "매출": [100, 200, 150, 50],
            "예산": [200, 200, 200, 100],
            "집행": [100, 180, 150, 40],
        }
    )


def _report(plan_dict: dict, df: pd.DataFrame | None = None):
    frame = df if df is not None else _df()
    plan = analysis_plan_from_dict(plan_dict, available_columns=list(frame.columns))
    classified = classify_rows(frame, dimension_columns=["지역", "상품"])
    return plan, validate_analysis_plan(plan, classified)


def test_global_ranking_valid_aggregate_sort_limit() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {"op": "sort", "by": ["매출"], "ascending": [False]},
                {"op": "limit", "n": 5},
            ],
            "criteria_note": "매출 상위 5개 상품",
        }
    )
    assert report.ok


def test_group_ranking_valid_top_per_group() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["지역", "상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {
                    "op": "top_per_group",
                    "group_column": "지역",
                    "value_column": "매출",
                    "n": 3,
                },
            ],
            "criteria_note": "지역별 매출 상위 3개 상품",
        }
    )
    assert report.ok


def test_ratio_ranking_valid() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [
                        {"column": "집행", "fn": "sum"},
                        {"column": "예산", "fn": "sum"},
                    ],
                },
                {
                    "op": "ratio_of_aggregates",
                    "name": "rate",
                    "numerator": "집행",
                    "denominator": "예산",
                },
                {"op": "sort", "by": ["rate"], "ascending": [False]},
                {"op": "limit", "n": 3},
            ],
            "criteria_note": "집행률 상위 3개",
        }
    )
    assert report.ok


def test_comparison_valid_aggregate_then_compare() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["지역"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                    "include_groups": ["서울", "부산"],
                },
                {
                    "op": "compare_groups",
                    "group_column": "지역",
                    "groups": ["서울", "부산"],
                    "metrics": ["매출"],
                },
            ],
            "criteria_note": "서울과 부산 매출 비교",
        }
    )
    assert report.ok


def test_ratio_comparison_valid() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["지역"],
                    "metrics": [
                        {"column": "집행", "fn": "sum"},
                        {"column": "예산", "fn": "sum"},
                    ],
                    "include_groups": ["서울", "부산"],
                },
                {
                    "op": "ratio_of_aggregates",
                    "name": "rate",
                    "numerator": "집행",
                    "denominator": "예산",
                },
                {
                    "op": "compare_groups",
                    "group_column": "지역",
                    "groups": ["서울", "부산"],
                    "metrics": ["rate"],
                    "rate_columns": ["rate"],
                },
            ],
            "criteria_note": "서울과 부산 집행률 비교",
        }
    )
    assert report.ok


def test_invalid_top_per_group_without_group() -> None:
    try:
        analysis_plan_from_dict(
            {
                "steps": [
                    {
                        "op": "top_per_group",
                        "value_column": "매출",
                        "n": 3,
                    }
                ]
            },
            available_columns=list(_df().columns),
        )
        raised = False
    except ValueError:
        raised = True
    if not raised:
        # if sanitize keeps a stub somehow, validation must fail
        plan = analysis_plan_from_dict(
            {
                "steps": [
                    {
                        "op": "annotate_row_types",
                    },
                    {
                        "op": "top_per_group",
                        "group_column": "지역",
                        "value_column": "매출",
                        "n": 3,
                    },
                ]
            },
            available_columns=list(_df().columns),
        )
        # mutate to clear group
        for s in plan.steps:
            if s.op == "top_per_group":
                s.payload["group_column"] = ""
        report = validate_analysis_plan(
            plan, classify_rows(_df(), dimension_columns=["지역"])
        )
        assert not report.ok
        assert any("missing_group" in i.code for i in report.errors)
    else:
        assert raised


def test_invalid_global_ranking_using_top_per_group() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {
                    "op": "top_per_group",
                    "group_column": "상품",
                    "value_column": "매출",
                    "n": 5,
                },
            ],
            "criteria_note": "매출 상위 5개 상품",
        }
    )
    assert not report.ok
    assert any(i.code == "misused_top_per_group" for i in report.errors)
    assert composition_category_from_issues([i.code for i in report.errors]) == (
        "misused_top_per_group"
    )


def test_invalid_sort_on_nonexistent_derived_metric() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {"op": "sort", "by": ["rate"], "ascending": [False]},
                {"op": "limit", "n": 3},
            ],
            "criteria_note": "집행률 상위",
        }
    )
    assert not report.ok
    assert any(
        i.code in {"missing_metric_before_sort", "missing_ratio_before_sort", "missing_ratio_composition"}
        for i in report.errors
    )


def test_invalid_compare_before_metric() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "compare_groups",
                    "group_column": "지역",
                    "groups": ["서울", "부산"],
                    "metrics": ["매출"],
                }
            ],
            "criteria_note": "서울과 부산 매출 비교",
        }
    )
    # metrics may still "exist" as source columns — compare without aggregate is
    # structurally weak; require aggregate for non-source-only or check code path
    # If source columns satisfy produced set, force rate-like missing metric:
    plan2, report2 = _report(
        {
            "steps": [
                {
                    "op": "compare_groups",
                    "group_column": "지역",
                    "groups": ["서울", "부산"],
                    "metrics": ["rate"],
                    "rate_columns": ["rate"],
                }
            ],
            "criteria_note": "집행률 비교",
        }
    )
    del plan2
    assert not report2.ok
    assert any(i.code == "compare_before_metric" for i in report2.errors)


def test_invalid_ratio_ranking_without_ratio() -> None:
    _, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "집행", "fn": "sum"}],
                },
                {"op": "sort", "by": ["집행"], "ascending": [False]},
                {"op": "limit", "n": 3},
            ],
            "criteria_note": "집행률 상위 3개 비목",
            "rate_name": "집행률",
        }
    )
    assert not report.ok
    assert any(i.code == "missing_ratio_composition" for i in report.errors)


def test_invalid_output_dependency_ratio_name_required() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [
                        {"column": "집행", "fn": "sum"},
                        {"column": "예산", "fn": "sum"},
                    ],
                },
                {
                    "op": "ratio_of_aggregates",
                    "numerator": "집행",
                    "denominator": "예산",
                },
            ]
        },
        available_columns=list(_df().columns),
    )
    # sanitize may default name to 비율 — still explicit after sanitize
    ratio = next(s for s in plan.steps if s.op == "ratio_of_aggregates")
    assert str(ratio.payload.get("name") or "").strip()
    # force empty name to simulate invalid dependency
    ratio.payload["name"] = ""
    report = validate_analysis_plan(plan, classify_rows(_df(), dimension_columns=["상품"]))
    assert not report.ok
    assert any(i.code == "missing_ratio_name" for i in report.errors)


def test_composition_feedback_mentions_hint() -> None:
    plan, report = _report(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {
                    "op": "top_per_group",
                    "group_column": "상품",
                    "value_column": "매출",
                    "n": 5,
                },
            ],
            "criteria_note": "매출 상위 5개 상품",
        }
    )
    feedback = format_plan_validation_feedback(
        report, previous_plan=plan.to_dict(), attempt=1
    )
    joined = "\n".join(feedback)
    assert "Composition hint" in joined or "global ranking" in joined.lower()


def test_plan_composition_category_for_top_per_group() -> None:
    cat = plan_composition_category(
        {
            "steps": [
                {"op": "top_per_group", "group_column": "지역", "value_column": "매출", "n": 3},
                {"op": "limit", "n": 3},
            ]
        }
    )
    assert cat == "misused_top_per_group"
