"""입력 정규화 계층 단위 테스트."""

from __future__ import annotations

import pandas as pd

from core.io.normalize import (
    align_column_names,
    canonicalize_column_name,
    column_match_key,
    normalize_dataframe,
    suggest_key_columns,
)


def test_canonicalize_column_name_collapses_spaces_and_symbols() -> None:
    assert canonicalize_column_name("  실행 예산 ") == "실행_예산"
    assert canonicalize_column_name("코드/명칭") == "코드_명칭"
    assert column_match_key("실행 예산") == column_match_key("실행_예산")


def test_normalize_dataframe_coerces_numeric_strings() -> None:
    df = pd.DataFrame(
        {
            "이름": ["갑", "을"],
            "금액": ["1,000", "2,500"],
            "비고": ["a", "b"],
        }
    )
    cleaned = normalize_dataframe(df)
    assert list(cleaned.columns) == ["이름", "금액", "비고"]
    assert pd.api.types.is_numeric_dtype(cleaned["금액"])
    assert int(cleaned.iloc[0]["금액"]) == 1000


def test_suggest_key_columns_prefers_unique_id_like() -> None:
    df = pd.DataFrame(
        {
            "항목코드": ["A1", "A2", "A3", "A4"],
            "금액": [10, 20, 10, 40],
            "지역": ["서울", "서울", "부산", "서울"],
        }
    )
    keys = suggest_key_columns(df)
    assert keys[0] == "항목코드"


def test_align_column_names_maps_equivalent_headers() -> None:
    left = pd.DataFrame({"항목 코드": [1, 2], "금액": [10, 20]})
    right = pd.DataFrame({"항목_코드": [1, 3], "수량": [5, 7]})
    left_n = normalize_dataframe(left)
    right_n = normalize_dataframe(right)
    aligned = align_column_names([left_n, right_n])
    assert "항목_코드" in aligned[0].columns
    assert "항목_코드" in aligned[1].columns
