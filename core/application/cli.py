"""Headless CLI: one JSON request on stdin, one JSON response on stdout.

Usage:
    python -m core.application.cli < request.json > response.json

Logs and diagnostics go to stderr. Exit codes follow contracts.CLI_EXIT_CODES.
This CLI does not implement incomplete thread timeouts or cancellation.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

from core.application.contracts import (
    CLI_EXIT_CODES,
    error_response,
)
from core.application.headless import analyze_excel

logger = logging.getLogger("core.application.cli")


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
