"""DEV-5 read_table의 Hermes 연결 스크립트 검사. Hermes와 모델을 부르지 않는다.

실제 connect.py를 subprocess로 실행한다. 실제 래퍼를 부르는 사례는 전용 fixture와
tmp_path의 임시 xlsx만 읽는다. summary, schema, quality, read_range 회귀는
tests/test_hermes_excel_bridge.py가 맡는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook
from openpyxl.utils import column_index_from_string

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "development" / "dev0" / "fixtures"
TABLE_FIXTURES = FIXTURES / "dev5-read-table"
TABLES = TABLE_FIXTURES / "tables.xlsx"
TABLES_EXPECTED = TABLE_FIXTURES / "expected.json"
CONNECT = ROOT / "hermes" / "excel-analyzer" / "connect.py"
CONFIG_ENV = "EXCEL_ANALYZER_OPERATOR_CONFIG"

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="운영 래퍼는 Linux만 지원한다")

# --- 기대값. 명세에서 정했다. 구현 출력으로 고치지 않는다. ---

_CELL_FIELDS = ("value", "value_type", "number_format", "formula", "cached_value", "cached_value_state")


def _cell(
    value: object,
    value_type: str,
    number_format: str = "General",
    *,
    formula: str | None = None,
    cached_value: object = None,
    cached_value_state: str = "not_applicable",
) -> dict:
    return {
        "value": value,
        "value_type": value_type,
        "number_format": number_format,
        "formula": formula,
        "cached_value": cached_value,
        "cached_value_state": cached_value_state,
    }


def _header(value: object, merge: dict | None = None) -> dict:
    return {"value": value, "value_type": "empty" if value is None else "string", "merge": merge}


def _merge(role: str, anchor: str, span: str) -> dict:
    return {"role": role, "anchor": anchor, "range": span}


_EMPTY = _cell(None, "empty")

_CASES = {
    "codes": {
        "sheet": "Codes",
        "sheet_index": 0,
        "address": "A3:C4",
        "header": "3",
        "header_rows": [3],
        "columns": [
            {"source_column": "A", "key": "이름", "display_name": "이름"},
            {"source_column": "B", "key": "코드", "display_name": "코드"},
            {"source_column": "C", "key": "수량", "display_name": "수량"},
        ],
        "headers": {"A3": _header("이름"), "B3": _header("코드"), "C3": _header("수량")},
        "rows": [
            {
                "source_row": 4,
                "cells": {
                    "A4": _cell("가", "string"),
                    "B4": _cell("00123", "string"),
                    "C4": _cell(123, "number", "00000"),
                },
            }
        ],
    },
    "merge": {
        "sheet": "Merge",
        "sheet_index": 1,
        "address": "A3:F6",
        "header": "3,4",
        "header_rows": [3, 4],
        "columns": [
            {"source_column": "A", "key": "구분", "display_name": "구분", "display_derived": True},
            {"source_column": "B", "key": "매출_1월", "display_name": "매출_1월"},
            {"source_column": "C", "key": "column_C", "display_name": "매출_1월", "display_derived": True},
            {"source_column": "D", "key": "column_D", "display_name": None},
            {"source_column": "E", "key": "column_E", "display_name": None},
            {"source_column": "F", "key": "column_F", "display_name": None, "display_derived": True},
        ],
        "headers": {
            "A3": _header("구분", _merge("anchor", "A3", "A3:A4")),
            "A4": _header(None, _merge("covered", "A3", "A3:A4")),
            "B3": _header("매출", _merge("anchor", "B3", "B3:C3")),
            "B4": _header("1월"),
            "C3": _header(None, _merge("covered", "B3", "B3:C3")),
            "C4": _header("1월"),
            "D3": _header(None),
            "D4": _header(None),
            "E3": _header(None, _merge("anchor", "E3", "E3:F3")),
            "E4": _header(None),
            "F3": _header(None, _merge("covered", "E3", "E3:F3")),
            "F4": _header(None),
        },
        "rows": [
            {
                "source_row": 5,
                "cells": {
                    "A5": _cell("사과", "string"),
                    "B5": _cell(10, "number"),
                    "C5": _EMPTY,
                    "D5": _EMPTY,
                    "E5": _EMPTY,
                    "F5": _EMPTY,
                },
            },
            {"source_row": 6, "cells": {f"{letter}6": _EMPTY for letter in "ABCDEF"}},
        ],
    },
    "plain": {
        "sheet": "Plain",
        "sheet_index": 2,
        "address": "A1:B2",
        "header": "none",
        "header_rows": [],
        "columns": [
            {"source_column": "A", "key": "column_A", "display_name": None},
            {"source_column": "B", "key": "column_B", "display_name": None},
        ],
        "headers": {},
        "rows": [
            {
                "source_row": 1,
                "cells": {
                    "A1": _cell(0, "number", formula="=1-1", cached_value=0, cached_value_state="stored"),
                    "B1": _cell(None, "formula", formula="=2+2", cached_value=None, cached_value_state="absent"),
                },
            },
            {"source_row": 2, "cells": {"A2": _cell("가", "string"), "B2": _cell(1, "number")}},
        ],
    },
    "invalid_header": {
        "sheet": "Codes",
        "address": "A3:C4",
        "header": "2",
        "code": "invalid_header",
        "exit_code": 2,
    },
}
# Plain 시트 XML. A1은 <v>0</v>, B1은 <v> 요소 자체가 없다.
_CACHE_XML = {"sheet": "Plain", "stored": {"A1": ["1-1", "0"]}, "absent": {"B1": "2+2"}}
_EXPECTED_BODY = json.loads(
    json.dumps({"file": TABLES.name, "cases": _CASES, "cache_xml": _CACHE_XML}, ensure_ascii=False)
)

_TABLE_PUBLIC_KEYS = {
    "bridge", "task", "request_id", "execution_id", "status", "wrapper_exit_code", "text", "data",
    "artifacts", "error", "operation", "recalculated", "values_from", "freshness", "source", "requested",
    "table", "structural_state", "structural_state_covers_semantic_correctness", "input_integrity",
}
_TABLE_SUCCESS_KEYS = ("values_from", "freshness", "source", "requested", "table")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

# --- 합성 xlsx ---

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sheet_member(archive: zipfile.ZipFile, sheet_name: str) -> str:
    """workbook.xml의 시트 관계 id와 rels의 Target으로 시트 XML 위치를 찾는다. sheetN.xml을 가정하지 않는다."""
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


def _sheet_root(path: Path, sheet_name: str) -> ET.Element:
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read(_sheet_member(archive, sheet_name)))


def _rewrite_plain_cache(path: Path) -> None:
    """openpyxl은 수식의 계산값을 저장하지 않고 빈 <v>를 남긴다. Plain 시트 XML에서
    A1의 <v>에 0을 넣고, B1의 <v> 요소는 빈 것까지 모두 지운다."""
    with zipfile.ZipFile(path) as archive:
        member = _sheet_member(archive, _CACHE_XML["sheet"])
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    assert member in {info.filename for info, _data in entries}, member
    rewritten = []
    for info, data in entries:
        if info.filename == member:
            root = ET.fromstring(data)
            before = _refs_with_value(root)
            for ref, (formula, value) in _CACHE_XML["stored"].items():
                cell = _xml_cell(root, ref)
                assert cell.findtext(f"{{{MAIN_NS}}}f") == formula, ref
                values = cell.findall(f"{{{MAIN_NS}}}v")
                assert len(values) <= 1, ref
                element = values[0] if values else ET.SubElement(cell, f"{{{MAIN_NS}}}v")
                assert not (element.text or "").strip(), ref
                element.text = value
            for ref, formula in _CACHE_XML["absent"].items():
                cell = _xml_cell(root, ref)
                assert cell.findtext(f"{{{MAIN_NS}}}f") == formula, ref
                for element in cell.findall(f"{{{MAIN_NS}}}v"):
                    cell.remove(element)
            assert _refs_with_value(root) == (before | set(_CACHE_XML["stored"])) - set(_CACHE_XML["absent"])
            ET.register_namespace("", MAIN_NS)
            data = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
        rewritten.append((info, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)


def _assert_cache_xml(path: Path) -> None:
    """connect를 부르기 전에 시트 XML을 직접 본다. 캐시 0은 <v>0</v>, 캐시 없음은 <v> 요소가 없다."""
    root = _sheet_root(path, _CACHE_XML["sheet"])
    for ref, (formula, value) in _CACHE_XML["stored"].items():
        cell = _xml_cell(root, ref)
        assert cell.findtext(f"{{{MAIN_NS}}}f") == formula, ref
        assert [element.text for element in cell.findall(f"{{{MAIN_NS}}}v")] == [value], ref
    for ref, formula in _CACHE_XML["absent"].items():
        cell = _xml_cell(root, ref)
        assert cell.findtext(f"{{{MAIN_NS}}}f") == formula, ref
        assert cell.findall(f"{{{MAIN_NS}}}v") == [], ref


def _build_tables(path: Path) -> None:
    """Codes: 3행 헤더와 4행 데이터, 10행 아래 다른 표. Merge: 3·4행 병합 헤더와 빈 열·빈 행.
    Plain: 헤더 없는 표와 캐시 0·캐시 없음 수식."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    codes = wb.active
    codes.title = "Codes"
    codes["A1"] = "제목"
    codes["A3"] = "이름"
    codes["B3"] = "코드"
    codes["C3"] = "수량"
    codes["A4"] = "가"
    codes["B4"] = "00123"
    codes["C4"] = 123
    codes["C4"].number_format = "00000"
    codes["A10"] = "항목"
    codes["B10"] = "값"
    codes["A11"] = "다른"
    codes["B11"] = 99

    merge = wb.create_sheet("Merge")
    merge["A3"] = "구분"
    merge.merge_cells("A3:A4")
    merge["B3"] = "매출"
    merge.merge_cells("B3:C3")
    merge["B4"] = "1월"
    merge["C4"] = "1월"
    merge.merge_cells("E3:F3")
    merge["A5"] = "사과"
    merge["B5"] = 10

    plain = wb.create_sheet("Plain")
    plain["A1"] = "=1-1"
    plain["B1"] = "=2+2"
    plain["A2"] = "가"
    plain["B2"] = 1
    wb.save(path)
    _rewrite_plain_cache(path)


