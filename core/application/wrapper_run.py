"""Linux 실행 래퍼. 엔진 CLI를 별도 프로세스로 호출한다."""

from __future__ import annotations

import errno
import json
import math
import os
import shutil
import signal
import stat
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter

from core.application.contracts import CLI_EXIT_CODES, CONTRACT_VERSION, SUPPORTED_STATUSES
from core.application.wrapper_config import OperatorConfig, source_identity
from core.application.wrapper_paths import (
    PathRejected,
    PreparedInput,
    conflicts_with_inputs,
    locate_output_file,
    copy_regular_file,
    ensure_directory,
    is_inside,
    real_path,
    resolve_input,
    sha256_file,
)
from core.io.cell_reader import MAX_CELLS
from core.io.cell_reader import OPERATION as READ_RANGE_OPERATION
from core.io.table_reader import OPERATION as READ_TABLE_OPERATION

WRAPPER_VERSION = "1"
BUSY_EXIT = 8
EXECUTION_ID_RE = r"[0-9a-f]{32}"
ALLOWED_REQUEST_KEYS = {
    "contract_version",
    "request_id",
    "user_prompt",
    "inputs",
    "profile_name",
    "analysis_mode",
    "timeout_seconds",
}
ANALYZE_OPERATION = "analyze"
READ_REQUEST_KEYS = {
    "contract_version",
    "request_id",
    "operation",
    "inputs",
    "read",
    "timeout_seconds",
}
_SUCCESS_STATUSES = {"success"}
_READ_STATUSES = {"success", "invalid_request", "execution_failed"}
_READ_SUCCESS_KEYS = ("values_from", "source", "requested", "returned", "cells")
_READ_CELL_KEYS = frozenset(
    {
        "a1",
        "row",
        "column_index",
        "column_letter",
        "value",
        "value_type",
        "formula",
        "cached_value",
        "cached_value_state",
        "merge",
    }
)
_READ_VALUE_TYPES = {"empty", "number", "string", "boolean", "datetime", "error", "formula"}
_READ_CACHED_STATES = {"stored", "absent", "not_applicable"}
_READ_OPERATIONS = (READ_RANGE_OPERATION, READ_TABLE_OPERATION)
_TABLE_READ_KEYS = {"address", "header"}
_TABLE_SUCCESS_KEYS = ("values_from", "freshness", "source", "requested", "table")
_TABLE_KEYS = frozenset({"address", "blank_rows", "blank_columns", "columns", "rows"})
_TABLE_COLUMN_KEYS = frozenset({"key", "display_name", "source_column", "column_index", "headers"})
_TABLE_HEADER_KEYS = frozenset({"row", "a1", "value", "value_type", "merge"})
_TABLE_HEADER_FORMULA_KEYS = frozenset({"formula", "cached_value", "cached_value_state"})
_TABLE_CELL_KEYS = frozenset(
    {"a1", "key", "value", "value_type", "number_format", "formula", "cached_value", "cached_value_state"}
)
_MAX_TABLE_HEADER_ROWS = 2
_PUBLIC_INTEGRITY_ITEM_KEYS = (
    "source_before",
    "source_after",
    "source_state",
    "copy_before",
    "copy_after",
    "copy_state",
)


@dataclass(frozen=True)
class ProcessLaunch:
    """테스트·측정 하네스 전용. 요청 JSON으로는 받지 않는다."""

    argv: tuple[str, ...]


class _Drain(threading.Thread):
    def __init__(self, pipe: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self.pipe = pipe
        self.limit = limit
        self.kept = bytearray()
        self.total = 0
        self.discarded = 0

    def run(self) -> None:
        while True:
            block = self.pipe.read(65536)
            if not block:
                break
            self.total += len(block)
            room = self.limit - len(self.kept)
            if room > 0:
                self.kept.extend(block[:room])
                if len(block) > room:
                    self.discarded += len(block) - room
            else:
                self.discarded += len(block)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(
    *,
    request_id: str,
    status: str,
    exit_code: int,
    execution_id: str | None = None,
    message: str = "",
    code: str = "",
    stage: str = "",
    retryable: bool = False,
    structural_state: str = "unknown",
    cleanup: str = "not_applicable",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "wrapper_version": WRAPPER_VERSION,
        "supported_os": "linux",
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "execution_id": execution_id,
        "status": status,
        "exit_code": exit_code,
        "semantic_correctness_guaranteed": False,
        "structural_state": structural_state,
        "text": "",
        "data": {"shape": [0, 0], "columns": [], "preview_records": []},
        "artifacts": [],
        "warnings": [],
        "error": None,
        "cleanup": {"state": cleanup},
        "duplicate_request_id": False,
        "duplicate_policy": "new_execution_id",
    }
    if code:
        payload["error"] = {
            "code": code,
            "message": message,
            "stage": stage,
            "retryable": retryable,
        }
        payload["text"] = message
    if extra:
        payload.update(extra)
    return payload


def _unsupported() -> tuple[dict[str, Any], int]:
    return (
        _response(
            request_id="",
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            message="이 래퍼는 Linux에서만 실행한다.",
            code="unsupported_platform",
            stage="platform",
            retryable=False,
        ),
        CLI_EXIT_CODES["execution_failed"],
    )


def _reject(
    request_id: str,
    code: str,
    message: str,
    *,
    stage: str,
    retryable: bool = False,
    status: str = "invalid_request",
    exit_code: int | None = None,
) -> tuple[dict[str, Any], int]:
    code_num = CLI_EXIT_CODES.get(status, CLI_EXIT_CODES["invalid_request"]) if exit_code is None else exit_code
    return (
        _response(
            request_id=request_id,
            status=status,
            exit_code=code_num,
            message=message,
            code=code,
            stage=stage,
            retryable=retryable,
        ),
        code_num,
    )


def _read_operation(payload: Any) -> str | None:
    """read_range나 read_table 요청이면 그 이름을, 아니면 None을 돌려준다."""
    if not isinstance(payload, dict):
        return None
    operation = payload.get("operation")
    if operation == READ_RANGE_OPERATION or operation == READ_TABLE_OPERATION:
        return str(operation)
    return None


def _request_operation(payload: dict[str, Any]) -> str:
    """생략은 analyze다. analyze, read_range, read_table 밖의 값은 analyze로 바꾸지 않고 거절한다."""
    if "operation" not in payload:
        return ANALYZE_OPERATION
    operation = payload["operation"]
    if operation == ANALYZE_OPERATION or operation in _READ_OPERATIONS:
        return str(operation)
    raise PathRejected("invalid_operation", "지원하지 않는 작업이다.")


def _check_envelope(payload: dict[str, Any]) -> None:
    version = payload.get("contract_version", CONTRACT_VERSION)
    if version != CONTRACT_VERSION:
        raise PathRejected("unsupported_contract_version", "계약 1.0만 받는다.")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128 or "\x00" in request_id:
        raise PathRejected("invalid_request_id", "request_id가 필요하다.")


def _parse_read_request(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) - READ_REQUEST_KEYS:
        raise PathRejected("forbidden_request_field", "read_range 요청은 모델, 프롬프트, 출력, 명령을 지정할 수 없다.")
    _check_envelope(payload)
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise PathRejected("missing_inputs", "입력이 필요하다.")
    if len(inputs) != 1:
        raise PathRejected("invalid_input_count", "read_range 입력은 하나만 받는다.")
    read = payload.get("read")
    if not isinstance(read, dict) or set(read) != {"address"}:
        raise PathRejected("invalid_read", "read는 address 하나만 가진 객체여야 한다.")
    return payload


def _parse_table_request(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) - READ_REQUEST_KEYS:
        raise PathRejected("forbidden_request_field", "read_table 요청은 모델, 프롬프트, 출력, 명령을 지정할 수 없다.")
    _check_envelope(payload)
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise PathRejected("missing_inputs", "입력이 필요하다.")
    if len(inputs) != 1:
        raise PathRejected("invalid_input_count", "read_table 입력은 하나만 받는다.")
    read = payload.get("read")
    if not isinstance(read, dict) or "address" not in read or set(read) - _TABLE_READ_KEYS:
        raise PathRejected("invalid_read", "read는 address와 header만 가진 객체여야 한다.")
    return payload


def _parse_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PathRejected("malformed_request", "요청은 JSON 객체여야 한다.")
    operation = _request_operation(payload)
    if operation == READ_RANGE_OPERATION:
        return _parse_read_request(payload)
    if operation == READ_TABLE_OPERATION:
        return _parse_table_request(payload)
    unknown = sorted(set(payload) - ALLOWED_REQUEST_KEYS - {"operation"})
    if unknown:
        raise PathRejected("forbidden_request_field", "요청이 실행 환경이나 명령을 지정할 수 없다.")
    _check_envelope(payload)
    prompt = payload.get("user_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise PathRejected("missing_user_prompt", "user_prompt가 필요하다.")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise PathRejected("missing_inputs", "입력이 필요하다.")
    return payload


def _accepted_timeout(payload: dict[str, Any], config: OperatorConfig) -> float:
    raw = payload.get("timeout_seconds", config.default_timeout_seconds)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PathRejected("invalid_timeout", "timeout_seconds는 유한한 양수여야 한다.")
    try:
        number = float(raw)
    except (OverflowError, ValueError, ArithmeticError) as exc:
        raise PathRejected("invalid_timeout", "timeout_seconds는 유한한 양수여야 한다.") from exc
    if not math.isfinite(number) or number <= 0:
        raise PathRejected("invalid_timeout", "timeout_seconds는 유한한 양수여야 한다.")
    if number > config.max_timeout_seconds:
        raise PathRejected(
            "timeout_above_maximum",
            "요청 시간이 운영 최대값을 넘는다. 값을 줄여 실행하지 않고 거절한다.",
        )
    return number


def _prepare_inputs(
    payload: dict[str, Any],
    config: OperatorConfig,
    inputs_dir: Path,
    sink: list[PreparedInput] | None = None,
) -> list[PreparedInput]:
    raw_inputs = payload["inputs"]
    if len(raw_inputs) > config.max_input_files:
        raise PathRejected("too_many_files", "입력 파일 수가 한도를 넘는다.")
    prepared: list[PreparedInput] = sink if sink is not None else []
    total = 0
    for index, item in enumerate(raw_inputs, start=1):
        if not isinstance(item, dict):
            raise PathRejected("invalid_input", "입력 항목은 객체여야 한다.")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PathRejected("invalid_input", "입력 경로가 필요하다.")
        source = resolve_input(
            Path(raw_path),
            config.allowed_input_roots,
            extensions=config.allowed_extensions,
        )
        info = os.lstat(source)
        if info.st_size > config.max_file_bytes:
            raise PathRejected("file_too_large", "입력 파일이 크기 한도를 넘는다.")
        total += info.st_size
        if total > config.max_total_bytes:
            raise PathRejected("total_too_large", "입력 전체 크기가 한도를 넘는다.")
        display = item.get("display_name") or source.name
        if not isinstance(display, str) or not display.strip() or "/" in display or "\\" in display:
            raise PathRejected("invalid_input", "display_name은 파일 이름만 허용한다.")
        sheet = item.get("sheet", 0)
        if not isinstance(sheet, (int, str)) or isinstance(sheet, bool):
            raise PathRejected("invalid_input", "sheet 값이 올바르지 않다.")
        copy_path = inputs_dir / f"{index:02d}-{Path(display).name}"
        digest, inode = copy_regular_file(source, copy_path)
        prepared.append(
            PreparedInput(
                source=source,
                copy_path=copy_path,
                display_name=Path(display).name,
                sheet=sheet,
                source_sha256=digest,
                copy_sha256=digest,
                inode=inode,
            )
        )
    return prepared


def _acquire_lock(config: OperatorConfig) -> tuple[int, bool]:
    lock_path = config.workspace_root / ".wrapper.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    import fcntl

    deadline = time.monotonic() + config.lock_wait_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return -1, False
            time.sleep(0.05)


def _release_lock(fd: int) -> None:
    if fd < 0:
        return
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _note_duplicate(config: OperatorConfig, request_id: str, execution_id: str) -> bool:
    index = config.workspace_root / "request_index.jsonl"
    duplicate = False
    if index.is_file():
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("request_id") == request_id:
                duplicate = True
                break
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"request_id": request_id, "execution_id": execution_id}, ensure_ascii=False) + "\n")
    return duplicate


