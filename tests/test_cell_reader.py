"""DEV-4 셀 범위 읽기 검사. 합성 xlsx만 쓴다."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from core.io import cell_reader
from core.io.cell_reader import read_range

DISPLAY_NAME = "book.xlsx"
REQUEST_ID = "dev4-test-1"

EXPECTED_ERRORS = {
    "invalid_address": ("invalid_request", "request", "셀 주소가 올바르지 않다."),
    "reversed_range": ("invalid_request", "request", "범위의 시작이 끝보다 뒤다."),
    "range_too_large": ("invalid_request", "request", "범위가 2000칸을 넘는다."),
    "unsupported_format": ("invalid_request", "request", "xlsx 파일만 읽을 수 있다."),
    "invalid_sheet": ("invalid_request", "file_loading", "시트를 찾을 수 없다."),
    "invalid_file": ("invalid_request", "file_loading", "통합 문서를 열 수 없다."),
    "execution_failed": ("execution_failed", "read", "셀 범위를 읽지 못했다."),
}

FAILURE_ABSENT_KEYS = ("cells", "source", "requested", "returned", "values_from")


def _save(tmp_path: Path, build, name: str = "book.xlsx") -> Path:
    wb = Workbook()
    build(wb)
    path = tmp_path / name
    wb.save(path)
    return path


def _read(path: Path, address: str, sheet=0) -> dict:
    return read_range(
        path,
        sheet=sheet,
        address=address,
        display_name=DISPLAY_NAME,
        request_id=REQUEST_ID,
    )


def _by_a1(result: dict) -> dict[str, dict]:
    return {cell["a1"]: cell for cell in result["cells"]}


def _assert_success(result: dict, address: str, count: int) -> None:
    assert result["contract_version"] == "1.0"
    assert result["request_id"] == REQUEST_ID
    assert result["status"] == "success"
    assert result["operation"] == "read_range"
    assert result["recalculated"] is False
    assert result["values_from"] == "stored_workbook"
    assert result["requested"] == {"address": address}
    assert result["returned"] == {"address": address, "cell_count": count}
    assert len(result["cells"]) == count
    assert "error" not in result


def _assert_failure(result: dict, code: str) -> None:
    status, stage, message = EXPECTED_ERRORS[code]
    assert result["contract_version"] == "1.0"
    assert result["request_id"] == REQUEST_ID
    assert result["status"] == status
    assert result["operation"] == "read_range"
    assert result["recalculated"] is False
    for key in FAILURE_ABSENT_KEYS:
        assert key not in result
    error = result["error"]
    assert error["code"] == code
    assert error["message"] == message
    assert error["stage"] == stage
    assert error["retryable"] is False
    if code in ("invalid_file", "execution_failed"):
        assert isinstance(error["exception_type"], str) and error["exception_type"]
    else:
        assert "exception_type" not in error


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_cached_values(path: Path, cached: dict[str, str], errors: tuple[str, ...] = ()) -> None:
    """openpyxl이 저장한 시트 XML의 수식 칸에 저장 계산값을 직접 넣는다."""
    member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    rewritten = []
    for info, data in entries:
        if info.filename == member:
            xml = data.decode("utf-8")
            for ref, value in cached.items():
                cell_re = re.compile(rf'<c r="{ref}"([^>]*)>(.*?)</c>', re.S)
                match = cell_re.search(xml)
                assert match is not None, ref
                attrs, body = match.group(1), match.group(2)
                assert "<f" in body, ref
                body, replaced = re.subn(r"<v\s*/>|<v>\s*</v>", f"<v>{value}</v>", body)
                assert replaced == 1, ref
                if ref in errors:
                    attrs = re.sub(r'\st="[^"]*"', "", attrs) + ' t="e"'
                xml = xml[: match.start()] + f'<c r="{ref}"{attrs}>{body}</c>' + xml[match.end():]
            data = xml.encode("utf-8")
        rewritten.append((info, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)


# --- 좌표 ---


def test_single_cell_keeps_original_row(tmp_path):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("B7", 10))

    result = _read(path, "B7")

    _assert_success(result, "B7", 1)
    assert result["source"] == {"display_name": DISPLAY_NAME, "sheet_name": "Sheet", "sheet_index": 0}
    assert result["cells"] == [
        {
            "a1": "B7",
            "row": 7,
            "column_index": 2,
            "column_letter": "B",
            "value": 10,
            "value_type": "number",
            "formula": None,
            "cached_value": None,
            "cached_value_state": "not_applicable",
            "merge": None,
        }
    ]


def test_rectangle_is_complete_and_row_major(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A3"] = "좌상"
        ws["D3"] = "우상"
        ws["A20"] = "좌하"
        ws["D20"] = "우하"
        ws["B10"] = 5

    path = _save(tmp_path, build)

    result = _read(path, "A3:D20")

    _assert_success(result, "A3:D20", 72)
    cells = result["cells"]
    assert [cell["a1"] for cell in cells[:6]] == ["A3", "B3", "C3", "D3", "A4", "B4"]
    assert [cell["a1"] for cell in cells[-5:]] == ["D19", "A20", "B20", "C20", "D20"]
    assert len({cell["a1"] for cell in cells}) == 72
    positions = [(cell["row"], cell["column_index"]) for cell in cells]
    assert positions == sorted(positions)

    by_a1 = _by_a1(result)
    assert by_a1["A3"]["value"] == "좌상"
    assert by_a1["D3"]["value"] == "우상"
    assert by_a1["A20"]["value"] == "좌하"
    assert by_a1["D20"]["value"] == "우하"
    assert by_a1["B10"]["value"] == 5
    for blank in ("C12", "A15", "D15"):
        cell = by_a1[blank]
        assert cell["value"] is None
        assert cell["value_type"] == "empty"
        assert cell["cached_value_state"] == "not_applicable"
        assert cell["merge"] is None
    assert by_a1["C12"]["row"] == 12
    assert by_a1["C12"]["column_index"] == 3
    assert by_a1["C12"]["column_letter"] == "C"


@pytest.mark.parametrize(
    ("address", "row", "column_index", "column_letter"),
    [("A1", 1, 1, "A"), ("XFD1048576", 1048576, 16384, "XFD")],
)
def test_sheet_edges_are_empty_cells(tmp_path, address, row, column_index, column_letter):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("C3", "x"))

    result = _read(path, address)

    _assert_success(result, address, 1)
    cell = result["cells"][0]
    assert cell["a1"] == address
    assert (cell["row"], cell["column_index"], cell["column_letter"]) == (row, column_index, column_letter)
    assert cell["value"] is None
    assert cell["value_type"] == "empty"
    assert cell["formula"] is None
    assert cell["cached_value"] is None
    assert cell["cached_value_state"] == "not_applicable"


# --- 값 ---


def test_scalar_values_keep_their_types(tmp_path):
    def build(wb):
        ws = wb.active
        ws["B1"] = "텍스트"
        ws["B2"] = 42
        ws["B3"] = 3.5
        ws["B4"] = True
        ws["B5"] = False
        ws["B6"] = dt.datetime(2026, 10, 19)
        ws["B7"] = dt.time(13, 45, 9)
        ws["B8"] = "#DIV/0!"
        ws["B9"] = "#태그"

    path = _save(tmp_path, build)

    result = _read(path, "B1:B9")

    _assert_success(result, "B1:B9", 9)
    by_a1 = _by_a1(result)
    expected = {
        "B1": ("텍스트", "string"),
        "B2": (42, "number"),
        "B3": (3.5, "number"),
        "B4": (True, "boolean"),
        "B5": (False, "boolean"),
        "B6": ("2026-10-19T00:00:00", "datetime"),
        "B7": ("13:45:09", "datetime"),
        "B8": ("#DIV/0!", "error"),
        "B9": ("#태그", "string"),
    }
    for a1, (value, value_type) in expected.items():
        cell = by_a1[a1]
        assert cell["value"] == value, a1
        assert cell["value_type"] == value_type, a1
        assert cell["formula"] is None, a1
        assert cell["cached_value"] is None, a1
        assert cell["cached_value_state"] == "not_applicable", a1
    assert type(by_a1["B2"]["value"]) is int
    assert type(by_a1["B4"]["value"]) is bool
    assert type(by_a1["B5"]["value"]) is bool


def test_formula_saved_by_openpyxl_has_no_cached_value(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"] = 2
        ws["B1"] = 3
        ws["C1"] = "=A1+B1"

    path = _save(tmp_path, build)

    result = _read(path, "C1")

    _assert_success(result, "C1", 1)
    cell = result["cells"][0]
    assert cell["value"] is None
    assert cell["value_type"] == "formula"
    assert cell["formula"].startswith("=")
    assert cell["formula"] == "=A1+B1"
    assert cell["cached_value"] is None
    assert cell["cached_value_state"] == "absent"
    assert result["recalculated"] is False


def test_stored_cached_values_are_reported_without_recalculation(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"] = 10
        ws["B1"] = 10
        ws["C1"] = "=A1+B1"
        ws["C2"] = "=A1-B1"
        ws["C3"] = "=1/0"

    path = _save(tmp_path, build)
    _set_cached_values(path, {"C1": "20", "C2": "0", "C3": "#DIV/0!"}, errors=("C3",))

    result = _read(path, "C1:C3")

    _assert_success(result, "C1:C3", 3)
    by_a1 = _by_a1(result)
    assert by_a1["C1"]["formula"] == "=A1+B1"
    assert by_a1["C1"]["cached_value"] == 20
    assert by_a1["C1"]["cached_value_state"] == "stored"
    assert by_a1["C2"]["formula"] == "=A1-B1"
    assert by_a1["C2"]["cached_value"] == 0
    assert by_a1["C2"]["cached_value"] is not None
    assert by_a1["C2"]["cached_value_state"] == "stored"
    assert by_a1["C3"]["formula"] == "=1/0"
    assert by_a1["C3"]["cached_value"] == "#DIV/0!"
    assert by_a1["C3"]["cached_value_state"] == "stored"
    for cell in result["cells"]:
        assert cell["value"] is None
        assert cell["value_type"] == "formula"
    assert result["recalculated"] is False


# --- 병합 ---


def _merged_book(wb):
    ws = wb.active
    ws["B7"] = 10
    ws.merge_cells("B7:B8")


def test_merge_anchor_holds_value_and_covered_cell_is_empty(tmp_path):
    path = _save(tmp_path, _merged_book)

    result = _read(path, "B7:B8")

    _assert_success(result, "B7:B8", 2)
    anchor, covered = result["cells"]
    assert anchor["a1"] == "B7"
    assert anchor["value"] == 10
    assert anchor["value_type"] == "number"
    assert anchor["merge"] == {"role": "anchor", "anchor": "B7", "range": "B7:B8"}
    assert covered["a1"] == "B8"
    assert covered["value"] is None
    assert covered["value_type"] == "empty"
    assert covered["formula"] is None
    assert covered["cached_value"] is None
    assert covered["cached_value_state"] == "not_applicable"
    assert covered["merge"] == {"role": "covered", "anchor": "B7", "range": "B7:B8"}


def test_covered_cell_alone_points_to_anchor_outside_request(tmp_path):
    path = _save(tmp_path, _merged_book)

    result = _read(path, "B8")

    _assert_success(result, "B8", 1)
    cell = result["cells"][0]
    assert cell["a1"] == "B8"
    assert cell["row"] == 8
    assert cell["value"] is None
    assert cell["value_type"] == "empty"
    assert cell["merge"] == {"role": "covered", "anchor": "B7", "range": "B7:B8"}


# --- 시트 ---


def _three_sheets(wb):
    first = wb.active
    first.title = "Sheet1"
    first["A1"] = "first"
    wb.create_sheet("Data")["A1"] = "second"
    wb.create_sheet("0")["A1"] = "zero"


@pytest.mark.parametrize(
    ("sheet", "sheet_name", "sheet_index", "value"),
    [
        ("Data", "Data", 1, "second"),
        (0, "Sheet1", 0, "first"),
        (1, "Data", 1, "second"),
        ("0", "0", 2, "zero"),
    ],
)
def test_sheet_selection(tmp_path, sheet, sheet_name, sheet_index, value):
    path = _save(tmp_path, _three_sheets)

    result = _read(path, "A1", sheet=sheet)

    _assert_success(result, "A1", 1)
    assert result["source"] == {
        "display_name": DISPLAY_NAME,
        "sheet_name": sheet_name,
        "sheet_index": sheet_index,
    }
    assert result["cells"][0]["value"] == value


@pytest.mark.parametrize("sheet", ["없음", "data", 3, -1, True, False])
def test_invalid_sheet(tmp_path, sheet):
    path = _save(tmp_path, _three_sheets)

    _assert_failure(_read(path, "A1", sheet=sheet), "invalid_sheet")


# --- 주소 ---


@pytest.mark.parametrize(
    ("address", "normalized", "first", "last"),
    [
        ("$b$7", "B7", "B7", "B7"),
        ("$A3:d$20", "A3:D20", "A3", "D20"),
        ("b7:B7", "B7:B7", "B7", "B7"),
    ],
)
def test_dollar_and_lowercase_are_normalized(tmp_path, address, normalized, first, last):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("B7", 1))

    result = _read(path, address)

    assert result["status"] == "success"
    assert result["requested"]["address"] == normalized
    assert result["returned"]["address"] == normalized
    assert result["cells"][0]["a1"] == first
    assert result["cells"][-1]["a1"] == last
    assert "$" not in json.dumps(result)


INVALID_ADDRESSES = [
    "A01",
    "A0",
    " Sheet1!B7",
    "Sheet1!B7",
    "A:C",
    "3:5",
    " A1",
    "A1 ",
    "A 1",
    "A1: B2",
    "A1:B2,C3",
    "A1:B2:C3",
    "XFE1",
    "A1048577",
    "",
    "B7\n",
]


@pytest.mark.parametrize("address", INVALID_ADDRESSES)
def test_invalid_address(tmp_path, address):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1))

    _assert_failure(_read(path, address), "invalid_address")
    _assert_failure(_read(tmp_path / "missing.xlsx", address), "invalid_address")


@pytest.mark.parametrize("address", ["D20:A3", "B3:A5", "A5:B3"])
def test_reversed_range_is_rejected_not_flipped(tmp_path, address):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1))

    _assert_failure(_read(path, address), "reversed_range")
    _assert_failure(_read(tmp_path / "missing.xlsx", address), "reversed_range")


# --- 크기 ---


def test_two_thousand_cells_is_allowed(tmp_path):
    def build(wb):
        ws = wb.active
        ws["A1"] = "first"
        ws["A2000"] = "last"

    path = _save(tmp_path, build)

    result = _read(path, "A1:A2000")

    _assert_success(result, "A1:A2000", 2000)
    assert result["cells"][0]["a1"] == "A1"
    assert result["cells"][0]["value"] == "first"
    assert result["cells"][-1]["a1"] == "A2000"
    assert result["cells"][-1]["value"] == "last"


@pytest.mark.parametrize("address", ["A1:A2001", "A1:B1001"])
def test_over_two_thousand_cells_is_rejected_before_reading(tmp_path, address):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1))

    _assert_failure(_read(path, address), "range_too_large")
    _assert_failure(_read(tmp_path / "missing.xlsx", address), "range_too_large")
    _assert_failure(_read(tmp_path / "missing.csv", address), "range_too_large")


# --- 형식과 파일 ---


@pytest.mark.parametrize("suffix", [".csv", ".xls", ".xlsm"])
def test_non_xlsx_extension_is_unsupported_even_with_xlsx_content(tmp_path, suffix):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1), name=f"book{suffix}")

    _assert_failure(_read(path, "A1"), "unsupported_format")
    _assert_failure(_read(tmp_path / f"missing{suffix}", "A1"), "unsupported_format")


def test_extension_check_ignores_case(tmp_path):
    path = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1), name="BOOK.XLSX")

    result = _read(path, "A1")

    _assert_success(result, "A1", 1)
    assert result["cells"][0]["value"] == 1


def test_broken_xlsx_is_invalid_file_without_path(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is not a zip archive")

    result = _read(path, "A1")

    _assert_failure(result, "invalid_file")
    text = json.dumps(result, ensure_ascii=False)
    assert str(path) not in text
    assert str(tmp_path) not in text
    assert "broken.xlsx" not in text


def test_missing_xlsx_is_invalid_file(tmp_path):
    _assert_failure(_read(tmp_path / "missing.xlsx", "A1"), "invalid_file")


def test_unexpected_error_uses_fixed_message(tmp_path, monkeypatch):
    from openpyxl.worksheet.worksheet import Worksheet

    path = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1))
    secret = str(tmp_path / "secret-path")

    def broken_cell(self, *args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(Worksheet, "cell", broken_cell)

    result = _read(path, "A1")

    _assert_failure(result, "execution_failed")
    assert result["error"]["exception_type"] == "RuntimeError"
    text = json.dumps(result, ensure_ascii=False)
    assert secret not in text
    assert str(tmp_path) not in text


# --- 경로, 무결성, 종료 ---


def test_results_serialize_without_input_path(tmp_path):
    path = _save(tmp_path, _merged_book)
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"broken")

    results = [
        _read(path, "A1:C9"),
        _read(path, "B8"),
        _read(path, "A1", sheet="없음"),
        _read(path, "D20:A3"),
        _read(path, "A1:A2001"),
        _read(tmp_path / "book.csv", "A1"),
        _read(broken, "A1"),
        _read(tmp_path / "missing.xlsx", "A1"),
    ]

    assert results[0]["status"] == "success"
    for result in results:
        text = json.dumps(result, ensure_ascii=False)
        assert str(path.resolve()) not in text
        assert str(path) not in text
        assert str(tmp_path) not in text
        assert str(broken) not in text


def test_reading_does_not_modify_file(tmp_path):
    def build(wb):
        _merged_book(wb)
        wb.active["C1"] = "=B7*2"

    path = _save(tmp_path, build)
    _set_cached_values(path, {"C1": "20"})
    before = _sha256(path)
    stat_before = path.stat().st_size

    assert _read(path, "A1:D10")["status"] == "success"
    assert _read(path, "XFD1048576")["status"] == "success"
    assert _read(path, "A1", sheet="없음")["status"] == "invalid_request"

    assert _sha256(path) == before
    assert path.stat().st_size == stat_before


def test_workbooks_are_closed_and_never_saved(tmp_path, monkeypatch):
    from openpyxl.workbook.workbook import Workbook as OpenpyxlWorkbook
    from openpyxl.worksheet.worksheet import Worksheet

    path = _save(tmp_path, _merged_book)
    plain = _save(tmp_path, lambda wb: wb.active.__setitem__("A1", 1), name="plain.xlsx")

    opened: list[int] = []
    closed: list[int] = []
    original_init = OpenpyxlWorkbook.__init__
    original_close = OpenpyxlWorkbook.close

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        opened.append(id(self))

    def tracking_close(self):
        closed.append(id(self))
        return original_close(self)

    def forbidden_save(self, *args, **kwargs):
        raise AssertionError("Workbook.save must not be called")

    monkeypatch.setattr(OpenpyxlWorkbook, "__init__", tracking_init)
    monkeypatch.setattr(OpenpyxlWorkbook, "close", tracking_close)
    monkeypatch.setattr(OpenpyxlWorkbook, "save", forbidden_save)

    assert _read(path, "B7:B8")["status"] == "success"
    assert _read(path, "A1", sheet="없음")["status"] == "invalid_request"

    def broken_cell(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(Worksheet, "cell", broken_cell)
    assert _read(plain, "A1")["status"] == "execution_failed"

    assert opened
    assert sorted(opened) == sorted(closed)


def test_module_does_not_import_analysis_loaders():
    tree = ast.parse(Path(cell_reader.__file__).read_text(encoding="utf-8"))
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
