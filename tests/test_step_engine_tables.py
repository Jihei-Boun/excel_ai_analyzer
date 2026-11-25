"""새 엔진이 처음 받는 표: 앱이 읽은 표 + (가능하면) 그 시트의 원래 칸. 모델은 부르지 않는다."""

from __future__ import annotations

import pandas as pd
import pytest
from openpyxl import Workbook

from core.analysis.step_engine import starting_tables
from core.io import table_structure
from core.io.excel_loader import SOURCE_ATTRS_KEY, load_tabular


@pytest.fixture
def sheet(tmp_path, monkeypatch):
    """제목·단위 줄이 머리글 위에 있는 합성 시트. 표 구조 LLM은 끄고 기존 방식으로 읽는다."""
    monkeypatch.setenv(table_structure.ENABLE_ENV, "off")
    wb = Workbook()
    ws = wb.active
    ws.title = "현황"
    for row in (["교육 현황"], ["(단위: 명)"], ["부서", "대상", "이수"], ["가팀", 20, 15], ["나팀", 30, 28]):
        ws.append(row)
    path = tmp_path / "sample.xlsx"
    wb.save(path)
    return path


def test_loaded_table_remembers_where_it_came_from(sheet) -> None:
    df = load_tabular(sheet, sheet_name="현황")
    source = df.attrs[SOURCE_ATTRS_KEY]
    assert source["path"] == str(sheet.resolve())
    assert source["sheet"] == "현황"
    assert source["shape"] == list(df.shape)


def test_whole_sheet_also_gets_the_raw_cells(sheet) -> None:
    df = load_tabular(sheet, sheet_name="현황")
    tables = starting_tables(df)
    assert list(tables) == ["표", "원본_셀"]
    raw = tables["원본_셀"]
    assert raw.iat[0, 0] == "교육 현황"  # 머리글 위 제목 줄까지 그대로
    assert raw.iat[4, 2] == 28
    assert tables["표"] is df


def test_filtered_table_gets_no_raw_cells(sheet) -> None:
    df = load_tabular(sheet, sheet_name="현황")
    part = df.iloc[:1]
    assert part.attrs.get(SOURCE_ATTRS_KEY)  # pandas는 필터 뒤에도 꼬리표를 남긴다
    assert list(starting_tables(part)) == ["표"]


def test_changed_columns_get_no_raw_cells(sheet) -> None:
    df = load_tabular(sheet, sheet_name="현황")
    assert list(starting_tables(df.assign(추가=1))) == ["표"]


def test_table_without_a_source_or_with_a_missing_file(sheet) -> None:
    assert list(starting_tables(pd.DataFrame({"a": [1]}))) == ["표"]
    df = load_tabular(sheet, sheet_name="현황")
    sheet.unlink()
    assert list(starting_tables(df)) == ["표"]
