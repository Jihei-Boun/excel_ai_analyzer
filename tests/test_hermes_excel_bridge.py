"""DEV-3 연결 스크립트. 기본 실행은 Hermes와 외부 모델을 호출하지 않는다."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "development" / "dev0" / "fixtures"
CONNECT = ROOT / "hermes" / "excel-analyzer" / "connect.py"
_SPEC = importlib.util.spec_from_file_location("excel_analyzer_connect", CONNECT)
assert _SPEC is not None and _SPEC.loader is not None
connect = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(connect)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="운영 래퍼는 Linux만 지원한다")


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


class _Capture:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.chunks: list[str] = []
        monkeypatch.setattr(sys.stdout, "write", self._write)

    def _write(self, text: str) -> int:
        self.chunks.append(text)
        return len(text)

    def json(self) -> dict:
        return json.loads("".join(self.chunks))


def _invoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str], *, roots: list[Path] | None = None) -> tuple[int, dict]:
    config = _operator(tmp_path, roots or [FIXTURES])
    monkeypatch.setenv(connect.CONFIG_ENV, str(config))
    captured = _Capture(monkeypatch)
    code = connect.main(args)
    return code, captured.json()


def test_unsupported_task_does_not_spawn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _blocked(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append(["called"])
        raise AssertionError("지원하지 않는 작업은 래퍼를 호출하면 안 된다")

    monkeypatch.setattr(connect.subprocess, "run", _blocked)
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "integrate", "--file", "/tmp/sales.xlsx", "--sheet", "0", "--request-id", "bad-task"],
    )
    assert code == 2
    assert body["error"]["code"] == "unsupported_task"
    assert calls == []


def _run_script(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    dropped = {connect.CONFIG_ENV, "FORCE_COLOR", "PYTHON_COLORS"}
    env = {key: value for key, value in os.environ.items() if key not in dropped}
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, str(CONNECT), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _single_json(stdout: bytes) -> dict:
    lines = stdout.decode("utf-8").splitlines()
    assert len(lines) == 1, lines
    body = json.loads(lines[0])
    assert isinstance(body, dict)
    return body


def _assert_argument_failure(completed: subprocess.CompletedProcess[bytes]) -> dict:
    assert completed.returncode == 2
    body = _single_json(completed.stdout)
    assert body["status"] == "invalid_request"
    assert body["text"] is None
    assert body["data"] is None
    assert body["artifacts"] == []
    assert body["execution_id"] is None
    assert body["wrapper_exit_code"] is None
    assert body["error"]["code"] == "invalid_arguments"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert b"usage:" not in completed.stdout
    assert b"Traceback" not in completed.stdout + completed.stderr
    return body


def test_script_unknown_argument_emits_json_only(tmp_path: Path) -> None:
    completed = _run_script(
        [
            "--task", "summary",
            "--file", "/tmp/a.xlsx",
            "--sheet", "0",
            "--request-id", "unknown-arg",
            "--config", "/tmp/other.json",
        ],
        tmp_path,
    )
    body = _assert_argument_failure(completed)
    assert "--config" in body["error"]["message"]


def test_script_missing_required_argument_emits_json_only(tmp_path: Path) -> None:
    completed = _run_script(
        ["--task", "summary", "--sheet", "0", "--request-id", "missing-file-arg"],
        tmp_path,
    )
    body = _assert_argument_failure(completed)
    assert "--file" in body["error"]["message"]


def test_script_help_stays_text(tmp_path: Path) -> None:
    completed = _run_script(["--help"], tmp_path)
    assert completed.returncode == 0
    text = completed.stdout.decode("utf-8")
    assert text.startswith("usage: excel-analyzer-connect")
    assert "--task" in text and "--request-id" in text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_wrapper_argv_is_a_list_without_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    class _Done:
        returncode = 2
        stdout = json.dumps(
            {"status": "invalid_request", "error": {"code": "missing_file", "message": "없다"}, "request_id": "argv"}
        ).encode()
        stderr = b""

    def _run(argv: list[str], **kwargs: object) -> _Done:
        seen["argv"] = argv
        seen["shell"] = kwargs.get("shell", False)
        seen["cwd"] = kwargs.get("cwd")
        return _Done()

    monkeypatch.setattr(connect.subprocess, "run", _run)
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", "/tmp/not-there.xlsx", "--sheet", "0", "--request-id", "argv"],
    )
    assert code == 2
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert seen["shell"] is False
    assert argv[1:4] == ["-m", "core.application.wrapper", "--config"]
    assert body["status"] == "invalid_request"
    assert "python" not in body


def test_relative_path_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(connect.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawn")))
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", "sales.xlsx", "--sheet", "0", "--request-id", "relative"],
    )
    assert code == 2
    assert body["error"]["code"] == "absolute_path_required"


def test_mock_responses_keep_failure_and_not_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {"n": 0}

    def script(response: dict, returncode: int) -> None:
        class _Done:
            stdout = json.dumps(response).encode()
            stderr = b""

        _Done.returncode = returncode

        def _once(argv: list[str], **kwargs: object) -> _Done:
            del argv, kwargs
            calls["n"] += 1
            return _Done()

        monkeypatch.setattr(connect.subprocess, "run", _once)

    script(
        {
            "status": "success",
            "request_id": "mock-ok",
            "execution_id": "abc",
            "text": "요약",
            "data": {"shape": [0, 0], "columns": [], "preview_records": []},
            "artifacts": [],
            "error": None,
            "safety": {"validation_status": "passed", "validation": {"structural": {"state": "not_run"}}},
            "input_integrity": {"state": "unchanged", "authoritative": True, "items": []},
        },
        0,
    )
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", "/tmp/a.xlsx", "--sheet", "0", "--request-id", "mock-ok"],
    )
    assert code == 0
    assert body["status"] == "success"
    assert body["structural_state"] == "not_run"
    assert body["structural_state_covers_semantic_correctness"] is False
    assert "validation_status" not in body
    assert body["data"]["shape"] == [0, 0]

    script(
        {"status": "timeout", "request_id": "mock-time", "execution_id": "def", "error": {"code": "wrapper_timeout", "message": "시간"}},
        6,
    )
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "quality", "--file", "/tmp/a.xlsx", "--sheet", "0", "--request-id", "mock-time"],
    )
    assert code == 6
    assert body["status"] == "timeout"
    assert body["text"] is None
    assert body["artifacts"] == []
    assert calls["n"] == 2

    script({"status": "success", "text": "잘못", "artifacts": [{"path": "/tmp/nope.xlsx"}]}, 1)
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "schema", "--file", "/tmp/a.xlsx", "--sheet", "Sheet1", "--request-id", "mock-mismatch"],
    )
    assert code == 1
    assert body["status"] == "exit_code_mismatch"
    assert body["text"] is None
    assert body["artifacts"] == []


def test_direct_summary_schema_and_quality(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sales = FIXTURES / "sales.xlsx"
    quality = FIXTURES / "quality.xlsx"
    before = {
        sales: hashlib.sha256(sales.read_bytes()).hexdigest(),
        quality: hashlib.sha256(quality.read_bytes()).hexdigest(),
    }
    code, summary = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", str(sales), "--sheet", "0", "--request-id", "direct-summary"],
    )
    assert code == 0
    assert summary["status"] == "success"
    assert summary["wrapper_exit_code"] == 0
    assert summary["execution_id"]
    assert summary["artifacts"] == []
    assert "5행" in summary["text"] and "3열" in summary["text"]
    assert summary["structural_state"] in {"not_run", "unknown"}
    assert summary["input_integrity"]["state"] == "unchanged"
    assert summary["input_integrity"]["files"][0]["before"] == before[sales]
    assert summary["input_integrity"]["files"][0]["after"] == before[sales]

    code, schema = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "schema", "--file", str(quality), "--sheet", "0", "--request-id", "direct-schema"],
    )
    assert code == 0
    assert schema["status"] == "success"
    assert schema["data"]["preview_records"][0]["행 수"] == 4
    assert schema["data"]["preview_records"][0]["열 수"] == 3
    assert schema["artifacts"]
    artifact = schema["artifacts"][0]
    assert artifact["exists"] is True
    assert artifact["sha256_matches"] is True
    assert artifact["size_matches"] is True
    assert Path(artifact["path"]).is_file()

    code, quality_body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "quality", "--file", str(quality), "--sheet", "0", "--request-id", "direct-quality"],
    )
    assert code == 0
    assert quality_body["status"] == "success"
    assert "결측 셀: 1개" in quality_body["text"]
    assert "완전 중복 행: 1개" in quality_body["text"]
    assert quality_body["artifacts"] == []
    assert "셀 좌표" not in quality_body
    assert hashlib.sha256(sales.read_bytes()).hexdigest() == before[sales]
    assert hashlib.sha256(quality.read_bytes()).hexdigest() == before[quality]


def test_direct_missing_outside_and_bad_sheet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sales = FIXTURES / "sales.xlsx"
    missing = tmp_path / "missing.xlsx"
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", str(missing), "--sheet", "0", "--request-id", "direct-missing"],
    )
    assert code == 2
    assert body["status"] == "invalid_request"
    assert body["error"]["code"] == "missing_file"
    assert body["text"] is None

    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(sales.read_bytes())
    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", str(outside), "--sheet", "0", "--request-id", "direct-outside"],
        roots=[FIXTURES],
    )
    assert code == 2
    assert body["error"]["code"] == "outside_root"
    assert body["text"] is None

    code, body = _invoke(
        monkeypatch,
        tmp_path,
        ["--task", "summary", "--file", str(sales), "--sheet", "없는시트", "--request-id", "direct-bad-sheet"],
    )
    assert code != 0
    assert body["status"] != "success"
    assert body["artifacts"] == []


# --- DEV-4 read_range. 모델을 부르지 않는다. ---

READ_FIXTURES = FIXTURES / "dev4-read-range"
CELLS = READ_FIXTURES / "cells.xlsx"
CELLS_EXPECTED = READ_FIXTURES / "expected.json"
_CELL_FIELDS = ("value", "value_type", "formula", "cached_value", "cached_value_state", "merge")
_READ_KEYS = ("values_from", "source", "requested", "returned", "cells")
_ANALYSIS_PROMPTS = {
    "summary": "이 파일의 주요 내용을 요약해줘",
    "schema": "컬럼 목록을 보여줘",
    "quality": "이 파일의 데이터 품질을 분석해줘",
}
_ANALYSIS_PUBLIC_KEYS = {
    "bridge", "task", "request_id", "execution_id", "status", "wrapper_exit_code", "text", "data",
    "artifacts", "error", "structural_state", "structural_state_covers_semantic_correctness", "input_integrity",
}


def _set_cached_values(path: Path, cached: dict[str, str]) -> None:
    """openpyxl이 저장한 시트 XML의 수식 칸에 저장 계산값을 직접 넣는다."""
    member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    rewritten = []
    for info, data in entries:
        if info.filename == member:
            xml = data.decode("utf-8")
            for ref, value in cached.items():
                match = re.search(rf'<c r="{ref}"([^>]*)>(.*?)</c>', xml, re.S)
                assert match is not None, ref
                body = match.group(2)
                assert "<f" in body, ref
                body, replaced = re.subn(r"<v\s*/>|<v>\s*</v>", f"<v>{value}</v>", body)
                assert replaced == 1, ref
                xml = xml[: match.start()] + f'<c r="{ref}"{match.group(1)}>{body}</c>' + xml[match.end():]
            data = xml.encode("utf-8")
        rewritten.append((info, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)


def _drop_value_elements(path: Path, refs: tuple[str, ...]) -> None:
    """openpyxl이 수식 칸에 남기는 빈 <v>를 지워 <v> 요소 자체가 없는 수식 칸을 만든다."""
    member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    rewritten = []
    for info, data in entries:
        if info.filename == member:
            xml = data.decode("utf-8")
            for ref in refs:
                match = re.search(rf'<c r="{ref}"([^>]*)>(.*?)</c>', xml, re.S)
                assert match is not None, ref
                body = match.group(2)
                assert "<f" in body, ref
                body, removed = re.subn(r"<v\s*/>|<v>\s*</v>", "", body)
                assert removed <= 1, ref
                assert "<v" not in body, ref
                xml = xml[: match.start()] + f'<c r="{ref}"{match.group(1)}>{body}</c>' + xml[match.end():]
            data = xml.encode("utf-8")
        rewritten.append((info, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)


def _sheet_cell_xml(path: Path, ref: str) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    match = re.search(rf'<c r="{ref}"[^>]*>(.*?)</c>', xml, re.S)
    assert match is not None, ref
    return match.group(1)


def _build_cells(path: Path) -> None:
    """B2 정수, A4:B4 병합, A5 수식과 저장값 0, B5 <v> 없는 수식. C4·C5는 빈 칸."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["B2"] = 10
    ws["A4"] = "기준"
    ws.merge_cells("A4:B4")
    ws["A5"] = "=1-1"
    ws["B5"] = "=2+2"
    wb.save(path)
    _set_cached_values(path, {"A5": "0"})
    _drop_value_elements(path, ("B5",))


