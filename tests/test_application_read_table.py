"""DEV-5 read_table의 CLI·래퍼 연결. 합성 xlsx는 tmp_path에만 만들고 Linux에서만 실행한다."""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook

from core.application import cli
from core.application.contracts import CLI_EXIT_CODES, SUPPORTED_OPERATIONS, ContractError
from core.application.headless import parse_analyze_request
from core.application.wrapper_config import load_operator_config
from core.application.wrapper_run import ProcessLaunch, run_job
from core.io.table_reader import read_table

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="DEV-2 래퍼는 Linux만 지원한다")

PUBLIC_INTEGRITY_KEYS = {
    "display_name",
    "source_before",
    "source_after",
    "source_state",
    "copy_before",
    "copy_after",
    "copy_state",
}
TABLE_SUCCESS_KEYS = ("values_from", "freshness", "source", "requested", "table")
FAILURE_ABSENT_KEYS = ("table", "source", "requested", "values_from", "freshness", "cells", "returned", "data")
_ABSENT = object()

# --- 기대값. 파일을 만들기 전에 정한다. ---

# Data 시트 A3:H8, 헤더 [3, 4]: (key, display_name, display_derived)
TWO_LINE_COLUMNS = [
    ("구분", "구분", True),
    ("매출_1월", "매출_1월", False),
    ("매출_2월", "매출_2월", True),
    ("코드", "코드", False),
    ("column_E", "코드", False),
    ("column_F", None, False),
    ("column_G", None, False),
    ("column_H", None, True),
]
TWO_LINE_SOURCE_ROWS = [5, 6, 7, 8]
TWO_LINE_HEADER_MERGES = {
    "A3": {"role": "anchor", "anchor": "A3", "range": "A3:A4"},
    "A4": {"role": "covered", "anchor": "A3", "range": "A3:A4"},
    "B3": {"role": "anchor", "anchor": "B3", "range": "B3:C3"},
    "C3": {"role": "covered", "anchor": "B3", "range": "B3:C3"},
    "G3": {"role": "anchor", "anchor": "G3", "range": "G3:H3"},
    "H3": {"role": "covered", "anchor": "G3", "range": "G3:H3"},
}
# a1: (value, value_type, number_format, formula, cached_value, cached_value_state)
TWO_LINE_CELLS = {
    "A5": ("사과", "string", "General", None, None, "not_applicable"),
    "B5": (10, "number", "General", None, None, "not_applicable"),
    "D5": ("00123", "string", "General", None, None, "not_applicable"),
    "E5": (123, "number", "00000", None, None, "not_applicable"),
    "F5": (None, "empty", "General", None, None, "not_applicable"),
    "G5": (30, "number", "General", "=B5+C5", 30, "stored"),
    "H5": (0, "number", "General", "=B5-B5", 0, "stored"),
    "A6": (None, "empty", "General", None, None, "not_applicable"),
    "H6": (None, "empty", "General", None, None, "not_applicable"),
    "B7": (2.5, "number", "General", None, None, "not_applicable"),
    "G7": (None, "formula", "General", "=B7*2", None, "absent"),
    "B8": (True, "boolean", "General", None, None, "not_applicable"),
}

# Simple 시트 A1:C3
SINGLE_KEYS = ["이름", "수량", "코드"]
SINGLE_SOURCE_ROWS = [2, 3]
NO_HEADER_KEYS = ["column_A", "column_B", "column_C"]
NO_HEADER_SOURCE_ROWS = [1, 2, 3]

# Data 시트 C3:E5, 헤더 [3]. C3의 기준 셀 B3은 범위 밖이다.
OUTSIDE_ANCHOR_COLUMNS = [("column_C", None), ("코드", "코드"), ("column_E", "코드")]

# Two 시트의 두 범위
TWO_RANGES = {
    "A1:B3": (["x", "y"], {"A2": 1, "B3": 4}),
    "E1:F3": (["p", "q"], {"E2": "a", "F3": "d"}),
}

# big.xlsx: A1:T100은 헤더 포함 2000칸, A1:A2001은 2001칸
BIG_OK_ADDRESS = "A1:T100"
BIG_OK_KEYS = [f"h{index}" for index in range(1, 21)]
BIG_OK_ROWS = 99
BIG_OVER_ADDRESS = "A1:A2001"


# --- 준비 ---


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DATA_CACHED = {"G5": "30", "H5": "0"}
DATA_UNCACHED = {"G7": "B7*2"}


