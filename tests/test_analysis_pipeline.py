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
        footer_labels=("내부흡수액", "외부유출액"),
    )
    types = classified[ROW_TYPE_COL].tolist()
    assert types[0] == "detail"
    assert types[2] == "subtotal"
    assert types[5] == "footer"
    assert types[3] == "detail"


def test_generic_mode_skips_budget_column_prefs() -> None:
    """일반 모드에서는 예산 컬럼 강제 보정을 하지 않는다."""
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
    }
    df = pd.DataFrame({c: [1, 2] for c in cols})
    df["비목분류"] = ["내부인건비", "연구활동비"]

    def fake_chat_json(prompt: str, **kwargs):
        return dict(wrong)

    plan = build_analysis_plan(
        "내부인건비와 연구활동비의 집행 효율을 비교해서 해석해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="generic",
        chat_json_fn=fake_chat_json,
    )
    ratio_steps = [s for s in plan.steps if s.op == "ratio_of_aggregates"]
    assert ratio_steps
    # prefs 미적용 → LLM이 고른 당년도/계획예산 유지
    assert ratio_steps[0].payload["numerator"] == "당년도집행"
    assert ratio_steps[0].payload["denominator"] == "계획예산"
    assert plan.footer_labels == []


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
        profile_name="budget",
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
        profile_name="budget",
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
        profile_name="budget",
    )
    assert fixed["numerator"] == "집행계_합계"
    assert fixed["denominator"] == "실행예산_합계"
    assert "합계" in fixed["criteria_note"]

    # 당년 명시 시에는 당년 컬럼 유지
    current = apply_execution_rate_column_prefs(
        "당년 기준 집행률을 비교해줘",
        dict(wrong),
        cols,
        profile_name="budget",
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
        profile_name="budget",
        chat_json_fn=fake_chat_json,
    )
    ratio_steps = [s for s in plan.steps if s.op == "ratio_of_aggregates"]
    assert ratio_steps
    assert ratio_steps[0].payload["numerator"] == "집행계_합계"
    assert ratio_steps[0].payload["denominator"] == "실행예산_합계"


def _sample_corr_budget_df() -> pd.DataFrame:
    """당년도집행·가집행금액 상관 기대값에 가까운 세부 비용 17행."""
    # 둘 다 양수 3행 + 당년만 양수 6행 + 둘 다 0 8행 = 17
    rows = [
        ("내부인건비", 16_513_830, 0),
        ("계약직", 1_200_000, 0),
        ("간접비", 5_419_500, 0),
        ("연구시설장비비", 2_167_000, 0),
        ("연구재료비", 355_510, 0),
        ("기타연구", 78_000, 0),
        ("연구용SW활용비", 1_677_813, 347_356),
        ("국내여비", 289_000, 184_960),
        ("회의비", 280_600, 189_600),
        ("항목A", 0, 0),
        ("항목B", 0, 0),
        ("항목C", 0, 0),
        ("항목D", 0, 0),
        ("항목E", 0, 0),
        ("항목F", 0, 0),
        ("항목G", 0, 0),
        ("항목H", 0, 0),
    ]
    return pd.DataFrame(
        {
            "비목분류": ["연구활동비"] * 17,
            "비용명": [r[0] for r in rows],
            "당년도집행": [r[1] for r in rows],
            "가집행금액": [r[2] for r in rows],
        }
    )


def test_correlation_on_detail_rows_near_zero() -> None:
    from core.prompt_intent import wants_structured_analysis

    assert wants_structured_analysis("당년도집행과 가집행금액의 상관관계를 분석해줘")

    df = _sample_corr_budget_df()
    plan = analysis_plan_from_dict(
        {
            "operation": "correlation",
            "x_column": "당년도집행",
            "y_column": "가집행금액",
            "label_column": "비용명",
            "interpret": True,
        },
        available_columns=list(df.columns),
    )
    assert any(s.op == "correlation" for s in plan.steps)
    assert plan.interpret
    assert not any(s.op in {"aggregate", "ratio_of_aggregates", "compare_groups"} for s in plan.steps)

    classified = classify_rows(df, dimension_columns=["비목분류", "비용명"])
    result, meta = execute_analysis_plan(classified, plan)
    corr = meta["correlation"]
    assert corr["n"] == 17
    assert corr["both_positive_count"] == 3
    assert corr["x_only_positive_count"] == 6
    assert corr["both_zero_count"] == 8
    assert abs(corr["x_sum"] - float(df["당년도집행"].sum())) < 1
    assert abs(corr["y_sum"] - float(df["가집행금액"].sum())) < 1
    # 전체는 거의 무상관 (금액 큰 행에 가집행 0)
    assert abs(float(corr["pearson_r"])) < 0.2
    assert float(corr["r_squared"]) < 0.05
    assert corr["strength"] in {"무상관~매우약함", "약함"}
    # 양수 교집합만은 강하지만 표본 3개 — 경고에 반영
    assert corr["both_positive_pearson_r"] is not None
    assert float(corr["both_positive_pearson_r"]) > 0.95
    assert any("3개" in w or "양수" in w for w in corr["warnings"])
    assert list(result.columns) == ["지표", "값"]
    assert "Pearson_r" in set(result["지표"].astype(str))