def _cells_layout_ok(path: Path) -> bool:
    try:
        a5 = _sheet_cell_xml(path, "A5")
        b5 = _sheet_cell_xml(path, "B5")
    except (AssertionError, KeyError, OSError, zipfile.BadZipFile, UnicodeError):
        return False
    a5_ok = "<f>1-1</f>" in a5 and re.findall(r"<v[\s>/]", a5) == ["<v>"] and "<v>0</v>" in a5
    return a5_ok and "<f>2+2</f>" in b5 and "<v" not in b5


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _load_expected(expected_path: Path) -> dict:
    """expected.json을 읽기만 한다. 불완전하면 빠진 항목을 밝혀 실패하고 어떤 파일도 고치지 않는다."""
    assert expected_path.is_file(), f"{expected_path.name}이 없다"
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{expected_path.name}이 JSON이 아니다: {exc}") from None
    assert isinstance(expected, dict), f"{expected_path.name}이 JSON 객체가 아니다"
    missing = []
    digest = expected.get("sha256")
    if not isinstance(digest, str) or _SHA256_HEX.fullmatch(digest) is None:
        missing.append(f"sha256(64자 hex, 현재 {digest!r})")
    if not isinstance(expected.get("file"), str) or not expected["file"]:
        missing.append("file")
    if not isinstance(expected.get("cells"), dict):
        missing.append("cells")
    assert not missing, f"{expected_path.name}에 빠지거나 잘못된 항목: {', '.join(missing)}"
    return expected


def _ensure_cells(cells: Path = CELLS, expected_path: Path = CELLS_EXPECTED) -> dict:
    """기대 파일이 완전할 때만 진행한다. cells.xlsx는 그 경로에 없을 때만 만든다.

    이미 있는 파일은 해시 누락, 해시 불일치, 칸 구조 불일치가 있어도 덮어쓰지 않는다.
    """
    expected = _load_expected(expected_path)
    assert expected["file"] == cells.name, f"{expected_path.name}의 file이 {cells.name}이 아니다"
    if not os.path.lexists(cells):
        _build_cells(cells)
    return expected


def _read_fixture(cells: Path = CELLS, expected_path: Path = CELLS_EXPECTED) -> dict:
    expected = _ensure_cells(cells, expected_path)
    digest = _sha256(cells)
    assert digest == expected["sha256"], (
        f"{cells.name}의 sha256 {digest}가 {expected_path.name}의 sha256 {expected['sha256']}와 다르다"
    )
    return expected


def _fake_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    """요청 JSON 확인 전용 래퍼 대역. repo_cwd의 core.application.wrapper를 대신한다."""
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
        "record = Path(os.environ['DEV4_FAKE_RECORDS']) / (payload['request_id'] + '.json')\n"
        "record.write_text(json.dumps({'argv': sys.argv[1:], 'payload': payload}, ensure_ascii=False), encoding='utf-8')\n"
        "print(json.dumps({'status': 'success', 'request_id': payload['request_id'], 'execution_id': 'fake',\n"
        "    'text': 'fake', 'data': {'shape': [0, 0], 'columns': [], 'preview_records': []}, 'artifacts': [],\n"
        "    'error': None, 'structural_state': 'not_run',\n"
        "    'input_integrity': {'state': 'unchanged', 'authoritative': True, 'items': []}}))\n",
        encoding="utf-8",
    )
    config = tmp_path / "fake-operator.json"
    config.write_text(
        json.dumps({"python_path": sys.executable, "repo_cwd": str(repo), "workspace_root": str(tmp_path / "work")}),
        encoding="utf-8",
    )
    return config, records


