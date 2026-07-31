"""결과 후처리·리스트 표시 단위 테스트."""

from __future__ import annotations

import pandas as pd

from core.prompt_router import postprocess_table_result
from core.result_format import (
    exclude_aggregate_rows,
    expects_list_display,
    restore_source_row_order,
    to_list_display,
    wants_explicit_sort,
)


def test_expects_list_display() -> None:
    assert expects_list_display("항목 리스트로 보여줘") is True
    assert expects_list_display("표로 보여줘") is False
    assert expects_list_display("각 시트의 행 수와 컬럼 목록을 비교해줘") is False


def test_exclude_aggregate_rows_removes_totals() -> None:
    df = pd.DataFrame(
        {
            "항목": ["연구활동비", "합계", "인건비", "소계"],
            "금액": [10, 100, 20, 50],
        }
    )
    filtered, excluded = exclude_aggregate_rows(df, "항목 리스트")
    assert excluded == 2
    assert filtered["항목"].tolist() == ["연구활동비", "인건비"]


def test_to_list_display_returns_values() -> None:
    df = pd.DataFrame({"비용명": ["노트북", "모니터", "키보드"]})
    result = to_list_display(df, "비용명 리스트로 보여줘")
    assert result is not None
    assert result.values == ["노트북", "모니터", "키보드"]


def test_to_list_display_skips_constant_filter_column() -> None:
    """비용명=121로 필터된 뒤엔 항목명 등 다른 컬럼을 리스트로 쓴다."""
    df = pd.DataFrame(
        {
            "비용명": [121, 121],
            "항목": ["노트북", "모니터"],
            "예산잔액": [1000, 500],
        }
    )
    result = to_list_display(df, "비용명이 121인 것만 리스트로 뽑아줘")
    assert result is not None
    assert result.values == ["121: 노트북", "121: 모니터"]
    assert "1000" not in "".join(result.values)


def test_to_list_display_groups_by_source_file() -> None:
    df = pd.DataFrame(
        {
            "출처파일": ["a.xlsx", "a.xlsx", "b.xlsx"],
            "비용명": [121, 121, 121],
            "항목": ["노트북", "모니터", "키보드"],
        }
    )
    result = to_list_display(df, "비용명이 121인 것만 파일별로 리스트로 뽑아줘")
    assert result is not None
    assert result.groups is not None
    assert set(result.groups.keys()) == {"a.xlsx", "b.xlsx"}
    assert result.groups["a.xlsx"] == ["121: 노트북", "121: 모니터"]
    assert result.groups["b.xlsx"] == ["121: 키보드"]


def test_restore_source_row_order_from_alphabetical() -> None:
    source = pd.DataFrame(
        {
            "비용_명": [
                "연구활동비",
                "재료비",
                "회의비",
                "출장비",
                "소프트웨어 구독료",
                "인쇄·제본비",
            ],
            "실행_예산": [1500000, 2200000, 600000, 1300000, 900000, 350000],
            "집행_금액": [1280000.0, 1850000.0, 420000.0, 980000.0, 900000.0, None],
        }
    )
    result = pd.DataFrame(
        {
            "비용_명": [
                "소프트웨어 구독료",
                "연구활동비",
                "인쇄·제본비",
                "재료비",
                "출장비",
                "회의비",
            ],
            "실행_예산": [900000, 1500000, 350000, 2200000, 1300000, 600000],
            "집행_금액": [900000.0, 1280000.0, None, 1850000.0, 980000.0, 420000.0],
            "집행률": [100.0, 85.333, None, 84.091, 75.385, 70.0],
        }
    )
    restored = restore_source_row_order(result, source, prompt="집행률을 계산해줘")
    assert restored["비용_명"].tolist() == source["비용_명"].tolist()
    assert restored["집행률"].tolist()[0] == 85.333


def test_restore_source_row_order_skips_when_user_asks_sort() -> None:
    source = pd.DataFrame({"비용_명": ["B", "A"], "금액": [1, 2]})
    result = pd.DataFrame({"비용_명": ["A", "B"], "금액": [2, 1]})
    assert wants_explicit_sort("금액 내림차순으로 정렬해줘") is True
    kept = restore_source_row_order(
        result, source, prompt="금액 내림차순으로 정렬해줘"
    )
    assert kept["비용_명"].tolist() == ["A", "B"]


def test_postprocess_restores_order() -> None:
    source = pd.DataFrame(
        {
            "비용_명": ["연구활동비", "재료비"],
            "실행_예산": [10, 20],
            "집행_금액": [8, 15],
        }
    )
    result = pd.DataFrame(
        {
            "비용_명": ["재료비", "연구활동비"],
            "실행_예산": [20, 10],
            "집행_금액": [15, 8],
            "집행률": [75.0, 80.0],
        }
    )
    out, _summary, _meta = postprocess_table_result(
        result,
        "집행률을 계산해줘",
        "PandasAI 결과",
        source_df=source,
    )
    assert out["비용_명"].tolist() == ["연구활동비", "재료비"]
    assert out["집행률"].tolist() == [80.0, 75.0]