def test_correlation_plan_not_ratio_group_comparison() -> None:
    from core.analysis_plan_builder import build_analysis_plan

    df = _sample_corr_budget_df()

    def fake_chat_json(prompt: str, **kwargs):
        # LLM이 잘못 비율 비교로 내려도, 테스트에서는 올바른 correlation을 반환
        return {
            "operation": "correlation",
            "x_column": "당년도집행",
            "y_column": "가집행금액",
            "label_column": "비용명",
            "interpret": True,
        }

    plan = build_analysis_plan(
        "당년도집행과 가집행금액의 상관관계를 분석해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        chat_json_fn=fake_chat_json,
    )
    assert any(s.op == "correlation" for s in plan.steps)
    assert not any(s.op == "ratio_of_aggregates" for s in plan.steps)


def test_pipeline_correlation_with_interpretation_mock() -> None:
    df = _sample_corr_budget_df()

    def fake_chat_json(prompt: str, **kwargs):
        return {
            "operation": "correlation",
            "x_column": "당년도집행",
            "y_column": "가집행금액",
            "label_column": "비용명",
            "interpret": True,
        }

    def fake_chat_text(prompt: str, **kwargs):
        return (
            "전체적으로 선형 상관이 거의 없습니다(r≈0). "
            "가집행이 있는 행은 3개뿐이라 전체 상관이 약합니다."
        )

    result = run_analysis_pipeline(
        "당년도집행과 가집행금액의 상관관계를 분석해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        chat_json_fn=fake_chat_json,
        chat_text_fn=fake_chat_text,
    )
    assert "Pearson" in result.reply or "r=" in result.reply
    assert "무상관" in result.reply or "거의 없" in result.reply
    corr = result.meta.get("correlation") or {}
    assert abs(float(corr["pearson_r"])) < 0.2


def _sample_carryover_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "비목분류": [
                "연구활동비",
                "연구활동비",
                "연구재료비",
                "연구활동비",
                "연구활동비",
                "연구활동비",
                "연구활동비",
                "연구활동비",
            ],
            "비용명": [272, 366, 221, 353, 231, 222, 293, 271],
            "비용명_2": [
                "국외여비",
                "세미나비",
                "재료비",
                "전문가활용비",
                "문헌구입비",
                "사무용소모품비",
                "네트워크사용료",
                "회의비",
            ],
            "계획예산": [0, 1_200_000, 5_465_000, 600_000, 800_000, 600_000, 0, 0],
            "실행예산_이월예산": [
                2_400_000,
                700_000,
                665_000,
                400_000,
                300_000,
                163_832,
                138_400,
                3_315_900,
            ],
            "실행예산_당해예산": [0, 0, 1_000_000, 0, 0, 0, 0, 0],
            "집행계_이월집행": [0, 0, 355_510, 0, 0, 0, 30_800, 0],
            "집행계_당해집행": [0, 0, 0, 0, 0, 0, 0, 189_600],
            "집행계_합계": [0, 0, 355_510, 0, 0, 0, 30_800, 189_600],
            "기타열A": [1] * 8,
            "기타열B": [2] * 8,
        }
    )


