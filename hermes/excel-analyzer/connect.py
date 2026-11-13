#!/usr/bin/env python3
"""Hermes excel-analyzer와 운영 래퍼를 잇는 얇은 연결.

계산과 검증은 하지 않는다. 운영 설정의 Python으로
python -m core.application.wrapper만 호출한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

BRIDGE = "excel-analyzer"
CONFIG_ENV = "EXCEL_ANALYZER_OPERATOR_CONFIG"
TASKS = {
    "summary": "이 파일의 주요 내용을 요약해줘",
    "schema": "컬럼 목록을 보여줘",
    "quality": "이 파일의 데이터 품질을 분석해줘",
}
ANALYZE_TASK = "analyze"
QUESTION_MAX_CHARS = 500
READ_TASK = "read_range"
READ_KEYS = ("values_from", "source", "requested", "returned", "cells")
TABLE_TASK = "read_table"
TABLE_KEYS = ("values_from", "freshness", "source", "requested", "table")
READ_TASKS = (READ_TASK, TABLE_TASK)
_PUBLIC_KEYS = {READ_TASK: READ_KEYS, TABLE_TASK: TABLE_KEYS}
_HEADER_ROW = re.compile(r"[+-]?[0-9]+")
STATUS_EXIT = {
    "success": 0,
    "execution_failed": 1,
    "invalid_request": 2,
    "cannot_plan": 3,
    "validation_failed": 4,
    "model_unavailable": 5,
    "timeout": 6,
    "cancelled": 7,
    "busy": 8,
}


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    try:
        args = _parser().parse_args(raw)
    except _BridgeError as exc:
        return _fail(None, exc.code, exc.message, request_id="", hidden=_argv_hidden(raw))
    task = str(args.task)
    hidden = _read_hidden(args.file) if task in READ_TASKS else None
    if task not in TASKS and task not in READ_TASKS and task != ANALYZE_TASK:
        return _fail(
            task,
            "unsupported_task",
            "지원 작업은 summary, schema, quality, analyze, read_range, read_table뿐이다.",
            request_id=args.request_id,
        )
    if task == ANALYZE_TASK and (args.question is None or not str(args.question).strip()):
        return _fail(task, "missing_question", "analyze에는 --question이 필요하다.", request_id=args.request_id)
    if task != ANALYZE_TASK and args.question is not None:
        return _fail(task, "forbidden_argument", "--question은 analyze에만 쓴다.", request_id=args.request_id, hidden=hidden)
    if task in READ_TASKS and (args.address is None or not str(args.address).strip()):
        return _fail(task, "missing_address", f"{task}에는 --address가 필요하다.", request_id=args.request_id, hidden=hidden)
    if task not in READ_TASKS and args.address is not None:
        return _fail(task, "forbidden_argument", "--address는 read_range와 read_table에만 쓴다.", request_id=args.request_id)
    if task != TABLE_TASK and args.header is not None:
        return _fail(task, "forbidden_argument", "--header는 read_table에만 쓴다.", request_id=args.request_id, hidden=hidden)
    try:
        header = _header(args.header) if task == TABLE_TASK else None
        question = _question(args.question) if task == ANALYZE_TASK else None
        request_id = _request_id(args.request_id)
        source = _absolute_file(args.file)
        sheet = _sheet(args.sheet)
        config_path, operator = _operator_launch()
    except _BridgeError as exc:
        return _fail(task, exc.code, exc.message, request_id=getattr(args, "request_id", ""), hidden=hidden)
    inputs = [{"path": str(source), "sheet": sheet, "display_name": source.name}]
    if task in READ_TASKS:
        read: dict[str, Any] = {"address": str(args.address)}
        if header is not None:
            read["header"] = header
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": request_id,
            "operation": task,
            "inputs": inputs,
            "read": read,
        }
    else:
        payload = {
            "contract_version": "1.0",
            "request_id": request_id,
            "user_prompt": question if task == ANALYZE_TASK else TASKS[task],
            "inputs": inputs,
            "profile_name": "generic",
            "analysis_mode": "single",
        }
    print(f"wrapper_request_id={request_id} task={task}", file=sys.stderr)
    try:
        completed = subprocess.run(
            [operator["python_path"], "-m", "core.application.wrapper", "--config", str(config_path)],
            cwd=operator["repo_cwd"],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return _fail(task, "wrapper_spawn_failed", exc.__class__.__name__, request_id=request_id, hidden=hidden)
    if completed.stderr:
        sys.stderr.buffer.write(completed.stderr)
    if task in READ_TASKS:
        hidden = _read_hidden(str(source), operator["workspace_root"])
        return _emit_read(task, request_id, completed.returncode, completed.stdout, hidden)
    return _emit_wrapper(task, request_id, completed.returncode, completed.stdout, question=question)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _BridgeError("invalid_arguments", message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="excel-analyzer-connect")
    parser.add_argument("--task", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--address")
    parser.add_argument("--header")
    parser.add_argument("--question")
    return parser


class _BridgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _request_id(value: str) -> str:
    text = str(value).strip()
    if not text or len(text) > 128 or "\x00" in text:
        raise _BridgeError("invalid_request_id", "request_id는 1자 이상 128자 이하여야 한다.")
    return text


def _question(value: str) -> str:
    """사용자 질문은 그대로 엔진에 넘긴다. 길이와 제어 문자만 막는다."""
    text = str(value).strip()
    if len(text) > QUESTION_MAX_CHARS:
        raise _BridgeError("question_too_long", f"질문은 {QUESTION_MAX_CHARS}자 이하여야 한다.")
    if any(ord(ch) < 32 and ch not in "\t\n" for ch in text):
        raise _BridgeError("invalid_question", "질문에 제어 문자가 들어 있다.")
    return text


def _header(value: str | None) -> dict[str, list[int]] | None:
    """생략은 None이라 read에 header를 넣지 않는다. 행 번호의 의미는 래퍼가 판단한다."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "none":
        return {"rows": []}
    tokens = [token.strip() for token in text.split(",")]
    if not all(_HEADER_ROW.fullmatch(token) for token in tokens):
        raise _BridgeError("invalid_arguments", "--header는 none 또는 쉼표로 구분한 원본 행 번호여야 한다.")
    return {"rows": [int(token) for token in tokens]}


