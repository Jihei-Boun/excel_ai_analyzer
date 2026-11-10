"""DEV-4 read_range의 CLI·래퍼 연결. 합성 xlsx만 쓰고 Linux에서만 실행한다."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from core.application import cli
from core.application.contracts import CLI_EXIT_CODES, ContractError
from core.application.headless import parse_analyze_request
from core.application.wrapper_config import load_operator_config
from core.application.wrapper_run import ProcessLaunch, run_job
from core.io.cell_reader import read_range

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


# --- 준비 ---


def _set_cached_values(path: Path, cached: dict[str, str]) -> None:
    """openpyxl은 수식의 계산값을 저장하지 않는다. 시트 XML의 <v>에 직접 넣는다."""
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


def _book(path: Path) -> Path:
    """A3:B3은 빈 행, B7:B8은 병합, D1·D2는 저장값 있는 수식, D3은 저장값 없는 수식."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "항목"
    ws["B1"] = "값"
    ws["A2"] = "사과"
    ws["B2"] = 10
    ws["A4"] = "배"
    ws["B4"] = 2.5
    ws["B7"] = 7
    ws.merge_cells("B7:B8")
    ws["D1"] = "=B2+B4"
    ws["D2"] = "=B2-B2"
    ws["D3"] = "=B2*2"
    wb.create_sheet("Other")["A1"] = "second"
    wb.save(path)
    _set_cached_values(path, {"D1": "12.5", "D2": "0"})
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_body(tmp_path: Path, **overrides: object) -> dict:
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
    body.update(overrides)
    return body


def _config(tmp_path: Path, **overrides: object):
    (tmp_path / "inputs").mkdir(exist_ok=True)
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(_config_body(tmp_path, **overrides)), encoding="utf-8")
    return load_operator_config(path)


def _read_request(path: Path, address: str, *, request_id: str = "read-1", sheet: object = "Data", **extra: object) -> dict:
    payload = {
        "contract_version": "1.0",
        "request_id": request_id,
        "operation": "read_range",
        "inputs": [{"path": str(path), "sheet": sheet, "display_name": path.name}],
        "read": {"address": address},
    }
    payload.update(extra)
    return payload


def _cli_request(path: Path, address: str, *, sheet: object = "Data", request_id: str = "cli-1") -> dict:
    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "operation": "read_range",
        "inputs": [{"path": str(path), "sheet": sheet, "display_name": path.name}],
        "read": {"address": address},
    }


def _run_cli_process(payload: dict) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "core.application.cli"],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        check=False,
        timeout=120,
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
            raise AssertionError(f"{name} must not be called for read_range")

        return _called

    monkeypatch.setattr("core.application.headless.route_single_prompt", _record("route_single_prompt"))
    monkeypatch.setattr("core.application.headless.route_multi_prompt", _record("route_multi_prompt"))
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
        assert "source" not in item and "copy" not in item


def _by_a1(cells: list[dict]) -> dict[str, dict]:
    return {cell["a1"]: cell for cell in cells}


# --- CLI 프로세스 ---


