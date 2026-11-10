"""Headless CLI: one JSON request on stdin, one JSON response on stdout.

Usage:
    python -m core.application.cli < request.json > response.json

Logs and diagnostics go to stderr. Exit codes follow contracts.CLI_EXIT_CODES.
This CLI does not implement incomplete thread timeouts or cancellation.

operation "read_range" reads stored cells only. It never reaches analyze_excel,
the routers, or the model.

operation "read_table" reads a user-specified range with user-specified header
rows. It also never reaches analyze_excel, the routers, or the model.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import PurePosixPath
from typing import Any, TextIO

from core.application.contracts import (
    CLI_EXIT_CODES,
    CONTRACT_VERSION,
    error_response,
)
from core.application.headless import analyze_excel
from core.io.cell_reader import OPERATION as READ_RANGE_OPERATION
from core.io.cell_reader import read_range
from core.io.table_reader import OPERATION as READ_TABLE_OPERATION
from core.io.table_reader import read_table

logger = logging.getLogger("core.application.cli")

_READ_REQUEST_KEYS = frozenset({"contract_version", "request_id", "operation", "inputs", "read"})
_TABLE_READ_KEYS = frozenset({"address", "header"})
_READ_REQUEST_ERRORS = {
    "forbidden_request_field": "read_range 요청에 허용되지 않은 필드가 있다.",
    "unsupported_contract_version": "계약 1.0만 받는다.",
    "missing_request_id": "request_id가 필요하다.",
    "missing_inputs": "입력이 필요하다.",
    "invalid_input_count": "read_range 입력은 하나만 받는다.",
    "invalid_input": "입력 항목이 올바르지 않다.",
    "invalid_read": "read는 address 하나만 가진 객체여야 한다.",
}
_TABLE_REQUEST_ERRORS = {
    **_READ_REQUEST_ERRORS,
    "forbidden_request_field": "read_table 요청에 허용되지 않은 필드가 있다.",
    "invalid_input_count": "read_table 입력은 하나만 받는다.",
    "invalid_read": "read는 address와 header만 가진 객체여야 한다.",
}


class _ReadRequestError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def main(argv: list[str] | None = None) -> int:
    del argv  # v1: stdin-only, no flags
    _configure_stderr_logging()
    raw = sys.stdin.read()
    request_id = ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        response = error_response(
            request_id="",
            status="invalid_request",
            code="malformed_json",
            message="stdin must contain a single JSON object.",
            stage="request_validation",
            retryable=False,
        ).to_dict()
        _write_response(response)
        logger.error("Malformed JSON on stdin: %s", exc.__class__.__name__)
        return CLI_EXIT_CODES["invalid_request"]

    if isinstance(payload, dict):
        request_id = str(payload.get("request_id") or "")

    if is_read_range_request(payload):
        response = _run_read_range(payload)
    elif is_read_table_request(payload):
        response = _run_read_table(payload)
    else:
        response = _run_analyze(payload)
    if not isinstance(response, dict):
        response = error_response(
            request_id=request_id,
            status="execution_failed",
            code="invalid_response",
            message="Analyzer returned a non-object response.",
            stage="execution",
            retryable=False,
        ).to_dict()
    _write_response(response)
    status = str(response.get("status") or "execution_failed")
    logger.info(
        "request_id=%s status=%s elapsed_ms=%s",
        response.get("request_id") or request_id or "-",
        status,
        (response.get("timing") or {}).get("elapsed_ms"),
    )
    return CLI_EXIT_CODES.get(status, CLI_EXIT_CODES["execution_failed"])


def _run_analyze(payload: Any) -> dict[str, Any]:
    captured = _StdoutToStderr()
    with captured:
        return analyze_excel(payload)


def is_read_range_request(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("operation") == READ_RANGE_OPERATION


def _run_read_range(payload: dict[str, Any]) -> dict[str, Any]:
    raw_id = payload.get("request_id")
    request_id = raw_id.strip() if isinstance(raw_id, str) else ""
    try:
        path, sheet, display_name, address = _parse_read_request(payload)
    except _ReadRequestError as err:
        return _read_request_failure(request_id, err.code)
    captured = _StdoutToStderr()
    with captured:
        return read_range(
            path,
            sheet=sheet,
            address=address,
            display_name=display_name,
            request_id=request_id,
        )


def is_read_table_request(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("operation") == READ_TABLE_OPERATION


def _run_read_table(payload: dict[str, Any]) -> dict[str, Any]:
    raw_id = payload.get("request_id")
    request_id = raw_id.strip() if isinstance(raw_id, str) else ""
    try:
        path, sheet, display_name = _parse_read_input(payload)
        read = payload.get("read")
        if not isinstance(read, dict) or "address" not in read or set(read) - _TABLE_READ_KEYS:
            raise _ReadRequestError("invalid_read")
    except _ReadRequestError as err:
        return _read_request_failure(request_id, err.code, operation=READ_TABLE_OPERATION)
    captured = _StdoutToStderr()
    with captured:
        return read_table(
            path,
            sheet=sheet,
            address=read["address"],
            header=read.get("header"),
            display_name=display_name,
            request_id=request_id,
        )


def _parse_read_request(payload: dict[str, Any]) -> tuple[str, Any, str, Any]:
    path, sheet, display_name = _parse_read_input(payload)
    read = payload.get("read")
    if not isinstance(read, dict) or set(read) != {"address"}:
        raise _ReadRequestError("invalid_read")
    return path, sheet, display_name, read["address"]


def _parse_read_input(payload: dict[str, Any]) -> tuple[str, Any, str]:
    if set(payload) - _READ_REQUEST_KEYS:
        raise _ReadRequestError("forbidden_request_field")
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise _ReadRequestError("unsupported_contract_version")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise _ReadRequestError("missing_request_id")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise _ReadRequestError("missing_inputs")
    if len(inputs) != 1:
        raise _ReadRequestError("invalid_input_count")
    item = inputs[0]
    if not isinstance(item, dict):
        raise _ReadRequestError("invalid_input")
    path = item.get("path")
    if not isinstance(path, str) or not path.strip() or not PurePosixPath(path).is_absolute():
        raise _ReadRequestError("invalid_input")
    display_name = item.get("display_name", PurePosixPath(path).name)
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or "/" in display_name
        or "\\" in display_name
    ):
        raise _ReadRequestError("invalid_input")
    return path, item.get("sheet", 0), display_name


def _read_request_failure(request_id: str, code: str, *, operation: str = READ_RANGE_OPERATION) -> dict[str, Any]:
    messages = _TABLE_REQUEST_ERRORS if operation == READ_TABLE_OPERATION else _READ_REQUEST_ERRORS
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "status": "invalid_request",
        "operation": operation,
        "recalculated": False,
        "error": {
            "code": code,
            "message": messages[code],
            "stage": "request",
            "retryable": False,
        },
    }


def _write_response(response: dict[str, Any]) -> None:
    json.dump(response, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _configure_stderr_logging() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )


class _StdoutToStderr:
    """Keep accidental prints off stdout so the CLI emits JSON only."""

    def __init__(self) -> None:
        self._stdout: TextIO | None = None

    def __enter__(self) -> "_StdoutToStderr":
        self._stdout = sys.stdout
        sys.stdout = sys.stderr
        return self

    def __exit__(self, *exc: object) -> None:
        if self._stdout is not None:
            sys.stdout = self._stdout


if __name__ == "__main__":
    raise SystemExit(main())
