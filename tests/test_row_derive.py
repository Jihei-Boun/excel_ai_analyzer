"""행마다 여러 열을 더하거나 평균 내기 (derive_column sum / mean)."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "분류": ["활동비", "활동비", "소 계"],
            "비용명": [271.0, 272.0, None],
            "비용명_2": ["국내여비", "국외여비", None],
            "이월": [100, 400, 500],
            "당해": [300, 1000, 1300],
            "추가": [None, 50, 50],
        }
    )


def _plan(plan_dict: dict):
    df = _df()
    plan = analysis_plan_from_dict(plan_dict, available_columns=list(df.columns), profile_name="generic")
    return plan, validate_analysis_plan(plan, df, profile_name="generic"), df


def _steps(expr: dict, name: str | None = "합") -> dict:
    derive = {"op": "derive_column", "expr": expr}
    if name is not None:
        derive["name"] = name
    return {
        "steps": [
            {"op": "annotate_row_types"},
            {"op": "filter_rows", "include_row_types": ["detail"]},
            derive,
        ]
    }


def test_row_sum_across_columns() -> None:
    plan, report, df = _plan(_steps({"sum": ["이월", "당해"]}))
    assert report.ok, [i.message for i in report.issues]
    out, _ = execute_analysis_plan(df, plan)
    assert list(out["합"]) == [400, 1400]


def test_row_mean_across_three_columns_skips_blank() -> None:
    plan, report, df = _plan(_steps({"mean": ["이월", "당해", "추가"]}, name="평균"))
    assert report.ok, [i.message for i in report.issues]
    out, _ = execute_analysis_plan(df, plan)
    assert list(out["평균"]) == [200.0, 1450 / 3]


def test_type_columns_form_and_alias_are_normalized() -> None:
    plan, report, _ = _plan(_steps({"type": "add", "columns": ["이월", "당해"]}))
    assert report.ok
    step = next(s for s in plan.steps if s.op == "derive_column")
    assert step.payload["expr"] == {"sum": ["이월", "당해"]}
    plan2, _, _ = _plan(_steps({"avg": ["이월", "당해"]}))
    assert next(s for s in plan2.steps if s.op == "derive_column").payload["expr"] == {
        "mean": ["이월", "당해"]
    }


def test_row_sum_without_name_gets_default_name() -> None:
    plan, report, df = _plan(_steps({"sum": ["이월", "당해"]}, name=None))
    assert report.ok
    out, _ = execute_analysis_plan(df, plan)
    assert "이월+당해" in out.columns


def test_row_sum_all_blank_stays_blank() -> None:
    df = pd.DataFrame({"항목": ["a"], "x": [None], "y": [None]})
    plan = analysis_plan_from_dict(
        {"steps": [{"op": "derive_column", "name": "합", "expr": {"sum": ["x", "y"]}}]},
        available_columns=list(df.columns),
    )
    out, _ = execute_analysis_plan(df, plan)
    assert pd.isna(out.loc[0, "합"])


def test_code_column_rejected_as_row_sum_operand() -> None:
    _, report, _ = _plan(_steps({"sum": ["비용명", "이월"]}))
    assert not report.ok
    assert "code_column_metric" in [i.code for i in report.errors]


def test_code_column_rejected_as_aggregate_metric() -> None:
    _, report, _ = _plan(
        {
            "operation": "aggregate",
            "group_by": [],
            "metrics": [{"column": "비용명", "fn": "sum"}, {"column": "이월", "fn": "sum"}],
        }
    )
    assert not report.ok
    assert "code_column_metric" in [i.code for i in report.errors]


def test_top_level_derive_operation_is_expanded() -> None:
    plan, report, df = _plan(
        {
            "operation": "derive_column",
            "name": "합",
            "expr": {"sum": ["이월", "당해"]},
            "select_columns": ["비용명_2", "이월", "당해"],
        }
    )
    assert report.ok, [i.message for i in report.issues]
    assert [s.op for s in plan.steps] == [
        "annotate_row_types",
        "filter_rows",
        "derive_column",
        "select_columns",
    ]
    out, _ = execute_analysis_plan(df, plan)
    assert list(out.columns) == ["비용명_2", "이월", "당해", "합"]
    assert list(out["합"]) == [400, 1400]  # 소계 행 제외


def test_per_item_mean_as_column_aggregate_is_rejected() -> None:
    df = _df()
    plan = analysis_plan_from_dict(
        {"operation": "aggregate", "group_by": [], "metrics": [{"column": "이월", "fn": "mean"}, {"column": "당해", "fn": "mean"}]},
        available_columns=list(df.columns),
        profile_name="generic",
    )
    report = validate_analysis_plan(plan, df, profile_name="generic", user_prompt="이월과 당해 평균을 항목별로 구해줘")
    assert "per_row_combination_as_aggregate" in [i.code for i in report.errors]


def test_group_mean_by_category_is_not_flagged() -> None:
    df = _df()
    plan = analysis_plan_from_dict(
        {"operation": "aggregate", "group_by": ["분류"], "metrics": [{"column": "이월", "fn": "mean"}, {"column": "당해", "fn": "mean"}]},
        available_columns=list(df.columns),
        profile_name="generic",
    )
    report = validate_analysis_plan(plan, df, profile_name="generic", user_prompt="분류별 이월과 당해 평균을 구해줘")
    assert "per_row_combination_as_aggregate" not in [i.code for i in report.issues]


def _derive_report(expr: dict, prompt: str):
    df = _df()
    plan = analysis_plan_from_dict(_steps(expr), available_columns=list(df.columns), profile_name="generic")
    return validate_analysis_plan(plan, df, profile_name="generic", user_prompt=prompt)


def test_derive_operand_not_in_request_is_rejected() -> None:
    report = _derive_report({"mean": ["추가", "이월", "당해"]}, "이월과 당해 평균을 항목별로 구해줘")
    issue = next(i for i in report.errors if i.code == "derive_operand_not_requested")
    assert "추가" in issue.message


def test_derive_operands_matching_request_pass() -> None:
    report = _derive_report({"mean": ["이월", "당해"]}, "이월과 당해 평균을 항목별로 구해줘")
    assert "derive_operand_not_requested" not in [i.code for i in report.issues]


def test_derive_operands_with_synonyms_only_are_not_judged() -> None:
    report = _derive_report({"sum": ["이월", "당해"]}, "두 예산을 더해줘")
    assert "derive_operand_not_requested" not in [i.code for i in report.issues]


def test_prefixed_column_counts_as_mentioned_by_its_part() -> None:
    from core.analysis.analysis_plan_validate import _column_mentioned

    prompt = "국내여비의이월예산과당해예산평균"
    assert _column_mentioned("실행예산_이월예산", prompt)
    assert not _column_mentioned("계획예산", prompt)
    assert not _column_mentioned("집행계_합계", "실행예산합계를보여줘")


def test_per_row_retry_feedback_includes_skeleton_from_previous_plan() -> None:
    from core.analysis.analysis_plan_validate import format_plan_validation_feedback

    df = _df()
    previous = {
        "steps": [
            {"op": "annotate_row_types"},
            {
                "op": "filter_rows",
                "include_row_types": ["detail"],
                "column_filters": [{"column": "비용명_2", "values": ["국내여비"]}],
            },
            {"op": "aggregate", "group_by": [], "metrics": [{"column": "이월", "fn": "mean"}, {"column": "당해", "fn": "mean"}]},
        ]
    }
    plan = analysis_plan_from_dict(previous, available_columns=list(df.columns), profile_name="generic")
    report = validate_analysis_plan(plan, df, profile_name="generic", user_prompt="국내여비 각각의 이월과 당해 평균")
    lines = format_plan_validation_feedback(report, previous_plan=previous, df=df)
    text = "\n".join(lines)
    assert "retry_mode: regenerate" in text
    assert '"mean": ["이월", "당해"]' in text and '"국내여비"' in text


def _ratio_plan(name: str):
    df = _df()
    return analysis_plan_from_dict(
        _steps({"ratio": ["당해", "이월"]}, name=name), available_columns=list(df.columns), profile_name="generic"
    )


def test_rate_named_ratio_becomes_percent() -> None:
    from core.analysis.analysis_plan_sanitize import prefer_percent_for_rate

    plan = prefer_percent_for_rate(_ratio_plan("집행률"), "이월 대비 당해 집행률을 구해줘")
    step = next(s for s in plan.steps if s.op == "derive_column")
    assert step.payload["expr"] == {"percent_ratio": ["당해", "이월"]}
    out, _ = execute_analysis_plan(_df(), plan)
    assert list(out["집행률"]) == [300.0, 250.0]


def test_plain_ratio_stays_ratio_without_percent_request() -> None:
    from core.analysis.analysis_plan_sanitize import prefer_percent_for_rate

    plan = prefer_percent_for_rate(_ratio_plan("배수"), "당해가 이월의 몇 배인지 구해줘")
    step = next(s for s in plan.steps if s.op == "derive_column")
    assert step.payload["expr"] == {"ratio": ["당해", "이월"]}
    plan2 = prefer_percent_for_rate(_ratio_plan("배수"), "당해/이월을 %로 구해줘")
    assert next(s for s in plan2.steps if s.op == "derive_column").payload["expr"] == {
        "percent_ratio": ["당해", "이월"]
    }