def test_find_items_selects_minimal_columns_and_excludes_meeting() -> None:
    from core.prompt_intent import wants_structured_analysis

    prompt = "이월예산은 많은데 당해집행이 없는 항목을 찾고 그 의미를 설명해줘"
    assert wants_structured_analysis(prompt)

    df = _sample_carryover_df()
    plan = analysis_plan_from_dict(
        {
            "operation": "find_items",
            "numeric_filters": [
                {"column": "실행예산_이월예산", "op": "gt", "value": 0},
                {"column": "집행계_당해집행", "op": "eq", "value": 0},
            ],
            "sort_by": ["실행예산_이월예산"],
            "ascending": [False],
            "output_columns": [
                "비목분류",
                "비용명_2",
                "비용명",
                "실행예산_이월예산",
                "집행계_당해집행",
                "집행계_합계",
                "집행계_이월집행",
            ],
            "interpret": True,
        },
        available_columns=list(df.columns),
    )
    classified = classify_rows(df, dimension_columns=["비목분류", "비용명_2"])
    result, _meta = execute_analysis_plan(classified, plan)

    assert "회의비" not in set(result["비용명_2"].astype(str))
    assert list(result["비용명_2"].astype(str).head(3)) == [
        "국외여비",
        "세미나비",
        "재료비",
    ]
    assert "기타열A" not in result.columns
    assert "계획예산" not in result.columns
    assert "실행예산_당해예산" not in result.columns
    assert set(result.columns) <= {
        "비목분류",
        "비용명",
        "비용명_2",
        "실행예산_이월예산",
        "집행계_당해집행",
        "집행계_합계",
        "집행계_이월집행",
    }
    assert len(result) == 7


def test_find_items_prefs_override_wide_llm_plan() -> None:
    from core.analysis_plan_builder import build_analysis_plan

    df = _sample_carryover_df()

    def fake_chat_json(prompt: str, **kwargs):
        # LLM이 전체 열을 고르려 해도 prefs가 축소해야 함
        return {
            "steps": [
                {"op": "annotate_row_types"},
                {"op": "filter_rows", "include_row_types": ["detail"]},
                {"op": "select_columns", "columns": list(df.columns)},
            ],
            "interpret": False,
        }

    plan = build_analysis_plan(
        "이월예산은 많은데 당해집행이 없는 항목을 찾고 그 의미를 설명해줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="budget",
        chat_json_fn=fake_chat_json,
    )
    assert plan.interpret
    assert any(s.op == "filter_rows" and s.payload.get("numeric_filters") for s in plan.steps)
    select = next(s for s in plan.steps if s.op == "select_columns")
    cols = select.payload["columns"]
    assert "기타열A" not in cols
    assert "실행예산_이월예산" in cols
    assert "집행계_당해집행" in cols
    assert len(cols) <= 8


def test_condition_filter_projects_columns() -> None:
    from core.value_filter import try_condition_row_filter

    df = pd.DataFrame(
        {
            "비목분류": ["연구활동비", "연구활동비"],
            "비용명": [222, 271],
            "비용명_2": ["사무용소모품비", "국내여비"],
            "실행예산_합계": [163_832, 806_700],
            "집행계_합계": [0, 473_960],
            "기타열": [9, 9],
        }
    )
    result = try_condition_row_filter(
        df, "집행계가 0인데 실행예산이 있는 행만 골라줘", profile_name="budget"
    )
    assert result is not None
    assert "기타열" not in result.columns
    assert "집행계_합계" in result.columns
    assert "실행예산_합계" in result.columns


def test_rate_vs_mean_below_average_on_twin() -> None:
    from pathlib import Path

    from core.excel_loader import load_excel
    from core.pandasai_config import prepare_dataframe_for_ai
    from core.prompt_intent import wants_structured_analysis

    prompt = "비용명별 집행률을 구한 뒤 평균보다 낮은 항목만 표로 보여줘"
    assert wants_structured_analysis(prompt)

    path = Path(__file__).resolve().parents[1] / "data/uploads/03_트윈_예실대비표.xlsx"
    if not path.is_file():
        return
    df = prepare_dataframe_for_ai(load_excel(path))
    plan = analysis_plan_from_dict(
        {
            "operation": "rate_vs_mean",
            "numerator": "집행계_합계",
            "denominator": "실행예산_합계",
            "relation": "below",
            "interpret": False,
        },
        available_columns=list(df.columns),
        profile_name="budget",
    )
    assert any(s.op == "filter_vs_mean" for s in plan.steps)
    assert not plan.interpret

    classified = classify_rows(df, dimension_columns=["비용명", "비용명_2"])
    result, meta = execute_analysis_plan(classified, plan)
    assert len(result) == 9
    assert "계획예산" not in result.columns
    assert "집행률" in result.columns
    names = set(result["비용명_2"].astype(str).str.strip())
    assert names == {
        "사무용소모품비",
        "문헌구입비",
        "국외여비",
        "전문가활용비",
        "세미나비",
        "연구수당",
        "회의비",
        "재료비",
        "네트워크사용료",
    }
    assert "내부인건비" not in names
    assert "계약직내부인건비" not in names
    assert "연구용SW활용비" not in names
    assert "연구실 운영 소모성 경비" not in names
    assert "과제이월액" not in names
    mean = float((meta.get("vs_mean") or {})["mean"])
    assert abs(mean - 0.2846) < 0.01
    assert float(result["집행률"].max()) < mean


