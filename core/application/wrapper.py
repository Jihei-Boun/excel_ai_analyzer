"""운영 래퍼 진입점.

사용:
    python -m core.application.wrapper --config operator.json
    python -m core.application.wrapper --config operator.json cancel EXECUTION_ID

요청 JSON은 stdin이다. Python·명령·모델 주소·출력 위치는 요청으로 고르지 않는다.
이 모듈은 Linux만 지원한다.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from core.application.wrapper_config import ConfigError, load_operator_config
from core.application.wrapper_run import request_cancel, run_job


def _read_bounded(stream: Any, limit: int) -> tuple[bytes, bool]:
    """한도+1바이트를 넘기면 더 읽지 않는다."""
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        block = stream.read(min(65536, limit - total + 1))
        if not block:
            break
        if isinstance(block, str):
            block = block.encode("utf-8")
        chunks.append(block)
        total += len(block)
        if total > limit:
            return b"".join(chunks), True
    return b"".join(chunks), False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--config" not in args:
        _emit({"wrapper_version": "1", "status": "invalid_request", "error": {"code": "missing_config", "message": "운영 설정 경로가 필요하다.", "stage": "config", "retryable": False}})
        return 2
    config_path = args[args.index("--config") + 1] if args.index("--config") + 1 < len(args) else ""
    try:
        config = load_operator_config(config_path)
    except ConfigError as exc:
        _emit({"wrapper_version": "1", "status": "invalid_request", "error": {"code": exc.code, "message": exc.message, "stage": "config", "retryable": False}})
        return 2
    except (OverflowError, ValueError, ArithmeticError):
        _emit({"wrapper_version": "1", "status": "invalid_request", "error": {"code": "invalid_config", "message": "운영 설정의 숫자를 바꾸지 못했다.", "stage": "config", "retryable": False}})
        return 2
    if "cancel" in args:
        execution_id = args[-1]
        if execution_id in {"cancel", "--config", config_path}:
            _emit({"status": "invalid_request", "error": {"code": "invalid_execution_id", "message": "실행 ID가 필요하다.", "stage": "cancel", "retryable": False}})
            return 2
        result = request_cancel(config, execution_id)
        _emit(result)
        return 0 if result.get("status") == "cancel_requested" else 2
    source = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    raw, too_large = _read_bounded(source, config.max_request_bytes)
    if too_large:
        _emit({"wrapper_version": "1", "status": "invalid_request", "error": {"code": "request_too_large", "message": "요청이 크기 한도를 넘는다.", "stage": "request", "retryable": False}})
        return 2
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        _emit({"wrapper_version": "1", "status": "invalid_request", "error": {"code": "malformed_json", "message": "stdin은 JSON 객체여야 한다.", "stage": "request", "retryable": False}})
        return 2
    response, code = run_job(config, payload)
    _emit(response)
    return int(code)


def _emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
