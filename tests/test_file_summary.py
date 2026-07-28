"""파일 요약 단축 경로 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.excel_loader import load_excel
from core.file_summary import build_file_summary, is_summary_request


def _twin_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "uploads" / "03_트윈_예실대비표.xlsx"


def test_is_summary_request() -> None:
    assert is_summary_request("파일을 요약해줘") is True
    assert is_summary_request("이 파일 개요 알려줘") is True
    assert is_summary_request("연구활동비 리스트로 뽑아줘") is False
    assert is_summary_request("요약 차트로 보여줘") is False


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
    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "소 계", "간접비", "소 계", "내부흡수액", "외부유출액", "합 계"],
            "비용명_2": ["내부인건비", "", "간접비", "", "", "", ""],
            "계획예산": [100, 100, 50, 50, 100, 50, 150],
            "집행계_합계": [40, 40, 10, 10, 40, 10, 50],
            "예산잔액_합계": [60, 60, 40, 40, 60, 40, 100],
            "예산잔액_당해잔액": [60, 60, -5, -5, 60, -5, 55],
        }
    )
    text = build_file_summary(df, sheet_name="Sheet1", sheet_names=["Sheet1"])
    assert "전체 예산: **150원**" in text
    assert "누적 집행액: **50원**" in text
    assert "전체 예산잔액: **100원**" in text
    # 소계까지 합치면 300이 되므로 그 값이 나오면 실패
    assert "300원" not in text


def test_generic_summary_for_plain_table() -> None:
    df = pd.DataFrame({"지역": ["서울", "부산"], "매출": [1000, 2000]})
    text = build_file_summary(df, file_name="sales.xlsx")
    assert "표 형태 데이터" in text
    assert "2행 × 2열" in text
    assert "매출" in text
