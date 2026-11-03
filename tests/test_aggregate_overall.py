"""group_by 없는 전체 집계 (aggregate group_by=[])."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_executor import execute_analysis_plan
from core.analysis.analysis_ops import OVERALL_LABEL, OVERALL_LABEL_COLUMN, aggregate_groups
from core.analysis.analysis_pipeline import try_analysis_pipeline
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.analysis_plan_validate import validate_analysis_plan


def _ledger_df() -> pd.DataFrame:
    """분류 · 항목명 · 금액. 소계, 라벨 없는 footer 두 줄, 합계 줄을 포함한다."""
    return pd.DataFrame(
        {
            "분류": ["인건비", "인건비", "소 계", "활동비", "활동비", "활동비", "소 계", "내부분", "외부분", "합계"],
            "항목명": ["급여", "수당", None, "여비", "회의비", "인쇄비", None, None, None, None],
            "계획": [100, 50, 150, 30, 20, 10, 60, 120, 90, 210],
            "집행": [80, 40, 120, 0, 15, 5, 20, 100, 40, 140],
        }
    )


def _run(plan_dict: dict, df: pd.DataFrame, profile: str = "generic"):
    plan = analysis_plan_from_dict(
        plan_dict, available_columns=list(df.columns), profile_name=profile
    )
    report = validate_analysis_plan(plan, df, profile_name=profile)
    assert report.ok, [i.message for i in report.issues]
    return execute_analysis_plan(df, plan)


def test_aggregate_groups_empty_group_by_sums_detail_rows_only() -> None:
    result, meta = aggregate_groups(
        _ledger_df(),
        group_by=[],
        metrics=[{"column": "계획", "fn": "sum"}, {"column": "집행", "fn": "sum"}],
    )
    assert list(result.columns) == [OVERALL_LABEL_COLUMN, "계획", "집행"]
    assert result.iloc[0][OVERALL_LABEL_COLUMN] == OVERALL_LABEL
    assert result.iloc[0]["계획"] == 210
    assert result.iloc[0]["집행"] == 140
    assert meta["overall"]["detail_rows"] == 5
    assert meta["overall"]["total_row_check"]["계획"]["match"] is True
    assert meta["aggregate_warnings"] == []


def test_aggregate_overall_mean_uses_detail_rows() -> None:
    result, _ = aggregate_groups(
        _ledger_df(), group_by=[], metrics=[{"column": "계획", "fn": "mean"}]
    )
    assert result.iloc[0]["계획"] == 42.0  # (100+50+30+20+10) / 5


def test_aggregate_overall_warns_when_total_row_disagrees() -> None:
    df = _ledger_df()
    df.loc[df["분류"] == "합계", "계획"] = 999
    _, meta = aggregate_groups(df, group_by=[], metrics=[{"column": "계획", "fn": "sum"}])
    assert meta["overall"]["total_row_check"]["계획"]["match"] is False
    assert any("합계 행" in w for w in meta["aggregate_warnings"])


def test_compiled_overall_aggregate_plan() -> None:
    result, _ = _run(
        {"operation": "aggregate", "group_by": [], "metrics": [{"column": "계획", "fn": "sum"}]},
        _ledger_df(),
    )
    assert list(result.columns) == [OVERALL_LABEL_COLUMN, "계획"]
    assert len(result) == 1
    assert result.iloc[0]["계획"] == 210


def test_compiled_aggregate_with_column_filters_selects_rows() -> None:
    result, _ = _run(
        {
            "operation": "aggregate",
            "group_by": [],
            "column_filters": [{"column": "항목명", "values": ["여비", "회의비"]}],
            "metrics": [{"column": "계획", "fn": "sum"}],
        },
        _ledger_df(),
    )
    assert result.iloc[0]["계획"] == 50


def test_steps_selected_rows_sum() -> None:
    result, _ = _run(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "항목명", "values": ["급여", "인쇄비"]}],
                },
                {"op": "aggregate", "group_by": [], "metrics": [{"column": "집행", "fn": "sum"}]},
            ]
        },
        _ledger_df(),
    )
    assert len(result) == 1
    assert result.iloc[0]["집행"] == 85


def test_steps_group_mean_via_filter() -> None:
    result, _ = _run(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "분류", "values": ["활동비"]}],
                },
                {"op": "aggregate", "group_by": [], "metrics": [{"column": "계획", "fn": "mean"}]},
            ]
        },
        _ledger_df(),
    )
    assert result.iloc[0]["계획"] == 20.0


def test_unknown_group_by_column_is_still_rejected() -> None:
    df = _ledger_df()
    try:
        plan = analysis_plan_from_dict(
            {
                "operation": "aggregate",
                "group_by": ["없는열"],
                "metrics": [{"column": "계획", "fn": "sum"}],
            },
            available_columns=list(df.columns),
            profile_name="generic",
        )
    except ValueError:
        return
    report = validate_analysis_plan(plan, df, profile_name="generic")
    assert not report.ok


def test_grouped_aggregate_unchanged() -> None:
    result, _ = _run(
        {"operation": "aggregate", "group_by": ["분류"], "metrics": [{"column": "계획", "fn": "sum"}]},
        _ledger_df(),
    )
    assert OVERALL_LABEL_COLUMN not in result.columns
    rows = dict(zip(result["분류"], result["계획"]))
    assert rows["인건비"] == 150
    assert rows["활동비"] == 60


def test_pipeline_accepts_overall_aggregate_plan() -> None:
    df = _ledger_df()
    result = try_analysis_pipeline(
        "계획 전체 합계를 구해줘",
        df,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        max_retries=0,
        chat_json_fn=lambda *_a, **_k: {
            "operation": "aggregate",
            "group_by": [],
            "metrics": [{"column": "계획", "fn": "sum"}],
            "interpret": False,
        },
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is not None
    assert result.dataframe.iloc[0]["계획"] == 210


def _report(plan_dict: dict, prompt: str):
    df = _ledger_df()
    plan = analysis_plan_from_dict(plan_dict, available_columns=list(df.columns), profile_name="generic")
    return validate_analysis_plan(plan, df, profile_name="generic", user_prompt=prompt)


def test_overall_aggregate_rejects_invented_metric_column() -> None:
    report = _report(
        {
            "operation": "aggregate",
            "group_by": [],
            "metrics": [{"column": "계획", "fn": "sum"}, {"column": "여비_계획", "fn": "sum"}],
        },
        "여비 계획 합계",
    )
    assert not report.ok
    assert "unknown_metric_column" in [i.code for i in report.errors]


def test_overall_aggregate_requires_filter_when_prompt_names_items() -> None:
    report = _report(
        {"operation": "aggregate", "group_by": [], "metrics": [{"column": "계획", "fn": "sum"}]},
        "여비, 회의비의 계획 합계를 구해줘",
    )
    assert not report.ok
    issue = next(i for i in report.errors if i.code == "overall_aggregate_missing_selection")
    assert "항목명" in issue.message and "여비" in issue.message and "회의비" in issue.message


def test_overall_aggregate_with_filter_passes_selection_check() -> None:
    report = _report(
        {
            "operation": "aggregate",
            "group_by": [],
            "column_filters": [{"column": "항목명", "values": ["여비", "회의비"]}],
            "metrics": [{"column": "계획", "fn": "sum"}],
        },
        "여비, 회의비의 계획 합계를 구해줘",
    )
    assert report.ok, [i.message for i in report.issues]


def test_overall_aggregate_without_item_names_passes() -> None:
    report = _report(
        {"operation": "aggregate", "group_by": [], "metrics": [{"column": "계획", "fn": "sum"}]},
        "계획 전체 합계를 구해줘",
    )
    assert report.ok, [i.message for i in report.issues]


def test_pipeline_retries_until_selection_filter_is_added() -> None:
    plans = [
        {"operation": "aggregate", "group_by": [], "metrics": [{"column": "계획", "fn": "sum"}]},
        {
            "operation": "aggregate",
            "group_by": [],
            "column_filters": [{"column": "항목명", "values": ["여비", "회의비"]}],
            "metrics": [{"column": "계획", "fn": "sum"}],
            "interpret": False,
        },
    ]
    calls = {"n": 0}

    def fake_chat_json(*_a, **_k):
        plan = plans[min(calls["n"], len(plans) - 1)]
        calls["n"] += 1
        return plan

    result = try_analysis_pipeline(
        "여비, 회의비의 계획 합계를 구해줘",
        _ledger_df(),
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        max_retries=2,
        chat_json_fn=fake_chat_json,
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is not None
    assert calls["n"] >= 2
    assert result.dataframe.iloc[0]["계획"] == 50


def test_grouped_aggregate_requires_named_group_selection() -> None:
    report = _report(
        {"operation": "aggregate", "group_by": ["분류"], "metrics": [{"column": "계획", "fn": "mean"}]},
        "활동비 항목들의 계획 평균을 구해줘",
    )
    assert not report.ok
    issue = next(i for i in report.errors if i.code == "aggregate_missing_group_selection")
    assert "활동비" in issue.message and "include_groups" in issue.message


def test_grouped_aggregate_with_include_groups_passes() -> None:
    report = _report(
        {
            "operation": "aggregate",
            "group_by": ["분류"],
            "include_groups": ["활동비"],
            "metrics": [{"column": "계획", "fn": "mean"}],
        },
        "활동비 항목들의 계획 평균을 구해줘",
    )
    assert report.ok, [i.message for i in report.issues]


def test_grouped_aggregate_without_group_names_passes() -> None:
    report = _report(
        {"operation": "aggregate", "group_by": ["분류"], "metrics": [{"column": "계획", "fn": "sum"}]},
        "분류별 계획 합계를 보여줘",
    )
    assert report.ok, [i.message for i in report.issues]


def test_include_groups_mean_returns_named_group_only() -> None:
    result, _ = _run(
        {
            "operation": "aggregate",
            "group_by": ["분류"],
            "include_groups": ["활동비"],
            "metrics": [{"column": "계획", "fn": "mean"}],
        },
        _ledger_df(),
    )
    assert list(result["분류"]) == ["활동비"]
    assert result.iloc[0]["계획"] == 20.0


def test_grouped_aggregate_requires_selecting_named_items_of_other_column() -> None:
    report = _report(
        {"operation": "aggregate", "group_by": ["분류"], "metrics": [{"column": "계획", "fn": "sum"}]},
        "여비, 회의비의 계획을 표로 보여줘",
    )
    assert not report.ok
    issue = next(i for i in report.errors if i.code == "aggregate_missing_item_selection")
    assert "항목명" in issue.message and "여비" in issue.message


def test_item_filter_before_grouped_aggregate_passes() -> None:
    report = _report(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "항목명", "values": ["여비", "회의비"]}],
                },
                {"op": "aggregate", "group_by": ["항목명"], "metrics": [{"column": "계획", "fn": "sum"}]},
            ]
        },
        "여비, 회의비의 계획을 표로 보여줘",
    )
    assert report.ok, [i.message for i in report.issues]


def test_selected_items_listing_then_transpose() -> None:
    result, _ = _run(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "분류", "values": ["활동비"]}],
                },
                {"op": "select_columns", "columns": ["항목명", "계획", "집행"]},
            ]
        },
        _ledger_df(),
    )
    assert list(result["항목명"]) == ["여비", "회의비", "인쇄비"]


def test_listing_without_named_group_filter_is_rejected() -> None:
    report = _report(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {"op": "filter_rows", "include_row_types": ["detail"]},
                {"op": "select_columns", "columns": ["항목명", "계획", "집행"]},
            ]
        },
        "활동비 항목들의 계획과 집행을 보여줘",
    )
    assert not report.ok
    issue = next(i for i in report.errors if i.code == "listing_missing_selection")
    assert "활동비" in issue.message and "분류" in issue.message


def test_listing_with_filter_passes() -> None:
    report = _report(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "분류", "values": ["활동비"]}],
                },
                {"op": "select_columns", "columns": ["항목명", "계획", "집행"]},
            ]
        },
        "활동비 항목들의 계획과 집행을 보여줘",
    )
    assert report.ok, [i.message for i in report.issues]


def test_interpretation_can_be_disabled_by_env(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_text(*_a, **_k):
        calls["n"] += 1
        return "해석 문장"

    plan = {"operation": "aggregate", "group_by": [], "metrics": [{"column": "계획", "fn": "sum"}], "interpret": True}

    def run():
        return try_analysis_pipeline(
            "계획 전체 합계를 구해줘",
            _ledger_df(),
            base_url="http://localhost",
            model="dummy",
            profile_name="generic",
            max_retries=0,
            chat_json_fn=lambda *_a, **_k: plan,
            chat_text_fn=fake_text,
        )

    monkeypatch.setenv("EXCEL_ANALYZER_DISABLE_INTERPRET", "1")
    off = run()
    assert "해석 문장" not in off.reply and calls["n"] == 0
    monkeypatch.delenv("EXCEL_ANALYZER_DISABLE_INTERPRET")
    on = run()
    assert "해석 문장" in on.reply
