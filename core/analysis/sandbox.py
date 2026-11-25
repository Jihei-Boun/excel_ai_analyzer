"""모델이 쓴 pandas 코드를 따로 떨어진 프로세스에서 실행한다.

코드에 어떤 함수가 쓰였는지는 보지 않는다. 대신 코드를 실행하기 직전에 그 프로세스에
운영체제 한도(setrlimit)를 건다. 한 번 걸면 프로세스 스스로 풀 수 없다.

- 새 파일을 열 수 없다 (열 수 있는 파일 수 = 이미 열린 수). 파일 읽기·쓰기와
  네트워크 연결이 모두 여기에 걸린다. 리눅스에서는 연결도 파일로 센다.
- 파일 크기 0: 이미 열린 파일에도 쓸 수 없다.
- 새 프로세스를 만들 수 없다.
- CPU 시간·메모리 한도. 넘으면 그 프로세스만 끝난다.

표 질문에 답하는 코드는 파일, 네트워크, 다른 프로그램을 쓸 일이 없다. 그래서 이 한도는
맞는 코드를 막지 않는다.

결과는 피클로 받지 않는다. 피클을 풀면 보낸 쪽이 고른 코드가 실행될 수 있어서,
값마다 자료형을 표시한 JSON으로만 주고받는다.

완전한 격리는 아니다. 결과를 돌려받는 통로는 열려 있고, 같은 사용자 권한으로 돈다.
"""

from __future__ import annotations

import datetime as dt
import errno
import json
import math
import multiprocessing as mp
import os
import resource
import traceback
from typing import Any

import numpy as np
import pandas as pd

TIMEOUT_SECONDS = 30
EXTRA_MEMORY_BYTES = 2 * 1024**3
MAX_RESULT_BYTES = 64 * 1024**2
CLOSED_NOTE = (
    "this step runs in a closed process: it cannot open files, import modules that are not "
    "already loaded, use the network or start programs"
)
_START_NAMES = {"__builtins__", "tables", "pd", "np"}

_ctx = mp.get_context("forkserver")
# 작업용 프로세스는 이 서버 프로세스에서 복제된다. pandas를 미리 올려 두면 단계마다 다시 읽지 않는다.
_ctx.set_forkserver_preload(["core.analysis.sandbox_preload", __name__])


