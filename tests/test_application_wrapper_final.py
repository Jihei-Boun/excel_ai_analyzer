"""DEV-2 최종 보완 회귀. 프로세스 검사는 만든 자식을 실패 시에도 정리한다."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.application.contracts import CLI_EXIT_CODES
from core.application.wrapper_config import ConfigError, _finite_number, load_operator_config
from core.application.wrapper_run import run_job
import core.application.wrapper_run as wrapper_run

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "dev2_final_helpers",
    Path(__file__).with_name("test_application_wrapper.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPERS)
_config = _HELPERS._config
_request = _HELPERS._request
_success_body = _HELPERS._success_body
_body_line = _HELPERS._body_line
_copy_fixture = _HELPERS._copy_fixture

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="DEV-2 래퍼는 Linux만 지원한다")


class _Alarm:
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds

    def __enter__(self) -> "_Alarm":
        def _raise(signum: int, frame: object) -> None:
            del signum, frame
            raise TimeoutError("실행이 제한 시간 안에 끝나지 않았다")

        signal.signal(signal.SIGALRM, _raise)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc: object) -> None:
        signal.alarm(0)


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _assert_dead(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.05)
    info = wrapper_run._read_proc(pid)
    raise AssertionError(f"pid {pid}가 남아 있다: {info}")


def _own_session_sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time\ntime.sleep(30)\n"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _fork_during_sigterm(pidfile: Path, body: dict[str, object]) -> str:
    encoded = json.dumps(json.dumps(body, ensure_ascii=False))
    return (
        "import json, os, signal, sys, time\n"
        "def handle(signum, frame):\n"
        "    child = os.fork()\n"
        "    if child == 0:\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        fd = os.open('/dev/null', os.O_RDWR)\n"
        "        os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)\n"
        f"        open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "        while True:\n"
        "            time.sleep(30)\n"
        "    while True:\n"
        "        time.sleep(30)\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    signal.signal(signal.SIGTERM, handle)\n"
        "    while True:\n"
        "        time.sleep(30)\n"
        "req = json.load(sys.stdin)\n"
        f"body = json.loads({encoded})\n"
        "body['request_id'] = req['request_id']\n"
        "print(json.dumps(body))\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(0)\n"
    )


def test_scan_distinguishes_unreadable_gone_and_later_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """함수 수준이다. 실제 프로세스 트리의 종단 재현이 아니다."""

    def listdir(path: str) -> list[str]:
        assert path == "/proc"
        return ["111", "222", "self"]

    monkeypatch.setattr(os, "listdir", listdir)

    def unreadable(pid: int) -> wrapper_run._ProcInspect:
        if pid == 111:
            return wrapper_run._ProcInspect("unreadable", "permission")
        return wrapper_run._ProcInspect(
            "ok",
            "ok",
            {"state": "S", "ppid": 1, "pgrp": 9, "session": 9, "starttime": 500},
        )

    monkeypatch.setattr(wrapper_run, "_inspect_proc", unreadable)
    members, ok = wrapper_run._scan_group(9, 9, 100, latest_start=400)
    assert ok is False
    assert [item["pid"] for item in members] == [222]
    assert wrapper_run._last_scan_status == "unreadable"

    def later(pid: int) -> wrapper_run._ProcInspect:
        del pid
        return wrapper_run._ProcInspect(
            "ok",
            "ok",
            {"state": "S", "ppid": 50, "pgrp": 9, "session": 9, "starttime": 500},
        )

    monkeypatch.setattr(wrapper_run, "_inspect_proc", later)
    members, ok = wrapper_run._scan_group(9, 9, 100, latest_start=400)
    assert ok is True
    assert {item["pid"] for item in members} == {111, 222}
    assert all(item["starttime"] == 500 for item in members)

    def vanished(pid: int) -> wrapper_run._ProcInspect:
        if pid == 111:
            return wrapper_run._ProcInspect("gone", "enoent")
        return wrapper_run._ProcInspect(
            "ok",
            "ok",
            {"state": "S", "ppid": 50, "pgrp": 9, "session": 9, "starttime": 500},
        )

    monkeypatch.setattr(wrapper_run, "_inspect_proc", vanished)
    members, ok = wrapper_run._scan_group(9, 9, 100, latest_start=400)
    assert ok is True
    assert [item["pid"] for item in members] == [222]
    assert wrapper_run._last_scan_status == "ok"

    def parsed(pid: int) -> wrapper_run._ProcInspect:
        if pid == 111:
            return wrapper_run._ProcInspect("unreadable", "parse")
        return wrapper_run._ProcInspect("gone", "enoent")

    monkeypatch.setattr(wrapper_run, "_inspect_proc", parsed)
    members, ok = wrapper_run._scan_group(9, 9, 100)
    assert members == []
    assert ok is False
    assert wrapper_run._last_scan_status == "unreadable"


def test_unidentified_ppid_is_not_signable(monkeypatch: pytest.MonkeyPatch) -> None:
    member = {"pid": 222, "starttime": 500, "ppid": 1, "pgrp": 9, "session": 9, "state": "S"}
    assert wrapper_run._signable_member(member, {111: 100}, pgid=9, session=9, leader_start=100) is False

    def parent(pid: int) -> wrapper_run._ProcInspect:
        assert pid == 111
        return wrapper_run._ProcInspect(
            "ok",
            "ok",
            {"state": "Z", "ppid": 1, "pgrp": 9, "session": 9, "starttime": 100},
        )

    monkeypatch.setattr(wrapper_run, "_inspect_proc", parent)
    child = {"pid": 222, "starttime": 500, "ppid": 111, "pgrp": 9, "session": 9, "state": "S"}
    assert wrapper_run._signable_member(child, {111: 100}, pgid=9, session=9, leader_start=100) is True


def test_living_member_survives_a_vanished_proc_entry() -> None:
    sleeper = _own_session_sleeper()
    try:
        with _Alarm(8):
            info = wrapper_run._read_proc(sleeper.pid)
            assert info is not None
            for _ in range(20):
                gone = subprocess.run(
                    [sys.executable, "-c", "import os; os._exit(0)"],
                    check=True,
                    capture_output=True,
                )
                assert gone.returncode == 0
                members, ok = wrapper_run._scan_group(info["pgrp"], info["session"], info["starttime"], latest_start=0)
                assert ok is True
                assert any(item["pid"] == sleeper.pid for item in members)
            dead = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
            dead.wait(timeout=2)
            found = wrapper_run._inspect_proc(dead.pid)
            assert found.kind == "gone"
            assert found.reason == "enoent"
            real_listdir = os.listdir

            def injected(path: str) -> list[str]:
                names = real_listdir(path)
                if path == "/proc":
                    names.append(str(dead.pid))
                return names

            os.listdir = injected  # type: ignore[assignment]
            try:
                members, ok = wrapper_run._scan_group(info["pgrp"], info["session"], info["starttime"])
            finally:
                os.listdir = real_listdir  # type: ignore[assignment]
            assert ok is True
            assert any(item["pid"] == sleeper.pid for item in members)
            assert _alive(sleeper.pid)
    finally:
        _kill(sleeper.pid)
        sleeper.wait(timeout=2)


def test_descendant_forked_during_sigterm_is_reaped(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.4, max_timeout_seconds=8, default_timeout_seconds=5)
    pidfile = tmp_path / "late-grand.pid"
    owned: list[int] = []
    response: dict[str, object] = {}
    body = _success_body("late-grand")
    try:
        with _Alarm(8):
            response, code = run_job(
                config,
                _request(sales, request_id="late-grand", timeout_seconds=5),
                launch=wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", _fork_during_sigterm(pidfile, body))),
            )
        assert pidfile.is_file()
        owned.append(int(pidfile.read_text(encoding="utf-8")))
        assert response["status"] == "success"
        assert code == 0
        cleanup = response["cleanup"]
        assert isinstance(cleanup, dict)
        assert cleanup["state"] == "complete"
        assert cleanup["group_confirmed"] is True
        assert cleanup["live_pids"] == []
        assert "SIGTERM" in cleanup["signals"]
        assert "SIGKILL" in cleanup["signals"]
        assert response["child_returncode"] == 0
        assert response["wrapper_exit_code"] == 0
        _assert_dead(owned[0])
        _assert_dead(int(cleanup["pid"]))
    finally:
        for pid in owned:
            _kill(pid)
        cleanup = response.get("cleanup")
        if isinstance(cleanup, dict) and cleanup.get("pid"):
            _kill(int(cleanup["pid"]))
        for pid in cleanup.get("live_pids", []) if isinstance(cleanup, dict) else []:
            _kill(int(pid))


def test_injected_unreadable_pid_blocks_success_without_signaling_stranger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이 호스트의 /proc/stat은 읽을 수 있다. 권한 실패는 검사 경로에 주입한 것이다."""
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.2, max_timeout_seconds=8)
    stranger = _own_session_sleeper()
    real = wrapper_run._inspect_proc

    def injected(pid: int) -> wrapper_run._ProcInspect:
        if pid == stranger.pid:
            return wrapper_run._ProcInspect("unreadable", "permission")
        return real(pid)

    monkeypatch.setattr(wrapper_run, "_inspect_proc", injected)
    body = _success_body("unreadable-block")
    script = "import json,sys\nreq=json.load(sys.stdin)\n" + _body_line(body) + "body['request_id']=req['request_id']\nprint(json.dumps(body))\n"
    try:
        with _Alarm(8):
            response, code = run_job(
                config,
                _request(sales, request_id="unreadable-block"),
                launch=wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", script)),
            )
        assert code == CLI_EXIT_CODES["execution_failed"]
        assert response["status"] == "execution_failed"
        assert response["error"]["code"] == "cleanup_incomplete"
        assert response["artifacts"] == []
        assert response["cleanup"]["group_scan"] == "unreadable"
        assert _alive(stranger.pid)
        assert stranger.pid not in response["cleanup"]["live_pids"]
    finally:
        _kill(stranger.pid)
        stranger.wait(timeout=2)


