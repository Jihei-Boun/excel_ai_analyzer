"""Phase 3: Plan Validator · Result Validator · Planner retry."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_pipeline import try_analysis_pipeline
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import (
    format_plan_validation_feedback,
    validate_analysis_plan,
)
from core.analysis.analysis_result_validate import validate_analysis_result
from core.schema.row_classify import classify_rows


def _sales_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "부서": ["영업", "연구", "영업", "연구"],
            "상품": ["A", "B", "C", "D"],
            "매출": [100, 200, 50, 80],
            "비용": [40, 60, 20, 30],
        }
    )


def _classified(df: pd.DataFrame):
    return classify_rows(df, dimension_columns=["부서", "상품"])


# ---------------------------------------------------------------------------
# Valid plans
# ---------------------------------------------------------------------------


def test_plan_valid_numeric_aggregate() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                },
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert report.ok, report.summary_text()


def test_plan_valid_ratio() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [
                        {"column": "매출", "fn": "sum"},
                        {"column": "비용", "fn": "sum"},
                    ],
                    "prefer_subtotals": False,
                },
                {
                    "op": "ratio_of_aggregates",
                    "name": "비율",
                    "numerator": "비용",
                    "denominator": "매출",
                },
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert report.ok, report.summary_text()


def test_plan_valid_group_comparison() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
            "prefer_subtotals": True,
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert report.ok, report.summary_text()


def test_plan_valid_correlation() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "operation": "correlation",
            "x_column": "매출",
            "y_column": "비용",
            "label_column": "상품",
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert report.ok, report.summary_text()


def test_plan_valid_filter() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "부서", "values": ["영업"]}],
                }
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert report.ok, report.summary_text()


# ---------------------------------------------------------------------------
# Invalid plans
# ---------------------------------------------------------------------------


def test_plan_invalid_nonexistent_column() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    # inject missing metric after sanitize by mutating
    plan.steps[0].payload["metrics"] = [{"column": "존재하지않는매출", "fn": "sum"}]
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "missing_metric_column" for i in report.errors)


def test_plan_invalid_string_mean() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["매출"],
                    "metrics": [{"column": "부서", "fn": "mean"}],
                }
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "non_numeric_aggregate" for i in report.errors)


def test_plan_invalid_nonexistent_group_value() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "없는부서"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
        },
        available_columns=list(_sales_df().columns),
    )
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "missing_group_value" for i in report.errors)


def test_plan_invalid_denominator_all_zero() -> None:
    raw = _sales_df()
    raw["매출"] = 0
    df = _classified(raw)
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [
                        {"column": "매출", "fn": "sum"},
                        {"column": "비용", "fn": "sum"},
                    ],
                },
                {
                    "op": "ratio_of_aggregates",
                    "name": "비율",
                    "numerator": "비용",
                    "denominator": "매출",
                },
            ]
        },
        available_columns=list(raw.columns),
    )
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "denominator_all_zero" for i in report.errors)


def test_plan_invalid_detail_subtotal_double_count() -> None:
    raw = pd.DataFrame(
        {
            "부서": ["영업", "영업", "소 계", "연구"],
            "매출": [100, 50, 150, 80],
        }
    )
    df = classify_rows(raw, dimension_columns=["부서"])
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail", "subtotal"],
                },
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
            ]
        },
        available_columns=list(raw.columns),
    )
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "detail_subtotal_double_count" for i in report.errors)


def test_plan_invalid_filter_operator() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "numeric_filters": [{"column": "매출", "op": "gt", "value": 10}],
                }
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    plan.steps[0].payload["numeric_filters"] = [
        {"column": "매출", "op": "regex_match", "value": 10}
    ]
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "unsupported_filter_op" for i in report.errors)


def test_plan_invalid_sort_target() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "sort", "by": ["매출"], "ascending": [False]},
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    plan.steps[0].payload["by"] = ["없는비율"]
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "missing_sort_column" for i in report.errors)


def test_plan_invalid_operation_dependency() -> None:
    """sort(비율)인데 비율을 만들지 않음."""
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                },
                {"op": "sort", "by": ["비율"], "ascending": [False]},
                {"op": "limit", "n": 5},
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    # sanitize may drop sort if column unknown — force payload
    if not any(s.op == "sort" for s in plan.steps):
        from core.analysis.analysis_plan_types import AnalysisStep

        plan.steps.append(AnalysisStep("sort", {"by": ["비율"], "ascending": [False]}))
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "missing_sort_column" for i in report.errors)


def test_plan_invalid_limit_non_positive() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "limit", "n": 5},
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    plan.steps[0].payload["n"] = 0
    report = validate_analysis_plan(plan, df)
    assert not report.ok
    assert any(i.code == "invalid_limit" for i in report.errors)


def test_plan_feedback_includes_candidates_not_orders() -> None:
    df = _classified(_sales_df())
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    plan.steps[0].payload["metrics"] = [{"column": "매출액", "fn": "sum"}]
    report = validate_analysis_plan(plan, df)
    feedback = format_plan_validation_feedback(
        report,
        previous_plan=plan.to_dict(),
        df=_sales_df(),
        profile_name="generic",
    )
    text = "\n".join(feedback)
    assert "Previous invalid plan:" in text or "Previous plan:" in text
    assert "Validation errors:" in text
    assert "Use " not in text  # 강제 지시 금지
    assert "후보" in text or "candidates" in text.lower() or "Available" in text


# ---------------------------------------------------------------------------
# Result validator skeleton
# ---------------------------------------------------------------------------


def test_result_validator_empty_and_nan() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        },
        available_columns=list(_sales_df().columns),
    )
    empty = validate_analysis_result(pd.DataFrame(), plan)
    assert not empty.ok
    assert any(i.code == "empty_dataframe" for i in empty.errors)

    nan_df = pd.DataFrame({"부서": ["영업"], "매출": [float("nan")]})
    nan_report = validate_analysis_result(nan_df, plan)
    assert not nan_report.ok
    assert any(i.code == "all_nan_result" for i in nan_report.errors)


# ---------------------------------------------------------------------------
# Planner retry
# ---------------------------------------------------------------------------


def test_planner_retry_invalid_then_valid() -> None:
    df = _sales_df()
    calls = {"n": 0}

    def fake_chat_json(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "operation": "group_comparison",
                "group_column": "부서",
                "groups": ["영업", "없는부서"],
                "numerator": "비용",
                "denominator": "매출",
                "rate_name": "비율",
            }
        return {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["영업", "연구"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
            "prefer_subtotals": True,
            "interpret": False,
        }

    result = try_analysis_pipeline(
        "영업과 연구를 비교해줘",
        df,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        max_retries=2,
        chat_json_fn=fake_chat_json,
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is not None
    assert calls["n"] >= 2
    assert isinstance(result.dataframe, pd.DataFrame)
    assert not result.dataframe.empty


def test_planner_retry_exhausted_returns_none() -> None:
    df = _sales_df()

    def always_bad(*_a, **_k):
        return {
            "operation": "group_comparison",
            "group_column": "부서",
            "groups": ["없는A", "없는B"],
            "numerator": "비용",
            "denominator": "매출",
            "rate_name": "비율",
        }

    result = try_analysis_pipeline(
        "없는 그룹을 비교해줘",
        df,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        max_retries=2,
        chat_json_fn=always_bad,
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is None


def test_soft_prefs_do_not_rewrite_existing_columns() -> None:
    """Phase 3B: 기본 경로에서는 존재하는 LLM 컬럼을 preferred로 덮어쓰지 않는다."""
    from core.analysis.analysis_plan_builder import build_analysis_plan

    cols = [
        "비목분류",
        "계획예산",
        "당년도집행",
        "실행예산_합계",
        "집행계_합계",
    ]
    wrong = {
        "operation": "group_comparison",
        "group_column": "비목분류",
        "groups": ["내부인건비", "연구활동비"],
        "numerator": "당년도집행",
        "denominator": "계획예산",
        "rate_name": "집행률",
    }
    df = pd.DataFrame({c: [1, 2] for c in cols})
    df["비목분류"] = ["내부인건비", "연구활동비"]

    plan = build_analysis_plan(
        "내부인건비와 연구활동비의 집행 효율을 비교해서 해석해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="budget",
        chat_json_fn=lambda *_a, **_k: dict(wrong),
    )
    ratio_steps = [s for s in plan.steps if s.op == "ratio_of_aggregates"]
    assert ratio_steps
    assert ratio_steps[0].payload["numerator"] == "당년도집행"
    assert ratio_steps[0].payload["denominator"] == "계획예산"


def test_safety_normalization_canonicalizes_whitespace() -> None:
    from core.analysis.analysis_column_prefs import apply_safety_column_normalization

    data = {
        "operation": "aggregate",
        "group_column": "부 서",
        "numerator": "매 출",
    }
    fixed = apply_safety_column_normalization(
        data, ["부서", "매출", "비용"]
    )
    assert fixed["group_column"] == "부서"
    assert fixed["numerator"] == "매출"


def test_budget_regression_via_correct_plan_not_hardcoding() -> None:
    """예산 비교는 prefs rewrite 없이 올바른 plan으로 통과한다."""
    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비", "내부인건비", "연구활동비"],
            "실행예산_합계": [1000, 2000, 500, 800],
            "집행계_합계": [800, 500, 400, 200],
        }
    )

    def good_plan(*_a, **_k):
        return {
            "operation": "group_comparison",
            "group_column": "비목분류",
            "groups": ["내부인건비", "연구활동비"],
            "numerator": "집행계_합계",
            "denominator": "실행예산_합계",
            "rate_name": "집행률",
            "prefer_subtotals": True,
            "interpret": False,
        }

    result = try_analysis_pipeline(
        "내부인건비와 연구활동비 중 집행이 더 잘 된 곳을 비교해줘",
        df,
        base_url="http://localhost",
        model="dummy",
        profile_name="budget",
        chat_json_fn=good_plan,
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is not None
    assert "집행률" in result.dataframe.columns or len(result.dataframe) >= 1