def _sheet_member(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rel_ids = [
        sheet.get(f"{{{DOC_REL_NS}}}id")
        for sheet in workbook.iter(f"{{{MAIN_NS}}}sheet")
        if sheet.get("name") == sheet_name
    ]
    assert len(rel_ids) == 1 and rel_ids[0], sheet_name
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = [rel.get("Target") for rel in rels.iter(f"{{{PKG_REL_NS}}}Relationship") if rel.get("Id") == rel_ids[0]]
    assert len(targets) == 1 and targets[0], rel_ids[0]
    target = targets[0]
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _xml_cell(root: ET.Element, ref: str) -> ET.Element:
    found = [cell for cell in root.iter(f"{{{MAIN_NS}}}c") if cell.get("r") == ref]
    assert len(found) == 1, ref
    return found[0]


def _refs_with_value(root: ET.Element) -> set[str]:
    return {cell.get("r") for cell in root.iter(f"{{{MAIN_NS}}}c") if cell.find(f"{{{MAIN_NS}}}v") is not None}


def _data_sheet_root(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read(_sheet_member(archive, "Data")))


def _rewrite_formula_values(path: Path, cached: dict[str, str], uncached: dict[str, str]) -> None:
    """openpyxl은 수식의 계산값을 저장하지 않고 빈 <v>를 남긴다. Data 시트 XML에서
    cached의 <v>에 저장값을 넣고, uncached의 <v> 자식은 제거한다."""
    with zipfile.ZipFile(path) as archive:
        member = _sheet_member(archive, "Data")
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    assert member in {info.filename for info, _data in entries}, member
    rewritten = []
    for info, data in entries:
        if info.filename == member:
            root = ET.fromstring(data)
            before = _refs_with_value(root)
            for ref, value in cached.items():
                cell = _xml_cell(root, ref)
                assert cell.find(f"{{{MAIN_NS}}}f") is not None, ref
                values = cell.findall(f"{{{MAIN_NS}}}v")
                assert len(values) <= 1, ref
                element = values[0] if values else ET.SubElement(cell, f"{{{MAIN_NS}}}v")
                assert not (element.text or "").strip(), ref
                element.text = value
            for ref, formula in uncached.items():
                cell = _xml_cell(root, ref)
                assert cell.findtext(f"{{{MAIN_NS}}}f") == formula, ref
                for element in cell.findall(f"{{{MAIN_NS}}}v"):
                    cell.remove(element)
            assert _refs_with_value(root) == (before | set(cached)) - set(uncached)
            ET.register_namespace("", MAIN_NS)
            data = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
        rewritten.append((info, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)


def _assert_formula_fixture_xml(path: Path) -> None:
    """테스트가 만든 파일의 Data 시트 XML을 직접 본다."""
    root = _data_sheet_root(path)
    for ref, formula in DATA_UNCACHED.items():
        cell = _xml_cell(root, ref)
        assert cell.findtext(f"{{{MAIN_NS}}}f") == formula, ref
        assert cell.findall(f"{{{MAIN_NS}}}v") == [], ref
    for ref, value in DATA_CACHED.items():
        cell = _xml_cell(root, ref)
        assert cell.find(f"{{{MAIN_NS}}}f") is not None, ref
        values = cell.findall(f"{{{MAIN_NS}}}v")
        assert [element.text for element in values] == [value], ref


def _assert_g7_absent(table: dict) -> None:
    g7 = _cells(table)["G7"]
    assert g7["formula"] == "=B7*2"
    assert g7["cached_value"] is None
    assert g7["cached_value_state"] == "absent"


def _book(path: Path) -> Path:
    """Data: 1행 제목, 3·4행 두 줄 헤더, 5~8행 데이터, 6행과 F열은 비어 있다.
    G5·H5는 저장값 있는 수식(30, 0), G7은 <v> 자식이 없는 수식이다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "월간 보고서"
    ws["A3"] = "구분"
    ws.merge_cells("A3:A4")
    ws["B3"] = "매출"
    ws.merge_cells("B3:C3")
    ws["D3"] = "코드"
    ws["E3"] = "코드"
    ws.merge_cells("G3:H3")
    ws["B4"] = "1월"
    ws["C4"] = "2월"
    ws["A5"] = "사과"
    ws["B5"] = 10
    ws["C5"] = 20
    ws["D5"] = "00123"
    ws["E5"] = 123
    ws["E5"].number_format = "00000"
    ws["G5"] = "=B5+C5"
    ws["H5"] = "=B5-B5"
    ws["A7"] = "배"
    ws["B7"] = 2.5
    ws["G7"] = "=B7*2"
    ws["A8"] = "합계"
    ws["B8"] = True

    simple = wb.create_sheet("Simple")
    for a1, value in {"A1": "이름", "B1": "수량", "C1": "코드", "A2": "가", "B2": 1, "C2": "00123",
                      "A3": "나", "B3": 2, "C3": "00045"}.items():
        simple[a1] = value

    two = wb.create_sheet("Two")
    for a1, value in {"A1": "x", "B1": "y", "A2": 1, "B2": 2, "A3": 3, "B3": 4,
                      "E1": "p", "F1": "q", "E2": "a", "F2": "b", "E3": "c", "F3": "d"}.items():
        two[a1] = value
    wb.save(path)
    _rewrite_formula_values(path, DATA_CACHED, DATA_UNCACHED)
    return path


def _big_book(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Big"
    for column, key in enumerate(BIG_OK_KEYS, start=1):
        ws.cell(row=1, column=column, value=key)
    for row in range(2, 101):
        ws.cell(row=row, column=1, value=row)
    wb.save(path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config(tmp_path: Path):
    (tmp_path / "inputs").mkdir(exist_ok=True)
    body = {
        "python_path": sys.executable,
        "repo_cwd": str(ROOT),
        "allowed_input_roots": [str(tmp_path / "inputs")],
        "workspace_root": str(tmp_path / "work"),
        "publish_root": str(tmp_path / "publish"),
        "model_base_url": "http://127.0.0.1:9",
        "model_name": "qwen2.5:7b",
        "allowed_profiles": ["generic"],
        "max_input_files": 8,
        "max_file_bytes": 5_000_000,
        "max_total_bytes": 10_000_000,
        "max_timeout_seconds": 120,
        "default_timeout_seconds": 90,
        "grace_seconds": 0.4,
        "lock_wait_seconds": 2,
        "max_stdout_bytes": 2_000_000,
        "max_stderr_bytes": 1_000_000,
        "allowed_extensions": [".xlsx", ".xls", ".xlsm", ".csv"],
    }
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return load_operator_config(path)


def _request(
    path: Path,
    address: str,
    header: object = _ABSENT,
    *,
    request_id: str = "tbl-1",
    sheet: object = "Data",
    **extra: object,
) -> dict:
    read: dict = {"address": address}
    if header is not _ABSENT:
        read["header"] = header
    payload = {
        "contract_version": "1.0",
        "request_id": request_id,
        "operation": "read_table",
        "inputs": [{"path": str(path), "sheet": sheet, "display_name": path.name}],
        "read": read,
    }
    payload.update(extra)
    return payload


def _direct(path: Path, address: str, header: object, *, sheet: object = "Data", request_id: str = "tbl-1") -> dict:
    result = read_table(path, sheet=sheet, address=address, header=header, display_name=path.name, request_id=request_id)
    return json.loads(json.dumps(result, ensure_ascii=False))


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _run_cli_process(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "core.application.cli"],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=_env(),
        check=False,
        timeout=120,
    )


def _run_wrapper_process(config, payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "core.application.wrapper", "--config", str(config.source_path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=_env(),
        check=False,
        timeout=180,
    )


def _run_cli_inline(payload: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[dict, int]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    code = cli.main([])
    return json.loads(capsys.readouterr().out), code


def _router_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _record(name: str):
        def _called(*_args: object, **_kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"{name} must not be called for read_table")

        return _called

    monkeypatch.setattr("core.application.headless.route_single_prompt", _record("route_single_prompt"))
    monkeypatch.setattr("core.application.headless.route_multi_prompt", _record("route_multi_prompt"))
    return calls


def _no_analyze(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls = _router_spy(monkeypatch)

    def _blocked(*_args: object, **_kwargs: object) -> None:
        calls.append("analyze_excel")
        raise AssertionError("analyze_excel must not be called for read_table")

    monkeypatch.setattr(cli, "analyze_excel", _blocked)
    return calls


def _evidence(config, response: dict, name: str) -> dict:
    return json.loads((config.workspace_root / response["execution_id"] / "evidence" / name).read_text(encoding="utf-8"))


def _assert_no_paths(response: dict, tmp_path: Path, *extra: Path | str) -> None:
    text = json.dumps(response, ensure_ascii=False, default=str)
    forbidden = {str(tmp_path), os.path.realpath(tmp_path), *(str(item) for item in extra)}
    for item in forbidden:
        assert item not in text, item


def _assert_public_integrity(response: dict, *, state: str, authoritative: bool, count: int) -> None:
    integrity = response["input_integrity"]
    assert integrity["state"] == state
    assert integrity["authoritative"] is authoritative
    assert len(integrity["items"]) == count
    for item in integrity["items"]:
        assert set(item) == PUBLIC_INTEGRITY_KEYS


def _assert_evidence_integrity(config, response: dict, source: Path) -> str:
    """증거에는 경로를 포함한 전체 기록이 남는다. 작업 사본 경로를 돌려준다."""
    evidence = _evidence(config, response, "input_integrity.json")
    item = evidence["items"][0]
    assert item["source"] == str(source.resolve())
    assert Path(item["copy"]).parent == config.workspace_root / response["execution_id"] / "inputs"
    assert evidence["state"] == response["input_integrity"]["state"]
    assert evidence["authoritative"] is response["input_integrity"]["authoritative"]
    public = response["input_integrity"]["items"][0]
    for key in PUBLIC_INTEGRITY_KEYS - {"display_name"}:
        assert public[key] == item[key]
    return item["copy"]


def _assert_wrapper_failure(response: dict, code: int, error_code: str) -> None:
    assert response["status"] == ("invalid_request" if code == 2 else "execution_failed")
    assert code == response["exit_code"]
    assert response["error"]["code"] == error_code
    assert response["operation"] == "read_table"
    assert response["recalculated"] is False
    assert response["structural_state"] == "not_applicable"
    assert response["artifacts"] == []
    for key in FAILURE_ABSENT_KEYS:
        assert key not in response


def _cells(table: dict) -> dict[str, dict]:
    return {cell["a1"]: cell for row in table["rows"] for cell in row["cells"]}


def _assert_two_line_table(table: dict) -> None:
    assert table["address"] == "A3:H8"
    assert (table["blank_rows"], table["blank_columns"]) == ("kept", "kept")
    columns = table["columns"]
    assert [(c["key"], c["display_name"], c.get("display_derived", False)) for c in columns] == TWO_LINE_COLUMNS
    for column in columns:
        if "display_derived" in column:
            assert column["display_derived"] is True
    assert [c["source_column"] for c in columns] == list("ABCDEFGH")
    assert [c["column_index"] for c in columns] == list(range(1, 9))
    headers = {header["a1"]: header for column in columns for header in column["headers"]}
    assert [h["row"] for h in columns[0]["headers"]] == [3, 4]
    for a1, merge in TWO_LINE_HEADER_MERGES.items():
        assert headers[a1]["merge"] == merge
    for covered in ("A4", "C3", "H3"):
        assert (headers[covered]["value"], headers[covered]["value_type"]) == (None, "empty")
    assert (headers["G3"]["value"], headers["F3"]["value"]) == (None, None)
    assert [row["source_row"] for row in table["rows"]] == TWO_LINE_SOURCE_ROWS
    cells = _cells(table)
    assert len(cells) == 8 * 4
    for a1, (value, value_type, number_format, formula, cached, state) in TWO_LINE_CELLS.items():
        cell = cells[a1]
        assert cell["value"] == value and type(cell["value"]) is type(value), a1
        assert cell["value_type"] == value_type, a1
        assert cell["number_format"] == number_format, a1
        assert cell["formula"] == formula, a1
        assert cell["cached_value"] == cached, a1
        assert cell["cached_value_state"] == state, a1
    assert cells["H5"]["value"] is not None and cells["H5"]["cached_value"] is not None
    for row in table["rows"]:
        assert [cell["key"] for cell in row["cells"]] == [key for key, _name, _derived in TWO_LINE_COLUMNS]
        assert [cell["a1"] for cell in row["cells"]] == [f"{letter}{row['source_row']}" for letter in "ABCDEFGH"]
    for row in (5, 6, 7, 8):
        assert cells[f"F{row}"]["value_type"] == "empty"
    for letter in "ABCDEFGH":
        assert cells[f"{letter}6"]["value_type"] == "empty"


# --- CLI 프로세스 ---


def test_cli_process_returns_table_reader_json_unchanged(tmp_path: Path) -> None:
    book = _book(tmp_path / "data" / "book.xlsx")
    _assert_formula_fixture_xml(book)
    before = _sha256(book)
    files_before = sorted(path.name for path in book.parent.iterdir())

    result = _run_cli_process(_request(book, "A3:H8", {"rows": [3, 4]}, request_id="cli-1"))

    assert result.returncode == CLI_EXIT_CODES["success"] == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed == _direct(book, "A3:H8", {"rows": [3, 4]}, request_id="cli-1")
    assert parsed["status"] == "success"
    assert parsed["operation"] == "read_table"
    assert parsed["recalculated"] is False
    assert parsed["values_from"] == "stored_workbook"
    assert parsed["freshness"] == "unconfirmed"
    assert parsed["source"] == {"display_name": "book.xlsx", "sheet_name": "Data", "sheet_index": 0}
    assert parsed["requested"] == {"address": "A3:H8", "header": {"rows": [3, 4]}}
    for key in ("data", "preview_records", "artifacts", "text", "cells"):
        assert key not in parsed
    _assert_two_line_table(parsed["table"])
    _assert_g7_absent(parsed["table"])
    assert _sha256(book) == before
    assert sorted(path.name for path in book.parent.iterdir()) == files_before
    assert str(tmp_path) not in result.stdout


def test_cli_process_missing_header_exits_invalid_request(tmp_path: Path) -> None:
    book = _book(tmp_path / "data" / "book.xlsx")

    result = _run_cli_process(_request(book, "A3:H8", request_id="cli-2"))

    assert result.returncode == CLI_EXIT_CODES["invalid_request"] == 2
    parsed = json.loads(result.stdout)
    assert parsed == _direct(book, "A3:H8", None, request_id="cli-2")
    assert parsed["error"]["code"] == "missing_header"
    assert "table" not in parsed
    assert str(tmp_path) not in result.stdout


# --- CLI 분기 ---


def test_cli_single_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")

    parsed, code = _run_cli_inline(_request(book, "A1:C3", {"rows": [1]}, sheet="Simple"), monkeypatch, capsys)

    assert code == 0
    assert parsed == _direct(book, "A1:C3", {"rows": [1]}, sheet="Simple")
    table = parsed["table"]
    assert [c["key"] for c in table["columns"]] == SINGLE_KEYS
    assert all("display_derived" not in c for c in table["columns"])
    assert [row["source_row"] for row in table["rows"]] == SINGLE_SOURCE_ROWS
    cells = _cells(table)
    assert (cells["C2"]["value"], cells["C2"]["value_type"]) == ("00123", "string")
    assert calls == []


def test_cli_explicit_no_header_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")

    parsed, code = _run_cli_inline(_request(book, "A1:C3", {"rows": []}, sheet="Simple"), monkeypatch, capsys)

    assert code == 0
    assert parsed["requested"]["header"] == {"rows": []}
    table = parsed["table"]
    assert [c["key"] for c in table["columns"]] == NO_HEADER_KEYS
    assert [c["display_name"] for c in table["columns"]] == [None, None, None]
    assert all(c["headers"] == [] for c in table["columns"])
    assert [row["source_row"] for row in table["rows"]] == NO_HEADER_SOURCE_ROWS
    assert _cells(table)["A1"]["value"] == "이름"
    assert calls == []


@pytest.mark.parametrize("header", [_ABSENT, None])
def test_cli_missing_or_null_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    header: object,
) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")

    parsed, code = _run_cli_inline(_request(book, "A3:H8", header), monkeypatch, capsys)

    assert code == CLI_EXIT_CODES["invalid_request"]
    assert parsed == _direct(book, "A3:H8", None)
    assert parsed["error"] == {"code": "missing_header", "message": "헤더 지정이 없다.", "stage": "request", "retryable": False}
    assert "table" not in parsed
    assert calls == []


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ({"rows": [4]}, "헤더는 범위의 첫 행에서 시작해야 한다."),
        ({"rows": [3, 5]}, "헤더 행은 겹치지 않고 연속된 오름차순이어야 한다."),
        ({"rows": [4, 3]}, "헤더 행은 겹치지 않고 연속된 오름차순이어야 한다."),
        ({"rows": [3, 3]}, "헤더 행은 겹치지 않고 연속된 오름차순이어야 한다."),
        ({"rows": [3, 4, 5]}, "헤더는 두 줄까지다."),
        ({"rows": [2]}, "헤더 행이 요청 범위 안에 있지 않다."),
        ({"rows": [True]}, "헤더 행 목록이 올바르지 않다."),
        ({"rows": "3"}, "헤더 행 목록이 올바르지 않다."),
        ({}, "헤더 행 목록이 올바르지 않다."),
        ([3], "헤더 행 목록이 올바르지 않다."),
    ],
)
def test_cli_invalid_header_is_not_corrected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    header: object,
    message: str,
) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")

    parsed, code = _run_cli_inline(_request(book, "A3:H8", header), monkeypatch, capsys)

    assert code == CLI_EXIT_CODES["invalid_request"]
    assert parsed == _direct(book, "A3:H8", header)
    assert parsed["error"]["code"] == "invalid_header"
    assert parsed["error"]["message"] == message
    assert "table" not in parsed
    assert calls == []


def test_cli_outside_anchor_and_two_ranges_on_one_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")

    parsed, code = _run_cli_inline(_request(book, "C3:E5", {"rows": [3]}), monkeypatch, capsys)
    assert code == 0
    columns = parsed["table"]["columns"]
    assert [(c["key"], c["display_name"]) for c in columns] == OUTSIDE_ANCHOR_COLUMNS
    assert all("display_derived" not in c for c in columns)
    assert columns[0]["headers"][0]["merge"] == {"role": "covered", "anchor": "B3", "range": "B3:C3"}
    assert columns[0]["headers"][0]["value"] is None

    for address, (keys, values) in TWO_RANGES.items():
        parsed, code = _run_cli_inline(_request(book, address, {"rows": [1]}, sheet="Two"), monkeypatch, capsys)
        assert code == 0
        table = parsed["table"]
        assert [c["key"] for c in table["columns"]] == keys
        cells = _cells(table)
        assert set(cells) == {f"{c['source_column']}{row}" for c in table["columns"] for row in (2, 3)}
        for a1, value in values.items():
            assert cells[a1]["value"] == value
    assert calls == []


@pytest.mark.parametrize(
    ("address", "sheet", "name", "code"),
    [
        ("A0", "Data", "book.xlsx", "invalid_address"),
        ("Data!A1", "Data", "book.xlsx", "invalid_address"),
        ("H8:A3", "Data", "book.xlsx", "reversed_range"),
        ("A1:A2001", "Data", "book.xlsx", "range_too_large"),
        ("A1", "없음", "book.xlsx", "invalid_sheet"),
        ("A1", 9, "book.xlsx", "invalid_sheet"),
        ("A1", 0, "book.csv", "unsupported_format"),
    ],
)
def test_cli_table_reader_errors_pass_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    address: str,
    sheet: object,
    name: str,
    code: str,
) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")
    target = book if name == "book.xlsx" else book.with_name(name)
    if target != book:
        target.write_bytes(book.read_bytes())

    parsed, exit_code = _run_cli_inline(_request(target, address, {"rows": [1]}, sheet=sheet), monkeypatch, capsys)

    assert parsed == _direct(target, address, {"rows": [1]}, sheet=sheet)
    assert parsed["error"]["code"] == code
    assert parsed["operation"] == "read_table"
    assert exit_code == CLI_EXIT_CODES["invalid_request"] == 2
    assert calls == []
    assert str(tmp_path) not in json.dumps(parsed, ensure_ascii=False)


def test_cli_execution_failure_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _no_analyze(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(str(book))

    monkeypatch.setattr("core.io.table_reader._build_table", _boom)
    parsed, exit_code = _run_cli_inline(_request(book, "A3:H8", {"rows": [3]}), monkeypatch, capsys)

    assert exit_code == CLI_EXIT_CODES["execution_failed"] == 1
    assert parsed["status"] == "execution_failed"
    assert parsed["error"]["code"] == "execution_failed"
    assert parsed["error"]["exception_type"] == "RuntimeError"
    assert "table" not in parsed
    assert str(tmp_path) not in json.dumps(parsed, ensure_ascii=False)
    assert calls == []


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(user_prompt="표 읽어줘"), "forbidden_request_field"),
        (lambda p: p.update(model={"base_url": "http://127.0.0.1:9", "name": "x"}), "forbidden_request_field"),
        (lambda p: p.update(output_directory="/tmp"), "forbidden_request_field"),
        (lambda p: p.update(analysis_mode="single"), "forbidden_request_field"),
        (lambda p: p.update(profile_name="generic"), "forbidden_request_field"),
        (lambda p: p["inputs"].append(dict(p["inputs"][0])), "invalid_input_count"),
        (lambda p: p.update(inputs=[]), "missing_inputs"),
        (lambda p: p["inputs"][0].update(path="book.xlsx"), "invalid_input"),
        (lambda p: p.update(read={"address": "A3", "header": {"rows": [3]}, "sheet": "x"}), "invalid_read"),
        (lambda p: p.update(read={"header": {"rows": [3]}}), "invalid_read"),
        (lambda p: p.pop("read"), "invalid_read"),
        (lambda p: p.update(contract_version="2.0"), "unsupported_contract_version"),
    ],
)
def test_cli_table_request_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutate,
    code: str,
) -> None:
    calls = _no_analyze(monkeypatch)
    payload = _request(_book(tmp_path / "data" / "book.xlsx"), "A3:H8", {"rows": [3]})
    mutate(payload)

    parsed, exit_code = _run_cli_inline(payload, monkeypatch, capsys)

    assert parsed["status"] == "invalid_request"
    assert parsed["operation"] == "read_table"
    assert parsed["recalculated"] is False
    assert parsed["error"]["code"] == code
    assert parsed["error"]["retryable"] is False
    assert "table" not in parsed
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert calls == []
    assert str(tmp_path) not in json.dumps(parsed, ensure_ascii=False)


def test_cli_unknown_operation_stays_invalid_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _router_spy(monkeypatch)
    payload = _request(_book(tmp_path / "data" / "book.xlsx"), "A3:H8", {"rows": [3]})
    payload["operation"] = "read_tables"

    parsed, exit_code = _run_cli_inline(payload, monkeypatch, capsys)

    assert parsed["status"] == "invalid_request"
    assert parsed["error"]["code"] == "invalid_operation"
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert calls == []


def test_analyze_parser_still_rejects_read_table(tmp_path: Path) -> None:
    assert "read_table" not in SUPPORTED_OPERATIONS
    payload = {
        "contract_version": "1.0",
        "request_id": "p-1",
        "operation": "read_table",
        "inputs": [{"path": str(tmp_path / "a.xlsx")}],
        "user_prompt": "표",
        "analysis_mode": "single",
        "output_directory": str(tmp_path / "out"),
    }
    with pytest.raises(ContractError) as caught:
        parse_analyze_request(payload)
    assert caught.value.code == "invalid_operation"


# --- 래퍼 프로세스 ---


def test_wrapper_process_reads_two_line_header_table(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    _assert_formula_fixture_xml(book)
    before = _sha256(book)
    config = _config(tmp_path)

    result = _run_wrapper_process(config, _request(book, "A3:H8", {"rows": [3, 4]}, request_id="wrap-1"))

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "success"
    assert response["exit_code"] == response["wrapper_exit_code"] == response["child_returncode"] == 0
    assert response["operation"] == "read_table"
    assert response["recalculated"] is False
    assert response["values_from"] == "stored_workbook"
    assert response["freshness"] == "unconfirmed"
    assert response["structural_state"] == "not_applicable"
    assert response["artifacts"] == []
    assert response["error"] is None
    assert "data" not in response and "preview_records" not in json.dumps(response)
    assert response["source"] == {"display_name": "book.xlsx", "sheet_name": "Data", "sheet_index": 0}
    assert response["requested"] == {"address": "A3:H8", "header": {"rows": [3, 4]}}
    direct = _direct(book, "A3:H8", {"rows": [3, 4]}, request_id="wrap-1")
    assert response["table"] == direct["table"]
    _assert_two_line_table(response["table"])
    _assert_g7_absent(response["table"])
    assert response["cleanup"]["state"] == "complete"
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)

    copy_path = _assert_evidence_integrity(config, response, book)
    _assert_no_paths(response, tmp_path, book, book.resolve(), copy_path, config.workspace_root)
    assert str(tmp_path) not in result.stdout

    engine_request = _evidence(config, response, "engine_request.json")
    assert engine_request == {
        "contract_version": "1.0",
        "request_id": "wrap-1",
        "operation": "read_table",
        "inputs": [{"path": copy_path, "sheet": "Data", "display_name": "book.xlsx"}],
        "read": {"address": "A3:H8", "header": {"rows": [3, 4]}},
    }
    assert _evidence(config, response, "engine_response.json") == direct
    meta = _evidence(config, response, "execution_meta.json")
    assert meta["operation"] == "read_table"
    assert meta["model_base_url"] is None and meta["model_name"] is None and meta["profile_name"] is None
    assert meta["input_integrity"]["items"][0]["source"] == str(book.resolve())
    assert _evidence(config, response, "wrapper_response.json") == response
    assert not (config.publish_root / response["execution_id"]).exists()
    assert _sha256(book) == before


def test_wrapper_process_missing_header_failure_hides_paths(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    before = _sha256(book)
    config = _config(tmp_path)

    result = _run_wrapper_process(config, _request(book, "A3:H8", request_id="wrap-2"))

    assert result.returncode == CLI_EXIT_CODES["invalid_request"], result.stderr
    response = json.loads(result.stdout)
    _assert_wrapper_failure(response, result.returncode, "missing_header")
    assert response["child_returncode"] == 2
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    copy_path = _assert_evidence_integrity(config, response, book)
    _assert_no_paths(response, tmp_path, book, copy_path, config.workspace_root)
    assert _evidence(config, response, "engine_request.json")["read"] == {"address": "A3:H8"}
    assert _sha256(book) == before


@pytest.mark.parametrize("form", ["full", "tail"])
def test_wrapper_process_blocks_path_in_request_id(tmp_path: Path, form: str) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    source = str(book.resolve())
    request_id = source[-128:] if form == "full" else source[-40:]
    assert "/" in request_id and request_id in source

    result = _run_wrapper_process(config, _request(book, "A3:H8", {"rows": [3]}, request_id=request_id))

    assert result.returncode == CLI_EXIT_CODES["execution_failed"], result.stderr
    assert request_id not in result.stdout
    response = json.loads(result.stdout)
    _assert_wrapper_failure(response, result.returncode, "path_exposure_blocked")
    assert response["request_id"] == ""
    assert response["child_returncode"] == 0
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    copy_path = _assert_evidence_integrity(config, response, book)
    _assert_no_paths(response, tmp_path, book, copy_path)
    assert _evidence(config, response, "wrapper_response.json") == response


# --- 래퍼 run_job, 자식은 실제 CLI ---


@pytest.mark.parametrize(
    ("address", "header", "sheet", "keys", "source_rows"),
    [
        ("A1:C3", {"rows": [1]}, "Simple", SINGLE_KEYS, SINGLE_SOURCE_ROWS),
        ("A1:C3", {"rows": []}, "Simple", NO_HEADER_KEYS, NO_HEADER_SOURCE_ROWS),
        ("C3:E5", {"rows": [3]}, "Data", [key for key, _name in OUTSIDE_ANCHOR_COLUMNS], [4, 5]),
        ("A1:B3", {"rows": [1]}, "Two", TWO_RANGES["A1:B3"][0], [2, 3]),
        ("E1:F3", {"rows": [1]}, "Two", TWO_RANGES["E1:F3"][0], [2, 3]),
    ],
)
def test_wrapper_reads_tables_through_real_cli(
    tmp_path: Path,
    address: str,
    header: dict,
    sheet: str,
    keys: list[str],
    source_rows: list[int],
) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _request(book, address, header, sheet=sheet))

    assert code == 0, response
    assert response["operation"] == "read_table"
    direct = _direct(book, address, header, sheet=sheet)
    for key in TABLE_SUCCESS_KEYS:
        assert response[key] == direct[key]
    table = response["table"]
    assert [c["key"] for c in table["columns"]] == keys
    assert [row["source_row"] for row in table["rows"]] == source_rows
    assert "data" not in response
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    copy_path = _assert_evidence_integrity(config, response, book)
    _assert_no_paths(response, tmp_path, copy_path)


def test_wrapper_reads_2000_cells_and_rejects_2001(tmp_path: Path) -> None:
    book = _big_book(tmp_path / "inputs" / "big.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _request(book, BIG_OK_ADDRESS, {"rows": [1]}, sheet="Big"))

    assert code == 0, response
    table = response["table"]
    assert [c["key"] for c in table["columns"]] == BIG_OK_KEYS
    assert len(table["rows"]) == BIG_OK_ROWS
    assert len(table["columns"]) * (1 + len(table["rows"])) == 2000
    assert all(len(row["cells"]) == 20 for row in table["rows"])
    assert table["rows"][-1]["source_row"] == 100
    assert table["rows"][-1]["cells"][0]["value"] == 100
    assert "preview_records" not in json.dumps(response)

    response, code = run_job(config, _request(book, BIG_OVER_ADDRESS, {"rows": [1]}, sheet="Big", request_id="tbl-2"))

    _assert_wrapper_failure(response, code, "range_too_large")
    assert response["child_returncode"] == 2
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)


@pytest.mark.parametrize(
    ("address", "header", "sheet", "name", "code"),
    [
        ("A3:H8", None, "Data", "book.xlsx", "missing_header"),
        ("A3:H8", {"rows": [4]}, "Data", "book.xlsx", "invalid_header"),
        ("A01", {"rows": [1]}, "Data", "book.xlsx", "invalid_address"),
        ("H8:A3", {"rows": [8]}, "Data", "book.xlsx", "reversed_range"),
        ("A1", {"rows": [1]}, "없음", "book.xlsx", "invalid_sheet"),
        ("A1", {"rows": [1]}, 0, "book.csv", "unsupported_format"),
    ],
)
def test_wrapper_table_errors_keep_integrity_and_hide_paths(
    tmp_path: Path,
    address: str,
    header: object,
    sheet: object,
    name: str,
    code: str,
) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    target = book if name == "book.xlsx" else book.with_name(name)
    if target != book:
        target.write_bytes(book.read_bytes())
    config = _config(tmp_path)

    response, exit_code = run_job(config, _request(target, address, header, sheet=sheet))

    _assert_wrapper_failure(response, exit_code, code)
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert response["child_returncode"] == 2
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    copy_path = _assert_evidence_integrity(config, response, target)
    _assert_no_paths(response, tmp_path, target, copy_path)
    engine_read = _evidence(config, response, "engine_request.json")["read"]
    assert engine_read == {"address": address, "header": header}


def test_wrapper_copy_mutation_is_not_authoritative(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    original = book.read_bytes()
    config = _config(tmp_path)
    script = (
        "import json,os,sys\n"
        "req=json.load(sys.stdin)\n"
        "path=req['inputs'][0]['path']\n"
        "os.chmod(path, 0o644)\n"
        "open(path,'ab').write(b'x')\n"
        "print(json.dumps({'contract_version':'1.0','request_id':req['request_id'],'status':'success',"
        "'operation':'read_table','recalculated':False}))\n"
    )

    response, code = run_job(
        config,
        _request(book, "A3:H8", {"rows": [3]}),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    _assert_wrapper_failure(response, code, "input_changed")
    _assert_public_integrity(response, state="changed", authoritative=False, count=1)
    assert response["input_integrity"]["items"][0]["copy_state"] == "changed"
    evidence = _evidence(config, response, "input_integrity.json")
    assert evidence["authoritative"] is False
    assert evidence["items"][0]["copy_state"] == "changed"
    _assert_no_paths(response, tmp_path, evidence["items"][0]["copy"])
    assert book.read_bytes() == original


def test_wrapper_child_never_calls_model_or_routers(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    marker = tmp_path / "router_called.txt"
    config = _config(tmp_path)
    script = (
        "import sys\n"
        "import core.application.cli as cli\n"
        "import core.application.headless as headless\n"
        "import core.routing.route_multi as route_multi\n"
        "import core.routing.route_single as route_single\n"
        f"MARKER={str(marker)!r}\n"
        "def _called(*_a, **_k):\n"
        "    open(MARKER, 'a').write('called\\n')\n"
        "    raise SystemExit(99)\n"
        "headless.route_single_prompt = _called\n"
        "headless.route_multi_prompt = _called\n"
        "route_single.route_single_prompt = _called\n"
        "route_multi.route_multi_prompt = _called\n"
        "cli.analyze_excel = _called\n"
        "raise SystemExit(cli.main([]))\n"
    )

    response, code = run_job(
        config,
        _request(book, "A3:H8", {"rows": [3, 4]}),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    assert code == 0, response
    assert response["status"] == "success"
    assert len(response["table"]["rows"]) == 4
    assert not marker.exists()
    engine_request = _evidence(config, response, "engine_request.json")
    for key in ("user_prompt", "model", "output_directory", "analysis_mode", "profile_name"):
        assert key not in engine_request
    meta = _evidence(config, response, "execution_meta.json")
    assert meta["model_base_url"] is None and meta["model_name"] is None


def test_wrapper_passes_engine_execution_failure(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    script = (
        "import sys\n"
        "import core.application.cli as cli\n"
        "import core.io.table_reader as table_reader\n"
        "def _boom(*_a, **_k):\n"
        "    raise RuntimeError('boom')\n"
        "table_reader._build_table = _boom\n"
        "raise SystemExit(cli.main([]))\n"
    )

    response, code = run_job(
        config,
        _request(book, "A3:H8", {"rows": [3]}),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    _assert_wrapper_failure(response, code, "execution_failed")
    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["child_returncode"] == 1
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)


# --- 래퍼 응답 검사 ---


def _engine_launch(mutation: str = "", exit_code: int | None = None) -> ProcessLaunch:
    """실제 read_table 결과를 만든 뒤 mutation을 적용해 출력한다."""
    script = (
        "import json,sys\n"
        "from core.application.contracts import CLI_EXIT_CODES\n"
        "from core.io.table_reader import read_table\n"
        "req=json.load(sys.stdin)\n"
        "item=req['inputs'][0]\n"
        "out=read_table(item['path'],sheet=item['sheet'],address=req['read']['address'],"
        "header=req['read'].get('header'),display_name=item['display_name'],request_id=req['request_id'])\n"
        f"{mutation}\n"
        "print(json.dumps(out,ensure_ascii=False))\n"
        f"code={exit_code!r}\n"
        "raise SystemExit(CLI_EXIT_CODES[out['status']] if code is None else code)\n"
    )
    return ProcessLaunch(argv=(sys.executable, "-c", script))


def test_wrapper_accepts_unmodified_engine_and_missing_display_derived(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    request = _request(book, "A3:H8", {"rows": [3, 4]})

    response, code = run_job(config, request, launch=_engine_launch())
    assert code == 0, response
    _assert_two_line_table(response["table"])

    strip = "for column in out['table']['columns']:\n    column.pop('display_derived', None)"
    response, code = run_job(config, dict(request, request_id="tbl-2"), launch=_engine_launch(strip))
    assert code == 0, response
    assert all("display_derived" not in column for column in response["table"]["columns"])
    assert len(response["table"]["rows"]) == 4


@pytest.mark.parametrize(
    ("mutation", "problem"),
    [
        ("out['table']['rows'].pop()", "invalid_field_type"),
        ("out['table']['rows']=out['table']['rows'][:1]", "invalid_field_type"),
        ("out['table']['columns'].pop()", "invalid_field_type"),
        ("out['table']['rows'][0]['cells'].pop()", "invalid_field_type"),
        ("out['table']['rows'][1]['source_row']=99", "invalid_field_type"),
        ("out['table']['rows'][0]['cells'][0]['a1']='Z9'", "invalid_field_type"),
        ("out['table']['columns'][1]['key']=out['table']['columns'][0]['key']", "invalid_field_type"),
        ("out['table']['columns'][0]['display_derived']=False", "invalid_field_type"),
        ("out['table']['columns'][0]['headers'].pop()", "invalid_field_type"),
        ("out['table']['blank_rows']='dropped'", "invalid_field_type"),
        ("out['table']['address']='A3:H7'", "invalid_field_type"),
        ("out.pop('freshness')", "invalid_field_type"),
        ("out['freshness']='fresh'", "invalid_field_type"),
        ("out['recalculated']=True", "invalid_field_type"),
        ("out.pop('table')", "invalid_field_type"),
        ("out['operation']='read_range'", "operation_mismatch"),
        ("out['source']['display_name']='other.xlsx'", "display_name_mismatch"),
        ("out['request_id']='other'", "request_id_mismatch"),
        ("out['table']['rows'][0]['cells'][0]['value_type']=['string']", "invalid_field_type"),
        ("out['table']['rows'][0]['cells'][0]['cached_value_state']={'state':'stored'}", "invalid_field_type"),
        ("out['table']['columns'][0]['headers'][0]['value_type']=['string']", "invalid_field_type"),
        ("out['table']['columns'][0]['headers'][0]['merge']['role']=['anchor']", "invalid_field_type"),
        ("out['table']['rows'][0]['cells'][0]['merge']={'role':{'r':1},'anchor':'A5','range':'A5:A5'}", "invalid_field_type"),
        (
            "out['table']['columns'][0]['headers'][0].update(formula='=1',cached_value=None,cached_value_state=['stored'])",
            "invalid_field_type",
        ),
        ("out['table']['blank_rows']=['kept']", "invalid_field_type"),
    ],
)
def test_wrapper_rejects_malformed_table_engine_output(tmp_path: Path, mutation: str, problem: str) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _request(book, "A3:H8", {"rows": [3, 4]}), launch=_engine_launch(mutation))

    _assert_wrapper_failure(response, code, problem)
    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["stage"] == "engine_output"
    _assert_no_paths(response, tmp_path, book)


def test_wrapper_rejects_failure_response_with_table(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    mutation = "out['table']={'address':'A3:H8','blank_rows':'kept','blank_columns':'kept','columns':[],'rows':[]}"

    response, code = run_job(config, _request(book, "A3:H8"), launch=_engine_launch(mutation))

    _assert_wrapper_failure(response, code, "invalid_field_type")
    assert response["child_returncode"] == 2


def test_wrapper_status_must_match_child_exit(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _request(book, "A3:H8", {"rows": [3]}), launch=_engine_launch(exit_code=2))

    _assert_wrapper_failure(response, code, "status_exit_mismatch")
    assert response["child_returncode"] == 2


# --- 래퍼 요청 거절 ---


_NEVER = ProcessLaunch(argv=(sys.executable, "-c", "raise SystemExit(99)"))


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(operation="read_tables"), "invalid_operation"),
        (lambda p: p.update(user_prompt="표 읽어줘"), "forbidden_request_field"),
        (lambda p: p.update(profile_name="generic"), "forbidden_request_field"),
        (lambda p: p.update(analysis_mode="single"), "forbidden_request_field"),
        (lambda p: p.update(model={"base_url": "http://127.0.0.1:9", "name": "x"}), "forbidden_request_field"),
        (lambda p: p.update(output_directory="/tmp"), "forbidden_request_field"),
        (lambda p: p.update(command=["sh"]), "forbidden_request_field"),
        (lambda p: p["inputs"].append(dict(p["inputs"][0])), "invalid_input_count"),
        (lambda p: p.update(inputs=[]), "missing_inputs"),
        (lambda p: p.update(read={"address": "A3", "header": {"rows": [3]}, "sheet": "x"}), "invalid_read"),
        (lambda p: p.update(read={"header": {"rows": [3]}}), "invalid_read"),
        (lambda p: p.pop("read"), "invalid_read"),
    ],
)
def test_wrapper_rejects_bad_table_requests_before_launch(tmp_path: Path, mutate, code: str) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    payload = _request(book, "A3:H8", {"rows": [3]})
    mutate(payload)

    response, exit_code = run_job(config, payload, launch=_NEVER)

    assert response["status"] == "invalid_request"
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == code
    assert response["execution_id"] is None
    if code != "invalid_operation":
        assert response["operation"] == "read_table"
        assert "data" not in response
    _assert_no_paths(response, tmp_path, book)


def test_wrapper_outside_root_and_symlinks_are_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    outside = _book(tmp_path / "outside" / "book.xlsx")
    secret = _book(tmp_path / "secret" / "hidden.xlsx")
    link_dir = tmp_path / "inputs" / "via"
    link_dir.symlink_to(secret.parent, target_is_directory=True)
    direct = tmp_path / "inputs" / "link.xlsx"
    direct.symlink_to(secret)

    cases = [
        (outside, "outside_root"),
        (link_dir / "hidden.xlsx", "outside_root"),
        (direct, "symlink_rejected"),
    ]
    for index, (path, code) in enumerate(cases):
        response, exit_code = run_job(
            config, _request(path, "A3:H8", {"rows": [3]}, request_id=f"deny-{index}"), launch=_NEVER
        )
        _assert_wrapper_failure(response, exit_code, code)
        _assert_public_integrity(response, state="unconfirmed", authoritative=False, count=0)
        _assert_no_paths(response, tmp_path, path, secret, outside)
        assert _evidence(config, response, "input_integrity.json")["authoritative"] is False
