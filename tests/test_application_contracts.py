"""Contract validation tests for the headless application boundary."""

from __future__ import annotations

from pathlib import Path

from core.application import analyze_excel
from tests.application_support import base_request, write_table


def _assert_invalid(response: dict, *, code: str | None = None) -> None:
    assert response["status"] == "invalid_request"
    assert "error" in response
    assert response["error"]["code"]
    assert response["error"]["message"]
    assert response["error"]["stage"]
    assert response["error"]["retryable"] is False
    if code:
        assert response["error"]["code"] == code
    assert "traceback" not in str(response).lower()


def test_valid_single_request_contract(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    response = analyze_excel(base_request(tmp_path, paths=[path], prompt="이 파일의 주요 내용을 요약해줘"))
    assert response["status"] == "success"
    assert response["contract_version"] == "1.0"
    assert response["request_id"] == "req-1"
    assert response["text"]
    assert response["data"]["route"] == "route_single_prompt"
    assert isinstance(response["artifacts"], list)
    assert response["safety"]["validation_status"] == "passed"


def test_valid_multi_request_contract(tmp_path: Path) -> None:
    left = write_table(tmp_path / "a.xlsx", {"항목": ["A"], "값": [1]})
    right = write_table(tmp_path / "b.xlsx", {"항목": ["B"], "값": [2]})
    response = analyze_excel(
        base_request(
            tmp_path,
            paths=[left, right],
            prompt="두 파일을 요약해줘",
        )
    )
    assert response["status"] == "success"
    assert response["data"]["route"] == "route_multi_prompt"
    assert response["request_id"] == "req-1"


def test_unsupported_contract_version(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    payload["contract_version"] = "2.0"
    _assert_invalid(analyze_excel(payload), code="unsupported_contract_version")


def test_missing_request_id(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    payload["request_id"] = ""
    _assert_invalid(analyze_excel(payload), code="missing_request_id")


def test_invalid_analysis_mode(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    payload["analysis_mode"] = "batch"
    _assert_invalid(analyze_excel(payload), code="invalid_analysis_mode")


def test_single_with_multiple_inputs(tmp_path: Path) -> None:
    left = write_table(tmp_path / "a.xlsx")
    right = write_table(tmp_path / "b.xlsx")
    payload = base_request(tmp_path, paths=[left, right], prompt="요약해줘", analysis_mode="single")
    _assert_invalid(analyze_excel(payload), code="invalid_input_count")


def test_multi_with_fewer_than_two_inputs(tmp_path: Path) -> None:
    path = write_table(tmp_path / "a.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘", analysis_mode="multi")
    _assert_invalid(analyze_excel(payload), code="invalid_input_count")


def test_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    _assert_invalid(analyze_excel(payload), code="unsupported_extension")


def test_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "gone.xlsx"
    payload = base_request(tmp_path, paths=[missing], prompt="요약해줘")
    response = analyze_excel(payload)
    _assert_invalid(response, code="missing_file")
    assert response["error"]["stage"] == "file_loading"


def test_invalid_sheet(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    payload["inputs"][0]["sheet"] = "NoSuchSheet"
    _assert_invalid(analyze_excel(payload), code="invalid_sheet")


def test_invalid_profile(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘", profile_name="not-a-profile")
    _assert_invalid(analyze_excel(payload), code="invalid_profile")


def test_invalid_timeout(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘", timeout_seconds=0)
    _assert_invalid(analyze_excel(payload), code="invalid_timeout")
    payload["timeout_seconds"] = -5
    _assert_invalid(analyze_excel(payload), code="invalid_timeout")
    payload["timeout_seconds"] = "fast"
    _assert_invalid(analyze_excel(payload), code="invalid_timeout")


def test_invalid_output_directory(tmp_path: Path) -> None:
    path = write_table(tmp_path / "input.xlsx")
    payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    payload["output_directory"] = "relative/out"
    _assert_invalid(analyze_excel(payload), code="invalid_output_directory")
    payload["output_directory"] = ""
    _assert_invalid(analyze_excel(payload), code="invalid_output_directory")
    as_file = tmp_path / "not-a-dir"
    as_file.write_text("x", encoding="utf-8")
    payload["output_directory"] = str(as_file)
    _assert_invalid(analyze_excel(payload), code="invalid_output_directory")