def test_forged_reused_member_is_not_signaled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 PGID로 보이게 만든 멤버다. 실제 커널의 PGID 재사용 자체가 아니다."""
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.2, max_timeout_seconds=8)
    stranger = _own_session_sleeper()
    real_scan = wrapper_run._scan_group

    def forged(pgid: int, session: int, leader_start: int, latest_start: int | None = None) -> tuple[list[dict[str, object]], bool]:
        members, ok = real_scan(pgid, session, leader_start, latest_start=latest_start)
        info = wrapper_run._read_proc(stranger.pid)
        if info is None:
            return members, ok
        members = [item for item in members if item["pid"] != stranger.pid]
        members.append(
            {
                "pid": stranger.pid,
                "state": "S",
                "starttime": info["starttime"],
                "pgrp": pgid,
                "ppid": 1,
                "session": session,
            }
        )
        return members, ok

    monkeypatch.setattr(wrapper_run, "_scan_group", forged)
    body = _success_body("forged-reuse")
    script = "import json,sys\nreq=json.load(sys.stdin)\n" + _body_line(body) + "body['request_id']=req['request_id']\nprint(json.dumps(body))\n"
    try:
        with _Alarm(8):
            response, code = run_job(
                config,
                _request(sales, request_id="forged-reuse"),
                launch=wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", script)),
            )
        assert code == CLI_EXIT_CODES["execution_failed"]
        assert response["error"]["code"] == "cleanup_incomplete"
        assert response["artifacts"] == []
        assert _alive(stranger.pid)
        cleanup = response["cleanup"]
        assert stranger.pid in set(cleanup["live_pids"]) | set(cleanup["unidentified_pids"])
    finally:
        _kill(stranger.pid)
        stranger.wait(timeout=2)


def test_huge_integer_is_config_error_not_clamped() -> None:
    with pytest.raises(ConfigError) as caught:
        _finite_number(10**400, "max_timeout_seconds", minimum=1e-9)
    assert caught.value.code == "invalid_config"
    with pytest.raises(ConfigError):
        _finite_number(float("nan"), "max_timeout_seconds", minimum=1e-9)
    with pytest.raises(ConfigError):
        _finite_number(True, "grace_seconds", minimum=0)


def test_wrapper_process_rejects_huge_config_without_traceback(tmp_path: Path) -> None:
    body = json.loads(_config(tmp_path).source_path.read_text(encoding="utf-8"))
    body["max_timeout_seconds"] = 10**400
    path = tmp_path / "huge.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "core.application.wrapper", "--config", str(path)],
        input=b"{}\n",
        capture_output=True,
        cwd=str(ROOT),
        check=False,
        timeout=8,
    )
    assert proc.returncode == 2
    assert b"Traceback" not in proc.stderr
    parsed = json.loads(proc.stdout.decode("utf-8"))
    assert parsed["status"] == "invalid_request"
    assert parsed["error"]["code"] == "invalid_config"
    normal = load_operator_config(_config(tmp_path, grace_seconds=0, lock_wait_seconds=0).source_path)
    assert normal.max_timeout_seconds == 30
    assert normal.default_timeout_seconds == 10
    assert normal.grace_seconds == 0


def test_huge_request_timeout_is_invalid_request(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, max_timeout_seconds=30, default_timeout_seconds=10, grace_seconds=0)
    with _Alarm(8):
        response, code = run_job(config, _request(sales, request_id="huge-timeout", timeout_seconds=10**400))
    assert code == CLI_EXIT_CODES["invalid_request"] == 2
    assert response["status"] == "invalid_request"
    assert response["error"]["code"] == "invalid_timeout"
    assert list(config.publish_root.glob("*")) == []
