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
