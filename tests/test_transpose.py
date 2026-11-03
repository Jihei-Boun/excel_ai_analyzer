"""행·열 전치 (plan transpose)."""

from __future__ import annotations

import pandas as pd
import pytest

from core.analysis.analysis_pipeline import try_analysis_pipeline
from core.analysis.analysis_plan_types import analysis_plan_from_dict
from core.analysis.ops_reshape import TRANSPOSE_HEADER, transpose_table


def _ledger_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "분류": ["인건비", "인건비", "소 계", "활동비", "활동비", "소 계", "합계"],
            "항목명": ["급여", "수당", None, "여비", "회의비", None, None],
            "계획": [100, 50, 150, 30, 20, 50, 200],
            "집행": [80, 40, 120, 0, 15, 15, 135],
        }
    )


def _pipeline(plan: dict, prompt: str = "분류별 계획, 집행 합계를 만들고 행과 열을 바꿔줘"):
    return try_analysis_pipeline(
        prompt,
        _ledger_df(),
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        max_retries=0,
        chat_json_fn=lambda *_a, **_k: plan,
        chat_text_fn=lambda *_a, **_k: "",
    )


def test_transpose_table_uses_label_column_as_header() -> None:
    df = pd.DataFrame({"지역": ["동", "서"], "매출": [10, 20], "비용": [1, 2]})
    out, meta = transpose_table(df)
    assert list(out.columns) == [TRANSPOSE_HEADER, "동", "서"]
    assert list(out[TRANSPOSE_HEADER]) == ["매출", "비용"]
    assert out.loc[0, "서"] == 20
    assert meta["label_column"] == "지역"
    assert meta["source_shape"] == [2, 3] and meta["result_shape"] == [2, 3]


def test_transpose_table_disambiguates_blank_and_duplicate_labels() -> None:
    df = pd.DataFrame({"이름": ["A", "A", None], "값": [1, 2, 3]})
    out, _ = transpose_table(df)
    assert list(out.columns) == [TRANSPOSE_HEADER, "A", "A (2)", "3"]


def test_transpose_table_without_label_column_uses_row_numbers() -> None:
    out, meta = transpose_table(pd.DataFrame({"x": [1, 2], "y": [3, 4]}))
    assert list(out.columns) == [TRANSPOSE_HEADER, "1", "2"]
    assert meta["label_column"] is None


def test_transpose_drops_row_type_meta_columns() -> None:
    df = pd.DataFrame({"지역": ["동"], "매출": [1], "_row_type": ["detail"]})
    out, _ = transpose_table(df)
    assert list(out[TRANSPOSE_HEADER]) == ["매출"]


def test_plan_transpose_flag_and_last_step_forms() -> None:
    cols = list(_ledger_df().columns)
    flag = analysis_plan_from_dict(
        {
            "operation": "aggregate",
            "group_by": ["분류"],
            "metrics": [{"column": "계획", "fn": "sum"}],
            "transpose": True,
        },
        available_columns=cols,
    )
    assert flag.transpose and all(s.op != "transpose" for s in flag.steps)
    step = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "aggregate", "group_by": ["분류"], "metrics": [{"column": "계획", "fn": "sum"}]},
                {"op": "transpose"},
            ]
        },
        available_columns=cols,
    )
    assert step.transpose and [s.op for s in step.steps] == ["aggregate"]
    assert step.to_dict()["transpose"] is True


def test_plan_rejects_transpose_that_is_not_last() -> None:
    with pytest.raises(ValueError, match="LAST"):
        analysis_plan_from_dict(
            {
                "steps": [
                    {"op": "transpose"},
                    {"op": "sort", "by": ["계획"], "ascending": [False]},
                ]
            },
            available_columns=list(_ledger_df().columns),
        )


def test_pipeline_group_totals_then_transpose() -> None:
    result = _pipeline(
        {
            "operation": "aggregate",
            "group_by": ["분류"],
            "metrics": [{"column": "계획", "fn": "sum"}, {"column": "집행", "fn": "sum"}],
            "transpose": True,
            "interpret": False,
        }
    )
    assert result is not None
    out = result.dataframe
    assert list(out.columns) == [TRANSPOSE_HEADER, "인건비", "활동비"]
    rows = out.set_index(TRANSPOSE_HEADER)
    assert rows.loc["계획", "인건비"] == 150 and rows.loc["집행", "활동비"] == 15
    assert result.meta["transpose"]["label_column"] == "분류"
    assert result.meta["analysis_plan"]["transpose"] is True


def test_pipeline_whole_table_transpose() -> None:
    result = _pipeline({"operation": "transpose"}, prompt="이 표의 행과 열을 바꿔줘")
    assert result is not None
    out = result.dataframe
    # 원본 7행 → 7개 열 + 항목 열, 원본 열 3개(라벨 열 제외) → 3행
    assert out.shape == (3, 8)
    assert list(out[TRANSPOSE_HEADER]) == ["항목명", "계획", "집행"]


def test_transpose_prefers_most_unique_label_column() -> None:
    df = pd.DataFrame(
        {"분류": ["활동비", "활동비", "활동비"], "항목명": ["여비", "회의비", "인쇄비"], "계획": [30, 20, 10]}
    )
    out, meta = transpose_table(df)
    assert meta["label_column"] == "항목명"
    assert list(out.columns) == [TRANSPOSE_HEADER, "여비", "회의비", "인쇄비"]
    assert list(out[TRANSPOSE_HEADER]) == ["분류", "계획"]


def test_select_code_column_keeps_split_name_column_for_transpose() -> None:
    df = pd.DataFrame(
        {
            "분류": ["활동비", "활동비"],
            "비용명": [271.0, 272.0],
            "비용명_2": ["국내여비", "국외여비"],
            "계획": [30, 100],
        }
    )
    result = try_analysis_pipeline(
        "활동비 항목들의 계획을 보여주고 행과 열을 바꿔줘",
        df,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        max_retries=0,
        chat_json_fn=lambda *_a, **_k: {
            "steps": [
                {"op": "annotate_row_types"},
                {
                    "op": "filter_rows",
                    "include_row_types": ["detail"],
                    "column_filters": [{"column": "분류", "values": ["활동비"]}],
                },
                {"op": "select_columns", "columns": ["비용명", "계획"]},
                {"op": "transpose"},
            ],
            "interpret": False,
        },
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is not None
    assert list(result.dataframe.columns) == [TRANSPOSE_HEADER, "국내여비", "국외여비"]