def test_cli_process_returns_cell_reader_json_unchanged(tmp_path: Path) -> None:
    book = _book(tmp_path / "data" / "book.xlsx")
    before = _sha256(book)
    files_before = sorted(path.name for path in book.parent.iterdir())

    result = _run_cli_process(_cli_request(book, "A1:D8"))

    assert result.returncode == CLI_EXIT_CODES["success"] == 0, result.stderr
    parsed = json.loads(result.stdout)
    direct = read_range(book, sheet="Data", address="A1:D8", display_name="book.xlsx", request_id="cli-1")
    assert parsed == json.loads(json.dumps(direct))
    assert parsed["status"] == "success"
    assert parsed["operation"] == "read_range"
    assert parsed["recalculated"] is False
    assert parsed["values_from"] == "stored_workbook"
    assert parsed["source"] == {"display_name": "book.xlsx", "sheet_name": "Data", "sheet_index": 0}
    assert parsed["returned"] == {"address": "A1:D8", "cell_count": 32}
    assert len(parsed["cells"]) == 32
    for key in ("data", "preview_records", "artifacts", "text"):
        assert key not in parsed

    cells = _by_a1(parsed["cells"])
    assert [cell["a1"] for cell in parsed["cells"][:5]] == ["A1", "B1", "C1", "D1", "A2"]
    assert (cells["A2"]["value"], cells["A2"]["value_type"]) == ("사과", "string")
    assert (cells["B2"]["value"], cells["B2"]["row"], cells["B2"]["column_index"]) == (10, 2, 2)
    for blank in ("A3", "B3", "C5", "A8"):
        assert cells[blank]["value"] is None
        assert cells[blank]["value_type"] == "empty"
    assert cells["B4"]["value"] == 2.5

    assert cells["D1"]["formula"] == "=B2+B4"
    assert cells["D1"]["value"] is None
    assert cells["D1"]["cached_value"] == 12.5
    assert cells["D1"]["cached_value_state"] == "stored"
    assert cells["D2"]["formula"] == "=B2-B2"
    assert cells["D2"]["cached_value"] == 0
    assert cells["D2"]["cached_value"] is not None
    assert cells["D2"]["cached_value_state"] == "stored"
    assert cells["D3"]["formula"] == "=B2*2"
    assert cells["D3"]["cached_value"] is None
    assert cells["D3"]["cached_value_state"] == "absent"

    assert cells["B7"]["value"] == 7
    assert cells["B7"]["merge"] == {"role": "anchor", "anchor": "B7", "range": "B7:B8"}
    assert cells["B8"]["value"] is None
    assert cells["B8"]["value_type"] == "empty"
    assert cells["B8"]["merge"] == {"role": "covered", "anchor": "B7", "range": "B7:B8"}

    assert _sha256(book) == before
    assert sorted(path.name for path in book.parent.iterdir()) == files_before
    assert result.stdout.strip().startswith("{") and result.stdout.strip().endswith("}")


def test_cli_process_cell_error_uses_invalid_request_exit(tmp_path: Path) -> None:
    book = _book(tmp_path / "data" / "book.xlsx")

    result = _run_cli_process(_cli_request(book, "D20:A3"))

    assert result.returncode == CLI_EXIT_CODES["invalid_request"] == 2
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "invalid_request"
    assert parsed["error"]["code"] == "reversed_range"
    assert parsed["recalculated"] is False
    assert "cells" not in parsed


# --- CLI 분기 ---