def test_rate_vs_mean_prefs_override_wrong_plan() -> None:
    from core.analysis_plan_builder import build_analysis_plan

    df = pd.DataFrame(
        {
            "비용명": [1, 2],
            "비용명_2": ["A", "B"],
            "실행예산_합계": [100, 200],
            "집행계_합계": [10, 20],
            "계획예산": [100, 200],
        }
    )

    def fake_chat_json(prompt: str, **kwargs):
        return {
            "operation": "group_comparison",
            "group_column": "비용명_2",
            "numerator": "계획예산",
            "denominator": "계획예산",
            "interpret": True,
        }

    plan = build_analysis_plan(
        "비용명별 집행률을 구한 뒤 평균보다 낮은 항목만 표로 보여줘",
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="budget",
        chat_json_fn=fake_chat_json,
    )
    assert any(s.op == "filter_vs_mean" for s in plan.steps)
    derive = next(s for s in plan.steps if s.op == "derive_column")
    assert derive.payload["expr"]["ratio"] == ["집행계_합계", "실행예산_합계"]
    assert not plan.interpret



def test_provisional_share_includes_ratio_column() -> None:
    from pathlib import Path

    from core.excel_loader import load_excel
    from core.pandasai_config import prepare_dataframe_for_ai
    from core.analysis_plan_builder import build_analysis_plan
    from core.prompt_intent import wants_structured_analysis

    prompt = "가집행금액이 있는 항목만 골라 당해누계에서 차지하는 비중을 계산해줘"
    assert wants_structured_analysis(prompt)

    path = Path(__file__).resolve().parents[1] / "data/uploads/03_트윈_예실대비표.xlsx"
    if not path.is_file():
        return
    df = prepare_dataframe_for_ai(load_excel(path))

    def fake_chat_json(prompt: str, **kwargs):
        return {"steps": []}

    plan = build_analysis_plan(
        prompt,
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="budget",
        chat_json_fn=fake_chat_json,
    )
    assert any(s.op == "derive_column" for s in plan.steps)
    classified = classify_rows(df, dimension_columns=["비용명", "비용명_2"])
    result, _ = execute_analysis_plan(classified, plan)
    assert "비중" in result.columns
    assert len(result) == 3
    names = set(result["비용명_2"].astype(str).str.strip())
    assert names == {"연구용SW활용비", "국내여비", "회의비"}
    by_name = {
        str(r["비용명_2"]).strip(): float(r["비중"]) for _, r in result.iterrows()
    }
    assert abs(by_name["연구용SW활용비"] - 17.15) < 0.05
    assert abs(by_name["국내여비"] - 39.02) < 0.05
    assert abs(by_name["회의비"] - 40.32) < 0.05


