"""CLI stdin/stdout contract tests."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.application.contracts import CLI_EXIT_CODES
from tests.application_support import base_request, write_table

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(payload: str, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "core.application.cli"],
        input=payload,
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        check=False,
    )


def test_cli_stdin_json_to_stdout_json(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="이 파일 요약해줘")
    result = _run_cli(json.dumps(payload))
    assert result.returncode == CLI_EXIT_CODES["success"]
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["request_id"] == "req-1"
    assert result.stdout.strip().startswith("{")
    json.loads(result.stdout)
    assert "분석" in result.stderr or "INFO" in result.stderr or result.stderr == "" or "core.application" in result.stderr


def test_cli_malformed_json() -> None:
    result = _run_cli("this is not json")
    assert result.returncode == CLI_EXIT_CODES["invalid_request"]
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "invalid_request"
    assert parsed["error"]["code"] == "malformed_json"
    assert parsed["error"]["retryable"] is False


def test_cli_stdout_has_no_diagnostic_text(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="이 파일 요약해줘")
    result = _run_cli(json.dumps(payload))
    stdout = result.stdout
    json.loads(stdout)
    assert stdout.count("{") >= 1
    assert "Traceback" not in stdout
    assert "INFO" not in stdout
    assert "WARNING" not in stdout
    stripped = stdout.strip()
    assert stripped.startswith("{") and stripped.endswith("}")


def test_cli_invalid_request_exit_code(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    payload["analysis_mode"] = "nope"
    result = _run_cli(json.dumps(payload))
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "invalid_request"
    assert result.returncode == CLI_EXIT_CODES["invalid_request"]
    assert parsed["error"]["message"]
    if result.stderr:
        json.loads(result.stdout)  # stdout still exclusive JSON
        assert result.stdout.strip() != result.stderr.strip()


def test_cli_closed_port_integrate_exit_code(tmp_path: Path) -> None:
    left = write_table(tmp_path / "customers.xlsx", {"고객ID": ["C1"], "매출": [10]})
    right = write_table(tmp_path / "grades.xlsx", {"고객ID": ["C1"], "등급": ["A"]})
    payload = base_request(
        tmp_path,
        paths=[left, right],
        prompt="선택한 파일들을 통합해줘",
        timeout_seconds=5,
        extra={"model": {"base_url": "http://127.0.0.1:9", "name": "qwen2.5:7b"}},
    )
    result = _run_cli(json.dumps(payload))
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "model_unavailable"
    assert parsed["error"]["code"] == "model_unavailable"
    assert parsed["error"]["exception_type"] == "ConnectionError"
    assert parsed["artifacts"] == []
    assert result.returncode == CLI_EXIT_CODES["model_unavailable"]
    assert result.returncode != 0


def test_cli_generic_integrate_exception_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("integrate stage failed")

    monkeypatch.setattr("core.routing.route_multi.try_integrate_pipeline", boom)
    left = write_table(tmp_path / "left.xlsx", {"항목": ["A"], "값": [1]})
    right = write_table(tmp_path / "right.xlsx", {"항목": ["B"], "값": [2]})
    payload = base_request(tmp_path, paths=[left, right], prompt="선택한 파일들을 통합해줘")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    from core.application.cli import main

    code = main()
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "execution_failed"
    assert parsed["error"]["exception_type"] == "RuntimeError"
    assert parsed["artifacts"] == []
    assert code == CLI_EXIT_CODES["execution_failed"]