def _absolute_file(value: str) -> Path:
    text = str(value).strip()
    if not text:
        raise _BridgeError("missing_file", "파일 경로가 필요하다.")
    path = Path(text)
    if not path.is_absolute():
        raise _BridgeError("absolute_path_required", "파일 경로는 절대 경로여야 한다.")
    return path


def _sheet(value: str) -> int | str:
    text = str(value).strip()
    if not text:
        raise _BridgeError("missing_sheet", "시트가 필요하다.")
    if text[0] == "-" or not text.isdigit():
        return text
    return int(text)


def _operator_launch() -> tuple[Path, dict[str, str]]:
    raw = os.environ.get(CONFIG_ENV, "").strip()
    if not raw:
        raise _BridgeError("missing_operator_config", "운영 설정 경로가 환경에 없다.")
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise _BridgeError("missing_operator_config", "운영 설정 파일을 찾을 수 없다.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _BridgeError("invalid_operator_config", exc.__class__.__name__) from exc
    if not isinstance(payload, dict):
        raise _BridgeError("invalid_operator_config", "운영 설정은 JSON 객체여야 한다.")
    python_path = payload.get("python_path")
    repo_cwd = payload.get("repo_cwd")
    if not isinstance(python_path, str) or not python_path or not isinstance(repo_cwd, str) or not repo_cwd:
        raise _BridgeError("invalid_operator_config", "python_path와 repo_cwd가 필요하다.")
    workspace_root = payload.get("workspace_root")
    return path, {
        "python_path": python_path,
        "repo_cwd": repo_cwd,
        "workspace_root": workspace_root if isinstance(workspace_root, str) else "",
    }


def _emit_wrapper(
    task: str, request_id: str, returncode: int, stdout: bytes, *, question: str | None = None
) -> int:
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _fail(task, "wrapper_output_unreadable", "래퍼 stdout이 JSON이 아니다.", request_id=request_id, wrapper_exit_code=returncode)
    if not isinstance(response, dict):
        return _fail(task, "wrapper_output_unreadable", "래퍼 응답이 객체가 아니다.", request_id=request_id, wrapper_exit_code=returncode)
    status = response.get("status")
    expected = STATUS_EXIT.get(str(status))
    if expected is None or expected != returncode:
        body = _public(task, request_id, response, returncode, status="exit_code_mismatch")
        body["error"] = {"code": "exit_code_mismatch", "message": "래퍼 상태와 종료 코드가 일치하지 않는다."}
        body["text"] = None
        body["data"] = None
        body["artifacts"] = []
        _write(body)
        return 1
    body = _public(task, request_id, response, returncode, status=str(status))
    if status != "success":
        body["text"] = None
        body["data"] = None
        body["artifacts"] = []
    if task == ANALYZE_TASK:
        warnings = _warnings(response.get("warnings")) if status == "success" else []
        body["question_sent"] = question
        body["warnings"] = warnings
        body["fallback_unverified"] = any(w["code"] == "unverified_fallback" for w in warnings)
        body["answer_table"] = _answer_table(body["data"]) if status == "success" else None
    _write(body)
    return returncode


def _answer_table(data: dict[str, Any] | None) -> str | None:
    """에이전트가 숫자를 옮겨 적지 않고 그대로 붙일 마크다운 표. 값은 바꾸지 않고 표시만 정한다."""
    if not isinstance(data, dict):
        return None
    columns = data.get("columns")
    records = data.get("preview_records")
    if not isinstance(columns, list) or not columns or not isinstance(records, list) or not records:
        return None
    names = [str(column) for column in columns]
    lines = [
        "| " + " | ".join(_cell(name) for name in names) + " |",
        "| " + " | ".join("---" for _ in names) + " |",
    ]
    for record in records:
        if isinstance(record, dict):
            lines.append("| " + " | ".join(_cell(record.get(name)) for name in names) + " |")
    shape = data.get("shape")
    total = shape[0] if isinstance(shape, list) and shape and isinstance(shape[0], int) else None
    if total is not None and total > len(records):
        lines.append("")
        lines.append(f"(전체 {total:,}행 중 앞 {len(records):,}행)")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, int):
        text = f"{value:,}"
    elif isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value.is_integer() and abs(value) < 1e15:
            text = f"{int(value):,}"
        else:
            # 1 이상은 소수 둘째 자리, 1 미만 비율은 넷째 자리까지 보여 0으로 뭉개지지 않게 한다.
            text = f"{value:,.2f}" if abs(value) >= 1 else f"{value:.4f}"
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _warnings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {"code": item["code"], "message": item["message"]}
        for item in value
        if isinstance(item, dict) and isinstance(item.get("code"), str) and isinstance(item.get("message"), str)
    ]