def test_top_n_per_group_balance_on_twin() -> None:
    from pathlib import Path

    from core.list_display import expects_list_display
    from core.excel_loader import load_excel
    from core.pandasai_config import prepare_dataframe_for_ai
    from core.prompt_intent import is_list_request, wants_structured_analysis

    prompt = "비목분류별로 가장 잔액이 큰 비용명 하나씩 뽑아줘"
    assert wants_structured_analysis(prompt)
    assert not is_list_request(prompt)
    assert not expects_list_display(prompt)

    path = Path(__file__).resolve().parents[1] / "data/uploads/03_트윈_예실대비표.xlsx"
    if not path.is_file():
        return
    df = prepare_dataframe_for_ai(load_excel(path))
    plan = analysis_plan_from_dict(
        {
            "operation": "top_n_per_group",
            "group_column": "비목분류",
            "value_column": "예산잔액_합계",
            "n": 1,
            "ascending": False,
            "interpret": False,
        },
        available_columns=list(df.columns),
        profile_name="budget",
    )
    plan.footer_labels = ["내부흡수액", "외부유출액"]
    assert any(s.op == "top_per_group" for s in plan.steps)
    assert not plan.interpret

    classified = classify_rows(
        df,
        dimension_columns=["비용명", "비용명_2"],
        footer_labels=plan.footer_labels,
    )
    result, meta = execute_analysis_plan(classified, plan)
    assert len(result) == 7
    assert list(result.columns) == ["비목분류", "비용명_2", "비용명", "예산잔액_합계"]
    expected = {
        "내부인건비": ("계약직내부인건비", 12_325_560),
        "간접비": ("간접비", 5_419_500),
        "연구수당": ("연구수당", 3_582_000),
        "연구활동비": ("회의비", 2_845_700),
        "연구재료비": ("재료비", 1_309_490),
        "연구시설장비비": ("연구장비구입비", 22_000),
        "기타": ("과제이월액", 0),
    }
    assert set(result["비목분류"].astype(str).str.strip()) == set(expected)
    for _, row in result.iterrows():
        cat = str(row["비목분류"]).strip()
        name, bal = expected[cat]
        assert str(row["비용명_2"]).strip() == name
        assert abs(float(row["예산잔액_합계"]) - bal) < 1
    assert float(result.iloc[0]["예산잔액_합계"]) >= float(result.iloc[-1]["예산잔액_합계"])
    assert (meta.get("top_per_group") or {}).get("kept") == 7


def test_top_n_per_group_prefs_override_wrong_plan() -> None:
    from core.analysis_plan_builder import build_analysis_plan

    df = pd.DataFrame(
        {
            "비목분류": ["A", "A", "B"],
            "비용명": [1, 2, 3],
            "비용명_2": ["x", "y", "z"],
            "예산잔액_합계": [10, 99, 5],
            "계획예산": [1, 1, 1],
        }
    )
    prompt = "비목분류별로 가장 잔액이 큰 비용명 하나씩 뽑아줘"

    def fake_chat_json(*_a, **_k):
        return {
            "operation": "group_comparison",
            "group_column": "비목분류",
            "numerator": "계획예산",
            "denominator": "계획예산",
            "interpret": True,
        }

    plan = build_analysis_plan(
        prompt,
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="budget",
        chat_json_fn=fake_chat_json,
    )
    assert any(s.op == "top_per_group" for s in plan.steps)
    classified = classify_rows(df, dimension_columns=["비용명", "비용명_2"])
    result, _ = execute_analysis_plan(classified, plan)
    assert len(result) == 2
    assert set(result["비용명_2"].astype(str)) == {"y", "z"}
    assert "계획예산" not in result.columns


def test_split_by_difference_plan_vs_exec_on_twin() -> None:
    from core.excel_loader import load_excel
    from core.pandasai_config import prepare_dataframe_for_ai
    from core.prompt_intent import wants_structured_analysis

    prompt = "계획예산보다 실행예산이 늘어난 항목과 줄어든 항목을 나눠서 설명해줘"
    assert wants_structured_analysis(prompt)

    path = Path(__file__).resolve().parents[1] / "data/uploads/03_트윈_예실대비표.xlsx"
    if not path.is_file():
        return
    df = prepare_dataframe_for_ai(load_excel(path))
    plan = analysis_plan_from_dict(
        {
            "operation": "split_by_difference",
            "left": "실행예산_합계",
            "right": "계획예산",
            "interpret": True,
        },
        available_columns=list(df.columns),
        profile_name="budget",
    )
    assert plan.interpret
    assert not any(s.op == "limit" for s in plan.steps)
    assert any(
        s.op == "derive_column" and s.payload.get("name") == "차이" for s in plan.steps
    )
    assert any(
        s.op == "derive_column" and s.payload.get("name") == "구분" for s in plan.steps
    )

    classified = classify_rows(df, dimension_columns=["비용명", "비용명_2"])
    result, _ = execute_analysis_plan(classified, plan)
    assert "차이" in result.columns and "구분" in result.columns
    assert len(result) == 17
    inc = result[result["구분"] == "증가"]
    dec = result[result["구분"] == "감소"]
    same = result[result["구분"] == "동일"]
    assert len(inc) == 4
    assert len(dec) == 11
    assert len(same) == 2
    assert abs(float(inc["차이"].sum()) - 25_302_382) < 1
    assert abs(float(dec["차이"].sum()) + 25_302_382) < 1
    assert set(inc["비용명_2"].astype(str).str.strip()) == {
        "내부인건비",
        "계약직내부인건비",
        "국외여비",
        "네트워크사용료",
    }
    assert "과제이월액" in set(dec["비용명_2"].astype(str).str.strip())
    # 증가가 먼저(내림차순)
    assert str(result.iloc[0]["구분"]) == "증가"