# --- 전용 fixture. 없을 때만 만들고, 있으면 대조만 한다. ---


def _ensure_fixture(xlsx: Path = TABLES, expected_path: Path = TABLES_EXPECTED) -> dict:
    """xlsx와 expected.json이 둘 다 없을 때만 만든다. 하나라도 있으면 어떤 파일도 쓰지 않고 대조한다."""
    if not os.path.lexists(xlsx) and not os.path.lexists(expected_path):
        building = xlsx.with_name(f".building-{xlsx.name}")
        _build_tables(building)
        _assert_cache_xml(building)
        os.replace(building, xlsx)
        body = {"file": _EXPECTED_BODY["file"], "sha256": _sha256(xlsx)}
        body.update({key: value for key, value in _EXPECTED_BODY.items() if key != "file"})
        expected_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert os.path.lexists(expected_path), f"{expected_path.name}이 없다. {xlsx.name}만 있어 새로 만들지 않는다"
    assert os.path.lexists(xlsx), f"{xlsx.name}이 없다. {expected_path.name}만 있어 새로 만들지 않는다"
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{expected_path.name}이 JSON이 아니다: {exc}") from None
    assert isinstance(expected, dict), f"{expected_path.name}이 JSON 객체가 아니다"
    digest = expected.get("sha256")
    assert isinstance(digest, str) and _SHA256_HEX.fullmatch(digest), f"{expected_path.name}의 sha256이 64자 hex가 아니다"
    actual = _sha256(xlsx)
    assert actual == digest, f"{xlsx.name}의 sha256 {actual}가 {expected_path.name}의 sha256 {digest}와 다르다"
    body = {key: value for key, value in expected.items() if key != "sha256"}
    assert body == _EXPECTED_BODY, f"{expected_path.name}의 기대값이 명세와 다르다"
    _assert_cache_xml(xlsx)
    return expected