def _run_connect(args: list[str], tmp_path: Path, config: Path, records: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    dropped = {connect.CONFIG_ENV, "FORCE_COLOR", "PYTHON_COLORS", "PYTHONPATH"}
    env = {key: value for key, value in os.environ.items() if key not in dropped}
    env[connect.CONFIG_ENV] = str(config)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    if records is not None:
        env["DEV4_FAKE_RECORDS"] = str(records)
    return subprocess.run(
        [sys.executable, str(CONNECT), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=180,
        check=False,
    )


def _read_args(address: str | None, *, sheet: str = "0", request_id: str = "dev4-read") -> list[str]:
    args = ["--task", "read_range", "--file", str(CELLS), "--sheet", sheet, "--request-id", request_id]
    if address is not None:
        args += ["--address", address]
    return args


def _assert_public_has_no_paths(stdout: bytes, tmp_path: Path) -> None:
    text = stdout.decode("utf-8")
    escaped = json.dumps(json.loads(text), ensure_ascii=True)
    forbidden = {
        str(CELLS),
        os.path.realpath(CELLS),
        str(READ_FIXTURES),
        str(tmp_path),
        os.path.realpath(tmp_path),
        str(tmp_path / "work"),
    }
    for path in forbidden:
        assert path not in text, path
        assert json.dumps(path, ensure_ascii=True)[1:-1] not in escaped, path


def _real_read(tmp_path: Path, address: str, *, sheet: str = "0", request_id: str = "dev4-read") -> tuple[int, dict, bytes]:
    config = _operator(tmp_path, [FIXTURES])
    completed = _run_connect(_read_args(address, sheet=sheet, request_id=request_id), tmp_path, config)
    assert b"Traceback" not in completed.stdout
    body = _single_json(completed.stdout)
    _assert_public_has_no_paths(completed.stdout, tmp_path)
    return completed.returncode, body, completed.stdout


def _assert_read_integrity(body: dict, digest: str) -> None:
    integrity = body["input_integrity"]
    assert integrity["state"] == "unchanged"
    assert integrity["authoritative"] is True
    assert len(integrity["files"]) == 1
    item = integrity["files"][0]
    assert item["display_name"] == "cells.xlsx"
    assert item["before"] == item["after"] == digest
    assert item["state"] == "unchanged"
    for key in ("path", "source", "copy"):
        assert key not in item


def _assert_cell(cell: dict, a1: str, expected: dict) -> None:
    assert cell["a1"] == a1
    for key in _CELL_FIELDS:
        assert cell[key] == expected["cells"][a1][key], (a1, key, cell[key])


def test_read_fixture_matches_expected_and_cache_layout() -> None:
    expected = _read_fixture()
    assert expected["sheet_name"] == "Data"
    a5 = _sheet_cell_xml(CELLS, "A5")
    assert "<f>1-1</f>" in a5
    assert re.findall(r"<v[\s>/]", a5) == ["<v>"]
    assert "<v>0</v>" in a5
    b5 = _sheet_cell_xml(CELLS, "B5")
    assert "<f>2+2</f>" in b5
    assert "<v" not in b5


_PAST_NS = 1_600_000_000 * 10**9


def _snapshot(path: Path) -> tuple[bytes, str, int] | None:
    if not path.exists():
        return None
    return path.read_bytes(), _sha256(path), path.stat().st_mtime_ns


def _age(*paths: Path) -> None:
    """덮어쓰기가 수정 시각으로 드러나도록 과거 시각으로 맞춘다."""
    for path in paths:
        os.utime(path, ns=(_PAST_NS, _PAST_NS))


def _temp_fixture(tmp_path: Path, sha256: object) -> tuple[Path, Path]:
    folder = tmp_path / "fixture"
    folder.mkdir()
    expected = json.loads(CELLS_EXPECTED.read_text(encoding="utf-8"))
    expected["sha256"] = sha256
    expected_path = folder / "expected.json"
    expected_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return folder / "cells.xlsx", expected_path


def _build_wrong_layout(path: Path) -> None:
    """A5에 저장값을 넣지 않은 수식 칸. 명세의 칸 구조와 다르다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["B2"] = 10
    ws["A5"] = "=1-1"
    ws["B5"] = "=2+2"
    wb.save(path)


def test_read_fixture_keeps_repository_fixture_untouched() -> None:
    before = _snapshot(CELLS)
    expected_before = _snapshot(CELLS_EXPECTED)
    assert before is not None, "저장소 cells.xlsx가 없다"
    assert expected_before is not None, "저장소 expected.json이 없다"

    expected = _read_fixture()

    assert expected["sha256"] == before[1]
    assert _snapshot(CELLS) == before
    assert _snapshot(CELLS_EXPECTED) == expected_before


def test_read_fixture_never_rewrites_existing_file_when_sha_null_and_layout_differs(tmp_path: Path) -> None:
    cells, expected_path = _temp_fixture(tmp_path, None)
    _build_wrong_layout(cells)
    assert not _cells_layout_ok(cells)
    _age(cells, expected_path)
    before = _snapshot(cells)
    expected_before = _snapshot(expected_path)

    with pytest.raises(AssertionError, match="sha256"):
        _read_fixture(cells, expected_path)

    assert _snapshot(cells) == before
    assert not _cells_layout_ok(cells)
    assert _snapshot(expected_path) == expected_before
    assert json.loads(expected_path.read_text(encoding="utf-8"))["sha256"] is None


def test_read_fixture_keeps_existing_file_on_hash_mismatch(tmp_path: Path) -> None:
    cells, expected_path = _temp_fixture(tmp_path, "0" * 64)
    shutil.copy2(CELLS, cells)
    _age(cells, expected_path)
    before = _snapshot(cells)
    expected_before = _snapshot(expected_path)

    with pytest.raises(AssertionError, match="sha256"):
        _read_fixture(cells, expected_path)

    assert _snapshot(cells) == before
    assert _snapshot(expected_path) == expected_before


def test_read_fixture_creates_only_missing_file_without_touching_expected(tmp_path: Path) -> None:
    cells, expected_path = _temp_fixture(tmp_path, "0" * 64)
    _age(expected_path)
    expected_before = _snapshot(expected_path)
    assert not cells.exists()

    _ensure_cells(cells, expected_path)

    assert cells.is_file()
    a5 = _sheet_cell_xml(cells, "A5")
    assert "<f>1-1</f>" in a5
    assert re.findall(r"<v[\s>/]", a5) == ["<v>"]
    assert "<v>0</v>" in a5
    b5 = _sheet_cell_xml(cells, "B5")
    assert "<f>2+2</f>" in b5
    assert "<v" not in b5
    assert _snapshot(expected_path) == expected_before

    _age(cells)
    created = _snapshot(cells)
    with pytest.raises(AssertionError, match="sha256"):
        _read_fixture(cells, expected_path)
    assert _snapshot(cells) == created
    assert _snapshot(expected_path) == expected_before


@pytest.mark.parametrize(
    ("content", "word"),
    [
        (None, "expected.json이 없다"),
        ("{not json", "JSON이 아니다"),
        ("[]", "JSON 객체가 아니다"),
        ({"sha256": None}, "sha256"),
        ({"sha256": "abc"}, "sha256"),
        ({"sha256": "g" * 64}, "sha256"),
        ({"file": None}, "file"),
        ({"cells": None}, "cells"),
    ],
)
def test_read_fixture_fails_on_incomplete_expected_without_writing(tmp_path: Path, content: object, word: str) -> None:
    cells, expected_path = _temp_fixture(tmp_path, "0" * 64)
    if content is None:
        expected_path.unlink()
    elif isinstance(content, str):
        expected_path.write_text(content, encoding="utf-8")
    else:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        expected.update(content)
        expected_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if expected_path.exists():
        _age(expected_path)
    expected_before = _snapshot(expected_path)

    with pytest.raises(AssertionError, match=word):
        _read_fixture(cells, expected_path)

    assert not os.path.lexists(cells)
    assert _snapshot(expected_path) == expected_before


def test_read_range_single_cell_through_real_wrapper(tmp_path: Path) -> None:
    expected = _read_fixture()
    before = _sha256(CELLS)

    code, body, _ = _real_read(tmp_path, "B2", request_id="dev4-read-b2")

    assert code == 0
    assert body["bridge"] == "excel-analyzer"
    assert body["task"] == "read_range"
    assert body["status"] == "success"
    assert body["wrapper_exit_code"] == 0
    assert body["request_id"] == "dev4-read-b2"
    assert body["execution_id"]
    assert body["error"] is None
    assert body["text"] is None
    assert body["data"] is None
    assert body["artifacts"] == []
    assert body["operation"] == "read_range"
    assert body["recalculated"] is False
    assert body["values_from"] == "stored_workbook"
    assert body["source"] == {"display_name": "cells.xlsx", "sheet_name": "Data", "sheet_index": 0}
    assert body["requested"] == {"address": "B2"}
    assert body["returned"] == {"address": "B2", "cell_count": 1}
    assert len(body["cells"]) == 1
    _assert_cell(body["cells"][0], "B2", expected)
    assert (body["cells"][0]["row"], body["cells"][0]["column_index"], body["cells"][0]["column_letter"]) == (2, 2, "B")
    assert body["structural_state"] == "not_applicable"
    assert body["structural_state_covers_semantic_correctness"] is False
    _assert_read_integrity(body, before)
    assert _sha256(CELLS) == before == expected["sha256"]


def test_read_range_keeps_blanks_merge_and_formulas(tmp_path: Path) -> None:
    expected = _read_fixture()
    before = _sha256(CELLS)

    code, body, _ = _real_read(tmp_path, "A4:C5", sheet="Data", request_id="dev4-read-range")

    assert code == 0
    assert body["status"] == "success"
    assert body["wrapper_exit_code"] == 0
    assert body["data"] is None and body["text"] is None
    assert body["recalculated"] is False
    assert body["source"] == {"display_name": "cells.xlsx", "sheet_name": "Data", "sheet_index": 0}
    assert body["requested"] == {"address": "A4:C5"}
    assert body["returned"] == {"address": "A4:C5", "cell_count": 6}
    order = ["A4", "B4", "C4", "A5", "B5", "C5"]
    assert [cell["a1"] for cell in body["cells"]] == order
    cells = {cell["a1"]: cell for cell in body["cells"]}
    for a1 in order:
        _assert_cell(cells[a1], a1, expected)

    assert cells["A4"]["value"] == "기준"
    assert cells["A4"]["merge"] == {"role": "anchor", "anchor": "A4", "range": "A4:B4"}
    assert cells["B4"]["value"] is None
    assert cells["B4"]["value_type"] == "empty"
    assert cells["B4"]["merge"]["role"] == "covered"
    for blank in ("C4", "C5"):
        assert cells[blank]["value"] is None
        assert cells[blank]["value_type"] == "empty"
        assert cells[blank]["merge"] is None

    assert cells["A5"]["formula"] == "=1-1"
    assert cells["A5"]["value"] is None
    assert cells["A5"]["cached_value"] == 0
    assert cells["A5"]["cached_value"] is not None
    assert cells["A5"]["cached_value_state"] == "stored"
    assert cells["B5"]["formula"] == "=2+2"
    assert cells["B5"]["cached_value"] is None
    assert cells["B5"]["cached_value_state"] == "absent"
    assert body["structural_state"] == "not_applicable"
    _assert_read_integrity(body, before)
    assert _sha256(CELLS) == before


@pytest.mark.parametrize(
    ("address", "sheet", "code"),
    [
        ("B", "Data", "invalid_address"),
        ("C5:A4", "Data", "reversed_range"),
        ("A1", "없는시트", "invalid_sheet"),
        ("A1:A2001", "Data", "range_too_large"),
    ],
)
def test_read_range_wrapper_errors_pass_through(tmp_path: Path, address: str, sheet: str, code: str) -> None:
    _read_fixture()
    before = _sha256(CELLS)

    exit_code, body, _ = _real_read(tmp_path, address, sheet=sheet, request_id=f"dev4-err-{code}")

    assert exit_code == 2
    assert body["status"] == "invalid_request"
    assert body["wrapper_exit_code"] == 2
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["recalculated"] is False
    for key in _READ_KEYS:
        assert body[key] is None
    assert body["text"] is None
    assert body["data"] is None
    assert body["artifacts"] == []
    assert _sha256(CELLS) == before


@pytest.mark.parametrize(
    ("args", "code", "word"),
    [
        (_read_args(None, request_id="dev4-no-address"), "missing_address", None),
        (_read_args("   ", request_id="dev4-blank-address"), "missing_address", None),
        (
            ["--task", "summary", "--file", str(CELLS), "--sheet", "0", "--request-id", "dev4-summary-address", "--address", "B2"],
            "forbidden_argument",
            None,
        ),
        (_read_args("B2", request_id="dev4-unknown") + ["--range", "A1"], "invalid_arguments", "--range"),
    ],
)
def test_read_range_argument_failures_do_not_spawn(tmp_path: Path, args: list[str], code: str, word: str | None) -> None:
    config, records = _fake_wrapper(tmp_path)

    completed = _run_connect(args, tmp_path, config, records)

    assert completed.returncode == 2
    body = _single_json(completed.stdout)
    assert body["status"] == "invalid_request"
    assert body["error"]["code"] == code
    assert body["wrapper_exit_code"] is None
    assert body["execution_id"] is None
    assert body["text"] is None and body["data"] is None and body["artifacts"] == []
    if word is not None:
        assert word in body["error"]["message"]
    assert b"usage:" not in completed.stdout
    assert b"Traceback" not in completed.stdout + completed.stderr
    assert list(records.iterdir()) == []


@pytest.mark.parametrize(
    ("make_args", "code"),
    [
        (lambda tmp: _read_args(None, request_id=str(CELLS)), "missing_address"),
        (lambda tmp: _read_args(None, request_id=str(CELLS)[-30:]), "missing_address"),
        (lambda tmp: _read_args("   ", request_id=str(tmp / "work")), "missing_address"),
        (lambda tmp: _read_args("B2", request_id=str(CELLS) + "x" * 130), "invalid_request_id"),
        (lambda tmp: _read_args("B2", request_id="dev4-unknown-path") + ["--range", str(CELLS)], "invalid_arguments"),
    ],
)
def test_read_range_early_failures_hide_paths(tmp_path: Path, make_args, code: str) -> None:
    config, records = _fake_wrapper(tmp_path)
    args = make_args(tmp_path)

    completed = _run_connect(args, tmp_path, config, records)

    assert completed.returncode == 2
    body = _single_json(completed.stdout)
    assert body["status"] == "invalid_request"
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["request_id"] is None
    _assert_public_has_no_paths(completed.stdout, tmp_path)
    assert b"Traceback" not in completed.stdout + completed.stderr
    assert list(records.iterdir()) == []


@pytest.mark.parametrize("task", ["summary", "schema", "quality"])
def test_analysis_tasks_send_unchanged_request(tmp_path: Path, task: str) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = FIXTURES / "sales.xlsx"
    request_id = f"dev4-fake-{task}"

    completed = _run_connect(
        ["--task", task, "--file", str(source), "--sheet", "0", "--request-id", request_id], tmp_path, config, records
    )

    assert completed.returncode == 0, completed.stderr
    body = _single_json(completed.stdout)
    assert set(body) == _ANALYSIS_PUBLIC_KEYS
    assert body["status"] == "success"
    assert body["task"] == task
    seen = json.loads((records / f"{request_id}.json").read_text(encoding="utf-8"))
    assert seen["argv"] == ["--config", str(config)]
    assert seen["payload"] == {
        "contract_version": "1.0",
        "request_id": request_id,
        "user_prompt": _ANALYSIS_PROMPTS[task],
        "inputs": [{"path": str(source), "sheet": 0, "display_name": "sales.xlsx"}],
        "profile_name": "generic",
        "analysis_mode": "single",
    }
    assert "operation" not in seen["payload"]
    assert "read" not in seen["payload"]


SKILL = ROOT / "hermes" / "excel-analyzer" / "SKILL.md"
_SKILL_VERSION_LINE = "version: 1.4.0"
_COVERED_SENTENCES = (
    "`merge.role`이 `covered`인 칸은 병합에 덮인 칸이다.",
    "그 칸의 `value`는 null이고 `value_type`은 `empty`다.",
    "답변은 이 두 필드를 그대로 말한다.",
    "병합 관계는 `merge.role`, `merge.anchor`, `merge.range`로 설명한다.",
    "기준 셀의 값을 그 칸의 값이나 내용으로 말하지 않는다.",
)
_COVERED_EXAMPLE = "B4의 값은 비어 있습니다. A4:B4 병합 범위에 속하며 기준 셀은 A4입니다."
_FORMULA_NOTICE = "수식을 재계산하지 않았으며, 파일에 저장된 계산값이 최신인지는 확인할 수 없습니다."
_FORMULA_CACHE_SENTENCES = (
    "`formula`는 수식 문자열이고 `cached_value`는 파일에 저장된 계산값이다",
    "`cached_value_state`가 `absent`이면",
    "`recalculated`가 false이면",
)


def test_skill_covered_wording_matches_expected_file_not_model_behavior() -> None:
    """지침 정합성 검사다. 모델 행동 검증이 아니며 Hermes와 모델을 부르지 않는다.

    기대 문장은 이 파일의 상수다. 저장소 밖 파일을 읽지 않는다.
    DEV-4 fixture는 읽기만 하고 해시가 그대로인지 확인한다.
    """
    untouched = (CELLS, CELLS_EXPECTED)
    before = {path: _sha256(path) for path in untouched}

    lines = SKILL.read_text(encoding="utf-8").splitlines()
    text = "\n".join(lines)
    assert [line for line in lines if line.startswith("version:")] == [_SKILL_VERSION_LINE]
    covered = [line for line in lines if _COVERED_SENTENCES[0] in line]
    assert len(covered) == 1, covered
    for sentence in _COVERED_SENTENCES:
        assert sentence in covered[0], sentence
    assert f"“{_COVERED_EXAMPLE}”" in covered[0]
    for sentence in _FORMULA_CACHE_SENTENCES:
        assert sentence in text, sentence
    assert f"“{_FORMULA_NOTICE}”" in text

    assert {path: _sha256(path) for path in untouched} == before


@pytest.mark.skipif(
    os.environ.get("HERMES_EXCEL_E2E") != "1",
    reason="HERMES_EXCEL_E2E=1일 때만 Hermes 설치 환경을 확인한다. 종단 검사가 아니며 Hermes를 호출하지 않는다",
)
def test_hermes_environment_prepared() -> None:
    home = Path(os.environ.get("HERMES_HOME", ""))
    assert home.is_absolute() and home.is_dir()
    skill = home / "skills" / "excel-analyzer"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "connect.py").is_file()


HERMES_PROFILE = Path("/home/raven323/hermes-excel-dev3")
HERMES_MODEL = "gemma4-excel-hermes"
HERMES_TIMEOUT_SECONDS = 900
_CONNECT_FLAGS = ("--task", "--file", "--sheet", "--request-id")
_TASK_LABELS = {"summary": "요약", "schema": "컬럼 목록 조회", "quality": "데이터 품질 진단"}
_CELL_REF = re.compile(
    r"(?<![A-Za-z0-9_$])\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])R[1-9][0-9]*C[1-9][0-9]*(?![A-Za-z0-9_])"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hermes_home() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or HERMES_PROFILE)
    assert home == HERMES_PROFILE, f"검증 프로필이 아니다: {home}"
    skill = home / "skills" / "excel-analyzer"
    assert (skill / "SKILL.md").is_file(), f"skill이 설치되지 않았다: {skill}"
    assert (skill / "connect.py").is_file(), f"연결 스크립트가 없다: {skill}"
    return home


def _hermes_executable() -> str:
    found = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    assert Path(found).is_file() and os.access(found, os.X_OK), f"hermes 실행 파일이 없다: {found}"
    return found


def _run_hermes(hermes: str, home: Path, workdir: Path, name: str, query: str) -> list[dict]:
    query_file = workdir / f"{name}.query.txt"
    query_file.write_text(query, encoding="utf-8")
    dropped = {
        connect.CONFIG_ENV,
        "HERMES_INFERENCE_MODEL",
        "HERMES_INFERENCE_PROVIDER",
        "FORCE_COLOR",
        "PYTHON_COLORS",
    }
    env = {key: value for key, value in os.environ.items() if key not in dropped}
    env["HERMES_HOME"] = str(home)
    env["PATH"] = os.pathsep.join([str(Path(hermes).parent), env.get("PATH", "")])
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    completed = subprocess.run(
        [
            hermes, "chat", "--oneshot", "--yolo", "--reasoning", "none",
            "-t", "terminal,skills", "--max-turns", "12",
            "--format", "stream-json", "--query-file", str(query_file),
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        timeout=HERMES_TIMEOUT_SECONDS,
        check=False,
    )
    (workdir / f"{name}.stream.jsonl").write_bytes(completed.stdout)
    (workdir / f"{name}.stderr.txt").write_bytes(completed.stderr)
    events: list[dict] = []
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "type" in item:
            events.append(item)
    results = [event for event in events if event.get("type") == "result"]
    stderr_tail = completed.stderr.decode("utf-8", errors="replace")[-2000:]
    assert completed.returncode == 0 and len(results) == 1 and results[0].get("exit_code") == 0, (
        f"{name}: hermes 종료 코드 {completed.returncode}, result {results}\n{stderr_tail}"
    )
    return events


def _command_tokens(command: str) -> list[str]:
    tokens: list[str] = []
    for token in shlex.split(command):
        if token.startswith("--") and "=" in token:
            tokens.extend(token.split("=", 1))
        else:
            tokens.append(token)
    return tokens


def _check_command(name: str, command: str, *, home: Path, expected: dict[str, str]) -> None:
    for word in ("--config", "http://", "https://", "11434", "operator", connect.CONFIG_ENV):
        assert word not in command, f"{name}: 명령에 {word}가 있다: {command}"
    tokens = _command_tokens(command)
    assert len(tokens) == 2 + 2 * len(_CONNECT_FLAGS), f"{name}: 명령 인자가 다르다: {command}"
    assert re.fullmatch(r"python3?(\.\d+)?", Path(tokens[0]).name), f"{name}: 실행기가 다르다: {command}"
    scripts = {
        "${HERMES_SKILL_DIR}/connect.py",
        "$HERMES_SKILL_DIR/connect.py",
        str(home / "skills" / "excel-analyzer" / "connect.py"),
    }
    assert tokens[1] in scripts, f"{name}: 설치된 연결 스크립트가 아니다: {command}"
    flags = tokens[2::2]
    assert sorted(flags) == sorted(_CONNECT_FLAGS), f"{name}: 플래그가 다르다: {command}"
    assert dict(zip(flags, tokens[3::2])) == expected, f"{name}: 인자 값이 다르다: {command}"


def _terminal_result(name: str, events: list[dict], call_at: int) -> dict:
    call_id = events[call_at].get("tool_call_id")
    for event in events[call_at + 1:]:
        if event.get("type") != "tool_result" or event.get("name") != "terminal":
            continue
        if call_id and event.get("tool_call_id") not in (None, call_id):
            continue
        return event
    raise AssertionError(f"{name}: 연결 스크립트 터미널 결과가 스트림에 없다")


def _bridge_output(name: str, result: dict) -> tuple[dict, object]:
    raw = str(result.get("output") or "")
    inner = raw
    exit_code: object = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and "output" in payload:
        inner = str(payload.get("output") or "")
        exit_code = payload.get("exit_code")
    found: list[dict] = []
    for line in inner.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(body, dict) and body.get("bridge") == connect.BRIDGE:
            found.append(body)
    assert len(found) == 1, f"{name}: 터미널 출력에서 연결 JSON 한 줄을 찾지 못했다\n{raw[:2000]}"
    return found[0], exit_code


def _hermes_case(
    hermes: str, home: Path, workdir: Path, name: str, *, task: str, source: Path
) -> tuple[dict, str, object]:
    request_id = f"e2e-{name}-{uuid.uuid4().hex}"
    query = (
        f"excel-analyzer skill을 사용해 아래 Excel 파일의 {_TASK_LABELS[task]}을(를) 해줘.\n"
        f"- 파일: {source}\n"
        "- 시트: 0\n"
        f"- 작업(task): {task}\n"
        f"- request-id: {request_id}\n"
        "결과를 한국어로 짧게 알려줘.\n"
    )
    events = _run_hermes(hermes, home, workdir, name, query)
    init = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    assert init, f"{name}: system/init 이벤트가 없다"
    model = str(init[0].get("model") or "")
    assert model.split(":")[0] == HERMES_MODEL, f"{name}: 에이전트 모델이 다르다: {model}"
    skill_at = next(
        (
            index
            for index, event in enumerate(events)
            if event.get("type") == "tool_use"
            and event.get("name") == "skill_view"
            and isinstance(event.get("input"), dict)
            and event["input"].get("name") == "excel-analyzer"
        ),
        None,
    )
    assert skill_at is not None, f"{name}: skill_view excel-analyzer 호출이 없다"
    calls = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "tool_use"
        and event.get("name") == "terminal"
        and "connect.py" in str((event.get("input") or {}).get("command", ""))
    ]
    assert len(calls) == 1, f"{name}: 연결 스크립트 터미널 호출 수 {len(calls)}"
    call_at = calls[0]
    assert call_at > skill_at, f"{name}: 연결 스크립트가 skill_view보다 먼저 실행됐다"
    _check_command(
        name,
        str(events[call_at]["input"]["command"]),
        home=home,
        expected={"--task": task, "--file": str(source), "--sheet": "0", "--request-id": request_id},
    )
    bridge, terminal_exit = _bridge_output(name, _terminal_result(name, events, call_at))
    assert bridge["request_id"] == request_id
    assert bridge["task"] == task
    answer = next(event for event in events if event.get("type") == "result").get("text")
    assert isinstance(answer, str) and answer.strip(), f"{name}: Hermes 최종 답변이 비었다"
    return bridge, answer, terminal_exit


def _assert_success(name: str, bridge: dict, terminal_exit: object, answer: str) -> None:
    assert bridge["status"] == "success", f"{name}: {bridge.get('status')} {bridge.get('error')}"
    assert bridge["wrapper_exit_code"] == 0
    if isinstance(terminal_exit, int):
        assert terminal_exit == 0
    assert isinstance(bridge["text"], str) and bridge["text"].strip()
    if not bridge.get("artifacts"):
        assert "excel-publish" not in answer, f"{name}: 없는 결과 경로를 말했다\n{answer}"


_NUMBER_TOKEN = (
    r"(?<![\d.,\-−])[-−]?(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])(?!,\d)"
)
_VALUE = rf"(?P<value>{_NUMBER_TOKEN})"
_DECO = r"[\s*`'\"]*"
_LABEL_GAP = rf"{_DECO}(?:(?:의\s*)?(?:개\s*)?수\s*)?(?:[:：=]|은|는|이|가)?{_DECO}(?:총\s*)?"
_EN_END = r"(?![A-Za-z])"


def _token_value(token: str) -> str:
    return token.replace(",", "").replace("−", "-")


def _paired_values(patterns: tuple[str, ...], answer: str) -> list[str]:
    return [
        _token_value(match.group("value"))
        for pattern in patterns
        for match in re.finditer(pattern, answer, re.I)
    ]


def _assert_summary_answer(bridge: dict, answer: str) -> None:
    text = bridge["text"]
    shape = re.search(r"([\d,]+)행 × ([\d,]+)열", text)
    total = re.search(r"`([^`]*매출[^`]*)`: 합 (-?[\d,.]+)", text)
    assert shape and total, f"요약 text에서 행·열과 매출 합을 찾지 못했다\n{text}"
    rows, cols, amount = (_token_value(value) for value in (shape.group(1), shape.group(2), total.group(2)))
    row_patterns = (
        rf"{_VALUE}\s*(?:개의?\s*)?(?:행|rows?{_EN_END})",
        rf"(?<![가-힣])(?:행|rows?{_EN_END}){_LABEL_GAP}{_VALUE}",
    )
    col_patterns = (
        rf"{_VALUE}\s*(?:개의?\s*)?(?:열|컬럼|columns?{_EN_END}|cols?{_EN_END})",
        rf"(?<![가-힣])(?:열|컬럼|columns?{_EN_END}|cols?{_EN_END}){_LABEL_GAP}{_VALUE}",
    )
    subject = rf"(?:{re.escape(total.group(1))}|매출(?:액|금액)?|sales|revenue)"
    sum_word = r"(?:합계|총합|총액|합|sum|total|총)"
    sum_patterns = (
        rf"{subject}{_DECO}(?:의\s*)?{sum_word}{_LABEL_GAP}{_VALUE}",
        rf"{sum_word}{_DECO}(?:of\s+)?{subject}{_LABEL_GAP}{_VALUE}",
    )
    assert rows in _paired_values(row_patterns, answer), f"요약 답변이 행 수 {rows}를 행으로 말하지 않았다\n{answer}"
    assert cols in _paired_values(col_patterns, answer), f"요약 답변이 열 수 {cols}를 열로 말하지 않았다\n{answer}"
    sums = _paired_values(sum_patterns, answer)
    assert sums and set(sums) == {amount}, f"요약 답변이 매출 합 {amount}를 매출 합으로 말하지 않았다 {sums}\n{answer}"


def _assert_schema(bridge: dict, answer: str) -> None:
    records = (bridge.get("data") or {}).get("preview_records") or []
    listed = records[0].get("컬럼 목록") if records and isinstance(records[0], dict) else None
    assert isinstance(listed, str) and listed, f"스키마 응답에 컬럼 목록이 없다\n{bridge.get('data')}"
    columns = [column.strip() for column in listed.split(",") if column.strip()]
    absent = [column for column in columns if column not in answer]
    assert not absent, f"스키마 답변에 컬럼 {absent}가 없다\n{answer}"
    for artifact in bridge.get("artifacts") or []:
        path = Path(artifact["path"])
        assert artifact["exists"] is path.is_file()
        assert artifact["exists"] is True, artifact
        data = path.read_bytes()
        assert artifact["size_matches"] is (len(data) == artifact["size_bytes"])
        assert artifact["sha256_matches"] is (hashlib.sha256(data).hexdigest() == artifact["sha256"])
        assert artifact["size_matches"] is True and artifact["sha256_matches"] is True, artifact


_ROW_COL_PAIR = re.compile(
    r"(\d+)\s*(?:번째\s*)?행\s*,?\s*(?:의\s*)?(\d+)\s*(?:번째\s*)?열"
    r"|(?:행|row)\s*(\d+)\s*[,/]?\s*(?:열|col(?:umn)?)\s*(\d+)",
    re.I,
)
_POSITION_REFS = (
    re.compile(r"\d+\s*(?:번째|번)\s*(?:행|열|셀)"),
    re.compile(r"(?<![가-힣])(?:행|열)\s*(?:번호\s*)?\d+(?!\d|\s*(?:개|건|행))"),
    re.compile(r"(?<![A-Za-z])[A-Z]{1,3}\s*열"),
    re.compile(r"\b(?:row|column)\s*#?\s*\d+", re.I),
    re.compile(r"\bcolumn\s+[A-Z]{1,3}\b"),
    re.compile(r"\b\d+(?:st|nd|rd|th)\s+(?:row|column)", re.I),
    re.compile(r"(?:인덱스|index)\s*[:#]?\s*\d+", re.I),
)


def _cell_positions(text: str, size: tuple[str, str] | None) -> list[str]:
    found = [match.group(0) for match in _CELL_REF.finditer(text)]
    for match in _ROW_COL_PAIR.finditer(text):
        pair = (match.group(1) or match.group(3), match.group(2) or match.group(4))
        if pair != size:
            found.append(match.group(0))
    for pattern in _POSITION_REFS:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return found


def _metric_line(labels: tuple[str, ...], units: tuple[str, ...]) -> re.Pattern[str]:
    label = "|".join(re.escape(item) for item in labels)
    unit = "|".join(re.escape(item) for item in units)
    return re.compile(rf"(?:-[ \t]+)?(?:{label})[ \t]*:[ \t]*(?P<value>[0-9]+)(?:[ \t]*(?:{unit}))?", re.I)


_MISSING_LINE = _metric_line(("결측 셀", "빈 셀", "missing cells", "missing cell"), ("개", "건", "cells", "cell"))
_DUPLICATE_LINE = _metric_line(
    ("완전 중복 행", "중복 행", "duplicate rows", "duplicate row"), ("개", "건", "행", "rows", "row")
)
_MISSING_WORD = re.compile(r"결측|누락|빈[ \t]*(?:셀|칸|값)|missing|blank|empty|null", re.I)
_DUPLICATE_WORD = re.compile(r"중복|duplicat", re.I)


def _assert_quality_answer(bridge: dict, answer: str) -> None:
    """비어 있지 않은 모든 줄이 `지표: 숫자 [단위]` 고정 형식이어야 한다.

    형식 밖 줄은 지표 이름이 없어도 앞 줄의 값을 부정하거나 정정할 수 있으므로 확인 불가로 거절한다.
    """
    text = bridge["text"]
    assert "결측 셀: 1개" in text and "완전 중복 행: 1개" in text, text
    missing: list[str] = []
    duplicate: list[str] = []
    missing_problems: list[str] = []
    duplicate_problems: list[str] = []
    others: list[str] = []
    for raw in answer.splitlines():
        line = raw.strip()
        if not line:
            continue
        if match := _MISSING_LINE.fullmatch(line):
            missing.append(match.group("value"))
            continue
        if match := _DUPLICATE_LINE.fullmatch(line):
            duplicate.append(match.group("value"))
            continue
        if _MISSING_WORD.search(line):
            missing_problems.append(line)
        if _DUPLICATE_WORD.search(line):
            duplicate_problems.append(line)
        others.append(line)
    assert not missing_problems, f"품질 답변의 결측 셀 표현을 확인할 수 없다 {missing_problems}\n{answer}"
    assert missing and set(missing) == {"1"}, f"품질 답변에 결측 셀 1개가 없다 {missing}\n{answer}"
    assert not duplicate_problems, f"품질 답변의 중복 행 표현을 확인할 수 없다 {duplicate_problems}\n{answer}"
    assert duplicate and set(duplicate) == {"1"}, f"품질 답변에 중복 행 1개가 없다 {duplicate}\n{answer}"
    assert not others, f"품질 답변의 형식 밖 줄을 확인할 수 없다 {others}\n{answer}"
    shape = re.search(r"([\d,]+)행 × ([\d,]+)열", text)
    size = (shape.group(1).replace(",", ""), shape.group(2).replace(",", "")) if shape else None
    if not _cell_positions(text, size):
        invented = _cell_positions(answer.replace(",", ""), size)
        assert not invented, f"품질 답변이 셀 좌표 {invented}를 만들었다\n{answer}"


_ANSWER_PATH = re.compile(r"(?<![\w/])(?:\$\{?\w+\}?|~)?/[^\s`'\"()<>\[\],]+")
_ANSWER_FILE_NAME = re.compile(r"[\w\-.]+\.(?:xlsx|xlsm|xls|csv|json|png|html|pdf|txt)\b", re.I)
_UNIT_NUMBER = re.compile(r"\d[\d,.]*\s*(?:행|열|개|원|건|%|rows?\b|columns?\b|cols?\b)", re.I)
_FACT_WORDS = (
    r"(?:매출|합계|(?<![가-힣])합|총합|평균|최[솟댓]값|결측|중복|컬럼|sales|revenue|\bsum\b|\btotal\b|average|\bmean\b"
    r"|missing cells?|duplicates?|columns?)"
)
_FACT_VALUE = re.compile(rf"{_FACT_WORDS}[^\d\n.。!?]{{0,16}}?\d|\d[^\d\n.。!?]{{0,6}}?{_FACT_WORDS}", re.I)
_SUCCESS_CLAIM = re.compile(
    r"성공(?:했|하였|적으로)|완료(?:했|하였|되었|됐|됨)|(?:생성|저장)(?:했|하였|되었|됐|됨)"
    r"|(?<!not )(?<!n't )(?:succeeded|successfully|completed|saved|created|generated)\b",
    re.I,
)


def _allowed_failure_path(token: str, missing: Path, home: Path) -> bool:
    path = re.sub(r"[^\x00-\x7f]+$", "", token).rstrip(".:;!?")
    if path in {
        "${HERMES_SKILL_DIR}/connect.py",
        "$HERMES_SKILL_DIR/connect.py",
        str(home / "skills" / "excel-analyzer" / "connect.py"),
    }:
        return True
    target = str(missing)
    parent = path.rstrip("/")
    return bool(parent) and (path == target or target.startswith(parent + "/"))


def _assert_missing_file(bridge: dict, terminal_exit: object, answer: str, *, missing: Path, home: Path) -> None:
    assert bridge["status"] == "invalid_request", bridge
    assert (bridge.get("error") or {}).get("code") == "missing_file", bridge
    assert bridge["wrapper_exit_code"] == 2
    if isinstance(terminal_exit, int):
        assert terminal_exit == 2
    assert bridge["text"] is None
    assert bridge.get("artifacts") == []
    assert re.search(
        r"missing_file|invalid_request|없|찾을 수|not found|does not exist|no such", answer, re.I
    ), f"실패 답변이 실패를 말하지 않았다\n{answer}"
    assert not re.search(r"^\s*\|.*\|\s*$", answer, re.M), f"실패 답변이 표를 만들었다\n{answer}"
    paths = _ANSWER_PATH.findall(answer)
    invented = [path for path in paths if not _allowed_failure_path(path, missing, home)]
    assert not invented, f"실패 답변이 입력 밖 경로 {invented}를 말했다\n{answer}"
    scrubbed = answer
    for allowed in sorted(paths, key=len, reverse=True):
        scrubbed = scrubbed.replace(allowed, " ")
    for allowed in (missing.name, missing.stem, str(bridge["request_id"]), "missing_file", "invalid_request"):
        scrubbed = scrubbed.replace(allowed, " ")
    scrubbed = re.sub(
        r"(?:시트|sheet)\s*[:=]?\s*0(?!\d)|(?:종료\s*코드|exit[_ ]code|wrapper_exit_code)\s*[:=은는이가]?\s*2(?!\d)",
        " ",
        scrubbed,
        flags=re.I,
    )
    names = _ANSWER_FILE_NAME.findall(scrubbed)
    assert not names, f"실패 답변이 입력 밖 파일 {names}를 말했다\n{answer}"
    facts = [match.group(0) for pattern in (_FACT_VALUE, _UNIT_NUMBER) for match in pattern.finditer(scrubbed)]
    assert not facts, f"실패 답변이 성공 수치 {facts}를 만들었다\n{answer}"
    claims = [match.group(0) for match in _SUCCESS_CLAIM.finditer(scrubbed)]
    assert not claims, f"실패 답변이 성공을 주장했다 {claims}\n{answer}"
    numbers = [value for value in re.findall(r"\d+(?:[.,]\d+)*", scrubbed) if value not in {"0", "1", "2"}]
    assert not numbers, f"실패 답변이 근거 없는 수치 {numbers}를 말했다\n{answer}"


_QUALITY_BRIDGE = {"text": "- 크기: 4행 × 3열\n- 결측 셀: 1개 (8.3%)\n- 완전 중복 행: 1개"}


@pytest.mark.parametrize(
    "answer",
    [
        "결측 셀은 2개이고 중복 행은 1개입니다.",
        "결측 셀은 1개입니다.",
        "완전 중복 행은 1개입니다.",
        "결측 1개, 중복 1개가 있습니다.",
        "결측 셀 1.0개, 중복 행 1개입니다.",
        "결측 셀 1.개, 중복 행 1개입니다.",
        "결측 셀 1개, 중복 행은 1.",
        "결측 셀 1개, 중복 행 0.5개입니다.",
        "결측 셀 1개, 중복 행 1,000개입니다.",
        "결측 셀 1개, 중복 행 1개, 결측 셀 2개입니다.",
        "결측 셀 1%와 중복 행 1개입니다.",
        "결측 셀 1명, 중복 행 1원입니다.",
        "결측 셀 1명, 중복 행 1개입니다.",
        "결측 셀 1개, 중복 행 1원입니다.",
        "결측 셀 1 명, 중복 행 1개입니다.",
        "결측 셀 1 rows, 중복 행 1개입니다.",
        "결측 셀 1개, 중복 행 1 cells입니다.",
        "결측 셀 1개, 중복 행 1열입니다.",
        "결측 셀 1개월, 중복 행 1개월",
        "결측 셀 1개국, 중복 행 1개",
        "결측 셀 1개, 중복 행 1개월",
        "결측 셀 1건수, 중복 행 1행렬",
        "missing cells: 1, duplicate rows: 1 rows",
        "1 missing cellar, 1 duplicate row",
        "결측 셀 2개, 중복 행 1개",
        "중복 행 1개, 결측 셀 2개",
        "결측 셀, 중복 행 1개입니다.",
        "중복 행 1개의 결측 셀이 있습니다.",
        "결측 셀 0.5개, 중복 행 1개입니다.",
        "missing cells: 1.0, duplicate rows: 1",
        "결측 셀 1개가 아니다. 중복 행 1개입니다.",
        "결측 셀은 1개가 아니라 2개이고 중복 행은 1개입니다.",
        "결측 셀 1개, 중복 행 1개가 아닙니다.",
        "결측 셀 1개 이상, 중복 행 1개",
        "결측 셀 약 1개, 중복 행 1개",
        "약 1개의 결측 셀과 1개의 중복 행",
        "not missing cells: 1, duplicate rows: 1",
        "missing cells: 1, not duplicate rows: 1",
        "No missing cells: 1, duplicate rows: 1",
        "There aren't missing cells: 1, duplicate rows: 1",
        "not 1 missing cell and 1 duplicate row",
        "아닌 결측 셀 1개, 중복 행 1개",
        "missing cells: 1 – 2, duplicate rows: 1",
        "missing cells: 1-2, duplicate rows: 1",
        "missing cells: 1 to 2, duplicate rows: 1",
        "missing cells: 1 or 2, duplicate rows: 1",
        "결측 셀 1~2개, 중복 행 1개",
        "결측 셀 1개 ~ 2개, 중복 행 1개",
        "결측 셀 1개, 중복 행 1개-2개",
        "결측 셀 1개, 중복 행 1개 또는 2개",
        "0 – 1개의 결측 셀과 1개의 중복 행",
        "1 missing cell and 0 to 1 duplicate row",
        "결측 셀: 1, 중복 행: 1",
        "결측 셀: 1개, 중복 행: 1",
        "결측 셀: 1, 중복 행: 1개",
        "결측 셀: 1 (8.3%), 중복 행: 1",
        "빈 셀 1개, 중복 행 1개입니다.",
        "결측 셀은 총 1개이고 중복된 행은 1개입니다.",
        "결측 셀 1개와 완전 중복 행 1개가 있습니다.",
        "결측 셀 1개, 완전 중복 행 1개",
        "결측 셀: 1개 (8.3%), 완전 중복 행: 1개",
        "결측 셀은 1개이고 중복 행은 1개입니다.",
        "결측 셀이 1 개, 중복 행이 1 개입니다.",
        "결측 셀 1건, 중복 행 1건입니다.",
        "결측 셀: 1개, 중복 행 1행입니다.",
        "1개의 결측 셀과 1개의 중복 행이 있습니다.",
        "missing cells: 1, duplicate rows: 1",
        "1 missing cell and 1 duplicate row",
        "결측 셀 1개가 아닐 뿐 아니라 2개\n중복 행: 1개",
        "결측 셀: 1개\n결측 셀 1개가 아닐 뿐 아니라 2개\n중복 행: 1개",
        "결측 셀: 1개\n중복 행: 1개\n결측 셀은 1개가 아니라 2개입니다.",
        "결측 셀: 1개\n중복 행: 1개\n위 수치는 틀렸습니다.",
        "결측 셀: 1개\n중복 행: 1개\n위 수치는 맞지 않습니다.",
        "결측 셀: 1개\n중복 행: 1개\n둘 다 사실과 다릅니다.",
        "위 수치는 맞지 않습니다.\n결측 셀: 1개\n중복 행: 1개",
        "missing cells: 1\nduplicate rows: 1\nThat is not correct.",
        "missing cells: 1\nduplicate rows: 1\nBoth values above are false.",
        "데이터 품질 진단 결과입니다.\n- 크기: 4행 × 3열\n- 결측 셀: 1개\n- 완전 중복 행: 1개",
        "결측 셀: 1개\n결측 셀: 2개\n중복 행: 1개",
        "빈 셀: 1개\n결측 셀: 2개\n중복 행: 1개",
        "결측 셀: 1개\n중복 행: 1개\n중복 행: 0개",
        "- 결측 셀: 1개 (8.3%)\n- 완전 중복 행: 1개",
        "- 결측 셀: 1개.\n- 완전 중복 행: 1개",
        "**결측 셀**: 1개\n**중복 행**: 1개",
        "결측 셀: 1개월\n중복 행: 1개",
        "결측 셀: 1개국\n중복 행: 1개",
        "결측 셀: 1개\n중복 행: 1개월",
        "결측 셀: 1개입니다\n중복 행: 1개",
        "결측 셀: 4개\n중복 행: 1개",
        "결측 셀: 1개\n중복 행: 3개",
        "결측 셀: 1.0개\n중복 행: 1개",
        "결측 셀: 1\n중복 행: 0.5",
        "missing cells: 1.0\nduplicate rows: 1",
        "결측 셀: 01개\n중복 행: 1개",
        "결측 셀: -1개\n중복 행: 1개",
        "결측 셀: 1 rows\n중복 행: 1개",
        "결측 셀: 1행\n중복 행: 1개",
        "결측 셀: 1개\n중복 행: 1 cells",
        "결측 셀: 1개\n중복 행: 1열",
        "missing cells:\n1\nduplicate rows: 1",
        "결측 셀:\n중복 행: 1",
        "missing cells: 1\nduplicate rows:\n1",
        "not missing cells: 1\nduplicate rows: 1",
        "missing cells: 1\nduplicate rows: 1 – 2",
        "결측 셀: 1개",
        "중복 행: 1개",
        "결측 셀이 1개 있습니다.\n중복 행: 1개",
        "결측 셀: 1개\n중복 행: 1개\n결측 셀과 중복 행이 있습니다.",
    ],
)
def test_quality_answer_rejects_unpaired_values(answer: str) -> None:
    with pytest.raises(AssertionError):
        _assert_quality_answer(_QUALITY_BRIDGE, answer)


def test_quality_answer_unit_must_be_whole_word() -> None:
    """`개` 뒤에 한글이 더 붙으면 단위가 아니다. `1개월`과 `1개국`은 `1개`가 아니다."""
    for answer in (
        "결측 셀 1개월, 중복 행 1개월",
        "결측 셀 1개국, 중복 행 1개",
        "결측 셀: 1개월\n중복 행: 1개",
        "결측 셀: 1개국\n중복 행: 1개",
    ):
        with pytest.raises(AssertionError, match="결측 셀 표현을 확인할 수 없다"):
            _assert_quality_answer(_QUALITY_BRIDGE, answer)
    with pytest.raises(AssertionError, match="중복 행 표현을 확인할 수 없다"):
        _assert_quality_answer(_QUALITY_BRIDGE, "결측 셀: 1개\n중복 행: 1개월")


def test_quality_answer_rejects_correction_and_conflict() -> None:
    with pytest.raises(AssertionError, match="결측 셀 표현을 확인할 수 없다"):
        _assert_quality_answer(_QUALITY_BRIDGE, "결측 셀: 1개\n결측 셀 1개가 아닐 뿐 아니라 2개\n중복 행: 1개")
    with pytest.raises(AssertionError, match="결측 셀 1개가 없다"):
        _assert_quality_answer(_QUALITY_BRIDGE, "결측 셀: 1개\n결측 셀: 2개\n중복 행: 1개")
    for answer in (
        "결측 셀: 1개\n중복 행: 1개\n위 수치는 틀렸습니다.",
        "결측 셀: 1개\n중복 행: 1개\n위 수치는 맞지 않습니다.",
        "위 수치는 맞지 않습니다.\n결측 셀: 1개\n중복 행: 1개",
    ):
        with pytest.raises(AssertionError, match="형식 밖 줄을 확인할 수 없다"):
            _assert_quality_answer(_QUALITY_BRIDGE, answer)


@pytest.mark.parametrize(
    "answer",
    [
        "결측 셀: 1\n중복 행: 1",
        "결측 셀: 1개\n완전 중복 행: 1개",
        "- 결측 셀: 1개\n- 완전 중복 행: 1개",
        "- 빈 셀: 1건\n- 중복 행: 1행",
        "결측 셀: 1 개\n중복 행: 1 건",
        "missing cells: 1\nduplicate rows: 1",
        "missing cell: 1 cell\nduplicate row: 1 row",
        "- missing cells: 1 cells\n- duplicate rows: 1 rows",
        "결측 셀: 1 cells\nduplicate rows: 1행",
        "  - 결측 셀: 1개  \r\n  - 완전 중복 행: 1개  ",
        "\n결측 셀: 1개\n\n중복 행: 1개\n",
    ],
)
def test_quality_answer_accepts_paired_values(answer: str) -> None:
    _assert_quality_answer(_QUALITY_BRIDGE, answer)


def _summary_bridge(amount: str) -> dict:
    return {
        "text": (
            "이 파일은 5행 × 3열 표 형태 데이터입니다.\n"
            f"* `매출`: 합 {amount} / 최소 0 / 최대 {amount}"
        )
    }


@pytest.mark.parametrize(
    ("amount", "said"),
    [
        ("0", "0.5"),
        ("240", "240.5"),
        ("240", "2400"),
        ("240", "1,240"),
        ("1,240", "240"),
        ("1,240", "1,240.5"),
    ],
)
def test_summary_answer_rejects_other_sum_token(amount: str, said: str) -> None:
    with pytest.raises(AssertionError):
        _assert_summary_answer(_summary_bridge(amount), f"5행 × 3열이고 `매출` 합계는 {said}입니다.")


@pytest.mark.parametrize(
    ("amount", "said"),
    [("0", "0"), ("240", "240"), ("1,240", "1,240"), ("1,240", "1240")],
)
def test_summary_answer_accepts_same_sum_token(amount: str, said: str) -> None:
    _assert_summary_answer(_summary_bridge(amount), f"5행 × 3열이고 `매출` 합계는 {said}입니다.")


@pytest.mark.parametrize(
    "answer",
    [
        "5.5행 × 3열이고 매출 합계는 240입니다.",
        "50행 × 3열이고 매출 합계는 240입니다.",
        "5행 × 3.0열이고 매출 합계는 240입니다.",
        "5행 × 3열이고 매출은 240입니다.",
        "5행 × 3열이고 매출 합계는 240.",
        "5행 × 3열이고 매출 합계는 240.원입니다.",
        "5.행 × 3열이고 매출 합계는 240입니다.",
    ],
)
def test_summary_answer_rejects_row_col_or_unlabeled_sum(answer: str) -> None:
    with pytest.raises(AssertionError):
        _assert_summary_answer(_summary_bridge("240"), answer)


@pytest.mark.skipif(
    os.environ.get("HERMES_EXCEL_E2E") != "1",
    reason="HERMES_EXCEL_E2E=1일 때만 실제 Hermes 종단 검사를 실행한다",
)
def test_hermes_end_to_end(tmp_path: Path) -> None:
    home = _hermes_home()
    hermes = _hermes_executable()
    sales = FIXTURES / "sales.xlsx"
    quality = FIXTURES / "quality.xlsx"
    missing = FIXTURES / f"hermes-e2e-missing-{uuid.uuid4().hex}.xlsx"
    assert not missing.exists()
    before = {sales: _sha256(sales), quality: _sha256(quality)}
    workdir = tmp_path / "hermes"
    workdir.mkdir()

    bridge, answer, terminal_exit = _hermes_case(hermes, home, workdir, "summary", task="summary", source=sales)
    _assert_success("summary", bridge, terminal_exit, answer)
    _assert_summary_answer(bridge, answer)

    bridge, answer, terminal_exit = _hermes_case(hermes, home, workdir, "schema", task="schema", source=quality)
    _assert_success("schema", bridge, terminal_exit, answer)
    _assert_schema(bridge, answer)

    bridge, answer, terminal_exit = _hermes_case(hermes, home, workdir, "quality", task="quality", source=quality)
    _assert_success("quality", bridge, terminal_exit, answer)
    _assert_quality_answer(bridge, answer)

    bridge, answer, terminal_exit = _hermes_case(hermes, home, workdir, "missing", task="summary", source=missing)
    _assert_missing_file(bridge, terminal_exit, answer, missing=missing, home=home)

    assert {sales: _sha256(sales), quality: _sha256(quality)} == before
    assert not missing.exists()