class CodeError(Exception):
    """작업용 프로세스 안에서 난 오류. line은 모델 코드의 줄 번호(모르면 None)."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


# ---------------------------------------------------------------------------
# 표 <-> JSON. 값마다 자료형을 남겨서 문자열 "3"과 숫자 3이 섞인 열도 그대로 돌아온다.
# ---------------------------------------------------------------------------


def _enc(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        return {"ts": value.isoformat()}
    if isinstance(value, dt.date):
        return {"date": value.isoformat()}
    if isinstance(value, (pd.Timedelta, dt.timedelta)):
        return {"td": pd.Timedelta(value).total_seconds()}
    if isinstance(value, tuple):
        return {"tuple": [_enc(v) for v in value]}
    return str(value)


def _dec(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "ts" in value:
        return pd.Timestamp(value["ts"])
    if "date" in value:
        return dt.date.fromisoformat(value["date"])
    if "td" in value:
        return pd.Timedelta(value["td"], unit="s")
    if "tuple" in value:
        return tuple(_dec(v) for v in value["tuple"])
    raise ValueError(f"unknown value tag {sorted(value)}")


def _restore(values: list[Any], dtype: str) -> pd.Series:
    series = pd.Series(values, dtype=object)
    if dtype == "object":
        return series
    try:
        return series.astype(dtype)
    except (TypeError, ValueError):
        return series


def encode_frame(df: pd.DataFrame) -> bytes:
    def labels(index: pd.Index) -> dict[str, Any]:
        return {
            "values": [_enc(v) for v in index],
            "names": [_enc(n) for n in index.names],
            "dtype": str(index.dtype) if not isinstance(index, pd.MultiIndex) else "object",
        }

    payload = {
        "columns": labels(df.columns),
        "index": labels(df.index),
        "data": [[_enc(v) for v in df.iloc[:, i]] for i in range(df.shape[1])],
        "dtypes": [str(t) for t in df.dtypes],
    }
    return json.dumps(payload, ensure_ascii=False).encode()


def decode_frame(raw: bytes) -> pd.DataFrame:
    payload = json.loads(raw)

    def index_from(part: dict[str, Any]) -> pd.Index:
        values = [_dec(v) for v in part["values"]]
        names = [_dec(n) for n in part["names"]]
        if len(names) > 1:
            return pd.MultiIndex.from_tuples(values, names=names)
        return pd.Index(_restore(values, part["dtype"]), name=names[0] if names else None)

    columns = index_from(payload["columns"])
    data = [_restore([_dec(v) for v in col], t) for col, t in zip(payload["data"], payload["dtypes"])]
    frame = pd.concat(data, axis=1) if data else pd.DataFrame(index=range(len(payload["index"]["values"])))
    frame.columns = columns
    frame.index = index_from(payload["index"])
    return frame


# ---------------------------------------------------------------------------
# 작업용 프로세스
# ---------------------------------------------------------------------------


def _lock_down(cpu_seconds: int) -> None:
    """지금 열린 것 말고는 아무것도 새로 열거나 만들 수 없게 한다."""
    with open("/proc/self/statm") as fh:  # noqa: PTH123 — 한도를 걸기 전이라 열 수 있다
        used = int(fh.read().split()[0]) * resource.getpagesize()
    open_fds = {int(fd) for fd in os.listdir("/proc/self/fd")}
    open_fds = {fd for fd in open_fds if _fd_alive(fd)}  # listdir가 쓴 fd는 이미 닫혔다
    limit = max(open_fds) + 1
    # 중간에 빈 번호가 있으면 한도 안에서 새 파일을 열 수 있다. /dev/null(읽기 전용)로 채운다.
    for _ in range(limit - len(open_fds)):
        os.open(os.devnull, os.O_RDONLY)
    resource.setrlimit(resource.RLIMIT_NOFILE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (used + EXTRA_MEMORY_BYTES, used + EXTRA_MEMORY_BYTES))


def _fd_alive(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def _child(conn: Any, code: str, tables: dict[str, pd.DataFrame], cpu_seconds: int) -> None:
    # 한도를 걸기 전에 오류 보고에 쓸 모듈을 미리 읽어 둔다 (한도 뒤에는 파일을 못 연다).
    traceback.format_exception_only(ValueError, ValueError("warm up"))
    _lock_down(cpu_seconds)
    scope: dict[str, Any] = {"tables": tables, "pd": pd, "np": np}
    try:
        exec(compile(code, "<string>", "exec"), scope)  # noqa: S102 — 한도가 걸린 프로세스 안
        result = scope.get("result")
        if result is None:
            # result를 깜빡했으면 이 단계에서 새로 만든 표(마지막 것)를 결과로 받는다.
            made = [v for k, v in scope.items() if k not in _START_NAMES and isinstance(v, (pd.DataFrame, pd.Series))]
            result = made[-1] if made else None
        if isinstance(result, pd.Series):
            result = result.to_frame()
        if not isinstance(result, pd.DataFrame):
            raise ValueError("`result` must be a DataFrame")
        conn.send_bytes(b"ok" + encode_frame(result))
    except BaseException as exc:  # noqa: BLE001
        line = None
        for frame in traceback.extract_tb(exc.__traceback__):
            if frame.filename == "<string>":
                line = frame.lineno
        message = traceback.format_exception_only(type(exc), exc)[-1].strip()
        if isinstance(exc, OSError) and exc.errno in (errno.EMFILE, errno.EFBIG):
            # 오류 문장만으로는 원인이 보이지 않는다 (모델이 "시스템을 재시작하라"고 답한 적이 있다).
            message += f" ({CLOSED_NOTE})"
        conn.send_bytes(b"er" + json.dumps({"message": message, "line": line}).encode())


def run_code(
    code: str,
    tables: dict[str, pd.DataFrame],
    *,
    timeout: int = TIMEOUT_SECONDS,
) -> pd.DataFrame:
    """code를 실행하고 그 코드가 result에 넣은 표를 돌려준다. 실패하면 CodeError."""
    receive, send = _ctx.Pipe(duplex=False)
    proc = _ctx.Process(target=_child, args=(send, code, tables, timeout), daemon=True)
    proc.start()
    send.close()
    try:
        if not receive.poll(timeout):
            raise CodeError(f"the code did not finish within {timeout} seconds")
        try:
            raw = receive.recv_bytes(MAX_RESULT_BYTES)
        except EOFError:
            raise CodeError(f"the code was stopped: it used more than {timeout} seconds of CPU or too much memory") from None
        except OSError:
            raise CodeError(f"the result is larger than {MAX_RESULT_BYTES // 1024**2} MB") from None
    finally:
        receive.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)
    kind, body = raw[:2], raw[2:]
    if kind == b"ok":
        return decode_frame(body)
    info = json.loads(body)
    raise CodeError(str(info.get("message")), info.get("line") if isinstance(info.get("line"), int) else None)