_PAST_NS = 1_600_000_000 * 10**9


def _snapshot(path: Path) -> tuple[bytes, int] | None:
    if not path.exists():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


def _age(*paths: Path) -> None:
    """덮어쓰기가 수정 시각으로 드러나도록 과거 시각으로 맞춘다."""
    for path in paths:
        os.utime(path, ns=(_PAST_NS, _PAST_NS))


def _write_expected(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- 실행 ---


def _operator(tmp_path: Path, roots: list[Path]) -> Path:
    body = {
        "python_path": sys.executable,
        "repo_cwd": str(ROOT),
        "allowed_input_roots": [str(path) for path in roots],
        "workspace_root": str(tmp_path / "work"),
        "publish_root": str(tmp_path / "publish"),
        "model_base_url": "http://127.0.0.1:11434",
        "model_name": "qwen2.5:7b",
        "allowed_profiles": ["generic"],
        "max_input_files": 1,
        "max_file_bytes": 5_000_000,
        "max_total_bytes": 5_000_000,
        "max_request_bytes": 1_000_000,
        "max_timeout_seconds": 120,
        "default_timeout_seconds": 60,
        "grace_seconds": 1,
        "lock_wait_seconds": 2,
        "max_stdout_bytes": 1_000_000,
        "max_stderr_bytes": 1_000_000,
        "allowed_extensions": [".xlsx", ".xls", ".xlsm", ".csv"],
    }
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _fake_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    """요청 JSON 확인 전용 래퍼 대역. request_id가 leak으로 시작하면 원본 경로를 응답에 넣는다."""
    repo = tmp_path / "fake-repo"
    package = repo / "core" / "application"
    package.mkdir(parents=True)
    (repo / "core" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    records = tmp_path / "fake-records"
    records.mkdir()
    (package / "wrapper.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "payload = json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
        "record = Path(os.environ['DEV5_FAKE_RECORDS']) / (payload['request_id'] + '.json')\n"
        "record.write_text(json.dumps({'argv': sys.argv[1:], 'payload': payload}, ensure_ascii=False), encoding='utf-8')\n"
        "item = payload['inputs'][0]\n"
        "name = item['path'] if payload['request_id'].startswith('leak') else item['display_name']\n"
        "print(json.dumps({'status': 'success', 'request_id': payload['request_id'], 'execution_id': 'fake',\n"
        "    'operation': payload.get('operation'), 'recalculated': False, 'text': '표', 'data': {'preview_records': []},\n"
        "    'values_from': 'stored_workbook', 'freshness': 'unconfirmed',\n"
        "    'source': {'display_name': name, 'sheet_name': 'Codes', 'sheet_index': 0},\n"
        "    'requested': payload.get('read'),\n"
        "    'table': {'address': 'A3:C4', 'blank_rows': 'kept', 'blank_columns': 'kept', 'columns': [], 'rows': []},\n"
        "    'cells': [], 'returned': {'address': 'A3:C4', 'cell_count': 0}, 'artifacts': [], 'error': None,\n"
        "    'structural_state': 'not_applicable',\n"
        "    'input_integrity': {'state': 'unchanged', 'authoritative': True, 'items': []}}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    config = tmp_path / "fake-operator.json"
    config.write_text(
        json.dumps({"python_path": sys.executable, "repo_cwd": str(repo), "workspace_root": str(tmp_path / "work")}),
        encoding="utf-8",
    )
    return config, records


def _run_connect(args: list[str], tmp_path: Path, config: Path, records: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    dropped = {CONFIG_ENV, "FORCE_COLOR", "PYTHON_COLORS", "PYTHONPATH"}
    env = {key: value for key, value in os.environ.items() if key not in dropped}
    env[CONFIG_ENV] = str(config)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    if records is not None:
        env["DEV5_FAKE_RECORDS"] = str(records)
    return subprocess.run(
        [sys.executable, str(CONNECT), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=180,
        check=False,
    )


def _single_json(stdout: bytes) -> dict:
    lines = stdout.decode("utf-8").splitlines()
    assert len(lines) == 1, lines
    body = json.loads(lines[0])
    assert isinstance(body, dict)
    return body


def _table_args(
    source: Path | str,
    sheet: str,
    address: str | None,
    header: str | None,
    request_id: str,
) -> list[str]:
    args = ["--task", "read_table", "--file", str(source), "--sheet", sheet, "--request-id", request_id]
    if address is not None:
        args += ["--address", address]
    if header is not None:
        args += ["--header", header]
    return args


def _case_args(name: str, source: Path = TABLES, *, sheet: str | None = None, request_id: str | None = None) -> list[str]:
    case = _CASES[name]
    return _table_args(source, sheet or case["sheet"], case["address"], case["header"], request_id or f"dev5-{name}")


def _forbidden_paths(source: Path, tmp_path: Path) -> set[str]:
    return {
        str(source),
        os.path.realpath(source),
        str(source.parent),
        str(TABLE_FIXTURES),
        str(tmp_path),
        os.path.realpath(tmp_path),
        str(tmp_path / "work"),
    }


def _assert_public_has_no_paths(stdout: bytes, paths: set[str]) -> None:
    text = stdout.decode("utf-8")
    escaped = json.dumps(json.loads(text), ensure_ascii=True)
    for path in paths:
        assert path not in text, path
        assert json.dumps(path, ensure_ascii=True)[1:-1] not in escaped, path


def _real_table(tmp_path: Path, args: list[str], source: Path, roots: list[Path]) -> tuple[int, dict]:
    config = _operator(tmp_path, roots)
    completed = _run_connect(args, tmp_path, config)
    assert b"Traceback" not in completed.stdout + completed.stderr
    body = _single_json(completed.stdout)
    _assert_public_has_no_paths(completed.stdout, _forbidden_paths(source, tmp_path))
    return completed.returncode, body


def _assert_integrity(body: dict, digest: str, display_name: str = "tables.xlsx") -> None:
    integrity = body["input_integrity"]
    assert integrity["state"] == "unchanged"
    assert integrity["authoritative"] is True
    assert len(integrity["files"]) == 1
    item = integrity["files"][0]
    assert item["display_name"] == display_name
    assert item["before"] == item["after"] == digest
    assert item["state"] == "unchanged"
    for key in ("path", "source", "copy"):
        assert key not in item


def _assert_table_success(body: dict, case: dict, display_name: str = "tables.xlsx") -> dict:
    assert set(body) == _TABLE_PUBLIC_KEYS
    assert body["bridge"] == "excel-analyzer"
    assert body["task"] == "read_table"
    assert body["operation"] == "read_table"
    assert body["status"] == "success"
    assert body["wrapper_exit_code"] == 0
    assert body["execution_id"]
    assert body["error"] is None
    assert body["text"] is None
    assert body["data"] is None
    assert body["artifacts"] == []
    assert body["recalculated"] is False
    assert body["values_from"] == "stored_workbook"
    assert body["freshness"] == "unconfirmed"
    assert body["structural_state"] == "not_applicable"
    assert body["structural_state_covers_semantic_correctness"] is False
    assert body["source"] == {"display_name": display_name, "sheet_name": case["sheet"], "sheet_index": case["sheet_index"]}
    assert body["requested"] == {"address": case["address"], "header": {"rows": case["header_rows"]}}

    table = body["table"]
    assert set(table) == {"address", "blank_rows", "blank_columns", "columns", "rows"}
    assert table["address"] == case["address"]
    assert table["blank_rows"] == "kept"
    assert table["blank_columns"] == "kept"

    columns = table["columns"]
    assert len(columns) == len(case["columns"])
    for column, expected in zip(columns, case["columns"]):
        letter = expected["source_column"]
        seen = {"source_column": column["source_column"], "key": column["key"], "display_name": column["display_name"]}
        if "display_derived" in column:
            seen["display_derived"] = column["display_derived"]
        assert seen == expected, letter
        assert column["column_index"] == column_index_from_string(letter)
        assert [header["row"] for header in column["headers"]] == case["header_rows"], letter
        for header in column["headers"]:
            assert header["a1"] == f"{letter}{header['row']}"
            shown = {"value": header["value"], "value_type": header["value_type"], "merge": header["merge"]}
            assert shown == case["headers"][header["a1"]], header["a1"]
            assert "formula" not in header

    rows = table["rows"]
    assert [row["source_row"] for row in rows] == [row["source_row"] for row in case["rows"]]
    for row, expected in zip(rows, case["rows"]):
        assert set(row) == {"source_row", "cells"}
        assert [cell["a1"] for cell in row["cells"]] == list(expected["cells"])
        for cell, column in zip(row["cells"], columns):
            assert cell["key"] == column["key"], cell["a1"]
            assert {key: cell[key] for key in _CELL_FIELDS} == expected["cells"][cell["a1"]], cell["a1"]
            assert "merge" not in cell, cell["a1"]
    return table


def _cells(table: dict) -> dict[str, dict]:
    return {cell["a1"]: cell for row in table["rows"] for cell in row["cells"]}


def _columns(table: dict) -> dict[str, dict]:
    return {column["source_column"]: column for column in table["columns"]}


# --- 전용 fixture 검사 ---


def test_fixture_matches_spec_hash_and_cache_xml() -> None:
    expected = _ensure_fixture()
    assert expected["file"] == "tables.xlsx"
    assert expected["sha256"] == _sha256(TABLES)
    _assert_cache_xml(TABLES)


def test_fixture_check_keeps_repository_files_untouched() -> None:
    _ensure_fixture()
    before = _snapshot(TABLES)
    expected_before = _snapshot(TABLES_EXPECTED)

    _ensure_fixture()

    assert _snapshot(TABLES) == before
    assert _snapshot(TABLES_EXPECTED) == expected_before


def test_fixture_is_created_only_when_both_files_are_missing(tmp_path: Path) -> None:
    xlsx = tmp_path / "fixture" / "tables.xlsx"
    expected_path = xlsx.parent / "expected.json"
    xlsx.parent.mkdir()

    expected = _ensure_fixture(xlsx, expected_path)

    assert expected["sha256"] == _sha256(xlsx)
    assert {key: value for key, value in expected.items() if key != "sha256"} == _EXPECTED_BODY
    _assert_cache_xml(xlsx)
    assert sorted(path.name for path in xlsx.parent.iterdir()) == ["expected.json", "tables.xlsx"]
    _age(xlsx, expected_path)
    created = _snapshot(xlsx)
    expected_created = _snapshot(expected_path)

    _ensure_fixture(xlsx, expected_path)

    assert _snapshot(xlsx) == created
    assert _snapshot(expected_path) == expected_created


def test_fixture_hash_mismatch_fails_without_overwrite(tmp_path: Path) -> None:
    _ensure_fixture()
    xlsx = tmp_path / "tables.xlsx"
    expected_path = tmp_path / "expected.json"
    shutil.copy2(TABLES, xlsx)
    _write_expected(expected_path, {"sha256": "0" * 64, **_EXPECTED_BODY})
    _age(xlsx, expected_path)
    before = _snapshot(xlsx)
    expected_before = _snapshot(expected_path)

    with pytest.raises(AssertionError, match="sha256"):
        _ensure_fixture(xlsx, expected_path)

    assert _snapshot(xlsx) == before
    assert _snapshot(expected_path) == expected_before


def test_fixture_expected_value_mismatch_fails_without_overwrite(tmp_path: Path) -> None:
    _ensure_fixture()
    xlsx = tmp_path / "tables.xlsx"
    expected_path = tmp_path / "expected.json"
    shutil.copy2(TABLES, xlsx)
    changed = json.loads(json.dumps(_EXPECTED_BODY, ensure_ascii=False))
    changed["cases"]["merge"]["columns"][2]["key"] = "매출_1월"
    _write_expected(expected_path, {"sha256": _sha256(xlsx), **changed})
    _age(xlsx, expected_path)
    before = _snapshot(xlsx)
    expected_before = _snapshot(expected_path)

    with pytest.raises(AssertionError, match="기대값이 명세와 다르다"):
        _ensure_fixture(xlsx, expected_path)

    assert _snapshot(xlsx) == before
    assert _snapshot(expected_path) == expected_before


@pytest.mark.parametrize("present", ["xlsx", "expected"])
def test_fixture_half_present_fails_without_creating(tmp_path: Path, present: str) -> None:
    _ensure_fixture()
    xlsx = tmp_path / "tables.xlsx"
    expected_path = tmp_path / "expected.json"
    if present == "xlsx":
        shutil.copy2(TABLES, xlsx)
    else:
        shutil.copy2(TABLES_EXPECTED, expected_path)

    with pytest.raises(AssertionError, match="새로 만들지 않는다"):
        _ensure_fixture(xlsx, expected_path)

    assert os.path.lexists(xlsx) is (present == "xlsx")
    assert os.path.lexists(expected_path) is (present == "expected")


# --- 실제 connect.py와 실제 래퍼 ---


def test_one_line_header_keeps_codes_and_ignores_other_table(tmp_path: Path) -> None:
    _ensure_fixture()
    digest = _sha256(TABLES)
    _assert_cache_xml(TABLES)

    code, body = _real_table(tmp_path, _case_args("codes"), TABLES, [FIXTURES])

    assert code == 0
    table = _assert_table_success(body, _CASES["codes"])
    cells = _cells(table)
    assert cells["B4"]["value"] == "00123" and cells["B4"]["value_type"] == "string"
    assert cells["C4"]["value"] == 123 and cells["C4"]["value_type"] == "number"
    assert cells["C4"]["number_format"] == "00000"
    for column in table["columns"]:
        assert "display_derived" not in column
        assert column["key"] == column["display_name"]
    assert [row["source_row"] for row in table["rows"]] == [4]
    shown = json.dumps(table, ensure_ascii=False)
    for word in ("제목", "항목", "다른", "A10", "B11"):
        assert word not in shown, word
    _assert_integrity(body, digest)
    assert _sha256(TABLES) == digest


def test_two_line_merged_header_keeps_blank_row_and_column(tmp_path: Path) -> None:
    _ensure_fixture()
    digest = _sha256(TABLES)
    _assert_cache_xml(TABLES)

    code, body = _real_table(tmp_path, _case_args("merge"), TABLES, [FIXTURES])

    assert code == 0
    table = _assert_table_success(body, _CASES["merge"])
    columns = _columns(table)
    assert columns["A"]["headers"][1]["a1"] == "A4"
    assert columns["A"]["headers"][1]["value"] is None
    assert columns["A"]["headers"][1]["merge"]["role"] == "covered"
    assert columns["C"]["key"] == "column_C" and columns["C"]["display_name"] == "매출_1월"
    assert columns["C"]["headers"][0]["value"] is None
    assert "display_derived" not in columns["D"]
    assert columns["F"]["display_name"] is None and columns["F"]["display_derived"] is True
    assert [row["source_row"] for row in table["rows"]] == [5, 6]
    assert all(cell["value"] is None and cell["value_type"] == "empty" for cell in table["rows"][1]["cells"])
    cells = _cells(table)
    assert cells["D5"]["key"] == "column_D" and cells["D6"]["key"] == "column_D"
    _assert_integrity(body, digest)
    assert _sha256(TABLES) == digest


def test_no_header_keeps_first_row_and_distinguishes_cache_zero_from_absent(tmp_path: Path) -> None:
    _ensure_fixture()
    digest = _sha256(TABLES)
    _assert_cache_xml(TABLES)

    code, body = _real_table(tmp_path, _case_args("plain"), TABLES, [FIXTURES])

    assert code == 0
    table = _assert_table_success(body, _CASES["plain"])
    assert [column["headers"] for column in table["columns"]] == [[], []]
    assert [row["source_row"] for row in table["rows"]] == [1, 2]
    cells = _cells(table)
    assert cells["A1"]["value"] == 0 and cells["A1"]["value"] is not None
    assert cells["A1"]["cached_value"] == 0 and cells["A1"]["cached_value_state"] == "stored"
    assert cells["B1"]["value"] is None and cells["B1"]["cached_value"] is None
    assert cells["B1"]["cached_value_state"] == "absent" and cells["B1"]["value_type"] == "formula"
    _assert_integrity(body, digest)
    assert _sha256(TABLES) == digest


@pytest.mark.parametrize(
    ("header", "code"),
    [(_CASES["invalid_header"]["header"], _CASES["invalid_header"]["code"]), (None, "missing_header")],
)
def test_wrapper_header_failures_pass_through(tmp_path: Path, header: str | None, code: str) -> None:
    _ensure_fixture()
    digest = _sha256(TABLES)
    case = _CASES["invalid_header"]
    args = _table_args(TABLES, case["sheet"], case["address"], header, f"dev5-{code}")

    exit_code, body = _real_table(tmp_path, args, TABLES, [FIXTURES])

    assert exit_code == case["exit_code"]
    assert body["status"] == "invalid_request"
    assert body["wrapper_exit_code"] == 2
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["task"] == body["operation"] == "read_table"
    assert body["recalculated"] is False
    for key in _TABLE_SUCCESS_KEYS:
        assert body[key] is None
    assert body["text"] is None and body["data"] is None and body["artifacts"] == []
    assert "cells" not in body
    assert _sha256(TABLES) == digest


def test_temporary_workbook_reads_by_sheet_index_without_paths(tmp_path: Path) -> None:
    source = tmp_path / "inputs" / "tables.xlsx"
    _build_tables(source)
    _assert_cache_xml(source)
    digest = _sha256(source)

    code, body = _real_table(
        tmp_path, _case_args("plain", source, sheet="2", request_id="dev5-temp-plain"), source, [source.parent]
    )

    assert code == 0
    _assert_table_success(body, _CASES["plain"])
    _assert_integrity(body, digest)

    code, body = _real_table(
        tmp_path, _case_args("merge", source, request_id="dev5-temp-merge"), source, [source.parent]
    )

    assert code == 0
    _assert_table_success(body, _CASES["merge"])
    assert _sha256(source) == digest


# --- 래퍼 대역: 요청 모양, 공개 응답 모양, 인자 실패 ---


@pytest.mark.parametrize(
    ("header", "read_header"),
    [
        ("3", {"rows": [3]}),
        ("3,4", {"rows": [3, 4]}),
        (" 3 , 4 ", {"rows": [3, 4]}),
        ("none", {"rows": []}),
        (None, None),
        ("2", {"rows": [2]}),
        ("4,3", {"rows": [4, 3]}),
        ("0", {"rows": [0]}),
        ("3,3,3", {"rows": [3, 3, 3]}),
    ],
)
def test_table_request_carries_header_as_given(tmp_path: Path, header: str | None, read_header: dict | None) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / "inputs" / "tables.xlsx"
    request_id = "dev5-request"

    completed = _run_connect(_table_args(source, "Codes", "A3:C4", header, request_id), tmp_path, config, records)

    assert completed.returncode == 0, completed.stderr
    seen = json.loads((records / f"{request_id}.json").read_text(encoding="utf-8"))
    assert seen["argv"] == ["--config", str(config)]
    read = {"address": "A3:C4"}
    if read_header is not None:
        read["header"] = read_header
    assert seen["payload"] == {
        "contract_version": "1.0",
        "request_id": request_id,
        "operation": "read_table",
        "inputs": [{"path": str(source), "sheet": "Codes", "display_name": "tables.xlsx"}],
        "read": read,
    }
    for key in ("user_prompt", "profile_name", "analysis_mode"):
        assert key not in seen["payload"]
    if header is None:
        assert "header" not in seen["payload"]["read"]


def test_negative_header_row_is_left_to_wrapper(tmp_path: Path) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / "inputs" / "tables.xlsx"
    args = _table_args(source, "Codes", "A3:C4", None, "dev5-negative") + ["--header=-1"]

    completed = _run_connect(args, tmp_path, config, records)

    assert completed.returncode == 0, completed.stderr
    seen = json.loads((records / "dev5-negative.json").read_text(encoding="utf-8"))
    assert seen["payload"]["read"] == {"address": "A3:C4", "header": {"rows": [-1]}}


def test_table_public_response_keeps_table_unchanged(tmp_path: Path) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / "inputs" / "tables.xlsx"

    completed = _run_connect(_table_args(source, "Codes", "A3:C4", "3", "dev5-public"), tmp_path, config, records)

    assert completed.returncode == 0, completed.stderr
    body = _single_json(completed.stdout)
    assert set(body) == _TABLE_PUBLIC_KEYS
    assert body["status"] == "success"
    assert body["task"] == body["operation"] == "read_table"
    assert body["text"] is None and body["data"] is None and body["artifacts"] == []
    assert body["table"] == {"address": "A3:C4", "blank_rows": "kept", "blank_columns": "kept", "columns": [], "rows": []}
    assert body["requested"] == {"address": "A3:C4", "header": {"rows": [3]}}
    assert body["freshness"] == "unconfirmed"
    assert "cells" not in body and "returned" not in body and "preview_records" not in completed.stdout.decode("utf-8")
    _assert_public_has_no_paths(completed.stdout, _forbidden_paths(source, tmp_path))


def test_table_public_response_with_path_is_blocked(tmp_path: Path) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / "inputs" / "tables.xlsx"

    completed = _run_connect(_table_args(source, "Codes", "A3:C4", "3", "leak-table"), tmp_path, config, records)

    assert completed.returncode == 1
    body = _single_json(completed.stdout)
    assert body["status"] == "execution_failed"
    assert body["error"]["code"] == "path_exposure_blocked"
    assert body["task"] == body["operation"] == "read_table"
    for key in _TABLE_SUCCESS_KEYS:
        assert body[key] is None
    _assert_public_has_no_paths(completed.stdout, _forbidden_paths(source, tmp_path))


def _assert_no_spawn_failure(completed: subprocess.CompletedProcess[bytes], records: Path, code: str) -> dict:
    assert completed.returncode == 2
    body = _single_json(completed.stdout)
    assert body["status"] == "invalid_request"
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["wrapper_exit_code"] is None
    assert body["execution_id"] is None
    assert body["text"] is None and body["data"] is None and body["artifacts"] == []
    assert b"usage:" not in completed.stdout
    assert b"Traceback" not in completed.stdout + completed.stderr
    assert list(records.iterdir()) == []
    return body


_SOURCE_NAME = "inputs/tables.xlsx"


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--task", "read_table", "--sheet", "Codes", "--request-id", "dev5-no-address", "--header", "3"], "missing_address"),
        (
            ["--task", "read_table", "--sheet", "Codes", "--request-id", "dev5-blank", "--address", "  ", "--header", "3"],
            "missing_address",
        ),
        (
            ["--task", "read_range", "--sheet", "0", "--request-id", "dev5-range-header", "--address", "B2", "--header", "3"],
            "forbidden_argument",
        ),
        (["--task", "summary", "--sheet", "0", "--request-id", "dev5-summary-header", "--header", "3"], "forbidden_argument"),
        (["--task", "schema", "--sheet", "0", "--request-id", "dev5-schema-header", "--header", "none"], "forbidden_argument"),
        (["--task", "quality", "--sheet", "0", "--request-id", "dev5-quality-header", "--header", "3"], "forbidden_argument"),
        (["--task", "summary", "--sheet", "0", "--request-id", "dev5-summary-address", "--address", "A1"], "forbidden_argument"),
    ],
)
def test_argument_failures_do_not_spawn(tmp_path: Path, args: list[str], code: str) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / _SOURCE_NAME

    completed = _run_connect(args + ["--file", str(source)], tmp_path, config, records)

    _assert_no_spawn_failure(completed, records, code)


@pytest.mark.parametrize("header", ["", " ", "a", "3,", ",3", "3,,4", "3;4", "3 4", "3.0", "None", "NONE", "none,3", "3-4", "[3]"])
def test_unreadable_header_tokens_fail_before_wrapper(tmp_path: Path, header: str) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / _SOURCE_NAME

    args = _table_args(source, "Codes", "A3:C4", None, "dev5-bad-token") + [f"--header={header}"]

    completed = _run_connect(args, tmp_path, config, records)

    body = _assert_no_spawn_failure(completed, records, "invalid_arguments")
    assert "--header" in body["error"]["message"]


@pytest.mark.parametrize(
    ("make_args", "code"),
    [
        (lambda src, tmp: _table_args(src, "Codes", None, "3", str(src)), "missing_address"),
        (lambda src, tmp: _table_args(src, "Codes", "A3:C4", "x", str(src)), "invalid_arguments"),
        (lambda src, tmp: _table_args(src, "Codes", "A3:C4", "x", str(tmp / "work")), "invalid_arguments"),
        (lambda src, tmp: _table_args(src, "Codes", "A3:C4", "3", str(src) + "x" * 130), "invalid_request_id"),
        (lambda src, tmp: _table_args(src, "Codes", "A3:C4", "3", "dev5-unknown") + ["--range", str(src)], "invalid_arguments"),
        (
            lambda src, tmp: ["--task", "read_range", "--file", str(src), "--sheet", "0", "--request-id", str(src),
                              "--address", "B2", "--header", "3"],
            "forbidden_argument",
        ),
    ],
)
def test_early_failures_hide_paths(tmp_path: Path, make_args, code: str) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = tmp_path / _SOURCE_NAME

    completed = _run_connect(make_args(source, tmp_path), tmp_path, config, records)

    body = _assert_no_spawn_failure(completed, records, code)
    assert body["request_id"] is None
    _assert_public_has_no_paths(completed.stdout, _forbidden_paths(source, tmp_path))


def test_help_lists_header_and_stays_text(tmp_path: Path) -> None:
    config, _records = _fake_wrapper(tmp_path)

    completed = _run_connect(["--help"], tmp_path, config)

    assert completed.returncode == 0
    text = completed.stdout.decode("utf-8")
    assert text.startswith("usage: excel-analyzer-connect")
    for flag in ("--task", "--file", "--sheet", "--request-id", "--address", "--header"):
        assert flag in text, flag
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