def _public(task: str, request_id: str, response: dict[str, Any], returncode: int, *, status: str) -> dict[str, Any]:
    structural = _structural_state(response)
    return {
        "bridge": BRIDGE,
        "task": task,
        "request_id": response.get("request_id") or request_id,
        "execution_id": response.get("execution_id"),
        "status": status,
        "wrapper_exit_code": returncode,
        "text": response.get("text"),
        "data": response.get("data") if isinstance(response.get("data"), dict) else None,
        "artifacts": _artifacts(response.get("artifacts")),
        "error": response.get("error") if isinstance(response.get("error"), dict) else None,
        "structural_state": structural,
        "structural_state_covers_semantic_correctness": False,
        "input_integrity": _integrity(response.get("input_integrity")),
    }


def _structural_state(response: dict[str, Any]) -> str:
    published = response.get("structural_state")
    if published in {"not_run", "passed", "failed", "unknown"}:
        return str(published)
    safety = response.get("safety")
    if not isinstance(safety, dict):
        return "unknown"
    validation = safety.get("validation")
    if not isinstance(validation, dict):
        return "unknown"
    structural = validation.get("structural")
    if not isinstance(structural, dict):
        return "unknown"
    state = structural.get("state")
    if state in {"not_run", "passed", "failed"}:
        return str(state)
    return "unknown"


def _artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    checked: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path)
        record = {
            "kind": item.get("kind"),
            "filename": item.get("filename"),
            "path": raw_path,
            "size_bytes": item.get("size_bytes"),
            "sha256": item.get("sha256"),
            "exists": path.is_file(),
        }
        if path.is_file() and isinstance(item.get("sha256"), str):
            record["sha256_matches"] = hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
            record["size_matches"] = path.stat().st_size == item.get("size_bytes")
        else:
            record["sha256_matches"] = False
            record["size_matches"] = False
        checked.append(record)
    return checked


def _integrity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    files = value.get("items")
    compact_files = []
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            compact_files.append(
                {
                    "path": item.get("source"),
                    "before": item.get("source_before"),
                    "after": item.get("source_after"),
                    "state": item.get("source_state"),
                }
            )
    return {
        "state": value.get("state"),
        "authoritative": value.get("authoritative"),
        "files": compact_files,
    }


