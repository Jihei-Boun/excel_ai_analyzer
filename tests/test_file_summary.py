"""파일 요약 단축 경로 단위 테스트 (fixture 매트릭스 + 계약 assert)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from core.io.excel_loader import load_excel
from core.summary.file_summary import build_file_summary, is_summary_request


def _twin_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "uploads" / "03_트윈_예실대비표.xlsx"


def budget_with_totals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "비목분류": ["내부인건비", "소 계", "간접비", "소 계", "내부흡수액", "외부유출액", "합 계"],
            "비용명_2": ["내부인건비", "", "간접비", "", "", "", ""],
            "계획예산": [100, 100, 50, 50, 100, 50, 150],
            "집행계_합계": [40, 40, 10, 10, 40, 10, 50],
            "예산잔액_합계": [60, 60, 40, 40, 60, 40, 100],
            "예산잔액_당해잔액": [60, 60, -5, -5, 60, -5, 55],
        }
    )


def plain_sales() -> pd.DataFrame:
    return pd.DataFrame({"지역": ["서울", "부산"], "매출": [1000, 2000]})


def english_numeric() -> pd.DataFrame:
    return pd.DataFrame({"Region": ["Seoul", "Busan"], "Revenue": [1000, 2000]})


def sales_with_dates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "판매일": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "매출": [200000, 300000, 400000],
            "지역": ["서울", "부산", "서울"],
        }
    )


def no_total_rows() -> pd.DataFrame:
    return pd.DataFrame({"항목": ["A", "B", "C"], "금액": [10, 20, 30]})


def empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def single_column() -> pd.DataFrame:
    return pd.DataFrame({"이름": ["홍길동", "김철수"]})


_FIXTURES: dict[str, Callable[[], pd.DataFrame]] = {
    "budget_with_totals": budget_with_totals,
    "plain_sales": plain_sales,
    "english_numeric": english_numeric,
    "no_total_rows": no_total_rows,
    "empty_df": empty_df,
    "single_column": single_column,
}


def test_is_summary_request() -> None:
    assert is_summary_request("파일을 요약해줘") is True
    assert is_summary_request("이 파일 개요 알려줘") is True
    assert is_summary_request("Summarize this file") is True
    assert is_summary_request("연구활동비 리스트로 뽑아줘") is False
    assert is_summary_request("요약 차트로 보여줘") is False


def test_generic_en_summary_is_english() -> None:
    text = build_file_summary(
        english_numeric(),
        file_name="sales_en.xlsx",
        profile_name="generic_en",
    )
    assert "This file" in text
    assert "table with 2 rows × 2 columns" in text
    assert "Numeric columns: 1" in text
    assert "Text columns: 1" in text
    assert "Key numeric columns (sum / min / max):" in text
    assert "sum 3,000" in text
    assert "min 1,000" in text
    assert "max 2,000" in text
    assert "Top values in text column (`Region`)" in text
    assert "표 형태 데이터" not in text
    assert "수치형 컬럼" not in text


def test_empty_df_shows_guidance_en() -> None:
    text = build_file_summary(empty_df(), profile_name="generic_en")
    assert "empty" in text.lower()
    assert "비어" not in text


def test_budget_summary_matches_twin_totals() -> None:
    path = _twin_path()
    if not path.is_file():
        return

    df = load_excel(path)
    text = build_file_summary(
        df,
        file_name=path.name,
        sheet_name="Sheet1",
        sheet_names=["Sheet1"],
        file_path=path,
        profile_name="budget",
    )

    assert "예실대비표" in text
    assert "29행 × 18열" in text
    assert "Sheet1" in text
    assert "69,638,788원" in text
    assert "27,455,969원" in text
    assert "42,182,819원" in text
    assert "39.4%" in text
    assert "내부인건비" in text
    assert "연구활동비" in text
    assert "10,990,230원" in text
    assert "계약직내부인건비" in text
    assert "12,325,560원" in text
    assert "국내여비" in text
    assert "회의비" in text
    assert "-184,960원" in text
    assert "-189,600원" in text
    assert "내부흡수액" in text
    assert "외부유출액" in text


def test_budget_summary_avoids_double_counting() -> None:
    """소계·합계·흡수/유출을 더해 금액을 부풀리지 않는다."""
    text = build_file_summary(
        budget_with_totals(),
        sheet_name="Sheet1",
        sheet_names=["Sheet1"],
        profile_name="budget",
    )
    assert "전체 예산: **150원**" in text
    assert "누적 집행액: **50원**" in text
    assert "전체 예산잔액: **100원**" in text
    # 소계까지 합치면 300이 되므로 그 값이 나오면 실패
    assert "300원" not in text


@pytest.mark.parametrize("fixture_id", list(_FIXTURES))
def test_summary_never_crashes_and_returns_text(fixture_id: str) -> None:
    df = _FIXTURES[fixture_id]()
    text = build_file_summary(df, file_name=f"{fixture_id}.xlsx")
    assert isinstance(text, str)
    assert text.strip()


@pytest.mark.parametrize("fixture_id", ["plain_sales", "english_numeric", "no_total_rows", "single_column"])
def test_non_budget_tables_are_not_misclassified(fixture_id: str) -> None:
    text = build_file_summary(_FIXTURES[fixture_id](), file_name=f"{fixture_id}.xlsx")
    assert "예실대비표" not in text
    assert "집행률" not in text


def test_budget_fixture_takes_budget_path() -> None:
    text = build_file_summary(
        budget_with_totals(),
        sheet_name="Sheet1",
        sheet_names=["Sheet1"],
        profile_name="budget",
    )
    assert "예실대비표" in text
    assert "전체 예산" in text
    assert "누적 집행액" in text


def test_budget_profile_off_uses_generic_summary() -> None:
    """예산 표 모드 OFF면 예산 fixture도 범용 요약을 쓴다."""
    text = build_file_summary(
        budget_with_totals(),
        file_name="budget.xlsx",
        sheet_name="Sheet1",
        sheet_names=["Sheet1"],
        profile_name="generic",
    )
    assert "예실대비표" not in text
    assert "전체 예산" not in text
    assert "집행률" not in text
    assert "표 형태 데이터" in text


def test_empty_df_shows_guidance() -> None:
    text = build_file_summary(empty_df())
    assert "비어" in text


def test_plain_sales_includes_numeric_stats_and_categories() -> None:
    text = build_file_summary(plain_sales(), file_name="sales.xlsx")
    assert "표 형태 데이터" in text
    assert "2행 × 2열" in text
    assert "매출" in text
    assert "합 3,000" in text
    assert "최소 1,000" in text
    assert "최대 2,000" in text
    assert "문자형 컬럼 상위 값 (`지역`)" in text
    assert "서울" in text
    assert "부산" in text


def test_english_numeric_uses_generic_path() -> None:
    text = build_file_summary(english_numeric(), file_name="sales_en.xlsx")
    assert "표 형태 데이터" in text
    assert "Revenue" in text
    assert "합 3,000" in text
    assert "최소 1,000" in text
    assert "최대 2,000" in text
    assert "문자형 컬럼 상위 값 (`Region`)" in text
    assert "전체 예산" not in text
    assert "예실대비표" not in text


def test_datetime_column_is_excluded_from_numeric_stats() -> None:
    text = build_file_summary(sales_with_dates(), file_name="sales_date.xlsx")
    assert "수치형 컬럼: 1개" in text
    assert "`매출`: 합 900,000 / 최소 200,000 / 최대 400,000" in text
    assert "`판매일`: 합" not in text