_DEAD_STATES = {"Z", "X"}
_CONFIRM_SECONDS = 1.0


@dataclass(frozen=True)
class _ProcInspect:
    """kind는 ok, gone, unreadable이다. gone은 PID 소멸이고 unreadable은 확인 실패다."""

    kind: str
    reason: str
    info: dict[str, Any] | None = None


_last_scan_status = "ok"


def _inspect_proc(pid: int) -> _ProcInspect:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if Path(f"/proc/{pid}").exists():
            return _ProcInspect("unreadable", "stat_missing")
        return _ProcInspect("gone", "enoent")
    except PermissionError:
        return _ProcInspect("unreadable", "permission")
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            if Path(f"/proc/{pid}").exists():
                return _ProcInspect("unreadable", "stat_missing")
            return _ProcInspect("gone", "enoent")
        return _ProcInspect("unreadable", "oserror")
    close = raw.rfind(")")
    if close < 0 or close + 2 >= len(raw):
        return _ProcInspect("unreadable", "parse")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        return _ProcInspect("unreadable", "parse")
    try:
        info = {
            "state": fields[0],
            "ppid": int(fields[1]),
            "pgrp": int(fields[2]),
            "session": int(fields[3]),
            "starttime": int(fields[19]),
        }
    except ValueError:
        return _ProcInspect("unreadable", "parse")
    return _ProcInspect("ok", "ok", info)


def _read_proc(pid: int) -> dict[str, Any] | None:
    found = _inspect_proc(pid)
    if found.kind != "ok":
        return None
    return found.info


def _uptime_ticks() -> int | None:
    try:
        hz = os.sysconf("SC_CLK_TCK")
        seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if hz <= 0:
        return None
    return int(seconds * hz)


def _leader_running(pid: int, starttime: int, pgid: int) -> bool:
    found = _inspect_proc(pid)
    info = found.info
    return bool(
        found.kind == "ok"
        and info is not None
        and info["starttime"] == starttime
        and info["pgrp"] == pgid
        and info["state"] not in _DEAD_STATES
    )


def _member_from(pid: int, info: dict[str, Any]) -> dict[str, Any]:
    return {
        "pid": pid,
        "state": info["state"],
        "starttime": info["starttime"],
        "pgrp": info["pgrp"],
        "ppid": info["ppid"],
        "session": info["session"],
    }