def _emit_read(task: str, request_id: str, returncode: int, stdout: bytes, hidden: list[str]) -> int:
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _fail(
            task, "wrapper_output_unreadable", "래퍼 stdout이 JSON이 아니다.",
            request_id=request_id, wrapper_exit_code=returncode, hidden=hidden,
        )
    if not isinstance(response, dict):
        return _fail(
            task, "wrapper_output_unreadable", "래퍼 응답이 객체가 아니다.",
            request_id=request_id, wrapper_exit_code=returncode, hidden=hidden,
        )
    status = response.get("status")
    expected = STATUS_EXIT.get(str(status))
    if expected is None or expected != returncode:
        body = _read_body(task, response, returncode, status="exit_code_mismatch", success=False)
        body["error"] = {"code": "exit_code_mismatch", "message": "래퍼 상태와 종료 코드가 일치하지 않는다."}
        code = 1
    else:
        body = _read_body(task, response, returncode, status=str(status), success=status == "success")
        code = returncode
    if _exposes_path(body, hidden):
        body = _read_body(
            task, {"execution_id": response.get("execution_id")}, returncode, status="execution_failed", success=False
        )
        body["error"] = {"code": "path_exposure_blocked", "message": "공개 응답에 경로가 들어 있어 공개하지 않는다."}
        code = 1
    _write(body)
    return code


def _read_body(task: str, response: dict[str, Any], returncode: int, *, status: str, success: bool) -> dict[str, Any]:
    published = response.get("structural_state")
    body: dict[str, Any] = {
        "bridge": BRIDGE,
        "task": task,
        "request_id": response.get("request_id") or None,
        "execution_id": response.get("execution_id"),
        "status": status,
        "wrapper_exit_code": returncode,
        "text": None,
        "data": None,
        "artifacts": [],
        "error": None if success or not isinstance(response.get("error"), dict) else response["error"],
        "operation": task,
        "recalculated": False,
    }
    for key in _PUBLIC_KEYS[task]:
        body[key] = response.get(key) if success else None
    body["structural_state"] = "not_applicable" if published == "not_applicable" else _structural_state(response)
    body["structural_state_covers_semantic_correctness"] = False
    body["input_integrity"] = _read_integrity(response.get("input_integrity"))
    return body


def _read_integrity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    files = []
    items = value.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            files.append(
                {
                    "display_name": item.get("display_name"),
                    "before": item.get("source_before"),
                    "after": item.get("source_after"),
                    "state": item.get("source_state"),
                    "copy_before": item.get("copy_before"),
                    "copy_after": item.get("copy_after"),
                    "copy_state": item.get("copy_state"),
                }
            )
    return {
        "state": value.get("state"),
        "authoritative": value.get("authoritative"),
        "files": files,
    }


def _argv_hidden(argv: list[str]) -> list[str] | None:
    """인자 해석이 실패해도 read_range·read_table 요청이면 공개 응답에서 가릴 경로를 구한다."""
    pre = _Parser(prog="excel-analyzer-connect", add_help=False)
    pre.add_argument("--task")
    pre.add_argument("--file")
    try:
        known, _ = pre.parse_known_args(argv)
    except _BridgeError:
        return None
    if known.task not in READ_TASKS:
        return None
    return _read_hidden(known.file)


def _read_hidden(file_value: Any, workspace_root: str | None = None) -> list[str]:
    hidden: list[str] = []
    text = str(file_value or "").strip()
    try:
        if text and Path(text).is_absolute():
            hidden += [text, os.path.realpath(text)]
    except ValueError:
        pass
    if workspace_root is None:
        try:
            workspace_root = _operator_launch()[1]["workspace_root"]
        except _BridgeError:
            workspace_root = ""
    if workspace_root:
        hidden.append(workspace_root)
    return hidden


def _exposes_path(body: dict[str, Any], hidden: list[str]) -> bool:
    text = json.dumps(body, ensure_ascii=False)
    for path in hidden:
        if len(path) > 1 and (path in text or json.dumps(path, ensure_ascii=False)[1:-1] in text):
            return True
    request_id = body.get("request_id")
    return isinstance(request_id, str) and "/" in request_id and any(request_id in path for path in hidden)


def _fail(
    task: str | None,
    code: str,
    message: str,
    *,
    request_id: str,
    wrapper_exit_code: int | None = None,
    hidden: list[str] | None = None,
) -> int:
    body: dict[str, Any] = {
        "bridge": BRIDGE,
        "task": task,
        "request_id": request_id or None,
        "execution_id": None,
        "status": "invalid_request",
        "wrapper_exit_code": wrapper_exit_code,
        "text": None,
        "data": None,
        "artifacts": [],
        "error": {"code": code, "message": message},
        "structural_state": "unknown",
        "structural_state_covers_semantic_correctness": False,
        "input_integrity": None,
    }
    if hidden and _exposes_path(body, hidden):
        body["request_id"] = None
        if _exposes_path(body, hidden):
            body["error"]["message"] = "오류 메시지에 경로가 들어 있어 공개하지 않는다."
    _write(body)
    return 2 if wrapper_exit_code is None else 1


def _write(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
