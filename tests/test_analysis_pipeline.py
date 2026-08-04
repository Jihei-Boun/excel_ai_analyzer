"""분석 계획 파이프라인·행 분류·실행기·검증 테스트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.analysis_executor import execute_analysis_plan
from core.analysis_pipeline import run_analysis_pipeline, try_analysis_pipeline
from core.analysis_plan_types import analysis_plan_from_dict
from core.analysis_validate import validate_analysis_result
from core.row_classify import ROW_TYPE_COL, classify_rows


def _sample_budget_like_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "카테고리": ["인건", "인건", "인건", "재료", "기타", "기타"],
            "항목코드": [121.0, 201.0, None, 221.0, 532.0, None],
            "항목명": ["내부인건비", "계약직", "", "재료비", "과제이월액", ""],
            "계획": [7_000_000, 10_914_000, 17_914_000, 5_465_000, 9_638_788, 51_799_788],
            "실행": [22_828_822, 17_849_160, 40_677_982, 1_665_000, 0, 35_970_966],
        }
    )


def test_row_classify_marks_subtotal_footer_and_detail() -> None:
    df = _sample_budget_like_df()
    df.loc[2, "카테고리"] = "소 계"
    df.loc[5, "카테고리"] = "외부유출액"
    classified = classify_rows(
        df,
        dimension_columns=["카테고리", "항목명"],
    )
    types = classified[ROW_TYPE_COL].tolist()
    assert types[0] == "detail"
    assert types[2] == "subtotal"
    assert types[5] == "footer"
    assert types[3] == "detail"


def test_executor_abs_diff_sort_limit() -> None:
    df = _sample_budget_like_df()
    df.loc[2, "카테고리"] = "소 계"
    df.loc[5, "카테고리"] = "외부유출액"
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "drop_blank_dimensions": True,
                    "dimension_columns": ["항목명"],
                },
                {
                    "op": "derive_column",
                    "name": "차이",
                    "expr": {"abs_diff": ["계획", "실행"]},
                },
                {"op": "sort", "by": ["차이"], "ascending": [False]},
                {"op": "limit", "n": 3},
                {
                    "op": "select_columns",
                    "columns": ["항목명", "계획", "실행", "차이"],
                },
            ],
            "criteria_note": "차이의 절댓값을 기준으로 내림차순 정렬했습니다.",
            "dimension_columns": ["항목명"],
            "output_columns": ["항목명", "계획", "실행", "차이"],
        },
        available_columns=list(df.columns),
    )
    classified = classify_rows(df, dimension_columns=["카테고리", "항목명"])
    result, meta = execute_analysis_plan(classified, plan)
    assert len(result) == 3
    assert list(result.columns) == ["항목명", "계획", "실행", "차이"]
    assert all(str(x).strip() for x in result["항목명"])
    assert result.iloc[0]["항목명"] == "내부인건비"
    assert abs(float(result.iloc[0]["차이"]) - 15_828_822) < 1
    assert "criteria_note" in meta


def test_validate_rejects_summary_rows_in_detail_result() -> None:
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {"op": "filter_rows", "include_row_types": ["detail"]},
                {"op": "limit", "n": 2},
                {"op": "select_columns", "columns": ["항목명", "계획"]},
            ],
            "dimension_columns": ["항목명"],
            "output_columns": ["항목명", "계획"],
        },
        available_columns=["항목명", "계획", "실행"],
    )
    bad = pd.DataFrame({"항목명": ["", "재료비"], "계획": [1, 2]})
    # 소계 라벨을 카테고리처럼 넣기 위해 classify가 subtotal로 볼 수 있게
    bad = pd.DataFrame({"항목명": ["소 계", "재료비"], "계획": [1, 2]})
    report = validate_analysis_result(bad, plan)
    assert not report.ok
    assert any(i.code == "summary_rows_mixed" for i in report.errors)


def test_compile_top_n_difference_high_level() -> None:
    cols = ["항목명", "계획", "실행"]
    plan = analysis_plan_from_dict(
        {
            "operation": "top_n_difference",
            "dimension_columns": ["항목명"],
            "value_columns": ["계획", "실행"],
            "difference_mode": "absolute",
            "sort": "descending",
            "limit": 5,
            "exclude_rows": {
                "blank_dimensions": True,
                "summary_rows": True,
                "footer_rows": True,
            },
        },
        available_columns=cols,
    )
    assert plan.limit_n == 5
    assert any(s.op == "derive_column" for s in plan.steps)
    assert "절댓값" in plan.criteria_note or "절대" in plan.criteria_note


def test_pipeline_with_mocked_llm() -> None:
    df = _sample_budget_like_df()
    df.loc[2, "카테고리"] = "소 계"
    df.loc[5, "카테고리"] = "외부유출액"

    def fake_chat_json(prompt: str, **kwargs):
        return {
            "operation": "top_n_difference",
            "dimension_columns": ["항목명"],
            "value_columns": ["계획", "실행"],
            "difference_mode": "absolute",
            "sort": "descending",
            "limit": 5,
            "exclude_rows": {
                "blank_dimensions": True,
                "summary_rows": True,
                "footer_rows": True,
            },
            "criteria_note": "차이의 절댓값을 기준으로 내림차순 정렬했습니다.",
        }

    result = run_analysis_pipeline(
        "계획과 실행 차이가 큰 상위 5개 항목을 보여줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        chat_json_fn=fake_chat_json,
    )
    assert len(result.dataframe) <= 5
    assert all(str(x).strip() for x in result.dataframe["항목명"])
    assert "소 계" not in set(result.dataframe["항목명"].astype(str))
    assert result.dataframe["계획"].max() < 51_799_788
    assert "절댓값" in result.reply or "절대" in result.plan.criteria_note
    assert result.validation.ok


def test_pipeline_on_twin_sample_with_mock() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "uploads" / "03_트윈_예실대비표.xlsx"
    if not path.is_file():
        pytest.skip("sample upload missing")

    from core.excel_loader import load_excel

    df = load_excel(path)

    def fake_chat_json(prompt: str, **kwargs):
        return {
            "operation": "top_n_difference",
            "dimension_columns": ["비용명_2"],
            "value_columns": ["계획예산", "실행예산_합계"],
            "difference_mode": "absolute",
            "sort": "descending",
            "limit": 5,
            "exclude_rows": {
                "blank_dimensions": True,
                "summary_rows": True,
                "footer_rows": True,
            },
            "criteria_note": "차이의 절댓값을 기준으로 내림차순 정렬했습니다.",
        }

    result = try_analysis_pipeline(
        "계획예산과 실행예산 차이가 큰 상위 5개 항목을 보여줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        use_budget_profile=True,
        chat_json_fn=fake_chat_json,
    )
    assert result is not None
    table = result.dataframe
    assert len(table) == 5
    assert all(str(x).strip() for x in table["비용명_2"].tolist())
    assert list(table["비용명_2"].head(2)) == ["내부인건비", "과제이월액"]


def test_classify_detects_subtotal_outside_dimension_cols() -> None:
    """비목분류의 '소 계'는 dimension 후보가 아니어도 subtotal이어야 한다."""
    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "소 계"],
            "비용명_2": ["계약직", ""],
            "실행예산_합계": [100, 100],
            "집행계_합계": [40, 40],
        }
    )
    classified = classify_rows(df, dimension_columns=["비용명_2"])
    assert classified[ROW_TYPE_COL].tolist() == ["detail", "subtotal"]


def test_group_comparison_execution_rate_on_twin() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "uploads" / "03_트윈_예실대비표.xlsx"
    if not path.is_file():
        pytest.skip("sample upload missing")

    from core.excel_loader import load_excel
    from core.pandasai_frame import prepare_dataframe_for_ai
    from core.prompt_intent import wants_structured_analysis

    assert wants_structured_analysis(
        "내부인건비와 연구활동비의 집행 효율을 비교해서 해석해줘"
    )

    df = prepare_dataframe_for_ai(load_excel(path))
    classified = classify_rows(df)
    plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "비목분류",
            "groups": ["내부인건비", "연구활동비"],
            "numerator": "집행계_합계",
            "denominator": "실행예산_합계",
            "rate_name": "집행률",
            "prefer_subtotals": True,
            "interpret": True,
        },
        available_columns=list(df.columns),
    )
    result, meta = execute_analysis_plan(classified, plan)
    assert list(result["비목분류"]) == ["내부인건비", "연구활동비"]
    rates = result["집행률"].astype(float).tolist()
    assert rates[0] == pytest.approx(0.4060, abs=0.001)
    assert rates[1] == pytest.approx(0.2808, abs=0.001)
    assert meta["aggregate_sources"]["내부인건비"] == "subtotal"
    rate_cmp = next(c for c in meta["comparison"] if c["metric"] == "집행률")
    assert rate_cmp["higher_group"] == "내부인건비"
    assert rate_cmp["diff_pp"] == pytest.approx(12.52, abs=0.05)


def test_pipeline_group_comparison_with_interpretation_mock() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "uploads" / "03_트윈_예실대비표.xlsx"
    if not path.is_file():
        pytest.skip("sample upload missing")

    from core.excel_loader import load_excel

    df = load_excel(path)

    def fake_chat_json(prompt: str, **kwargs):
        return {
            "operation": "group_comparison",
            "group_column": "비목분류",
            "groups": ["내부인건비", "연구활동비"],
            "numerator": "집행계_합계",
            "denominator": "실행예산_합계",
            "rate_name": "집행률",
            "prefer_subtotals": True,
            "interpret": True,
            "criteria_note": "비목 소계 기준 집행률 비교",
        }

    def fake_chat_text(prompt: str, **kwargs):
        return (
            "내부인건비 집행률이 약 40.6%로 연구활동비 28.1%보다 높습니다. "
            "연구활동비는 미집행 항목이 많아 평균 효율이 낮아 보입니다."
        )

    result = run_analysis_pipeline(
        "내부인건비와 연구활동비의 집행 효율을 비교해서 해석해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        use_budget_profile=True,
        chat_json_fn=fake_chat_json,
        chat_text_fn=fake_chat_text,
    )
    assert len(result.dataframe) == 2
    assert "40.6" in result.reply or "집행률" in result.reply
    assert "미집행" in result.reply
    assert result.plan.interpret is True


def test_aggregate_falls_back_to_detail_sum_without_subtotal() -> None:
    df = pd.DataFrame(
        {
            "비목분류": ["A", "A", "B", "B"],
            "항목": ["a1", "a2", "b1", "b2"],
            "예산": [100, 50, 200, 100],
            "집행": [40, 10, 50, 50],
        }
    )
    classified = classify_rows(df, dimension_columns=["비목분류", "항목"])
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "aggregate",
                    "group_by": ["비목분류"],
                    "metrics": [
                        {"column": "예산", "fn": "sum"},
                        {"column": "집행", "fn": "sum"},
                    ],
                    "prefer_subtotals": True,
                    "include_groups": ["A", "B"],
                },
                {
                    "op": "ratio_of_aggregates",
                    "name": "집행률",
                    "numerator": "집행",
                    "denominator": "예산",
                },
            ],
            "dimension_columns": ["비목분류"],
            "output_columns": ["비목분류", "예산", "집행", "집행률"],
        },
        available_columns=list(df.columns),
    )
    result, meta = execute_analysis_plan(classified, plan)
    assert list(result["비목분류"]) == ["A", "B"]
    assert float(result.loc[0, "예산"]) == 150
    assert float(result.loc[0, "집행률"]) == pytest.approx(50 / 150)
    assert meta["aggregate_sources"]["A"] == "detail_sum"


def test_filter_rows_column_values() -> None:
    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비", "간접비"],
            "항목": ["a", "b", "c"],
            "금액": [1, 2, 3],
        }
    )
    classified = classify_rows(df, dimension_columns=["비목분류", "항목"])
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [
                        {
                            "column": "비목분류",
                            "values": ["내부인건비", "연구활동비"],
                        }
                    ],
                    "drop_blank_dimensions": False,
                },
                {"op": "select_columns", "columns": ["비목분류", "항목", "금액"]},
            ],
            "dimension_columns": ["비목분류"],
        },
        available_columns=list(df.columns),
    )
    result, _ = execute_analysis_plan(classified, plan)
    assert set(result["비목분류"]) == {"내부인건비", "연구활동비"}


def test_execution_rate_prefs_override_wrong_llm_columns() -> None:
    from core.analysis_column_prefs import apply_execution_rate_column_prefs
    from core.analysis_plan_builder import build_analysis_plan

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
        "criteria_note": "당년도 집행액과 계획 예산의 비율",
    }
    fixed = apply_execution_rate_column_prefs(
        "내부인건비와 연구활동비의 집행 효율을 비교해서 해석해줘",
        wrong,
        cols,
    )
    assert fixed["numerator"] == "집행계_합계"
    assert fixed["denominator"] == "실행예산_합계"
    assert "합계" in fixed["criteria_note"]

    # 당년 명시 시에는 당년 컬럼 유지
    current = apply_execution_rate_column_prefs(
        "당년 기준 집행률을 비교해줘",
        dict(wrong),
        cols,
    )
    assert current["numerator"] == "당년도집행"
    assert current["denominator"] in {"당년도예산", "계획예산"}

    df = pd.DataFrame({c: [1, 2] for c in cols})
    df["비목분류"] = ["내부인건비", "연구활동비"]

    def fake_chat_json(prompt: str, **kwargs):
        return dict(wrong)

    plan = build_analysis_plan(
        "내부인건비와 연구활동비의 집행 효율을 비교해서 해석해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        chat_json_fn=fake_chat_json,
    )
    ratio_steps = [s for s in plan.steps if s.op == "ratio_of_aggregates"]
    assert ratio_steps
    assert ratio_steps[0].payload["numerator"] == "집행계_합계"
    assert ratio_steps[0].payload["denominator"] == "실행예산_합계"