def _scan_group(
    pgid: int,
    session: int,
    leader_start: int,
    *,
    latest_start: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """같은 그룹을 훑는다. latest_start는 더 이상 멤버를 숨기지 않는다.

    PID가 사라졌으면 건너뛴다. 권한·파싱·기타 읽기 실패가 있으면 확인된 빈 그룹으로
    보지 않는다. 리더 종료 tick만으로 후손과 PGID 재사용을 가르지 않는다.
    """
    global _last_scan_status
    del latest_start
    try:
        names = os.listdir("/proc")
    except OSError:
        _last_scan_status = "unavailable"
        return [], False
    members: list[dict[str, Any]] = []
    saw_unreadable = False
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        found = _inspect_proc(pid)
        if found.kind == "gone":
            continue
        if found.kind != "ok" or found.info is None:
            saw_unreadable = True
            continue
        info = found.info
        if info["pgrp"] != pgid or info["session"] != session:
            continue
        if info["starttime"] < leader_start:
            continue
        members.append(_member_from(pid, info))
    if saw_unreadable:
        _last_scan_status = "unreadable"
        return members, False
    _last_scan_status = "ok"
    return members, True


def _signal_matching(pid: int, sig: int, *, starttime: int, pgid: int) -> str:
    """sent, skipped, unreadable. 시작 tick이나 PGID가 다르면 신호하지 않는다."""
    found = _inspect_proc(pid)
    if found.kind == "gone":
        return "skipped"
    if found.kind != "ok" or found.info is None:
        return "unreadable"
    info = found.info
    if info["starttime"] != starttime or info["pgrp"] != pgid or info["state"] in _DEAD_STATES:
        return "skipped"
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return "skipped"
    return "sent"


def _signable_member(
    member: dict[str, Any],
    known: dict[int, int],
    *,
    pgid: int,
    session: int,
    leader_start: int,
) -> bool:
    """알고 있는 시작 tick이거나, 그 프로세스의 살아있는 부모 사슬일 때만 신호 대상이다."""
    if known.get(member["pid"]) == member["starttime"]:
        return True
    current = member.get("ppid")
    seen = {int(member["pid"])}
    for _hop in range(8):
        if not isinstance(current, int) or current <= 1 or current in seen:
            return False
        seen.add(current)
        if current in known:
            found = _inspect_proc(current)
            info = found.info
            return bool(
                found.kind == "ok"
                and info is not None
                and info["starttime"] == known[current]
                and info["pgrp"] == pgid
                and info["session"] == session
            )
        found = _inspect_proc(current)
        if found.kind != "ok" or found.info is None:
            return False
        info = found.info
        if info["pgrp"] != pgid or info["session"] != session or info["starttime"] < leader_start:
            return False
        current = info["ppid"]
    return False


def _killpg_if_leader(leader_pid: int, leader_start: int, pgid: int, sig: int) -> bool:
    if not _leader_running(leader_pid, leader_start, pgid):
        return False
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def _wait_child(proc: Any, seconds: float) -> None:
    try:
        proc.wait(timeout=max(0.0, seconds))
    except Exception:
        return


def _reap_group(
    proc: Any,
    *,
    grace: float,
    leader_start: int | None,
    pgid: int | None,
    session: int | None,
    known: dict[int, int],
) -> dict[str, Any]:
    """직접 자식의 종료와 그룹에 남은 실행 프로세스를 따로 확인한다."""
    signals: list[str] = []
    if leader_start is None or pgid is None or session is None:
        return {
            "signals": signals,
            "returncode": proc.returncode,
            "cleanup": "cleanup_incomplete",
            "alive": proc.poll() is None,
            "group_confirmed": False,
            "live_pids": [],
            "zombie_pids": [],
            "unidentified_pids": [],
            "group_scan": "unavailable",
            "pgid": pgid,
            "leader_starttime": leader_start,
        }

    noticed: int | None = None if _leader_running(proc.pid, leader_start, pgid) else _uptime_ticks()

    def observe() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
        nonlocal noticed
        leader_up = _leader_running(proc.pid, leader_start, pgid)
        members, ok = _scan_group(pgid, session, leader_start)
        status = _last_scan_status if ok else (_last_scan_status if _last_scan_status != "ok" else "unreadable")
        live = [item for item in members if item["state"] not in _DEAD_STATES]
        zombies = [item for item in members if item["state"] in _DEAD_STATES]
        seen = {item["pid"] for item in live} | {item["pid"] for item in zombies}
        for pid, start in known.items():
            if pid in seen:
                continue
            found = _inspect_proc(pid)
            if found.kind == "gone":
                continue
            if found.kind != "ok" or found.info is None:
                status = "unreadable"
                continue
            info = found.info
            if info["starttime"] != start or info["pgrp"] != pgid or info["session"] != session:
                continue
            record = _member_from(pid, info)
            if info["state"] in _DEAD_STATES:
                zombies.append(record)
            else:
                live.append(record)
        if proc.poll() is not None:
            live = [item for item in live if item["pid"] != proc.pid]
        if leader_up and status == "ok":
            return live, [], zombies, "ok"
        if not leader_up and noticed is None:
            noticed = _uptime_ticks()
        if noticed is not None:
            for item in [*live, *zombies]:
                if int(item["starttime"]) < noticed:
                    known.setdefault(int(item["pid"]), int(item["starttime"]))
        signable: list[dict[str, Any]] = []
        unidentified: list[dict[str, Any]] = []
        for item in live:
            if _signable_member(item, known, pgid=pgid, session=session, leader_start=leader_start):
                signable.append(item)
            else:
                unidentified.append(item)
        if status != "ok":
            return signable, unidentified, zombies, status
        if unidentified:
            return signable, unidentified, zombies, "unidentified"
        return signable, [], zombies, "ok"

    def deliver(sig_num: int, sig_name: str) -> str | None:
        try:
            if _killpg_if_leader(proc.pid, leader_start, pgid, sig_num):
                signals.append(sig_name)
                return None
            signable, unidentified, _zombies, status = observe()
            sent = False
            for member in signable:
                result = _signal_matching(member["pid"], sig_num, starttime=member["starttime"], pgid=pgid)
                if result == "unreadable":
                    return "unreadable"
                if result == "sent":
                    sent = True
            if sent:
                signals.append(sig_name)
            if status != "ok" or unidentified:
                return status if status != "ok" else "unidentified"
        except PermissionError:
            return "signal_failed"
        return None

    problem: str | None = None
    if proc.poll() is None:
        problem = deliver(signal.SIGTERM, "SIGTERM")
        _wait_child(proc, grace)
    else:
        _wait_child(proc, 1)
        signable, _unidentified, _zombies, status = observe()
        if signable:
            problem = deliver(signal.SIGTERM, "SIGTERM")
            if grace > 0:
                time.sleep(grace)
        elif status != "ok":
            problem = problem or status
    signable, unidentified, zombies, status = observe()
    if proc.poll() is None or signable:
        err = deliver(signal.SIGKILL, "SIGKILL")
        problem = problem or err
        _wait_child(proc, grace)
    elif status != "ok":
        problem = problem or status
    extra_kill = False
    deadline = time.monotonic() + _CONFIRM_SECONDS
    while True:
        signable, unidentified, zombies, status = observe()
        if status in {"unreadable", "unavailable"}:
            problem = problem or status
            break
        if not signable and proc.poll() is not None:
            if unidentified:
                problem = problem or "unidentified"
            break
        if time.monotonic() >= deadline:
            if signable or unidentified or proc.poll() is None:
                problem = problem or ("unidentified" if unidentified else "unconfirmed")
            break
        if signable and not extra_kill:
            err = deliver(signal.SIGKILL, "SIGKILL")
            problem = problem or err
            extra_kill = True
            _wait_child(proc, grace)
            continue
        time.sleep(0.02)
    if proc.poll() is not None:
        _wait_child(proc, 0.2)
    live = signable + unidentified
    confirmed = bool(status == "ok" and not live and not unidentified and proc.poll() is not None and problem is None)
    return {
        "signals": signals,
        "returncode": proc.returncode,
        "cleanup": "complete" if confirmed else "cleanup_incomplete",
        "alive": bool(live) or proc.poll() is None,
        "group_confirmed": confirmed,
        "live_pids": [item["pid"] for item in live],
        "zombie_pids": [item["pid"] for item in zombies],
        "unidentified_pids": [item["pid"] for item in unidentified],
        "group_scan": status if problem is None else (problem if problem != "signal_failed" else status),
        "pgid": pgid,
        "leader_starttime": leader_start,
    }


def _run_process(
    *,
    config: OperatorConfig,
    argv: list[str],
    engine_request: dict[str, Any],
    execution_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    merges = execution_dir / "intermediate" / "merges"
    charts = execution_dir / "intermediate" / "charts"
    merges.mkdir(parents=True, exist_ok=True)
    charts.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(config.repo_cwd)
    env["EXCEL_ANALYZER_MERGES_DIR"] = str(merges)
    env["EXCEL_ANALYZER_CHARTS_DIR"] = str(charts)
    env.pop("MULTI_SHADOW_ENABLED", None)
    import subprocess

    payload = json.dumps(engine_request, ensure_ascii=False).encode("utf-8")
    if len(payload) > config.max_request_bytes:
        raise PathRejected("request_too_large", "엔진 요청이 크기 한도를 넘는다.")
    started = time.monotonic()
    started_at = _now()
    proc = subprocess.Popen(
        argv,
        cwd=str(config.repo_cwd),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    identity = _read_proc(proc.pid)
    leader_start = None if identity is None else int(identity["starttime"])
    pgid = None if identity is None else int(identity["pgrp"])
    session = None if identity is None else int(identity["session"])
    known: dict[int, int] = {}
    stdout_drain = _Drain(proc.stdout, config.max_stdout_bytes)
    stderr_drain = _Drain(proc.stderr, config.max_stderr_bytes)
    stdout_drain.start()
    stderr_drain.start()
    reason = "completed"
    write_error = ""
    offset = 0
    stdin_closed = False
    deadline = started + timeout_seconds
    cancel_flag = execution_dir / "control" / "cancel"

    def close_stdin() -> None:
        nonlocal stdin_closed
        if stdin_closed:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        stdin_closed = True

    def remember_group() -> None:
        if leader_start is None or pgid is None or session is None:
            return
        if not _leader_running(proc.pid, leader_start, pgid):
            return
        members, ok = _scan_group(pgid, session, leader_start, latest_start=None)
        if not ok:
            return
        for member in members:
            known[int(member["pid"])] = int(member["starttime"])

    stop: dict[str, Any] | None = None
    try:
        os.set_blocking(proc.stdin.fileno(), False)
        while True:
            remember_group()
            if offset < len(payload):
                try:
                    wrote = os.write(proc.stdin.fileno(), payload[offset:])
                except BlockingIOError:
                    wrote = 0
                except BrokenPipeError:
                    write_error = "broken_pipe"
                    offset = len(payload)
                    close_stdin()
                except OSError as exc:
                    write_error = exc.__class__.__name__
                    close_stdin()
                    break
                else:
                    offset += wrote
                    if offset >= len(payload):
                        close_stdin()
            if proc.poll() is not None:
                if offset < len(payload):
                    write_error = write_error or "early_exit"
                    close_stdin()
                reason = "completed"
                break
            if cancel_flag.is_file():
                reason = "cancelled"
                break
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            time.sleep(0.05)
    finally:
        close_stdin()
        if stop is None:
            remember_group()
            stop = _reap_group(
                proc,
                grace=config.grace_seconds,
                leader_start=leader_start,
                pgid=pgid,
                session=session,
                known=known,
            )
            stdout_drain.join(timeout=2)
            stderr_drain.join(timeout=2)
            if stdout_drain.is_alive() or stderr_drain.is_alive():
                stop["cleanup"] = "cleanup_incomplete"
                stop["group_confirmed"] = False
    assert stop is not None
    elapsed = round(time.monotonic() - started, 3)
    return {
        "reason": reason,
        "started_at": started_at,
        "ended_at": _now(),
        "elapsed_seconds": elapsed,
        "returncode": stop["returncode"],
        "signals": stop["signals"],
        "cleanup": stop["cleanup"],
        "alive": stop["alive"],
        "pid": proc.pid,
        "pgid": stop.get("pgid"),
        "leader_starttime": stop.get("leader_starttime"),
        "group_confirmed": stop.get("group_confirmed"),
        "live_pids": stop.get("live_pids", []),
        "zombie_pids": stop.get("zombie_pids", []),
        "unidentified_pids": stop.get("unidentified_pids", []),
        "group_scan": stop.get("group_scan"),
        "write_error": write_error,
        "stdin_bytes_written": offset,
        "stdin_bytes_total": len(payload),
        "stdout": bytes(stdout_drain.kept),
        "stderr": bytes(stderr_drain.kept),
        "stdout_total": stdout_drain.total,
        "stderr_total": stderr_drain.total,
        "stdout_discarded": stdout_drain.discarded,
        "stderr_discarded": stderr_drain.discarded,
        "merges_dir": str(merges),
        "charts_dir": str(charts),
    }


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _structural_check(safety: Any) -> tuple[str, str]:
    """검증 정보가 없으면 unknown이다. 있는데 타입이 틀리면 프로토콜 실패다."""
    if not isinstance(safety, dict) or "validation" not in safety:
        return "unknown", ""
    validation = safety.get("validation")
    if not isinstance(validation, dict):
        return "", "invalid_structural_validation"
    if "structural" not in validation:
        return "unknown", ""
    structural = validation.get("structural")
    if not isinstance(structural, dict):
        return "", "invalid_structural_validation"
    state = structural.get("state")
    if state not in {"not_run", "passed", "failed"}:
        return "", "invalid_structural_validation"
    if "scope" in structural and not isinstance(structural.get("scope"), str):
        return "", "invalid_structural_validation"
    if "covers_semantic_correctness" in structural and not isinstance(
        structural.get("covers_semantic_correctness"), bool
    ):
        return "", "invalid_structural_validation"
    return str(state), ""


def _structural_state(safety: Any) -> str:
    state, problem = _structural_check(safety)
    if problem:
        return "unknown"
    return state


def _warnings_ok(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not all(isinstance(item.get(key), str) for key in ("code", "message", "stage")):
            return False
    return True


def _artifacts_shape_ok(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("path"), str) or not isinstance(item.get("kind"), str):
            return False
        if not isinstance(item.get("sha256"), str) or not _non_negative_int(item.get("size_bytes")):
            return False
    return True


def _error_shape_ok(status: str, parsed: dict[str, Any]) -> bool:
    error = parsed.get("error", None)
    if status == "success":
        return error is None
    if not isinstance(error, dict):
        return False
    if not all(isinstance(error.get(key), str) for key in ("code", "message", "stage")):
        return False
    if not isinstance(error.get("retryable"), bool):
        return False
    if "exception_type" in error and error.get("exception_type") is not None and not isinstance(
        error.get("exception_type"), str
    ):
        return False
    return True


def _parse_engine(stdout: bytes, *, discarded: int, request_id: str) -> tuple[dict[str, Any] | None, str]:
    if discarded > 0:
        return None, "output_truncated"
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None, "malformed_engine_output"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "malformed_engine_output"
    if not isinstance(parsed, dict):
        return None, "malformed_engine_output"
    if parsed.get("contract_version") != CONTRACT_VERSION:
        return None, "contract_mismatch"
    if parsed.get("request_id") != request_id:
        return None, "request_id_mismatch"
    status = parsed.get("status")
    if not isinstance(status, str) or status not in SUPPORTED_STATUSES:
        return None, "invalid_status"
    required = ("text", "data", "artifacts", "warnings", "safety", "timing")
    for key in required:
        if key not in parsed:
            return None, "missing_field"
    if not isinstance(parsed.get("text"), str):
        return None, "invalid_field_type"
    if not _warnings_ok(parsed.get("warnings")) or not _artifacts_shape_ok(parsed.get("artifacts")):
        return None, "invalid_field_type"
    data = parsed.get("data")
    if not isinstance(data, dict):
        return None, "invalid_field_type"
    shape = data.get("shape")
    columns = data.get("columns")
    preview = data.get("preview_records")
    if not isinstance(shape, list) or len(shape) != 2 or not all(_non_negative_int(item) for item in shape):
        return None, "invalid_field_type"
    if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
        return None, "invalid_field_type"
    if not isinstance(preview, list) or not all(isinstance(item, dict) for item in preview):
        return None, "invalid_field_type"
    timing = parsed.get("timing")
    if not isinstance(timing, dict) or not _non_negative_int(timing.get("elapsed_ms")):
        return None, "invalid_field_type"
    safety = parsed.get("safety")
    if not isinstance(safety, dict) or not isinstance(safety.get("unsafe_output_blocked"), bool):
        return None, "missing_safety_flag" if not isinstance(safety, dict) else "invalid_field_type"
    if "validation_status" in safety and not isinstance(safety.get("validation_status"), str):
        return None, "invalid_field_type"
    _state, structural_problem = _structural_check(safety)
    if structural_problem:
        return None, structural_problem
    if not _error_shape_ok(status, parsed):
        return None, "invalid_field_type"
    return parsed, ""


def _check_artifacts(engine: dict[str, Any], execution_dir: Path) -> tuple[list[dict[str, Any]], str]:
    checked: list[dict[str, Any]] = []
    for artifact in engine["artifacts"]:
        if not isinstance(artifact, dict):
            return [], "invalid_artifact"
        declared_size = artifact.get("size_bytes")
        declared_hash = artifact.get("sha256")
        path, path_problem = locate_output_file(Path(str(artifact.get("path") or "")), execution_dir)
        if path is None:
            return [], path_problem or "invalid_artifact"
        actual_hash = sha256_file(path)
        try:
            actual_size = path.stat().st_size
        except OSError:
            return [], "artifact_missing"
        if actual_hash is None:
            return [], "artifact_missing"
        if not _non_negative_int(declared_size) or actual_size != declared_size:
            return [], "artifact_size_mismatch"
        if not isinstance(declared_hash, str) or actual_hash != declared_hash:
            return [], "artifact_hash_mismatch"
        checked.append(
            {
                "kind": artifact.get("kind"),
                "filename": path.name,
                "source_path": str(path),
                "size_bytes": actual_size,
                "sha256": actual_hash,
            }
        )
    return checked, ""


def _observe_hash(path: Path, before: str) -> tuple[str | None, str]:
    try:
        info = os.lstat(path)
    except OSError:
        return None, "missing"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, "unreadable"
    digest = sha256_file(path)
    if digest is None:
        return None, "unreadable"
    if digest == before:
        return digest, "unchanged"
    return digest, "changed"


def _input_integrity(inputs: list[PreparedInput], *, cleanup_complete: bool) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in inputs:
        source_after, source_state = _observe_hash(item.source, item.source_sha256)
        copy_after, copy_state = _observe_hash(item.copy_path, item.copy_sha256)
        items.append(
            {
                "source": str(item.source),
                "copy": str(item.copy_path),
                "source_before": item.source_sha256,
                "source_after": source_after,
                "source_state": source_state,
                "copy_before": item.copy_sha256,
                "copy_after": copy_after,
                "copy_state": copy_state,
            }
        )
    states = [row["source_state"] for row in items] + [row["copy_state"] for row in items]
    if not cleanup_complete:
        for row in items:
            if row["source_state"] == "unchanged":
                row["source_state"] = "unconfirmed"
            if row["copy_state"] == "unchanged":
                row["copy_state"] = "unconfirmed"
        overall = "changed" if any(state == "changed" for state in states) else "unconfirmed"
        authoritative = False
    elif any(state == "changed" for state in states):
        overall = "changed"
        authoritative = False
    elif not items or any(state in {"missing", "unreadable"} for state in states):
        overall = "unconfirmed"
        authoritative = False
    elif all(state == "unchanged" for state in states):
        overall = "unchanged"
        authoritative = True
    else:
        overall = "unconfirmed"
        authoritative = False
    return {
        "state": overall,
        "authoritative": authoritative,
        "items": items,
    }


def _stage_publish_file(dest: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(dest, flags, 0o644)
    try:
        view = memoryview(payload)
        while view:
            wrote = os.write(fd, view)
            view = view[wrote:]
    finally:
        os.close(fd)


def _rename_publish(source: Path, dest: Path) -> None:
    os.rename(source, dest)


def _discard_own_tree(path: Path, root: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return
    if real_path(path) == real_path(root) or not is_inside(path, root):
        return
    name = path.name
    own = name.endswith(".partial") or (len(name) == 32 and all(ch in "0123456789abcdef" for ch in name))
    if not own:
        return
    shutil.rmtree(path)


def _publish(
    checked: list[dict[str, Any]],
    *,
    config: OperatorConfig,
    execution_id: str,
    inputs: list[PreparedInput],
) -> tuple[list[dict[str, Any]], str]:
    if not checked:
        return [], ""
    names = [str(item["filename"]) for item in checked]
    if len(names) != len(set(names)):
        raise PathRejected("duplicate_artifact_name", "같은 파일 이름의 산출물은 공개하지 않는다.")
    final_dir = config.publish_root / execution_id
    temp_dir = config.publish_root / f".{execution_id}.partial"
    if final_dir.exists() or temp_dir.exists():
        raise PathRejected("publish_exists", "이미 같은 실행의 출력 경로가 있다.")
    temp_dir = ensure_directory(temp_dir, config.publish_root)
    renamed = False
    staged: list[dict[str, Any]] = []
    try:
        for item in checked:
            dest = temp_dir / str(item["filename"])
            parent = real_path(dest.parent)
            if parent != real_path(temp_dir):
                raise PathRejected("outside_root", "공개 경로가 허용 루트 밖에 있다.")
            if conflicts_with_inputs(dest, inputs) or not is_inside(dest, temp_dir):
                raise PathRejected("output_overwrites_input", "출력이 원본 입력을 덮어쓴다.")
            payload = Path(item["source_path"]).read_bytes()
            try:
                _stage_publish_file(dest, payload)
            except FileExistsError as exc:
                raise PathRejected("duplicate_artifact_name", "같은 파일 이름의 산출물은 공개하지 않는다.") from exc
            except OSError as exc:
                raise PathRejected("publish_failed", "산출물을 임시 경로에 쓰지 못했다.") from exc
            digest = sha256_file(dest)
            size = dest.stat().st_size
            if digest != item["sha256"] or size != item["size_bytes"]:
                raise PathRejected("publish_hash_mismatch", "공개 파일의 해시가 일치하지 않는다.")
            staged.append(
                {
                    "kind": item["kind"],
                    "filename": dest.name,
                    "size_bytes": size,
                    "sha256": digest,
                }
            )
        try:
            _rename_publish(temp_dir, final_dir)
        except OSError as exc:
            raise PathRejected("publish_failed", "산출물 디렉터리를 공개하지 못했다.") from exc
        renamed = True
        published: list[dict[str, Any]] = []
        for item in staged:
            final_path = final_dir / str(item["filename"])
            if not is_inside(final_path, final_dir):
                raise PathRejected("outside_root", "공개된 경로가 허용 루트 밖에 있다.")
            digest = sha256_file(final_path)
            try:
                size = final_path.stat().st_size
            except OSError as exc:
                raise PathRejected("publish_hash_mismatch", "공개 파일을 다시 확인하지 못했다.") from exc
            if digest != item["sha256"] or size != item["size_bytes"]:
                raise PathRejected("publish_hash_mismatch", "공개 파일의 해시가 일치하지 않는다.")
            published.append(
                {
                    "kind": item["kind"],
                    "filename": final_path.name,
                    "path": str(real_path(final_path)),
                    "size_bytes": size,
                    "sha256": digest,
                }
            )
        return published, ""
    except Exception:
        if renamed:
            _discard_own_tree(final_dir, config.publish_root)
        else:
            _discard_own_tree(temp_dir, config.publish_root)
        raise


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _engine_request(
    payload: dict[str, Any],
    inputs: list[PreparedInput],
    config: OperatorConfig,
    execution_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    mode = payload.get("analysis_mode")
    if mode is None:
        mode = "single" if len(inputs) == 1 else "multi"
    if mode not in {"single", "multi"}:
        raise PathRejected("invalid_analysis_mode", "analysis_mode는 single 또는 multi여야 한다.")
    profile = str(payload.get("profile_name") or "generic")
    if profile not in config.allowed_profiles:
        raise PathRejected("profile_not_allowed", "프로필은 운영 설정에 등록된 이름만 쓸 수 있다.")
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": str(payload["request_id"]).strip(),
        "operation": "analyze",
        "inputs": [
            {
                "source_id": f"input-{index}",
                "path": str(item.copy_path),
                "sheet": item.sheet,
                "display_name": item.display_name,
            }
            for index, item in enumerate(inputs, start=1)
        ],
        "user_prompt": str(payload["user_prompt"]).strip(),
        "analysis_mode": mode,
        "profile_name": profile,
        "model": {"base_url": config.model_base_url, "name": config.model_name},
        "timeout_seconds": timeout_seconds,
        "output_directory": str(execution_dir / "engine_out"),
    }


def _read_engine_request(payload: dict[str, Any], inputs: list[PreparedInput]) -> dict[str, Any]:
    """모델, user_prompt, 출력 경로를 넣지 않는다. 경로는 작업 사본뿐이다."""
    item = inputs[0]
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": str(payload["request_id"]).strip(),
        "operation": READ_RANGE_OPERATION,
        "inputs": [
            {
                "path": str(item.copy_path),
                "sheet": item.sheet,
                "display_name": item.display_name,
            }
        ],
        "read": {"address": payload["read"]["address"]},
    }


def _table_engine_request(payload: dict[str, Any], inputs: list[PreparedInput]) -> dict[str, Any]:
    """모델, user_prompt, 출력 경로, 분석 모드, 프로필을 넣지 않는다. header는 요청에 있을 때만 옮긴다."""
    item = inputs[0]
    read: dict[str, Any] = {"address": payload["read"]["address"]}
    if "header" in payload["read"]:
        read["header"] = payload["read"]["header"]
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": str(payload["request_id"]).strip(),
        "operation": READ_TABLE_OPERATION,
        "inputs": [
            {
                "path": str(item.copy_path),
                "sheet": item.sheet,
                "display_name": item.display_name,
            }
        ],
        "read": read,
    }


@dataclass
class _JobTrack:
    inputs: list[PreparedInput]
    execution_dir: Path | None = None


def run_job(
    config: OperatorConfig,
    payload: Any,
    *,
    launch: ProcessLaunch | None = None,
) -> tuple[dict[str, Any], int]:
    if sys.platform != "linux":
        return _unsupported()
    track = _JobTrack(inputs=[])
    response, code = _run_job(config, payload, launch=launch, track=track)
    operation = _read_operation(payload)
    if operation is None:
        return response, code
    return _read_public(response, code, track=track, payload=payload, config=config, operation=operation)


def _run_job(
    config: OperatorConfig,
    payload: Any,
    *,
    launch: ProcessLaunch | None,
    track: _JobTrack,
) -> tuple[dict[str, Any], int]:
    request_id = ""
    lock_fd = -1
    execution_id: str | None = None
    execution_dir: Path | None = None
    inputs: list[PreparedInput] = track.inputs
    prepare_started = False
    try:
        request = _parse_request(payload)
        operation = _request_operation(request)
        request_id = str(request["request_id"]).strip()
        timeout_seconds = _accepted_timeout(request, config)
        if launch is not None and (not launch.argv or any(not isinstance(part, str) for part in launch.argv)):
            return _reject(request_id, "invalid_launch", "내부 실행 인자가 올바르지 않다.", stage="launch")
        lock_fd, locked = _acquire_lock(config)
        if not locked:
            response, code = _reject(
                request_id,
                "wrapper_busy",
                "다른 래퍼 작업이 실행 중이다.",
                stage="lock",
                retryable=True,
                status="busy",
                exit_code=BUSY_EXIT,
            )
            return response, code
        execution_id = uuid.uuid4().hex
        execution_dir = ensure_directory(config.workspace_root / execution_id, config.workspace_root)
        track.execution_dir = execution_dir
        ensure_directory(execution_dir / "control", config.workspace_root)
        ensure_directory(execution_dir / "inputs", config.workspace_root)
        ensure_directory(execution_dir / "evidence", config.workspace_root)
        duplicate = _note_duplicate(config, request_id, execution_id)
        prepare_started = True
        inputs = _prepare_inputs(request, config, execution_dir / "inputs", sink=inputs)
        if operation == READ_RANGE_OPERATION:
            engine_request = _read_engine_request(request, inputs)
        elif operation == READ_TABLE_OPERATION:
            engine_request = _table_engine_request(request, inputs)
        else:
            engine_request = _engine_request(request, inputs, config, execution_dir, timeout_seconds)
        identity = source_identity(config.repo_cwd)
        evidence = execution_dir / "evidence"
        _write_json(evidence / "identity.json", identity)
        _write_json(evidence / "wrapper_request.json", request)
        _write_json(evidence / "engine_request.json", engine_request)
        argv = list(launch.argv) if launch is not None else [str(config.python_path), "-m", "core.application.cli"]
        observed = _run_process(
            config=config,
            argv=argv,
            engine_request=engine_request,
            execution_dir=execution_dir,
            timeout_seconds=timeout_seconds,
        )
        (evidence / "engine_stdout.txt").write_bytes(observed["stdout"])
        (evidence / "engine_stderr.txt").write_bytes(observed["stderr"])
        public, exit_code = _finalize(
            request_id=request_id,
            execution_id=execution_id,
            execution_dir=execution_dir,
            config=config,
            inputs=inputs,
            observed=observed,
            duplicate=duplicate,
            identity=identity,
            timeout_seconds=timeout_seconds,
            operation=operation,
        )
        _write_json(evidence / "wrapper_response.json", public)
        uses_model = operation == ANALYZE_OPERATION
        _write_json(
            evidence / "execution_meta.json",
            {
                "execution_id": execution_id,
                "request_id": request_id,
                "operation": operation,
                "model_base_url": config.model_base_url if uses_model else None,
                "model_name": config.model_name if uses_model else None,
                "profile_name": engine_request.get("profile_name"),
                "timeout_seconds": timeout_seconds,
                "timeout_policy": "reject_above_maximum",
                "engine_timeout_enforced_by_engine": False,
                "started_at": observed["started_at"],
                "ended_at": observed["ended_at"],
                "elapsed_seconds": observed["elapsed_seconds"],
                "returncode": observed["returncode"],
                "child_returncode": observed["returncode"],
                "wrapper_exit_code": exit_code,
                "stop_reason": observed["reason"],
                "signals": observed["signals"],
                "cleanup": observed["cleanup"],
                "pid": observed["pid"],
                "pgid": observed.get("pgid"),
                "leader_starttime": observed.get("leader_starttime"),
                "group_confirmed": observed.get("group_confirmed"),
                "live_pids": observed.get("live_pids"),
                "zombie_pids": observed.get("zombie_pids"),
                "write_error": observed.get("write_error"),
                "stdin_bytes_written": observed.get("stdin_bytes_written"),
                "stdin_bytes_total": observed.get("stdin_bytes_total"),
                "input_hashes_before": {str(item.source): item.source_sha256 for item in inputs},
                "copy_hashes_before": {str(item.copy_path): item.copy_sha256 for item in inputs},
                "input_integrity": public.get("input_integrity"),
                "stdout_total": observed["stdout_total"],
                "stdout_discarded": observed["stdout_discarded"],
                "stderr_total": observed["stderr_total"],
                "stderr_discarded": observed["stderr_discarded"],
                "merges_dir": observed["merges_dir"],
                "charts_dir": observed["charts_dir"],
                "git_head": identity.get("git_head"),
                "diff_sha256": identity.get("diff_sha256"),
                "source": "direct",
            },
        )
        return public, exit_code
    except PathRejected as exc:
        response, code = _reject(request_id, exc.code, exc.message, stage="request")
        if execution_id is not None:
            response["execution_id"] = execution_id
        if prepare_started and execution_dir is not None and (execution_dir / "evidence").is_dir():
            partial = _input_integrity(inputs, cleanup_complete=True)
            response["input_integrity"] = partial
            response["wrapper_exit_code"] = code
            _write_json(execution_dir / "evidence" / "input_integrity.json", partial)
        if execution_dir is not None and (execution_dir / "evidence").is_dir():
            _write_json(execution_dir / "evidence" / "wrapper_response.json", response)
        return response, code
    except Exception as exc:
        response, code = _reject(
            request_id,
            "wrapper_exception",
            exc.__class__.__name__,
            stage="wrapper",
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
        )
        return response, code
    finally:
        _release_lock(lock_fd)


def _finalize(
    *,
    request_id: str,
    execution_id: str,
    execution_dir: Path,
    config: OperatorConfig,
    inputs: list[PreparedInput],
    observed: dict[str, Any],
    duplicate: bool,
    identity: dict[str, Any],
    timeout_seconds: float,
    operation: str = ANALYZE_OPERATION,
) -> tuple[dict[str, Any], int]:
    cleanup = str(observed["cleanup"])
    integrity = _input_integrity(inputs, cleanup_complete=cleanup == "complete")
    _write_json(execution_dir / "evidence" / "input_integrity.json", integrity)
    base_extra = {
        "duplicate_request_id": duplicate,
        "child_returncode": observed.get("returncode"),
        "input_integrity": integrity,
        "cleanup": {
            "state": cleanup,
            "signals": observed["signals"],
            "pid": observed["pid"],
            "pgid": observed.get("pgid"),
            "alive_after_stop": observed["alive"],
            "child_returncode": observed.get("returncode"),
            "group_confirmed": observed.get("group_confirmed"),
            "live_pids": list(observed.get("live_pids") or []),
            "zombie_pids": list(observed.get("zombie_pids") or []),
            "unidentified_pids": list(observed.get("unidentified_pids") or []),
            "group_scan": observed.get("group_scan"),
            "leader_starttime": observed.get("leader_starttime"),
        },
        "timing": {
            "started_at": observed["started_at"],
            "ended_at": observed["ended_at"],
            "elapsed_seconds": observed["elapsed_seconds"],
            "timeout_seconds": timeout_seconds,
        },
        "source_identity": {
            "git_head": identity.get("git_head"),
            "diff_sha256": identity.get("diff_sha256"),
            "source": identity.get("source"),
        },
    }

    def emit(response: dict[str, Any], code: int) -> tuple[dict[str, Any], int]:
        response["child_returncode"] = observed.get("returncode")
        response["wrapper_exit_code"] = code
        response["input_integrity"] = integrity
        return response, code

    if cleanup != "complete":
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            message="프로세스 정리를 확인하지 못했다.",
            code="cleanup_incomplete",
            stage="process",
            retryable=True,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    if observed["reason"] == "timeout":
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="timeout",
            exit_code=CLI_EXIT_CODES["timeout"],
            message="래퍼 실행 시간 제한으로 프로세스를 종료했다.",
            code="wrapper_timeout",
            stage="process",
            retryable=True,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["timeout"])
    if observed["reason"] == "cancelled":
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="cancelled",
            exit_code=CLI_EXIT_CODES["cancelled"],
            message="로컬 취소 요청으로 프로세스를 종료했다.",
            code="wrapper_cancelled",
            stage="process",
            retryable=False,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["cancelled"])
    if integrity["state"] == "changed" or not integrity["authoritative"]:
        changed = integrity["state"] == "changed"
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            message="원본 또는 작업 사본이 실행 중에 바뀌었다." if changed else "원본 또는 작업 사본의 불변을 확인하지 못했다.",
            code="input_changed" if changed else "input_unconfirmed",
            stage="input_integrity",
            retryable=False,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    if operation in _READ_OPERATIONS:
        response, code = _read_outcome(
            observed,
            request_id=request_id,
            execution_id=execution_id,
            execution_dir=execution_dir,
            display_name=inputs[0].display_name,
            cleanup=cleanup,
            extra=base_extra,
            operation=operation,
        )
        return emit(response, code)
    engine, problem = _parse_engine(
        observed["stdout"],
        discarded=int(observed["stdout_discarded"]),
        request_id=request_id,
    )
    if engine is None:
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            message="엔진 응답을 검증하지 못했다.",
            code=problem,
            stage="engine_output",
            retryable=False,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    (execution_dir / "evidence" / "engine_response.json").write_text(
        json.dumps(engine, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    status = str(engine["status"])
    expected = CLI_EXIT_CODES.get(status)
    if expected is None or observed["returncode"] != expected:
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            message="응답 상태와 프로세스 종료 코드가 일치하지 않는다.",
            code="status_exit_mismatch",
            stage="engine_output",
            retryable=False,
            structural_state=_structural_state(engine.get("safety")),
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    structural = _structural_state(engine.get("safety"))
    if engine["safety"].get("unsafe_output_blocked") is True or (status in _SUCCESS_STATUSES and structural == "failed"):
        response = _failure_public(
            engine,
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            code="unsafe_or_inconsistent_validation",
            message="성공 산출물로 공개하지 않는다.",
            stage="verification",
            structural=structural,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    if status not in _SUCCESS_STATUSES:
        error = engine.get("error") if isinstance(engine.get("error"), dict) else {}
        response = _failure_public(
            engine,
            request_id=request_id,
            execution_id=execution_id,
            status=status,
            exit_code=expected,
            code=str(error.get("code") or status),
            message=str(error.get("message") or engine.get("text") or status),
            stage=str(error.get("stage") or "engine"),
            retryable=bool(error.get("retryable")),
            structural=structural,
            cleanup=cleanup,
            extra=base_extra,
            warnings=engine.get("warnings") if isinstance(engine.get("warnings"), list) else [],
        )
        return emit(response, expected)
    checked, artifact_problem = _check_artifacts(engine, execution_dir)
    if artifact_problem:
        response = _failure_public(
            engine,
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            code=artifact_problem,
            message="산출물 검증에 실패했다.",
            stage="artifact",
            structural=structural,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    try:
        published, _publish_problem = _publish(
            checked,
            config=config,
            execution_id=execution_id,
            inputs=inputs,
        )
    except PathRejected as exc:
        response = _failure_public(
            engine,
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=CLI_EXIT_CODES["execution_failed"],
            code=exc.code,
            message=exc.message,
            stage="publish",
            structural=structural,
            cleanup=cleanup,
            extra=base_extra,
        )
        return emit(response, CLI_EXIT_CODES["execution_failed"])
    data = engine.get("data") if isinstance(engine.get("data"), dict) else {}
    response = _response(
        request_id=request_id,
        execution_id=execution_id,
        status="success",
        exit_code=0,
        structural_state=structural,
        cleanup=cleanup,
        extra=base_extra,
    )
    response["text"] = engine["text"]
    response["data"] = {
        "shape": list(data["shape"]),
        "columns": list(data["columns"]),
        "preview_records": list(data["preview_records"]),
    }
    response["artifacts"] = published
    response["warnings"] = list(engine["warnings"])
    response["error"] = None
    return emit(response, 0)


def _failure_public(
    engine: dict[str, Any],
    *,
    request_id: str,
    execution_id: str,
    status: str,
    exit_code: int,
    code: str,
    message: str,
    stage: str,
    structural: str,
    cleanup: str,
    extra: dict[str, Any],
    warnings: list[Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    del engine
    response = _response(
        request_id=request_id,
        execution_id=execution_id,
        status=status,
        exit_code=exit_code,
        message=message,
        code=code,
        stage=stage,
        retryable=retryable,
        structural_state=structural,
        cleanup=cleanup,
        extra=extra,
    )
    response["artifacts"] = []
    response["data"] = {"shape": [0, 0], "columns": [], "preview_records": []}
    response["warnings"] = list(warnings or [])
    return response


def _positive_int(value: Any) -> bool:
    return _non_negative_int(value) and value > 0


def _read_cell_ok(cell: Any) -> bool:
    if not isinstance(cell, dict) or set(cell) != _READ_CELL_KEYS:
        return False
    row, column = cell["row"], cell["column_index"]
    if not _positive_int(row) or not _positive_int(column) or column > 16_384:
        return False
    letter = get_column_letter(column)
    if cell["column_letter"] != letter or cell["a1"] != f"{letter}{row}":
        return False
    if cell["value_type"] not in _READ_VALUE_TYPES or cell["cached_value_state"] not in _READ_CACHED_STATES:
        return False
    formula = cell["formula"]
    if formula is not None and not (isinstance(formula, str) and formula.startswith("=")):
        return False
    merge = cell["merge"]
    if merge is not None:
        if not isinstance(merge, dict) or set(merge) != {"role", "anchor", "range"}:
            return False
        if merge["role"] not in {"anchor", "covered"}:
            return False
        if not isinstance(merge["anchor"], str) or not isinstance(merge["range"], str):
            return False
    return True


def _read_cells_ok(parsed: dict[str, Any]) -> bool:
    """셀 수, 행 우선 순서, 직사각형 전체가 주소와 맞는지 본다. 일부만 온 결과는 받지 않는다."""
    requested = parsed.get("requested")
    returned = parsed.get("returned")
    cells = parsed.get("cells")
    if not isinstance(requested, dict) or set(requested) != {"address"} or not isinstance(requested["address"], str):
        return False
    if not isinstance(returned, dict) or set(returned) != {"address", "cell_count"}:
        return False
    if returned["address"] != requested["address"] or not _positive_int(returned["cell_count"]):
        return False
    if not isinstance(cells, list) or not cells or len(cells) > MAX_CELLS or len(cells) != returned["cell_count"]:
        return False
    if not all(_read_cell_ok(cell) for cell in cells):
        return False
    positions = [(cell["row"], cell["column_index"]) for cell in cells]
    top, left = positions[0]
    bottom, right = positions[-1]
    if bottom < top or right < left or (bottom - top + 1) * (right - left + 1) != len(cells):
        return False
    expected = [(row, column) for row in range(top, bottom + 1) for column in range(left, right + 1)]
    if positions != expected:
        return False
    parts = returned["address"].split(":")
    if len(parts) > 2 or parts[0] != cells[0]["a1"] or parts[-1] != cells[-1]["a1"]:
        return False
    return True


def _parse_read_engine(
    stdout: bytes,
    *,
    discarded: int,
    request_id: str,
    display_name: str,
) -> tuple[dict[str, Any] | None, str]:
    """read_range 전용 응답 검사. analyze의 _parse_engine과 따로 둔다."""
    if discarded > 0:
        return None, "output_truncated"
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None, "malformed_engine_output"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "malformed_engine_output"
    if not isinstance(parsed, dict):
        return None, "malformed_engine_output"
    if parsed.get("contract_version") != CONTRACT_VERSION:
        return None, "contract_mismatch"
    if parsed.get("request_id") != request_id:
        return None, "request_id_mismatch"
    if parsed.get("operation") != READ_RANGE_OPERATION:
        return None, "operation_mismatch"
    status = parsed.get("status")
    if not isinstance(status, str) or status not in _READ_STATUSES:
        return None, "invalid_status"
    if parsed.get("recalculated") is not False:
        return None, "invalid_field_type"
    if not _error_shape_ok(status, parsed):
        return None, "invalid_field_type"
    if status != "success":
        if any(key in parsed for key in _READ_SUCCESS_KEYS):
            return None, "invalid_field_type"
        return parsed, ""
    if parsed.get("values_from") != "stored_workbook":
        return None, "invalid_field_type"
    source = parsed.get("source")
    if not isinstance(source, dict) or set(source) != {"display_name", "sheet_name", "sheet_index"}:
        return None, "invalid_field_type"
    if source["display_name"] != display_name:
        return None, "display_name_mismatch"
    if not isinstance(source["sheet_name"], str) or not _non_negative_int(source["sheet_index"]):
        return None, "invalid_field_type"
    if not _read_cells_ok(parsed):
        return None, "invalid_field_type"
    return parsed, ""


def _str_in(value: Any, allowed: set[str]) -> bool:
    """배열이나 객체 값으로 집합 검사가 TypeError를 내지 않게 문자열부터 확인한다."""
    return isinstance(value, str) and value in allowed


def _merge_shape_ok(merge: Any) -> bool:
    if not isinstance(merge, dict) or set(merge) != {"role", "anchor", "range"}:
        return False
    return (
        _str_in(merge["role"], {"anchor", "covered"})
        and isinstance(merge["anchor"], str)
        and isinstance(merge["range"], str)
    )


def _formula_ok(formula: Any) -> bool:
    return formula is None or (isinstance(formula, str) and formula.startswith("="))


def _table_header_rows(requested: Any) -> list[int] | None:
    if not isinstance(requested, dict) or set(requested) != {"address", "header"}:
        return None
    if not isinstance(requested["address"], str):
        return None
    header = requested["header"]
    if not isinstance(header, dict) or set(header) != {"rows"}:
        return None
    rows = header["rows"]
    if not isinstance(rows, list) or len(rows) > _MAX_TABLE_HEADER_ROWS or not all(_positive_int(row) for row in rows):
        return None
    if any(after != before + 1 for before, after in zip(rows, rows[1:])):
        return None
    return list(rows)


def _table_header_ok(header: Any, row: int, letter: str) -> bool:
    if not isinstance(header, dict) or not _TABLE_HEADER_KEYS <= set(header):
        return False
    extra = set(header) - _TABLE_HEADER_KEYS
    if extra and extra != _TABLE_HEADER_FORMULA_KEYS:
        return False
    if not _positive_int(header["row"]) or header["row"] != row or header["a1"] != f"{letter}{row}":
        return False
    if not _str_in(header["value_type"], _READ_VALUE_TYPES):
        return False
    if extra and (header["formula"] is None or not _formula_ok(header["formula"])):
        return False
    if extra and not _str_in(header["cached_value_state"], _READ_CACHED_STATES):
        return False
    return header["merge"] is None or _merge_shape_ok(header["merge"])


def _table_cell_ok(cell: Any, row: int, letter: str, key: str) -> bool:
    if not isinstance(cell, dict) or set(cell) - {"merge"} != _TABLE_CELL_KEYS:
        return False
    if cell["a1"] != f"{letter}{row}" or cell["key"] != key:
        return False
    if not _str_in(cell["value_type"], _READ_VALUE_TYPES) or not _str_in(cell["cached_value_state"], _READ_CACHED_STATES):
        return False
    if not _formula_ok(cell["formula"]):
        return False
    if cell["number_format"] is not None and not isinstance(cell["number_format"], str):
        return False
    return "merge" not in cell or _merge_shape_ok(cell["merge"])


def _table_ok(parsed: dict[str, Any]) -> bool:
    """열 설명, 헤더 칸, 데이터 행이 요청 직사각형 전체와 맞는지 본다. 일부만 온 표는 받지 않는다."""
    requested = parsed.get("requested")
    header_rows = _table_header_rows(requested)
    if header_rows is None:
        return False
    table = parsed.get("table")
    if not isinstance(table, dict) or set(table) != _TABLE_KEYS:
        return False
    if table["address"] != requested["address"]:
        return False
    if table["blank_rows"] != "kept" or table["blank_columns"] != "kept":
        return False
    columns, rows = table["columns"], table["rows"]
    if not isinstance(columns, list) or not columns or not isinstance(rows, list):
        return False
    letters: list[str] = []
    keys: list[str] = []
    for offset, column in enumerate(columns):
        if not isinstance(column, dict) or set(column) - {"display_derived"} != _TABLE_COLUMN_KEYS:
            return False
        if "display_derived" in column and column["display_derived"] is not True:
            return False
        index = column["column_index"]
        if not _positive_int(index) or index > 16_384:
            return False
        if offset and index != columns[0]["column_index"] + offset:
            return False
        letter = get_column_letter(index)
        if column["source_column"] != letter:
            return False
        if not isinstance(column["key"], str) or not column["key"]:
            return False
        if column["display_name"] is not None and not isinstance(column["display_name"], str):
            return False
        headers = column["headers"]
        if not isinstance(headers, list) or len(headers) != len(header_rows):
            return False
        if not all(_table_header_ok(header, row, letter) for header, row in zip(headers, header_rows)):
            return False
        letters.append(letter)
        keys.append(column["key"])
    if len(set(keys)) != len(keys):
        return False
    if header_rows:
        first_data = header_rows[-1] + 1
    elif rows and isinstance(rows[0], dict) and _positive_int(rows[0].get("source_row")):
        first_data = rows[0]["source_row"]
    else:
        return False
    for offset, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"source_row", "cells"}:
            return False
        source_row = first_data + offset
        if row["source_row"] != source_row:
            return False
        cells = row["cells"]
        if not isinstance(cells, list) or len(cells) != len(columns):
            return False
        if not all(_table_cell_ok(cell, source_row, letter, key) for cell, letter, key in zip(cells, letters, keys)):
            return False
    top = header_rows[0] if header_rows else first_data
    bottom = first_data + len(rows) - 1
    left = columns[0]["column_index"]
    if (bottom - top + 1) * len(columns) > MAX_CELLS:
        return False
    parts = table["address"].split(":")
    if len(parts) > 2:
        return False
    return parts[0] == f"{letters[0]}{top}" and parts[-1] == f"{get_column_letter(left + len(columns) - 1)}{bottom}"


def _parse_table_engine(
    stdout: bytes,
    *,
    discarded: int,
    request_id: str,
    display_name: str,
) -> tuple[dict[str, Any] | None, str]:
    """read_table 전용 응답 검사. analyze의 _parse_engine, read_range의 _parse_read_engine과 따로 둔다."""
    if discarded > 0:
        return None, "output_truncated"
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None, "malformed_engine_output"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "malformed_engine_output"
    if not isinstance(parsed, dict):
        return None, "malformed_engine_output"
    if parsed.get("contract_version") != CONTRACT_VERSION:
        return None, "contract_mismatch"
    if parsed.get("request_id") != request_id:
        return None, "request_id_mismatch"
    if parsed.get("operation") != READ_TABLE_OPERATION:
        return None, "operation_mismatch"
    status = parsed.get("status")
    if not isinstance(status, str) or status not in _READ_STATUSES:
        return None, "invalid_status"
    if parsed.get("recalculated") is not False:
        return None, "invalid_field_type"
    if not _error_shape_ok(status, parsed):
        return None, "invalid_field_type"
    if status != "success":
        if any(key in parsed for key in _TABLE_SUCCESS_KEYS):
            return None, "invalid_field_type"
        return parsed, ""
    if parsed.get("values_from") != "stored_workbook" or parsed.get("freshness") != "unconfirmed":
        return None, "invalid_field_type"
    source = parsed.get("source")
    if not isinstance(source, dict) or set(source) != {"display_name", "sheet_name", "sheet_index"}:
        return None, "invalid_field_type"
    if source["display_name"] != display_name:
        return None, "display_name_mismatch"
    if not isinstance(source["sheet_name"], str) or not _non_negative_int(source["sheet_index"]):
        return None, "invalid_field_type"
    if not _table_ok(parsed):
        return None, "invalid_field_type"
    return parsed, ""


def _read_outcome(
    observed: dict[str, Any],
    *,
    request_id: str,
    execution_id: str,
    execution_dir: Path,
    display_name: str,
    cleanup: str,
    extra: dict[str, Any],
    operation: str = READ_RANGE_OPERATION,
) -> tuple[dict[str, Any], int]:
    parse = _parse_table_engine if operation == READ_TABLE_OPERATION else _parse_read_engine
    engine, problem = parse(
        observed["stdout"],
        discarded=int(observed["stdout_discarded"]),
        request_id=request_id,
        display_name=display_name,
    )
    if engine is None:
        failed = CLI_EXIT_CODES["execution_failed"]
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=failed,
            message="엔진 응답을 검증하지 못했다.",
            code=problem,
            stage="engine_output",
            retryable=False,
            cleanup=cleanup,
            extra=extra,
        )
        return response, failed
    _write_json(execution_dir / "evidence" / "engine_response.json", engine)
    status = str(engine["status"])
    expected = CLI_EXIT_CODES[status]
    if observed["returncode"] != expected:
        failed = CLI_EXIT_CODES["execution_failed"]
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status="execution_failed",
            exit_code=failed,
            message="응답 상태와 프로세스 종료 코드가 일치하지 않는다.",
            code="status_exit_mismatch",
            stage="engine_output",
            retryable=False,
            cleanup=cleanup,
            extra=extra,
        )
        return response, failed
    if status != "success":
        error = engine["error"]
        response = _response(
            request_id=request_id,
            execution_id=execution_id,
            status=status,
            exit_code=expected,
            message=str(error["message"]),
            code=str(error["code"]),
            stage=str(error["stage"]),
            retryable=bool(error["retryable"]),
            cleanup=cleanup,
            extra=extra,
        )
        return response, expected
    response = _response(
        request_id=request_id,
        execution_id=execution_id,
        status="success",
        exit_code=0,
        cleanup=cleanup,
        extra=extra,
    )
    if operation == READ_TABLE_OPERATION:
        table = engine["table"]
        response["text"] = (
            f"{table['address']} 표를 읽었다. 열 {len(table['columns'])}개, 데이터 행 {len(table['rows'])}개. "
            "수식은 계산하지 않았다."
        )
        success_keys: tuple[str, ...] = _TABLE_SUCCESS_KEYS
    else:
        returned = engine["returned"]
        response["text"] = f"{returned['address']} 범위 {returned['cell_count']}칸을 읽었다. 수식은 계산하지 않았다."
        success_keys = _READ_SUCCESS_KEYS
    for key in success_keys:
        response[key] = engine[key]
    response["error"] = None
    return response, 0


def _public_integrity(integrity: dict[str, Any], inputs: list[PreparedInput]) -> dict[str, Any]:
    """판정이 끝난 무결성 기록에서 경로만 뺀다. 상태와 authoritative는 그대로 옮긴다."""
    items = []
    for prepared, row in zip(inputs, integrity.get("items") or []):
        public_row: dict[str, Any] = {"display_name": prepared.display_name}
        for key in _PUBLIC_INTEGRITY_ITEM_KEYS:
            public_row[key] = row.get(key)
        items.append(public_row)
    return {
        "state": integrity.get("state"),
        "authoritative": integrity.get("authoritative"),
        "items": items,
    }


def _hidden_paths(track: _JobTrack, payload: Any, config: OperatorConfig) -> list[str]:
    hidden = {str(config.workspace_root)}
    if track.execution_dir is not None:
        hidden.add(str(track.execution_dir))
    for item in track.inputs:
        hidden.update({str(item.source), str(item.copy_path), str(item.copy_path.parent)})
    raw_inputs = payload.get("inputs") if isinstance(payload, dict) else None
    if isinstance(raw_inputs, list):
        for item in raw_inputs:
            raw = item.get("path") if isinstance(item, dict) else None
            if isinstance(raw, str) and raw.startswith("/"):
                hidden.update({raw, os.path.realpath(raw)})
    return sorted(path for path in hidden if path.startswith("/") and len(path) > 1)


def _read_view(
    response: dict[str, Any],
    inputs: list[PreparedInput],
    operation: str = READ_RANGE_OPERATION,
) -> dict[str, Any]:
    public = dict(response)
    public.pop("data", None)
    public["operation"] = operation
    public["recalculated"] = False
    public["structural_state"] = "not_applicable"
    public["artifacts"] = []
    integrity = public.get("input_integrity")
    if isinstance(integrity, dict):
        public["input_integrity"] = _public_integrity(integrity, inputs)
    return public


def _string_has_path(value: str, hidden: list[str]) -> bool:
    return any(path in value for path in hidden)


def _exposes_path(value: Any, hidden: list[str]) -> bool:
    if isinstance(value, dict):
        return any(_exposes_path(key, hidden) or _exposes_path(item, hidden) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_exposes_path(item, hidden) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return False
    text = value if isinstance(value, str) else str(value)
    if _string_has_path(text, hidden):
        return True
    return _string_has_path(json.dumps(text, ensure_ascii=False), hidden)


def _request_id_exposes(request_id: str, hidden: list[str]) -> bool:
    """경로를 포함하거나, '/'가 든 경로 조각이면 경로를 드러내는 값으로 본다."""
    if _string_has_path(request_id, hidden):
        return True
    return "/" in request_id and any(request_id in path for path in hidden)


def _strip_paths(value: Any, hidden: list[str]) -> Any:
    if isinstance(value, dict):
        return {key: _strip_paths(item, hidden) for key, item in value.items() if not _exposes_path(key, hidden)}
    if isinstance(value, (list, tuple)):
        return [_strip_paths(item, hidden) for item in value]
    if _exposes_path(value, hidden):
        return ""
    return value


def _public_exposes(public: dict[str, Any], hidden: list[str]) -> bool:
    text = json.dumps(public, ensure_ascii=False, default=str)
    if _string_has_path(text, hidden) or _exposes_path(public, hidden):
        return True
    return _request_id_exposes(str(public.get("request_id") or ""), hidden)


def _blocked_read(
    public: dict[str, Any],
    *,
    code: int,
    inputs: list[PreparedInput],
    carry: bool,
    operation: str = READ_RANGE_OPERATION,
) -> dict[str, Any]:
    blocked = _response(
        request_id=str(public.get("request_id") or "") if carry else "",
        execution_id=public.get("execution_id"),
        status="execution_failed",
        exit_code=code,
        message="공개 응답에 경로가 들어 있어 공개하지 않는다.",
        code="path_exposure_blocked",
        stage="publish",
        retryable=False,
    )
    if carry:
        for key in ("cleanup", "input_integrity", "duplicate_request_id", "child_returncode", "timing", "source_identity"):
            if key in public:
                blocked[key] = public[key]
    blocked["wrapper_exit_code"] = code
    return _read_view(blocked, inputs, operation)


def _withheld_read(code: int, operation: str = READ_RANGE_OPERATION) -> dict[str, Any]:
    """입력에서 온 값이 하나도 없는 최종 차단 응답."""
    return {
        "status": "execution_failed",
        "exit_code": code,
        "wrapper_exit_code": code,
        "operation": operation,
        "error": {
            "code": "path_exposure_blocked",
            "message": "공개 응답에 경로가 들어 있어 공개하지 않는다.",
            "stage": "publish",
            "retryable": False,
        },
    }


def _read_public(
    response: dict[str, Any],
    code: int,
    *,
    track: _JobTrack,
    payload: Any,
    config: OperatorConfig,
    operation: str = READ_RANGE_OPERATION,
) -> tuple[dict[str, Any], int]:
    """read_range·read_table 공개 응답 변환. 증거 파일의 경로 기록은 건드리지 않는다."""
    public = _read_view(response, track.inputs, operation)
    hidden = _hidden_paths(track, payload, config)
    if _public_exposes(public, hidden):
        code = CLI_EXIT_CODES["execution_failed"]
        public = _blocked_read(public, code=code, inputs=track.inputs, carry=True, operation=operation)
        if _request_id_exposes(str(public.get("request_id") or ""), hidden):
            public["request_id"] = ""
        fallbacks = [
            lambda current: {key: _strip_paths(item, hidden) for key, item in current.items()},
            lambda current: _blocked_read(current, code=code, inputs=[], carry=False, operation=operation),
            lambda _current: _withheld_read(code, operation),
        ]
        while fallbacks and _public_exposes(public, hidden):
            public = fallbacks.pop(0)(public)
        if _public_exposes(public, hidden):
            public = {}
    if track.execution_dir is not None and (track.execution_dir / "evidence").is_dir():
        _write_json(track.execution_dir / "evidence" / "wrapper_response.json", public)
    return public, code


def request_cancel(config: OperatorConfig, execution_id: str) -> dict[str, Any]:
    if sys.platform != "linux":
        return {"status": "execution_failed", "code": "unsupported_platform"}
    import re

    if not re.fullmatch(EXECUTION_ID_RE, execution_id):
        return {"status": "invalid_request", "code": "invalid_execution_id"}
    execution = config.workspace_root / execution_id
    if not execution.is_dir() or not is_inside(execution, config.workspace_root):
        return {"status": "invalid_request", "code": "unknown_execution"}
    control = execution / "control"
    control.mkdir(parents=True, exist_ok=True)
    flag = control / "cancel"
    if not is_inside(flag, config.workspace_root):
        return {"status": "invalid_request", "code": "outside_root"}
    flag.write_text("cancel\n", encoding="utf-8")
    return {"status": "cancel_requested", "execution_id": execution_id, "code": "cancel_requested"}