def test_split_by_difference_prefs_override_top_n() -> None:
    from core.analysis_plan_builder import build_analysis_plan

    df = pd.DataFrame(
        {
            "비목분류": ["A", "A", "B", "B"],
            "비용명": [1, 2, 3, 4],
            "비용명_2": ["up1", "down1", "up2", "same"],
            "계획예산": [100, 200, 0, 50],
            "실행예산_합계": [150, 50, 40, 50],
        }
    )
    prompt = "계획예산보다 실행예산이 늘어난 항목과 줄어든 항목을 나눠서 설명해줘"

    def fake_chat_json(*_a, **_k):
        return {
            "operation": "top_n_difference",
            "value_columns": ["실행예산_합계", "계획예산"],
            "difference_mode": "absolute",
            "limit": 5,
            "interpret": False,
        }

    plan = build_analysis_plan(
        prompt,
        df,
        base_url="http://localhost:11434",
        model="dummy",
        profile_name="budget",
        chat_json_fn=fake_chat_json,
    )
    assert plan.interpret
    assert not any(s.op == "limit" for s in plan.steps)
    classified = classify_rows(df, dimension_columns=["비용명", "비용명_2"])
    result, _ = execute_analysis_plan(classified, plan)
    assert len(result) == 4
    assert (result["구분"] == "증가").sum() == 2
    assert (result["구분"] == "감소").sum() == 1
    assert (result["구분"] == "동일").sum() == 1


def test_group_efficiency_compare_indirect_vs_allowance_on_twin() -> None:
    from core.excel_loader import load_excel
    from core.pandasai_config import prepare_dataframe_for_ai
    from core.prompt_intent import is_complex_analysis, wants_structured_analysis
    from core.analysis_plan_builder import build_analysis_plan

    prompt = "간접비와 연구수당의 집행률 차이를 기준으로 어느 쪽이 더 효율적인지 설명해줘"
    assert wants_structured_analysis(prompt)
    assert is_complex_analysis(prompt)

    path = Path(__file__).resolve().parents[1] / "data/uploads/03_트윈_예실대비표.xlsx"
    if not path.is_file():
        return
    df = prepare_dataframe_for_ai(load_excel(path))

    def fake_chat_json(*_a, **_k):
        # Wrong plan that would previously lead nowhere useful
        return {
            "operation": "top_n_difference",
            "value_columns": ["집행계_합계", "실행예산_합계"],
            "limit": 5,
            "interpret": False,
        }

    plan = build_analysis_plan(
        prompt,
        df,
        base_url="http://localhost:11434",
        model="dummy",
        chat_json_fn=fake_chat_json,
        profile_name="budget",
    )
    assert any(s.op == "compare_groups" or s.op == "aggregate" for s in plan.steps)
    assert plan.interpret
    classified = classify_rows(df, dimension_columns=["비용명", "비용명_2"])
    result, meta = execute_analysis_plan(classified, plan)
    assert len(result) == 2
    assert "집행률" in result.columns
    by_cat = {
        str(r["비목분류"]).strip(): float(r["집행률"])
        for _, r in result.iterrows()
    }
    assert abs(by_cat["간접비"] - 0.5) < 0.01
    assert abs(by_cat["연구수당"] - 0.0) < 0.01


def test_value_filter_skipped_for_efficiency_compare_intent() -> None:
    from core.prompt_intent import is_complex_analysis, wants_structured_analysis

    prompt = "간접비와 연구수당의 집행률 차이를 기준으로 어느 쪽이 더 효율적인지 설명해줘"
    assert wants_structured_analysis(prompt)
    assert is_complex_analysis(prompt)
