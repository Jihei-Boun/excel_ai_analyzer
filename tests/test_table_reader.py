"""DEV-5 1단계 표 읽기 검사. tmp_path의 합성 xlsx만 쓴다."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from core.io import cell_reader, table_reader
from core.io.cell_reader import read_range
from core.io.table_reader import read_table

DISPLAY_NAME = "book.xlsx"
REQUEST_ID = "dev5-test-1"

SUCCESS_KEYS = {
    "contract_version",
    "request_id",
    "status",
    "operation",
    "recalculated",
    "values_from",
    "freshness",
    "source",
    "requested",
    "table",
}
FAILURE_KEYS = {"contract_version", "request_id", "status", "operation", "recalculated", "error"}
DATA_CELL_KEYS = {
    "a1",
    "key",
    "value",
    "value_type",
    "number_format",
    "formula",
    "cached_value",
    "cached_value_state",
}

EXPECTED_ERRORS = {
    "missing_header": ("invalid_request", "request", "헤더 지정이 없다."),
    "invalid_address": ("invalid_request", "request", "셀 주소가 올바르지 않다."),
    "reversed_range": ("invalid_request", "request", "범위의 시작이 끝보다 뒤다."),
    "range_too_large": ("invalid_request", "request", "범위가 2000칸을 넘는다."),
    "unsupported_format": ("invalid_request", "request", "xlsx 파일만 읽을 수 있다."),
    "invalid_sheet": ("invalid_request", "file_loading", "시트를 찾을 수 없다."),
    "invalid_file": ("invalid_request", "file_loading", "통합 문서를 열 수 없다."),
    "execution_failed": ("execution_failed", "read", "셀 범위를 읽지 못했다."),
}

HEADER_OUTSIDE = "헤더 행이 요청 범위 안에 있지 않다."
HEADER_MALFORMED = "헤더 행 목록이 올바르지 않다."
HEADER_ORDER = "헤더 행은 겹치지 않고 연속된 오름차순이어야 한다."
HEADER_NOT_FIRST = "헤더는 범위의 첫 행에서 시작해야 한다."
HEADER_TOO_MANY = "헤더는 두 줄까지다."


def _save(tmp_path: Path, build, name: str = "book.xlsx") -> Path:
    wb = Workbook()
    build(wb)
    path = tmp_path / name
    wb.save(path)
    return path


def _read(path: Path, address: str, header, sheet=0) -> dict:
    return read_table(
        path,
        sheet=sheet,
        address=address,
        header=header,
        display_name=DISPLAY_NAME,
        request_id=REQUEST_ID,
    )


def _assert_success(result: dict, address: str, header_rows: list[int]) -> None:
    assert set(result) == SUCCESS_KEYS
    assert result["contract_version"] == "1.0"
    assert result["request_id"] == REQUEST_ID
    assert result["status"] == "success"
    assert result["operation"] == "read_table"
    assert result["recalculated"] is False
    assert result["values_from"] == "stored_workbook"
    assert result["freshness"] == "unconfirmed"
    assert set(result["source"]) == {"display_name", "sheet_name", "sheet_index"}
    assert result["source"]["display_name"] == DISPLAY_NAME
    assert result["requested"] == {"address": address, "header": {"rows": header_rows}}
    table = result["table"]
    assert set(table) == {"address", "blank_rows", "blank_columns", "columns", "rows"}
    assert table["address"] == address
    assert table["blank_rows"] == "kept"
    assert table["blank_columns"] == "kept"
    for row in table["rows"]:
        assert set(row) == {"source_row", "cells"}
        assert len(row["cells"]) == len(table["columns"])
        for cell in row["cells"]:
            assert set(cell) - {"merge"} == DATA_CELL_KEYS


def _assert_failure(result: dict, code: str, message: str | None = None) -> None:
    assert set(result) == FAILURE_KEYS
    if code == "invalid_header":
        status, stage, expected_message = "invalid_request", "request", message
    else:
        status, stage, expected_message = EXPECTED_ERRORS[code]
    assert result["contract_version"] == "1.0"
    assert result["request_id"] == REQUEST_ID
    assert result["status"] == status
    assert result["operation"] == "read_table"
    assert result["recalculated"] is False
    error = result["error"]
    assert error["code"] == code
    assert error["message"] == expected_message
    assert error["stage"] == stage
    assert error["retryable"] is False
    if code in ("invalid_file", "execution_failed"):
        assert isinstance(error["exception_type"], str) and error["exception_type"]
        assert set(error) == {"code", "message", "stage", "retryable", "exception_type"}
    else:
        assert set(error) == {"code", "message", "stage", "retryable"}


def _cells(result: dict) -> dict[str, dict]:
    return {cell["a1"]: cell for row in result["table"]["rows"] for cell in row["cells"]}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sheet_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


def _cell_xml(xml: str, ref: str) -> str:
    match = re.search(rf'<c r="{ref}"[^>]*>(.*?)</c>', xml, re.S)
    assert match is not None, ref
    return match.group(1)


def _rewrite_formula_cache(
    path: Path,
    cached: dict[str, str],
    *,
    drop: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
) -> None:
    """openpyxl이 저장한 시트 XML의 수식 칸에 저장값을 넣거나 `<v>`를 지운다."""
    member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    rewritten = []
    for info, data in entries:
        if info.filename == member:
            xml = data.decode("utf-8")
            for ref in (*cached, *drop):
                cell_re = re.compile(rf'<c r="{ref}"([^>]*)>(.*?)</c>', re.S)
                match = cell_re.search(xml)
                assert match is not None, ref
                attrs, body = match.group(1), match.group(2)
                assert "<f" in body, ref
                body = re.sub(r"<v\s*/>|<v>\s*</v>", "", body)
                assert "<v" not in body, ref
                if ref in cached:
                    body += f"<v>{cached[ref]}</v>"
                if ref in errors:
                    attrs = re.sub(r'\st="[^"]*"', "", attrs) + ' t="e"'
                xml = xml[: match.start()] + f'<c r="{ref}"{attrs}>{body}</c>' + xml[match.end():]
            data = xml.encode("utf-8")
        rewritten.append((info, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)


def _record_cell_rows(monkeypatch) -> list[int]:
    rows: list[int] = []
    original = table_reader._cell_fields

    def recording(cell, cached_worksheet):
        rows.append(cell.row)
        return original(cell, cached_worksheet)

    monkeypatch.setattr(table_reader, "_cell_fields", recording)
    return rows


def _plain_cell(a1: str, key: str, value, value_type: str, number_format: str | None = "General") -> dict:
    return {
        "a1": a1,
        "key": key,
        "value": value,
        "value_type": value_type,
        "number_format": number_format,
        "formula": None,
        "cached_value": None,
        "cached_value_state": "not_applicable",
    }


def _header(row: int, a1: str, value, value_type: str, merge=None) -> dict:
    return {"row": row, "a1": a1, "value": value, "value_type": value_type, "merge": merge}


# --- 헤더 위치 ---


def test_one_row_header(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"], ws["C1"] = "이름", "수량", "단가"
        ws["A2"], ws["B2"], ws["C2"] = "사과", 3, 1.5
        ws["A3"], ws["B3"], ws["C3"] = "배", 5, 2.5
        ws["A4"], ws["B4"] = "감", 0

    path = _save(tmp_path, build)

    result = _read(path, "A1:C5", {"rows": [1]})

    _assert_success(result, "A1:C5", [1])
    assert result["source"] == {"display_name": DISPLAY_NAME, "sheet_name": "Sheet", "sheet_index": 0}
    assert result["table"]["columns"] == [
        {
            "key": "이름",
            "display_name": "이름",
            "source_column": "A",
            "column_index": 1,
            "headers": [_header(1, "A1", "이름", "string")],
        },
        {
            "key": "수량",
            "display_name": "수량",
            "source_column": "B",
            "column_index": 2,
            "headers": [_header(1, "B1", "수량", "string")],
        },
        {
            "key": "단가",
            "display_name": "단가",
            "source_column": "C",
            "column_index": 3,
            "headers": [_header(1, "C1", "단가", "string")],
        },
    ]
    rows = result["table"]["rows"]
    assert [row["source_row"] for row in rows] == [2, 3, 4, 5]
    assert rows[0]["cells"] == [
        _plain_cell("A2", "이름", "사과", "string"),
        _plain_cell("B2", "수량", 3, "number"),
        _plain_cell("C2", "단가", 1.5, "number"),
    ]
    assert rows[2]["cells"] == [
        _plain_cell("A4", "이름", "감", "string"),
        _plain_cell("B4", "수량", 0, "number"),
        _plain_cell("C4", "단가", None, "empty"),
    ]
    assert rows[3]["cells"] == [
        _plain_cell("A5", "이름", None, "empty"),
        _plain_cell("B5", "수량", None, "empty"),
        _plain_cell("C5", "단가", None, "empty"),
    ]


def test_header_on_third_row_ignores_rows_above(tmp_path, monkeypatch):
    def build(wb):
        ws = wb.active
        ws["A1"] = "보고서제목"
        ws["B2"] = "작성메모"
        ws["A3"], ws["B3"], ws["C3"], ws["D3"] = "지역", "1월", "2월", "3월"
        for row in range(4, 21):
            ws.cell(row=row, column=1, value=f"지역{row}")
            ws.cell(row=row, column=2, value=row * 10)
        ws["D20"] = "끝"

    path = _save(tmp_path, build)
    seen_rows = _record_cell_rows(monkeypatch)

    result = _read(path, "A3:D20", {"rows": [3]})

    _assert_success(result, "A3:D20", [3])
    columns = result["table"]["columns"]
    assert [column["key"] for column in columns] == ["지역", "1월", "2월", "3월"]
    assert [column["source_column"] for column in columns] == ["A", "B", "C", "D"]
    assert [column["column_index"] for column in columns] == [1, 2, 3, 4]
    rows = result["table"]["rows"]
    assert [row["source_row"] for row in rows] == list(range(4, 21))
    for row in rows:
        assert [cell["a1"] for cell in row["cells"]] == [f"{letter}{row['source_row']}" for letter in "ABCD"]
    cells = _cells(result)
    assert cells["A4"]["value"] == "지역4"
    assert cells["B20"]["value"] == 200
    assert cells["D20"]["value"] == "끝"
    text = json.dumps(result, ensure_ascii=False)
    assert "보고서제목" not in text
    assert "작성메모" not in text
    assert seen_rows
    assert min(seen_rows) == 3
    assert max(seen_rows) == 20


def test_two_row_merged_header(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A3"] = "구분"
        ws.merge_cells("A3:A4")
        ws["B3"] = "매출"
        ws.merge_cells("B3:C3")
        ws["B4"], ws["C4"] = "1월", "2월"
        ws["D3"] = "비고"
        ws["A5"], ws["B5"], ws["C5"] = "서울", 10, 20
        ws["A6"], ws["B6"], ws["C6"], ws["D6"] = "부산", 30, 40, "확인"

    path = _save(tmp_path, build)

    result = _read(path, "A3:D6", {"rows": [3, 4]})

    _assert_success(result, "A3:D6", [3, 4])
    vertical = {"anchor": "A3", "range": "A3:A4"}
    horizontal = {"anchor": "B3", "range": "B3:C3"}
    assert result["table"]["columns"] == [
        {
            "key": "구분",
            "display_name": "구분",
            "display_derived": True,
            "source_column": "A",
            "column_index": 1,
            "headers": [
                _header(3, "A3", "구분", "string", {"role": "anchor", **vertical}),
                _header(4, "A4", None, "empty", {"role": "covered", **vertical}),
            ],
        },
        {
            "key": "매출_1월",
            "display_name": "매출_1월",
            "source_column": "B",
            "column_index": 2,
            "headers": [
                _header(3, "B3", "매출", "string", {"role": "anchor", **horizontal}),
                _header(4, "B4", "1월", "string"),
            ],
        },
        {
            "key": "매출_2월",
            "display_name": "매출_2월",
            "display_derived": True,
            "source_column": "C",
            "column_index": 3,
            "headers": [
                _header(3, "C3", None, "empty", {"role": "covered", **horizontal}),
                _header(4, "C4", "2월", "string"),
            ],
        },
        {
            "key": "비고",
            "display_name": "비고",
            "source_column": "D",
            "column_index": 4,
            "headers": [
                _header(3, "D3", "비고", "string"),
                _header(4, "D4", None, "empty"),
            ],
        },
    ]
    rows = result["table"]["rows"]
    assert [row["source_row"] for row in rows] == [5, 6]
    assert rows[1]["cells"] == [
        _plain_cell("A6", "구분", "부산", "string"),
        _plain_cell("B6", "매출_1월", 30, "number"),
        _plain_cell("C6", "매출_2월", 40, "number"),
        _plain_cell("D6", "비고", "확인", "string"),
    ]


def test_empty_merged_header_inside_range_marks_covered_column_derived(tmp_path):
    def build(wb):
        ws = wb.active
        ws.merge_cells("A1:B1")
        ws["A2"], ws["B2"] = 1, 2

    path = _save(tmp_path, build)

    result = _read(path, "A1:B2", {"rows": [1]})

    _assert_success(result, "A1:B2", [1])
    span = {"anchor": "A1", "range": "A1:B1"}
    assert result["table"]["columns"] == [
        {
            "key": "column_A",
            "display_name": None,
            "source_column": "A",
            "column_index": 1,
            "headers": [_header(1, "A1", None, "empty", {"role": "anchor", **span})],
        },
        {
            "key": "column_B",
            "display_name": None,
            "display_derived": True,
            "source_column": "B",
            "column_index": 2,
            "headers": [_header(1, "B1", None, "empty", {"role": "covered", **span})],
        },
    ]
    (row,) = result["table"]["rows"]
    assert [(cell["a1"], cell["key"], cell["value"]) for cell in row["cells"]] == [
        ("A2", "column_A", 1),
        ("B2", "column_B", 2),
    ]


def test_anchor_outside_range_leaves_display_piece_empty(tmp_path, monkeypatch):
    def build(wb):
        ws = wb.active
        ws["A2"] = "범위밖"
        ws.merge_cells("A2:B3")
        ws["D2"] = "위쪽"
        ws.merge_cells("D2:D3")
        ws["C3"] = "값"
        ws["B4"], ws["C4"], ws["D4"] = 1, 2, 3

    path = _save(tmp_path, build)
    seen_rows = _record_cell_rows(monkeypatch)

    result = _read(path, "B3:D5", {"rows": [3]})

    _assert_success(result, "B3:D5", [3])
    assert result["table"]["columns"] == [
        {
            "key": "column_B",
            "display_name": None,
            "source_column": "B",
            "column_index": 2,
            "headers": [_header(3, "B3", None, "empty", {"role": "covered", "anchor": "A2", "range": "A2:B3"})],
        },
        {
            "key": "값",
            "display_name": "값",
            "source_column": "C",
            "column_index": 3,
            "headers": [_header(3, "C3", "값", "string")],
        },
        {
            "key": "column_D",
            "display_name": None,
            "source_column": "D",
            "column_index": 4,
            "headers": [_header(3, "D3", None, "empty", {"role": "covered", "anchor": "D2", "range": "D2:D3"})],
        },
    ]
    text = json.dumps(result, ensure_ascii=False)
    assert "범위밖" not in text
    assert "위쪽" not in text
    assert "display_derived" not in text
    assert min(seen_rows) >= 3


def test_two_ranges_on_same_sheet_do_not_mix(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"] = "왼키", "왼값"
        ws["A2"], ws["B2"] = "a", 1
        ws["A3"], ws["B3"] = "b", 2
        ws["C1"] = "가운데"
        ws["E1"], ws["F1"] = "오키", "오값"
        ws["E2"], ws["F2"] = "x", 10
        ws["E3"], ws["F3"] = "y", 20

    path = _save(tmp_path, build)

    left = _read(path, "A1:B3", {"rows": [1]})
    right = _read(path, "E1:F3", {"rows": [1]})

    _assert_success(left, "A1:B3", [1])
    _assert_success(right, "E1:F3", [1])
    assert [c["key"] for c in left["table"]["columns"]] == ["왼키", "왼값"]
    assert [c["source_column"] for c in left["table"]["columns"]] == ["A", "B"]
    assert [c["key"] for c in right["table"]["columns"]] == ["오키", "오값"]
    assert [c["source_column"] for c in right["table"]["columns"]] == ["E", "F"]
    assert [[cell["value"] for cell in row["cells"]] for row in left["table"]["rows"]] == [["a", 1], ["b", 2]]
    assert [[cell["value"] for cell in row["cells"]] for row in right["table"]["rows"]] == [["x", 10], ["y", 20]]
    assert set(_cells(right)) == {"E2", "F2", "E3", "F3"}
    left_text = json.dumps(left, ensure_ascii=False)
    right_text = json.dumps(right, ensure_ascii=False)
    for word in ("오키", "오값", "가운데"):
        assert word not in left_text
    for word in ("왼키", "왼값", "가운데"):
        assert word not in right_text


def test_no_header_makes_every_row_data(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"] = "범위밖제목"
        ws["A2"], ws["B2"] = "가", 1
        ws["A3"], ws["B3"] = "나", 2

    path = _save(tmp_path, build)

    result = _read(path, "A2:B3", {"rows": []})

    _assert_success(result, "A2:B3", [])
    assert result["table"]["columns"] == [
        {"key": "column_A", "display_name": None, "source_column": "A", "column_index": 1, "headers": []},
        {"key": "column_B", "display_name": None, "source_column": "B", "column_index": 2, "headers": []},
    ]
    rows = result["table"]["rows"]
    assert [row["source_row"] for row in rows] == [2, 3]
    assert rows[0]["cells"] == [
        _plain_cell("A2", "column_A", "가", "string"),
        _plain_cell("B2", "column_B", 1, "number"),
    ]
    assert "범위밖제목" not in json.dumps(result, ensure_ascii=False)


def test_header_covering_whole_range_has_no_data_rows(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"] = "상위", "상위2"
        ws["A2"], ws["B2"] = "하위", "하위2"

    path = _save(tmp_path, build)

    one = _read(path, "A1:B1", {"rows": [1]})
    two = _read(path, "A1:B2", {"rows": [1, 2]})

    _assert_success(one, "A1:B1", [1])
    _assert_success(two, "A1:B2", [1, 2])
    assert one["table"]["rows"] == []
    assert two["table"]["rows"] == []
    assert [c["key"] for c in one["table"]["columns"]] == ["상위", "상위2"]
    assert [c["key"] for c in two["table"]["columns"]] == ["상위_하위", "상위2_하위2"]


def test_blank_and_duplicate_headers_keep_columns_and_get_unique_keys(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["C1"], ws["D1"], ws["E1"] = "column_B", "이름", "이름", "column_B_2"
        for column in range(1, 6):
            ws.cell(row=2, column=column, value=column)

    path = _save(tmp_path, build)

    result = _read(path, "A1:E2", {"rows": [1]})

    _assert_success(result, "A1:E2", [1])
    columns = result["table"]["columns"]
    assert [c["display_name"] for c in columns] == ["column_B", None, "이름", "이름", "column_B_2"]
    assert [c["key"] for c in columns] == ["column_B", "column_B_2", "이름", "column_D", "column_E"]
    assert [c["headers"][0]["value"] for c in columns] == ["column_B", None, "이름", "이름", "column_B_2"]
    assert columns[1]["headers"] == [_header(1, "B1", None, "empty")]
    (row,) = result["table"]["rows"]
    assert [(cell["a1"], cell["key"], cell["value"]) for cell in row["cells"]] == [
        ("A2", "column_B", 1),
        ("B2", "column_B_2", 2),
        ("C2", "이름", 3),
        ("D2", "column_D", 4),
        ("E2", "column_E", 5),
    ]


def test_blank_rows_and_columns_are_kept(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"], ws["D1"] = "a", "b", "d"
        ws["A2"], ws["B2"], ws["D2"] = 1, 2, 4
        ws["A3"], ws["B3"], ws["D3"] = 5, 6, 8
        ws["A5"], ws["D5"] = 9, 12
        ws["B6"] = 14

    path = _save(tmp_path, build)

    result = _read(path, "A1:D6", {"rows": [1]})

    _assert_success(result, "A1:D6", [1])
    columns = result["table"]["columns"]
    assert [c["key"] for c in columns] == ["a", "b", "column_C", "d"]
    assert columns[2]["display_name"] is None
    rows = result["table"]["rows"]
    assert [row["source_row"] for row in rows] == [2, 3, 4, 5, 6]
    assert rows[2]["cells"] == [
        _plain_cell("A4", "a", None, "empty"),
        _plain_cell("B4", "b", None, "empty"),
        _plain_cell("C4", "column_C", None, "empty"),
        _plain_cell("D4", "d", None, "empty"),
    ]
    for row in rows:
        assert row["cells"][2] == _plain_cell(f"C{row['source_row']}", "column_C", None, "empty")
    assert [cell["value"] for cell in rows[3]["cells"]] == [9, None, None, 12]
    assert [cell["value"] for cell in rows[4]["cells"]] == [None, 14, None, None]


# --- 값 ---


def test_string_code_and_number_format_are_distinct(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"] = "코드문자", "코드숫자"
        ws["A2"] = "00123"
        ws["B2"] = 123
        ws["B2"].number_format = "00000"

    path = _save(tmp_path, build)

    result = _read(path, "A1:B2", {"rows": [1]})

    _assert_success(result, "A1:B2", [1])
    a2, b2 = result["table"]["rows"][0]["cells"]
    assert a2 == _plain_cell("A2", "코드문자", "00123", "string")
    assert b2 == _plain_cell("B2", "코드숫자", 123, "number", "00000")
    assert type(b2["value"]) is int


def test_read_range_response_still_has_no_number_format(tmp_path):
    def build(wb):
        ws = wb.active
        ws["B2"] = 123
        ws["B2"].number_format = "00000"

    path = _save(tmp_path, build)

    result = read_range(path, address="A1:B2", display_name=DISPLAY_NAME, request_id=REQUEST_ID)

    assert result["status"] == "success"
    assert "number_format" not in json.dumps(result)


def test_formula_cached_zero_and_absent(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"], ws["C1"], ws["D1"] = "차", "합", "=2026", "=1+1"
        ws["A2"], ws["B2"], ws["C2"], ws["D2"] = "=1-1", "=2+2", "=3*1", "=1/0"
        ws["A3"] = 1

    path = _save(tmp_path, build)
    _rewrite_formula_cache(
        path,
        {"C1": "2026", "A2": "0", "C2": "3", "D2": "#DIV/0!"},
        drop=("D1", "B2"),
        errors=("D2",),
    )
    xml = _sheet_xml(path)
    assert "<v>0</v>" in _cell_xml(xml, "A2")
    assert "<v" not in _cell_xml(xml, "B2")
    assert "<v" not in _cell_xml(xml, "D1")

    result = _read(path, "A1:D3", {"rows": [1]})

    _assert_success(result, "A1:D3", [1])
    columns = result["table"]["columns"]
    assert [c["key"] for c in columns] == ["차", "합", "2026", "column_D"]
    assert [c["display_name"] for c in columns] == ["차", "합", "2026", None]
    assert columns[0]["headers"] == [_header(1, "A1", "차", "string")]
    assert columns[2]["headers"] == [
        {
            "row": 1,
            "a1": "C1",
            "value": 2026,
            "value_type": "number",
            "merge": None,
            "formula": "=2026",
            "cached_value": 2026,
            "cached_value_state": "stored",
        }
    ]
    assert columns[3]["headers"] == [
        {
            "row": 1,
            "a1": "D1",
            "value": None,
            "value_type": "formula",
            "merge": None,
            "formula": "=1+1",
            "cached_value": None,
            "cached_value_state": "absent",
        }
    ]
    row2 = result["table"]["rows"][0]["cells"]
    assert row2 == [
        {
            "a1": "A2",
            "key": "차",
            "value": 0,
            "value_type": "number",
            "number_format": "General",
            "formula": "=1-1",
            "cached_value": 0,
            "cached_value_state": "stored",
        },
        {
            "a1": "B2",
            "key": "합",
            "value": None,
            "value_type": "formula",
            "number_format": "General",
            "formula": "=2+2",
            "cached_value": None,
            "cached_value_state": "absent",
        },
        {
            "a1": "C2",
            "key": "2026",
            "value": 3,
            "value_type": "number",
            "number_format": "General",
            "formula": "=3*1",
            "cached_value": 3,
            "cached_value_state": "stored",
        },
        {
            "a1": "D2",
            "key": "column_D",
            "value": "#DIV/0!",
            "value_type": "error",
            "number_format": "General",
            "formula": "=1/0",
            "cached_value": "#DIV/0!",
            "cached_value_state": "stored",
        },
    ]
    assert type(row2[0]["value"]) is int
    assert result["recalculated"] is False
    assert result["freshness"] == "unconfirmed"


def test_covered_data_cells_are_null_without_copy(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"] = "항목", "값"
        ws["A2"] = "병합값"
        ws.merge_cells("A2:B3")
        ws["A4"], ws["B4"] = "끝", 1

    path = _save(tmp_path, build)

    result = _read(path, "A1:B4", {"rows": [1]})

    _assert_success(result, "A1:B4", [1])
    span = {"anchor": "A2", "range": "A2:B3"}
    cells = _cells(result)
    assert cells["A2"] == {**_plain_cell("A2", "항목", "병합값", "string"), "merge": {"role": "anchor", **span}}
    for a1, key in (("B2", "값"), ("A3", "항목"), ("B3", "값")):
        assert cells[a1] == {**_plain_cell(a1, key, None, "empty", None), "merge": {"role": "covered", **span}}
    assert "merge" not in cells["A4"]
    assert "merge" not in cells["B4"]
    assert json.dumps(result["table"]["rows"], ensure_ascii=False).count("병합값") == 1


# --- 실패 ---


def _plain_book(tmp_path: Path, name: str = "book.xlsx") -> Path:
    return _save(tmp_path, lambda wb: wb.active.__setitem__("A3", "h"), name=name)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        *[
            (header, HEADER_MALFORMED)
            for header in (
                "3",
                3,
                [3],
                {},
                {"row": [3]},
                {"rows": 3},
                {"rows": "3"},
                {"rows": None},
                {"rows": (3,)},
                {"rows": [True]},
                {"rows": [3, False]},
                {"rows": ["3"]},
                {"rows": [3.0]},
            )
        ],
        *[({"rows": rows}, HEADER_OUTSIDE) for rows in ([1], [2, 3], [21], [0], [-3], [20, 21])],
        *[({"rows": rows}, HEADER_ORDER) for rows in ([4, 3], [3, 3], [3, 5])],
        *[({"rows": rows}, HEADER_NOT_FIRST) for rows in ([4], [4, 5], [20])],
        *[({"rows": rows}, HEADER_TOO_MANY) for rows in ([3, 4, 5], [3, 4, 5, 6])],
    ],
)
def test_invalid_header_is_rejected_before_opening(tmp_path, header, message):
    path = _plain_book(tmp_path)

    _assert_failure(_read(path, "A3:D20", header), "invalid_header", message)
    _assert_failure(_read(tmp_path / "missing.xlsx", "A3:D20", header), "invalid_header", message)
    _assert_failure(_read(tmp_path / "missing.csv", "A3:D20", header), "invalid_header", message)


def test_missing_header_is_rejected_before_opening(tmp_path):
    path = _plain_book(tmp_path)

    _assert_failure(_read(path, "A3:D20", None), "missing_header")
    _assert_failure(_read(tmp_path / "missing.xlsx", "A3:D20", None), "missing_header")
    _assert_failure(_read(tmp_path / "missing.csv", "A3:D20", None), "missing_header")


@pytest.mark.parametrize(
    ("address", "code"),
    [
        ("A1:B2:C3", "invalid_address"),
        ("Sheet1!A3", "invalid_address"),
        ("A0", "invalid_address"),
        ("", "invalid_address"),
        ("D20:A3", "reversed_range"),
        ("B3:A5", "reversed_range"),
    ],
)
def test_address_errors_come_before_header(tmp_path, address, code):
    path = _plain_book(tmp_path)

    _assert_failure(_read(path, address, {"rows": []}), code)
    _assert_failure(_read(path, address, None), code)
    _assert_failure(_read(path, address, {"rows": [99]}), code)


@pytest.mark.parametrize("sheet", ["없음", "data", 3, -1, True])
def test_invalid_sheet(tmp_path, sheet):
    def build(wb):
        wb.active.title = "Sheet1"
        wb.create_sheet("Data")

    path = _save(tmp_path, build)

    _assert_failure(_read(path, "A1", {"rows": []}, sheet=sheet), "invalid_sheet")


def test_sheet_by_name_and_index(tmp_path):
    def build(wb):
        wb.active.title = "Sheet1"
        wb.active["A1"] = "first"
        ws = wb.create_sheet("Data")
        ws["A1"], ws["A2"] = "헤더", "second"

    path = _save(tmp_path, build)

    by_name = _read(path, "A1:A2", {"rows": [1]}, sheet="Data")
    by_index = _read(path, "A1:A2", {"rows": [1]}, sheet=1)

    for result in (by_name, by_index):
        _assert_success(result, "A1:A2", [1])
        assert result["source"] == {"display_name": DISPLAY_NAME, "sheet_name": "Data", "sheet_index": 1}
        assert result["table"]["rows"][0]["cells"][0]["value"] == "second"


@pytest.mark.parametrize("suffix", [".csv", ".xls", ".xlsm"])
def test_non_xlsx_extension_is_unsupported(tmp_path, suffix):
    path = _plain_book(tmp_path, name=f"book{suffix}")

    _assert_failure(_read(path, "A3", {"rows": []}), "unsupported_format")


def test_broken_and_missing_xlsx_are_invalid_file(tmp_path):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a zip archive")

    _assert_failure(_read(broken, "A1", {"rows": []}), "invalid_file")
    _assert_failure(_read(tmp_path / "missing.xlsx", "A1", {"rows": []}), "invalid_file")


def test_unexpected_error_uses_fixed_message(tmp_path, monkeypatch):
    from openpyxl.worksheet.worksheet import Worksheet

    path = _plain_book(tmp_path)
    secret = str(tmp_path / "secret-path")

    def broken_cell(self, *args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(Worksheet, "cell", broken_cell)

    result = _read(path, "A3:B4", {"rows": [3]})

    _assert_failure(result, "execution_failed")
    assert result["error"]["exception_type"] == "RuntimeError"
    text = json.dumps(result, ensure_ascii=False)
    assert secret not in text
    assert str(tmp_path) not in text


def test_cell_reader_exception_is_execution_failed(tmp_path, monkeypatch):
    path = _plain_book(tmp_path)
    secret = str(tmp_path / "value-secret")

    def unsupported(cell, cached_worksheet):
        raise TypeError(secret)

    monkeypatch.setattr(table_reader, "_cell_fields", unsupported)

    result = _read(path, "A3:B4", {"rows": [3]})

    _assert_failure(result, "execution_failed")
    assert result["error"]["exception_type"] == "TypeError"
    assert secret not in json.dumps(result, ensure_ascii=False)


# --- 크기 ---


def test_two_thousand_cells_including_header_is_allowed(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"] = "열1", "열2"
        ws["A1000"] = "last"

    path = _save(tmp_path, build)

    result = _read(path, "A1:B1000", {"rows": [1]})

    _assert_success(result, "A1:B1000", [1])
    rows = result["table"]["rows"]
    assert len(rows) == 999
    assert len(result["table"]["columns"]) * (1 + len(rows)) == 2000
    assert rows[-1]["source_row"] == 1000
    assert rows[-1]["cells"][0]["value"] == "last"


@pytest.mark.parametrize("address", ["A1:A2001", "A1:B1001"])
def test_over_two_thousand_cells_is_rejected_before_opening(tmp_path, address):
    path = _plain_book(tmp_path)

    _assert_failure(_read(path, address, {"rows": [1]}), "range_too_large")
    _assert_failure(_read(tmp_path / "missing.xlsx", address, {"rows": [1]}), "range_too_large")


# --- 경로, 무결성, 종료 ---


def _merged_book(wb):
    ws = wb.active
    ws["A1"], ws["B1"] = "헤더", "값"
    ws["A2"] = 10
    ws.merge_cells("A2:A3")
    ws["C1"] = "=A2*2"


def test_results_serialize_without_paths(tmp_path):
    path = _save(tmp_path, _merged_book)
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"broken")

    results = [
        _read(path, "A1:C5", {"rows": [1]}),
        _read(path, "A1:C5", {"rows": []}),
        _read(path, "A1", {"rows": []}, sheet="없음"),
        _read(path, "A1:C5", None),
        _read(path, "A1:C5", {"rows": [2]}),
        _read(path, "D20:A3", {"rows": []}),
        _read(path, "A1:A2001", {"rows": [1]}),
        _read(tmp_path / "book.csv", "A1", {"rows": []}),
        _read(broken, "A1", {"rows": []}),
        _read(tmp_path / "missing.xlsx", "A1", {"rows": []}),
    ]

    assert results[0]["status"] == "success"
    assert results[1]["status"] == "success"
    for result in results:
        text = json.dumps(result, ensure_ascii=False)
        json.loads(text)
        assert str(path.resolve()) not in text
        assert str(path) not in text
        assert str(tmp_path) not in text
        assert str(broken) not in text
        if result["status"] != "success":
            assert "table" not in result


def test_reading_does_not_modify_file(tmp_path):
    path = _save(tmp_path, _merged_book)
    _rewrite_formula_cache(path, {"C1": "20"})
    before = _sha256(path)
    size_before = path.stat().st_size

    assert _read(path, "A1:C5", {"rows": [1]})["status"] == "success"
    assert _read(path, "A1:C5", {"rows": []})["status"] == "success"
    assert _read(path, "A1", {"rows": []}, sheet="없음")["status"] == "invalid_request"

    assert _sha256(path) == before
    assert path.stat().st_size == size_before


def test_workbooks_open_twice_close_always_and_never_save(tmp_path, monkeypatch):
    from openpyxl.workbook.workbook import Workbook as OpenpyxlWorkbook
    from openpyxl.worksheet.worksheet import Worksheet

    path = _save(tmp_path, _merged_book)
    plain = _plain_book(tmp_path, name="plain.xlsx")

    opened: list[int] = []
    closed: list[int] = []
    loads: list[tuple[bool, bool]] = []
    original_init = OpenpyxlWorkbook.__init__
    original_close = OpenpyxlWorkbook.close
    original_load = cell_reader.load_workbook

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        opened.append(id(self))

    def tracking_close(self):
        closed.append(id(self))
        return original_close(self)

    def forbidden_save(self, *args, **kwargs):
        raise AssertionError("Workbook.save must not be called")

    def tracking_load(filename, *args, **kwargs):
        loads.append((kwargs.get("read_only"), kwargs.get("data_only")))
        return original_load(filename, *args, **kwargs)

    monkeypatch.setattr(OpenpyxlWorkbook, "__init__", tracking_init)
    monkeypatch.setattr(OpenpyxlWorkbook, "close", tracking_close)
    monkeypatch.setattr(OpenpyxlWorkbook, "save", forbidden_save)
    monkeypatch.setattr(cell_reader, "load_workbook", tracking_load)

    assert _read(path, "A1:C3", {"rows": [1]})["status"] == "success"
    assert loads == [(False, False), (False, True)]
    assert len(opened) == 2
    assert sorted(opened) == sorted(closed)

    assert _read(path, "A1", {"rows": []}, sheet="없음")["status"] == "invalid_request"
    assert len(opened) == 4
    assert sorted(opened) == sorted(closed)

    def broken_cell(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(Worksheet, "cell", broken_cell)
    assert _read(plain, "A3", {"rows": []})["status"] == "execution_failed"
    assert len(opened) == 6
    assert sorted(opened) == sorted(closed)
    assert loads == [(False, False), (False, True)] * 3


def test_module_does_not_import_analysis_loaders():
    tree = ast.parse(Path(table_reader.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)
    forbidden = {"pandas", "load_excel", "load_tabular", "sanitize_dataframe", "normalize_dataframe"}
    assert not (imported & forbidden)
    assert not any(name.startswith(("pandas", "core.io.excel_loader", "core.io.normalize")) for name in imported)
