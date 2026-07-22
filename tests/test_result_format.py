"""결과 후처리·리스트 표시 단위 테스트."""

from __future__ import annotations

import pandas as pd

from core.result_format import exclude_aggregate_rows, expects_list_display, to_list_display


def test_expects_list_display() -> None:
    assert expects_list_display("항목 리스트로 보여줘") is True
    assert expects_list_display("표로 보여줘") is False


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