@pytest.mark.parametrize(
    ("address", "sheet", "name", "code"),
    [
        ("A0", "Data", "book.xlsx", "invalid_address"),
        ("Sheet1!B7", "Data", "book.xlsx", "invalid_address"),
        ("B3:A5", "Data", "book.xlsx", "reversed_range"),
        ("A1", "없음", "book.xlsx", "invalid_sheet"),
        ("A1", 5, "book.xlsx", "invalid_sheet"),
        ("A1", 0, "book.csv", "unsupported_format"),
        ("A1:A2001", "Data", "book.xlsx", "range_too_large"),
    ],
)
def test_cli_cell_reader_errors_pass_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    address: str,
    sheet: object,
    name: str,
    code: str,
) -> None:
    calls = _router_spy(monkeypatch)
    book = _book(tmp_path / "data" / "book.xlsx")
    target = book if name == "book.xlsx" else book.with_name(name)
    if target != book:
        target.write_bytes(book.read_bytes())

    parsed, exit_code = _run_cli_inline(_cli_request(target, address, sheet=sheet), monkeypatch, capsys)

    direct = read_range(target, sheet=sheet, address=address, display_name=name, request_id="cli-1")
    assert parsed == direct
    assert parsed["error"]["code"] == code
    assert exit_code == CLI_EXIT_CODES[parsed["status"]] == 2
    assert calls == []
    assert str(tmp_path) not in json.dumps(parsed, ensure_ascii=False)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(user_prompt="B7 읽어줘"), "forbidden_request_field"),
        (lambda p: p.update(model={"base_url": "http://127.0.0.1:9", "name": "x"}), "forbidden_request_field"),
        (lambda p: p.update(output_directory="/tmp"), "forbidden_request_field"),
        (lambda p: p["inputs"].append(dict(p["inputs"][0])), "invalid_input_count"),
        (lambda p: p.update(inputs=[]), "missing_inputs"),
        (lambda p: p.update(read={"address": "A1", "extra": 1}), "invalid_read"),
        (lambda p: p.pop("read"), "invalid_read"),
        (lambda p: p.update(contract_version="2.0"), "unsupported_contract_version"),
    ],
)
def test_cli_read_request_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutate,
    code: str,
) -> None:
    calls = _router_spy(monkeypatch)

    def _no_analyze(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("analyze_excel must not be called for read_range")

    monkeypatch.setattr(cli, "analyze_excel", _no_analyze)
    payload = _cli_request(_book(tmp_path / "data" / "book.xlsx"), "A1")
    mutate(payload)

    parsed, exit_code = _run_cli_inline(payload, monkeypatch, capsys)

    assert parsed["status"] == "invalid_request"
    assert parsed["operation"] == "read_range"
    assert parsed["recalculated"] is False
    assert parsed["error"]["code"] == code
    assert parsed["error"]["retryable"] is False
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert calls == []
    assert str(tmp_path) not in json.dumps(parsed, ensure_ascii=False)


def test_cli_read_range_never_reaches_analyze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _router_spy(monkeypatch)

    def _no_analyze(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("analyze_excel must not be called for read_range")

    monkeypatch.setattr(cli, "analyze_excel", _no_analyze)

    parsed, exit_code = _run_cli_inline(_cli_request(_book(tmp_path / "data" / "book.xlsx"), "B2"), monkeypatch, capsys)

    assert exit_code == 0
    assert parsed["cells"][0]["value"] == 10
    assert calls == []


def test_cli_without_operation_stays_analyze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[dict] = []

    def _record(payload: dict) -> dict:
        seen.append(payload)
        return {"contract_version": "1.0", "request_id": payload["request_id"], "status": "success"}

    monkeypatch.setattr(cli, "analyze_excel", _record)
    payload = {
        "contract_version": "1.0",
        "request_id": "analyze-1",
        "inputs": [{"path": str(tmp_path / "a.xlsx"), "sheet": 0}],
        "user_prompt": "요약해줘",
    }

    parsed, exit_code = _run_cli_inline(payload, monkeypatch, capsys)

    assert exit_code == 0
    assert parsed["status"] == "success"
    assert seen == [payload]


def test_cli_unknown_operation_is_rejected_by_analyze_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _router_spy(monkeypatch)
    payload = _cli_request(_book(tmp_path / "data" / "book.xlsx"), "A1")
    payload["operation"] = "write_range"

    parsed, exit_code = _run_cli_inline(payload, monkeypatch, capsys)

    assert parsed["status"] == "invalid_request"
    assert parsed["error"]["code"] == "invalid_operation"
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert calls == []


def test_analyze_parser_still_rejects_read_range(tmp_path: Path) -> None:
    payload = {
        "contract_version": "1.0",
        "request_id": "p-1",
        "operation": "read_range",
        "inputs": [{"path": str(tmp_path / "a.xlsx")}],
        "user_prompt": "B7",
        "analysis_mode": "single",
        "output_directory": str(tmp_path / "out"),
    }
    with pytest.raises(ContractError) as caught:
        parse_analyze_request(payload)
    assert caught.value.code == "invalid_operation"


# --- 래퍼 프로세스 ---


def test_wrapper_process_reads_single_cell_through_real_cli(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    before = _sha256(book)
    config = _config(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "core.application.wrapper", "--config", str(config.source_path)],
        input=json.dumps(_read_request(book, "$b$2", request_id="wrap-1"), ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        check=False,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "success"
    assert response["exit_code"] == 0
    assert response["child_returncode"] == 0
    assert response["operation"] == "read_range"
    assert response["recalculated"] is False
    assert response["values_from"] == "stored_workbook"
    assert response["requested"] == {"address": "B2"}
    assert response["returned"] == {"address": "B2", "cell_count": 1}
    assert response["source"] == {"display_name": "book.xlsx", "sheet_name": "Data", "sheet_index": 0}
    cell = response["cells"][0]
    assert (cell["a1"], cell["row"], cell["column_index"], cell["column_letter"]) == ("B2", 2, 2, "B")
    assert (cell["value"], cell["value_type"]) == (10, "number")
    assert response["artifacts"] == []
    assert "data" not in response
    assert response["cleanup"]["state"] == "complete"
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)

    integrity = _evidence(config, response, "input_integrity.json")
    copy_path = integrity["items"][0]["copy"]
    assert integrity["items"][0]["source"] == str(book.resolve())
    assert Path(copy_path).parent == config.workspace_root / response["execution_id"] / "inputs"
    assert integrity["authoritative"] is True
    _assert_no_paths(response, tmp_path, book, book.resolve(), copy_path, config.workspace_root)

    engine_request = _evidence(config, response, "engine_request.json")
    assert engine_request == {
        "contract_version": "1.0",
        "request_id": "wrap-1",
        "operation": "read_range",
        "inputs": [{"path": copy_path, "sheet": "Data", "display_name": "book.xlsx"}],
        "read": {"address": "$b$2"},
    }
    engine_response = _evidence(config, response, "engine_response.json")
    assert engine_response["cells"] == response["cells"]
    meta = _evidence(config, response, "execution_meta.json")
    assert meta["operation"] == "read_range"
    assert meta["model_base_url"] is None and meta["model_name"] is None
    assert meta["input_integrity"]["items"][0]["source"] == str(book.resolve())
    assert _evidence(config, response, "wrapper_response.json") == response
    assert not (config.publish_root / response["execution_id"]).exists()
    assert _sha256(book) == before


@pytest.mark.parametrize("form", ["full", "tail"])
def test_wrapper_process_blocks_path_in_request_id(tmp_path: Path, form: str) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    source = str(book.resolve())
    request_id = source[-128:] if form == "full" else source[-40:]
    assert "/" in request_id and len(request_id) <= 128 and request_id in source
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "core.application.wrapper", "--config", str(config.source_path)],
        input=json.dumps(_read_request(book, "B2", request_id=request_id), ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        check=False,
        timeout=180,
    )

    assert result.returncode == CLI_EXIT_CODES["execution_failed"], result.stderr
    assert request_id not in result.stdout
    response = json.loads(result.stdout)
    assert response["status"] == "execution_failed"
    assert response["exit_code"] == response["wrapper_exit_code"] == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["code"] == "path_exposure_blocked"
    assert response["error"]["stage"] == "publish"
    assert response["request_id"] == ""
    assert response["operation"] == "read_range"
    assert response["child_returncode"] == 0
    for key in ("cells", "source", "requested", "returned", "values_from", "data"):
        assert key not in response
    assert response["cleanup"]["state"] == "complete"
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)

    integrity = _evidence(config, response, "input_integrity.json")
    copy_path = integrity["items"][0]["copy"]
    assert integrity["items"][0]["source"] == source
    assert Path(copy_path).parent == config.workspace_root / response["execution_id"] / "inputs"
    for forbidden in (source, str(book), copy_path, str(config.workspace_root)):
        assert forbidden not in result.stdout
    _assert_no_paths(response, tmp_path, book, copy_path)
    assert _evidence(config, response, "engine_request.json")["request_id"] == request_id
    assert _evidence(config, response, "wrapper_response.json") == response


def test_wrapper_blocked_response_strips_carried_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    leaked = str(config.workspace_root / "leak")
    monkeypatch.setattr(
        "core.application.wrapper_run.source_identity",
        lambda _cwd: {"git_head": "abc", "diff_sha256": "def", "source": leaked},
    )

    response, code = run_job(config, _read_request(book, "B2", request_id="strip-1"))

    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["code"] == "path_exposure_blocked"
    assert response["request_id"] == "strip-1"
    assert response["source_identity"] == {"git_head": "abc", "diff_sha256": "def", "source": ""}
    assert "cells" not in response
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    copy_path = _evidence(config, response, "input_integrity.json")["items"][0]["copy"]
    _assert_no_paths(response, tmp_path, book, copy_path, leaked, config.workspace_root)
    assert _evidence(config, response, "identity.json")["source"] == leaked
    assert _evidence(config, response, "wrapper_response.json") == response


def test_wrapper_blocked_response_falls_back_when_strip_leaves_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    leaked = str(config.workspace_root / "leak")
    monkeypatch.setattr(
        "core.application.wrapper_run.source_identity",
        lambda _cwd: {"git_head": "abc", "diff_sha256": "def", "source": leaked},
    )
    monkeypatch.setattr("core.application.wrapper_run._strip_paths", lambda value, _hidden: value)

    response, code = run_job(config, _read_request(book, "B2", request_id="strip-2"))

    assert code == response["exit_code"] == CLI_EXIT_CODES["execution_failed"]
    assert response["status"] == "execution_failed"
    assert response["error"]["code"] == "path_exposure_blocked"
    assert response["request_id"] == ""
    for key in ("source_identity", "input_integrity", "timing", "cells"):
        assert key not in response
    assert response["cleanup"] == {"state": "not_applicable"}
    _assert_no_paths(response, tmp_path, book, leaked, config.workspace_root)
    assert _evidence(config, response, "wrapper_response.json") == response


def test_wrapper_blocked_response_withholds_when_rebuild_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.application import wrapper_run

    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    leaked = str(config.workspace_root / "leak")
    monkeypatch.setattr(
        "core.application.wrapper_run.source_identity",
        lambda _cwd: {"git_head": "abc", "diff_sha256": "def", "source": leaked},
    )
    monkeypatch.setattr("core.application.wrapper_run._strip_paths", lambda value, _hidden: value)
    monkeypatch.setattr(
        "core.application.wrapper_run._blocked_read",
        lambda _public, **_kwargs: {"request_id": "", "status": "execution_failed", "leak": leaked},
    )

    response, code = run_job(config, _read_request(book, "B2", request_id="strip-3"))

    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response == wrapper_run._withheld_read(code)
    _assert_no_paths(response, tmp_path, book, leaked, config.workspace_root)


# --- 래퍼 run_job, 자식은 실제 CLI ---


def test_wrapper_reads_range_with_formulas_and_merge(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _read_request(book, "A1:D8"))

    assert code == 0, response
    assert response["status"] == "success"
    direct = read_range(book, sheet="Data", address="A1:D8", display_name="book.xlsx", request_id="read-1")
    assert response["cells"] == json.loads(json.dumps(direct["cells"]))
    assert response["returned"]["cell_count"] == 32
    cells = _by_a1(response["cells"])
    assert cells["D2"]["cached_value"] == 0 and cells["D2"]["cached_value_state"] == "stored"
    assert cells["D3"]["cached_value_state"] == "absent"
    assert cells["B8"]["merge"]["role"] == "covered"
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    copy_path = _evidence(config, response, "input_integrity.json")["items"][0]["copy"]
    _assert_no_paths(response, tmp_path, copy_path)


@pytest.mark.parametrize(
    ("address", "sheet", "name", "code"),
    [
        ("A01", "Data", "book.xlsx", "invalid_address"),
        ("D20:A3", "Data", "book.xlsx", "reversed_range"),
        ("A1", "없음", "book.xlsx", "invalid_sheet"),
        ("A1", 0, "book.csv", "unsupported_format"),
        ("A1:B1001", "Data", "book.xlsx", "range_too_large"),
    ],
)
def test_wrapper_cell_errors_keep_integrity_and_hide_paths(
    tmp_path: Path,
    address: str,
    sheet: object,
    name: str,
    code: str,
) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    target = book if name == "book.xlsx" else book.with_name(name)
    if target != book:
        target.write_bytes(book.read_bytes())
    config = _config(tmp_path)

    response, exit_code = run_job(config, _read_request(target, address, sheet=sheet))

    assert response["status"] == "invalid_request"
    assert exit_code == response["exit_code"] == CLI_EXIT_CODES["invalid_request"]
    assert response["child_returncode"] == 2
    assert response["error"]["code"] == code
    assert response["operation"] == "read_range"
    assert response["recalculated"] is False
    for key in ("cells", "source", "requested", "returned", "values_from", "data"):
        assert key not in response
    assert response["artifacts"] == []
    _assert_public_integrity(response, state="unchanged", authoritative=True, count=1)
    evidence = _evidence(config, response, "input_integrity.json")
    assert evidence["items"][0]["source"] == str(target.resolve())
    assert evidence["items"][0]["copy"]
    _assert_no_paths(response, tmp_path, target, evidence["items"][0]["copy"])


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
        "'operation':'read_range','recalculated':False}))\n"
    )

    response, code = run_job(
        config,
        _read_request(book, "A1"),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["code"] == "input_changed"
    assert "cells" not in response
    _assert_public_integrity(response, state="changed", authoritative=False, count=1)
    assert response["input_integrity"]["items"][0]["copy_state"] == "changed"
    evidence = _evidence(config, response, "input_integrity.json")
    assert evidence["authoritative"] is False
    _assert_no_paths(response, tmp_path, evidence["items"][0]["copy"])
    assert book.read_bytes() == original


def test_wrapper_child_never_calls_routers(tmp_path: Path) -> None:
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
        _read_request(book, "A1:B4"),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    assert code == 0, response
    assert response["status"] == "success"
    assert response["returned"]["cell_count"] == 8
    assert not marker.exists()


def test_wrapper_rejects_malformed_read_engine_output(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    script = (
        "import json,sys\n"
        "req=json.load(sys.stdin)\n"
        "print(json.dumps({'contract_version':'1.0','request_id':req['request_id'],'status':'success',"
        "'operation':'read_range','recalculated':False,'values_from':'stored_workbook',"
        "'source':{'display_name':'book.xlsx','sheet_name':'Data','sheet_index':0},"
        "'requested':{'address':'A1:A2'},'returned':{'address':'A1:A2','cell_count':2},"
        "'cells':[{'a1':'A1','row':1,'column_index':1,'column_letter':'A','value':1,'value_type':'number',"
        "'formula':None,'cached_value':None,'cached_value_state':'not_applicable','merge':None}]}))\n"
    )

    response, code = run_job(
        config,
        _read_request(book, "A1:A2"),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["code"] == "invalid_field_type"
    assert "cells" not in response


def test_wrapper_status_must_match_child_exit(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    script = (
        "import json,sys\n"
        "req=json.load(sys.stdin)\n"
        "print(json.dumps({'contract_version':'1.0','request_id':req['request_id'],'status':'invalid_request',"
        "'operation':'read_range','recalculated':False,'error':{'code':'reversed_range',"
        "'message':'범위의 시작이 끝보다 뒤다.','stage':'request','retryable':False}}))\n"
        "raise SystemExit(0)\n"
    )

    response, code = run_job(
        config,
        _read_request(book, "D20:A3"),
        launch=ProcessLaunch(argv=(sys.executable, "-c", script)),
    )

    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["status"] == "execution_failed"
    assert response["error"]["code"] == "status_exit_mismatch"


# --- 래퍼 요청 거절 ---


_NEVER = ProcessLaunch(argv=(sys.executable, "-c", "raise SystemExit(99)"))


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(operation="write_range"), "invalid_operation"),
        (lambda p: p.update(operation=None), "invalid_operation"),
        (lambda p: p.update(user_prompt="B7 읽어줘"), "forbidden_request_field"),
        (lambda p: p.update(profile_name="generic"), "forbidden_request_field"),
        (lambda p: p.update(analysis_mode="single"), "forbidden_request_field"),
        (lambda p: p.update(model={"base_url": "http://127.0.0.1:9", "name": "x"}), "forbidden_request_field"),
        (lambda p: p.update(output_directory="/tmp"), "forbidden_request_field"),
        (lambda p: p.update(command=["sh"]), "forbidden_request_field"),
        (lambda p: p["inputs"].append(dict(p["inputs"][0])), "invalid_input_count"),
        (lambda p: p.update(inputs=[]), "missing_inputs"),
        (lambda p: p.update(read={"address": "A1", "sheet": "x"}), "invalid_read"),
        (lambda p: p.pop("read"), "invalid_read"),
    ],
)
def test_wrapper_rejects_bad_read_requests_before_launch(tmp_path: Path, mutate, code: str) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    payload = _read_request(book, "A1")
    mutate(payload)

    response, exit_code = run_job(config, payload, launch=_NEVER)

    assert response["status"] == "invalid_request"
    assert exit_code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == code
    assert response["execution_id"] is None
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
        response, exit_code = run_job(config, _read_request(path, "A1", request_id=f"deny-{index}"), launch=_NEVER)
        assert response["status"] == "invalid_request"
        assert exit_code == CLI_EXIT_CODES["invalid_request"]
        assert response["error"]["code"] == code
        assert response["operation"] == "read_range"
        _assert_public_integrity(response, state="unconfirmed", authoritative=False, count=0)
        _assert_no_paths(response, tmp_path, path, secret, outside)
        assert _evidence(config, response, "input_integrity.json")["authoritative"] is False


# --- analyze 호환 ---


def _analyze_probe() -> ProcessLaunch:
    script = (
        "import json,sys\n"
        "req=json.load(sys.stdin)\n"
        "body={'contract_version':'1.0','request_id':req['request_id'],'status':'success',"
        "'text':req['operation']+'|'+req['user_prompt'],"
        "'data':{'shape':[0,0],'columns':[],'preview_records':[]},'artifacts':[],'warnings':[],"
        "'safety':{'validation_status':'passed','unsafe_output_blocked':False},"
        "'timing':{'elapsed_ms':1}}\n"
        "print(json.dumps(body, ensure_ascii=False))\n"
    )
    return ProcessLaunch(argv=(sys.executable, "-c", script))


def _analyze_request(path: Path, **extra: object) -> dict:
    payload = {
        "contract_version": "1.0",
        "request_id": "an-1",
        "user_prompt": "이 파일 요약해줘",
        "inputs": [{"path": str(path), "sheet": 0, "display_name": path.name}],
    }
    payload.update(extra)
    return payload


@pytest.mark.parametrize("extra", [{}, {"operation": "analyze"}])
def test_wrapper_analyze_path_is_unchanged(tmp_path: Path, extra: dict) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _analyze_request(book, **extra), launch=_analyze_probe())

    assert code == 0, response
    assert response["status"] == "success"
    assert response["text"] == "analyze|이 파일 요약해줘"
    assert "operation" not in response
    assert set(response["data"]) == {"shape", "columns", "preview_records"}
    item = response["input_integrity"]["items"][0]
    assert item["source"] == str(book.resolve())
    assert item["copy"]
    engine_request = _evidence(config, response, "engine_request.json")
    assert engine_request["operation"] == "analyze"
    assert engine_request["user_prompt"] == "이 파일 요약해줘"
    assert engine_request["model"] == {"base_url": config.model_base_url, "name": config.model_name}
    assert "read" not in engine_request
    meta = _evidence(config, response, "execution_meta.json")
    assert meta["model_name"] == config.model_name
    assert meta["profile_name"] == "generic"


def test_wrapper_analyze_rejects_read_field(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)

    response, code = run_job(config, _analyze_request(book, read={"address": "A1"}), launch=_NEVER)

    assert code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == "forbidden_request_field"


def test_wrapper_analyze_still_requires_prompt(tmp_path: Path) -> None:
    book = _book(tmp_path / "inputs" / "book.xlsx")
    config = _config(tmp_path)
    payload = _analyze_request(book)
    payload.pop("user_prompt")

    response, code = run_job(config, payload, launch=_NEVER)

    assert code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == "missing_user_prompt"
