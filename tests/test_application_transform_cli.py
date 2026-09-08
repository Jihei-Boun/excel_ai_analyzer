"""CLI dispatch tests for natural-language transform (Phase I2-D3C)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from core.application import cli
from core.application.contracts import CLI_EXIT_CODES
from core.application.transform_contracts import TRANSFORM_CONTRACT_VERSION, TRANSFORM_OPERATION
from tests.application_support import base_request, write_table

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "core" / "application"
PROMPT = "Copy visible group rows onto a new sheet named Extracted."
UNICODE_PROMPT = "한글 그룹 행을 Extracted 시트로 복사해줘."


def _save(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "H1"
    ws["C1"] = "H3"
    ws["A2"] = "r2"
    wb.save(path)
    wb.close()
    return path


def _policies() -> dict[str, str]:
    return {
        "group_scope": "include_descendants_and_subtotals",
        "output_mode": "copy_with_new_sheet",
        "unmerge_fill_policy": "fill_all",
    }


def _transform_payload(tmp_path: Path, source: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": "req-d3c-1",
        "source": {"path": str(source.resolve())},
        "user_prompt": PROMPT,
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "model": "qwen2.5:7b",
        "policies": _policies(),
    }
    payload.update(overrides)
    return payload


def _extract_plan() -> dict[str, Any]:
    return {
        "op": "extract_to_sheet",
        "sheet": 1,
        "rows": [1, 2],
        "columns": [1, 3],
        "header_rows": [1],
        "include_header_rows": True,
        "destination_sheet_base_name": "Extracted",
        "preserve_selection_order": True,
    }


def _run_main(monkeypatch: Any, stdin_text: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    code = cli.main([])
    return code, stdout.getvalue(), stderr.getvalue()


def _subprocess_cli(payload: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "core.application.cli"],
        input=payload,
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        check=False,
    )


def _envelope(status: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "request_id": "req-d3c-1",
        "status": status,
        "text": extra.pop("text", status),
        "artifacts": extra.pop("artifacts", []),
        "warnings": extra.pop("warnings", []),
        "applied_operations": extra.pop("applied_operations", []),
    }
    payload.update(extra)
    return payload


def test_operation_transform_calls_prompt_facade_with_unmodified_payload(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = _save(tmp_path / "book.xlsx")
    original = _transform_payload(tmp_path, source, user_prompt=UNICODE_PROMPT)
    seen: dict[str, Any] = {}

    def fake(payload: Any) -> dict[str, Any]:
        seen["payload"] = payload
        return _envelope("success", artifacts=[{"path": str(tmp_path / "out" / "req-d3c-1_transformed.xlsx")}])

    monkeypatch.setattr(cli, "transform_excel_from_prompt", fake)
    monkeypatch.setattr(
        cli,
        "analyze_excel",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("analyze_excel must not run")),
    )
    code, stdout, stderr = _run_main(monkeypatch, json.dumps(original, ensure_ascii=False))
    assert seen["payload"] == original
    assert seen["payload"]["user_prompt"] == UNICODE_PROMPT
    assert seen["payload"]["policies"] == _policies()
    assert seen["payload"]["source"]["path"] == str(source.resolve())
    assert seen["payload"]["output_directory"] == str((tmp_path / "out").resolve())
    assert code == CLI_EXIT_CODES["success"]
    parsed = json.loads(stdout)
    assert parsed["status"] == "success"
    assert stdout.strip().startswith("{") and stdout.strip().endswith("}")
    assert "Traceback" not in stdout
    assert UNICODE_PROMPT not in stderr


def test_analyze_payload_still_calls_analyze_excel(tmp_path: Path, monkeypatch: Any) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="이 파일 요약해줘")
    seen: dict[str, Any] = {}

    def fake_analyze(item: Any) -> dict[str, Any]:
        seen["payload"] = item
        return {"status": "success", "request_id": "req-1", "text": "분석 완료", "data": {"route": "route_single_prompt"}}

    monkeypatch.setattr(cli, "analyze_excel", fake_analyze)
    monkeypatch.setattr(
        cli,
        "transform_excel_from_prompt",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("transform facade must not run")),
    )
    code, stdout, _stderr = _run_main(monkeypatch, json.dumps(payload, ensure_ascii=False))
    assert seen["payload"] == payload
    assert code == CLI_EXIT_CODES["success"]
    assert json.loads(stdout)["text"] == "분석 완료"


def test_explicit_coordinate_payload_does_not_call_transform_excel(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = _save(tmp_path / "book.xlsx")
    explicit = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": "req-explicit",
        "source": {"path": str(source.resolve())},
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "output_policy": {"source_immutable": True, "create_copy": True},
        "transformations": [_extract_plan()],
    }
    import core.application.transform_headless as explicit_api
    import core.application.transform_prompt_headless as prompt_api

    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("transform_excel must not run")

    monkeypatch.setattr(explicit_api, "transform_excel", boom)
    monkeypatch.setattr(prompt_api, "transform_excel", boom)
    called = {"n": 0}
    original = prompt_api.transform_excel_from_prompt

    def wrapped(payload: Any) -> dict[str, Any]:
        called["n"] += 1
        return original(payload)

    monkeypatch.setattr(cli, "transform_excel_from_prompt", wrapped)
    code, stdout, _stderr = _run_main(monkeypatch, json.dumps(explicit))
    assert called["n"] == 1
    parsed = json.loads(stdout)
    assert parsed["status"] == "invalid_request"
    assert parsed["artifacts"] == []
    assert code == CLI_EXIT_CODES["invalid_request"]


def test_status_exit_codes(tmp_path: Path, monkeypatch: Any) -> None:
    source = _save(tmp_path / "book.xlsx")
    payload = _transform_payload(tmp_path, source)
    cases = [
        ("success", CLI_EXIT_CODES["success"], [{"path": str(tmp_path / "out" / "ok.xlsx")}]),
        ("cannot_plan", CLI_EXIT_CODES["cannot_plan"], []),
        ("model_unavailable", CLI_EXIT_CODES["model_unavailable"], []),
        ("timeout", CLI_EXIT_CODES["timeout"], []),
        ("invalid_request", CLI_EXIT_CODES["invalid_request"], []),
        ("validation_failed", CLI_EXIT_CODES["validation_failed"], []),
        ("execution_failed", CLI_EXIT_CODES["execution_failed"], []),
        ("cancelled", CLI_EXIT_CODES["cancelled"], []),
    ]
    monkeypatch.setattr(
        cli,
        "analyze_excel",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("analyze_excel must not run")),
    )
    for status, expected, artifacts in cases:
        extra: dict[str, Any] = {"artifacts": artifacts}
        if status != "success":
            extra["error"] = {
                "code": status,
                "message": status,
                "stage": "planning",
                "retryable": False,
            }

        def fake(_payload: Any, *, _status: str = status, _extra: dict[str, Any] = extra) -> dict[str, Any]:
            return _envelope(_status, **_extra)

        monkeypatch.setattr(cli, "transform_excel_from_prompt", fake)
        code, stdout, stderr = _run_main(monkeypatch, json.dumps(payload))
        parsed = json.loads(stdout)
        assert parsed["status"] == status
        assert code == expected
        if status != "success":
            assert parsed["artifacts"] == []
        assert "Traceback" not in stdout
        assert PROMPT not in stderr
        stripped = stdout.strip()
        assert stripped.startswith("{") and stripped.endswith("}")


def test_unknown_status_is_not_success(tmp_path: Path, monkeypatch: Any) -> None:
    source = _save(tmp_path / "book.xlsx")
    monkeypatch.setattr(cli, "transform_excel_from_prompt", lambda *_a, **_k: _envelope("mystery"))
    code, stdout, _stderr = _run_main(monkeypatch, json.dumps(_transform_payload(tmp_path, source)))
    assert json.loads(stdout)["status"] == "mystery"
    assert code == CLI_EXIT_CODES["execution_failed"]
    assert code != CLI_EXIT_CODES["success"]


def test_cannot_plan_subprocess_without_llm(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    payload = _transform_payload(tmp_path, source)
    payload.pop("policies")
    result = _subprocess_cli(json.dumps(payload, ensure_ascii=False))
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "invalid_request"
    assert parsed["artifacts"] == []
    assert result.returncode == CLI_EXIT_CODES["invalid_request"]
    assert result.stdout.strip().startswith("{")
    assert "Traceback" not in result.stdout
    json.loads(result.stdout)


def test_explicit_coordinate_subprocess_is_natural_contract_error(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    explicit = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": "req-explicit",
        "source": {"path": str(source.resolve())},
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "output_policy": {"source_immutable": True, "create_copy": True},
        "transformations": [_extract_plan()],
    }
    result = _subprocess_cli(json.dumps(explicit))
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "invalid_request"
    assert parsed["artifacts"] == []
    assert result.returncode == CLI_EXIT_CODES["invalid_request"]
    assert parsed["error"]["code"] in {"unknown_field", "missing_user_prompt", "invalid_policies"}
    assert list((tmp_path / "out").glob("*.xlsx")) == []


def test_unicode_prompt_roundtrip_stdout(tmp_path: Path, monkeypatch: Any) -> None:
    source = _save(tmp_path / "book.xlsx")
    payload = _transform_payload(tmp_path, source, user_prompt=UNICODE_PROMPT)

    def fake(item: Any) -> dict[str, Any]:
        assert item["user_prompt"] == UNICODE_PROMPT
        return _envelope("success", text="변환 완료: Extracted")

    monkeypatch.setattr(cli, "transform_excel_from_prompt", fake)
    code, stdout, _stderr = _run_main(monkeypatch, json.dumps(payload, ensure_ascii=False))
    assert code == 0
    assert "변환 완료" in stdout
    assert "Extracted" in stdout


def test_diagnostic_print_does_not_break_stdout_json(tmp_path: Path, monkeypatch: Any) -> None:
    source = _save(tmp_path / "book.xlsx")

    def noisy(payload: Any) -> dict[str, Any]:
        print("PROGRESS should not be on stdout")
        return _envelope("success")

    monkeypatch.setattr(cli, "transform_excel_from_prompt", noisy)
    code, stdout, stderr = _run_main(monkeypatch, json.dumps(_transform_payload(tmp_path, source)))
    json.loads(stdout)
    assert "PROGRESS" not in stdout
    assert "PROGRESS" in stderr
    assert code == 0


def test_cli_has_no_semantic_or_demo_hardcoding() -> None:
    text = (APP_DIR / "cli.py").read_text(encoding="utf-8")
    needles = ("당해예산", "내부인건비", "재료비", "예실대비표", "04_분산")
    for needle in needles:
        assert needle not in text
    assert "import streamlit" not in text
    assert "from streamlit" not in text
    assert "core.routing" not in text
    assert "core.integrate" not in text
    assert "run_integration_pipeline" not in text
    assert "value_match" not in text
    assert "if \"병합\"" not in text
    assert "transform_excel(" not in text.replace("transform_excel_from_prompt", "")
    assert "observe_workbook" not in text
    assert "plan_transformations" not in text


def test_explicit_transform_excel_api_still_works(tmp_path: Path) -> None:
    from core.application import transform_excel

    source = _save(tmp_path / "book.xlsx")
    response = transform_excel(
        {
            "contract_version": TRANSFORM_CONTRACT_VERSION,
            "operation": TRANSFORM_OPERATION,
            "request_id": "req-explicit-smoke",
            "source": {"path": str(source.resolve())},
            "output_directory": str((tmp_path / "out").resolve()),
            "timeout_seconds": 90,
            "output_policy": {"source_immutable": True, "create_copy": True},
            "transformations": [_extract_plan()],
        }
    )
    assert response["status"] == "success"
    assert response["artifacts"]


def test_streamlit_not_imported_by_cli() -> None:
    retained = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "streamlit" or name.startswith("streamlit.")
    }
    for name in retained:
        sys.modules.pop(name, None)
    import importlib

    importlib.reload(cli)
    assert "streamlit" not in sys.modules
