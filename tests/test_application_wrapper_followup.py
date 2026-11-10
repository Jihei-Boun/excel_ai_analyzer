"""DEV-2 보완 회귀. 통제된 로컬 자식 프로세스만 사용한다."""

from __future__ import annotations

import hashlib
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
from core.application.wrapper import main
from core.application.wrapper_config import ConfigError, load_operator_config
from core.application.wrapper_paths import PathRejected
from core.application.wrapper_run import request_cancel, run_job
import core.application.wrapper_run as wrapper_run

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "dev2_wrapper_helpers",
    Path(__file__).with_name("test_application_wrapper.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_HELPERS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPERS)
_config = _HELPERS._config
_request = _HELPERS._request
_success_body = _HELPERS._success_body
_body_line = _HELPERS._body_line
_probe = _HELPERS._probe
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


def _assert_dead(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    info = wrapper_run._read_proc(pid)
    raise AssertionError(f"pid {pid}가 남아 있다: {info}")


def _grandchild_sleeper(pidfile: Path) -> str:
    return (
        "import os,signal,time\n"
        "pid=os.fork()\n"
        "if pid==0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    fd=os.open('/dev/null', os.O_RDWR)\n"
        "    os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "    while True:\n"
        "        time.sleep(30)\n"
        "time.sleep(30)\n"
    )


def _wait_cancel(config: object) -> None:
    import threading

    existing = {child.name for child in config.workspace_root.iterdir() if child.is_dir()}

    def _watch() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for child in config.workspace_root.iterdir():
                if child.is_dir() and len(child.name) == 32 and child.name not in existing:
                    request_cancel(config, child.name)
                    return
            time.sleep(0.05)

    threading.Thread(target=_watch, daemon=True).start()


def test_timeout_and_cancel_remove_sigterm_ignoring_grandchild(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.3, max_timeout_seconds=8, default_timeout_seconds=5)
    timeout_pid = tmp_path / "timeout-grand.pid"
    with _Alarm(8):
        timed, timed_code = run_job(
            config,
            _request(sales, request_id="grand-timeout", timeout_seconds=0.4),
            launch=wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", _grandchild_sleeper(timeout_pid))),
        )
    assert timed["status"] == "timeout"
    assert timed_code == CLI_EXIT_CODES["timeout"] == timed["wrapper_exit_code"]
    assert timed["cleanup"]["state"] == "complete"
    assert timed["cleanup"]["group_confirmed"] is True
    assert timed["cleanup"]["live_pids"] == []
    assert "SIGTERM" in timed["cleanup"]["signals"]
    assert "SIGKILL" in timed["cleanup"]["signals"]
    assert timed["child_returncode"] < 0
    assert timed["child_returncode"] != timed["wrapper_exit_code"]
    assert timed["artifacts"] == []
    _assert_dead(int(timeout_pid.read_text()))
    _assert_dead(int(timed["cleanup"]["pid"]))

    cancel_pid = tmp_path / "cancel-grand.pid"
    _wait_cancel(config)
    with _Alarm(8):
        cancelled, cancelled_code = run_job(
            config,
            _request(sales, request_id="grand-cancel", timeout_seconds=6),
            launch=wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", _grandchild_sleeper(cancel_pid))),
        )
    assert cancelled["status"] == "cancelled"
    assert cancelled_code == CLI_EXIT_CODES["cancelled"]
    assert cancelled["cleanup"]["state"] == "complete"
    assert cancelled["cleanup"]["live_pids"] == []
    assert "SIGKILL" in cancelled["cleanup"]["signals"]
    assert cancelled["input_integrity"]["state"] == "unchanged"
    assert cancelled["input_integrity"]["authoritative"] is True
    _assert_dead(int(cancel_pid.read_text()))


def test_normal_exit_does_not_leave_a_running_grandchild(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.2, max_timeout_seconds=8)
    pidfile = tmp_path / "done-grand.pid"
    body = _success_body("grand-ok")
    script = (
        "import json,os,signal,sys,time\n"
        "pid=os.fork()\n"
        "if pid==0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    fd=os.open('/dev/null', os.O_RDWR)\n"
        "    os.dup2(fd, 0); os.dup2(fd, 1); os.dup2(fd, 2); os.close(fd)\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "    while True:\n"
        "        time.sleep(30)\n"
        "req=json.load(sys.stdin)\n"
        + _body_line(body)
        + "body['request_id']=req['request_id']\n"
        "print(json.dumps(body))\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(0)\n"
    )
    with _Alarm(8):
        response, code = run_job(
            config,
            _request(sales, request_id="grand-ok"),
            launch=wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", script)),
        )
    assert code == 0
    assert response["status"] == "success"
    assert response["child_returncode"] == 0
    assert response["wrapper_exit_code"] == 0
    assert response["cleanup"]["state"] == "complete"
    assert "SIGKILL" in response["cleanup"]["signals"]
    assert response["cleanup"]["live_pids"] == []
    _assert_dead(int(pidfile.read_text()))


def test_unread_stdin_still_times_out_and_cancels(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.3, max_timeout_seconds=8, max_request_bytes=2_000_000)
    launch = wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", "import time\ntime.sleep(30)\n"))
    started = time.monotonic()
    with _Alarm(8):
        timed, code = run_job(
            config,
            _request(sales, request_id="stdin-timeout", timeout_seconds=0.4, prompt="x" * 200_000),
            launch=launch,
        )
    assert time.monotonic() - started < 4
    assert timed["status"] == "timeout"
    assert code == CLI_EXIT_CODES["timeout"]
    assert timed["cleanup"]["state"] == "complete"
    assert "SIGTERM" in timed["cleanup"]["signals"]
    meta = json.loads((config.workspace_root / timed["execution_id"] / "evidence" / "execution_meta.json").read_text())
    assert meta["stdin_bytes_total"] > 65_536
    _assert_dead(int(timed["cleanup"]["pid"]))

    _wait_cancel(config)
    started = time.monotonic()
    with _Alarm(8):
        cancelled, cancelled_code = run_job(
            config,
            _request(sales, request_id="stdin-cancel", timeout_seconds=6, prompt="y" * 200_000),
            launch=launch,
        )
    assert time.monotonic() - started < 4
    assert cancelled["status"] == "cancelled"
    assert cancelled_code == CLI_EXIT_CODES["cancelled"]
    assert cancelled["cleanup"]["state"] == "complete"
    _assert_dead(int(cancelled["cleanup"]["pid"]))


def test_request_over_byte_limit_is_rejected(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, max_request_bytes=80)
    response, code = run_job(config, _request(sales, request_id="too-big"))
    assert code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == "request_too_large"
    assert response["artifacts"] == []


def test_wrapper_stdin_stops_at_byte_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, max_request_bytes=100)

    class _Reader:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, size: int) -> bytes:
            self.calls += 1
            if self.calls > 3:
                raise AssertionError("한도를 넘긴 뒤에도 stdin을 읽는다")
            return b"x" * size

    reader = _Reader()
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": reader})())
    code = main(["--config", str(config.source_path)])
    assert code == 2
    assert reader.calls == 1


def test_bad_response_types_are_protocol_failures(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    cases = {
        "flag": {"safety": {**_success_body("flag")["safety"], "unsafe_output_blocked": "true"}},
        "text": {"text": []},
        "warnings": {"warnings": 3},
        "timing": {"timing": "bad"},
        "shape": {"data": {"shape": [True, 0], "columns": [], "preview_records": []}},
    }
    for name, override in cases.items():
        body = _success_body(name)
        body.update(override)
        if name == "flag":
            body["safety"] = override["safety"]
        response, code = run_job(
            config,
            _request(sales, request_id=name),
            launch=_probe(_body_line(body) + "body['request_id']=req['request_id']\n"),
        )
        assert code == CLI_EXIT_CODES["execution_failed"], name
        assert response["error"]["code"] in {"invalid_field_type", "invalid_structural_validation"}, name
        assert response["artifacts"] == []
        assert response["data"]["preview_records"] == []
        assert response["status"] != "success"


def test_valid_contract_and_zero_row_stay_success(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    legacy = _success_body("legacy-ok")
    del legacy["safety"]["validation"]
    legacy["safety"]["validation_status"] = "passed"
    response, code = run_job(
        config,
        _request(sales, request_id="legacy-ok"),
        launch=_probe(_body_line(legacy) + "body['request_id']=req['request_id']\n"),
    )
    assert code == 0
    assert response["status"] == "success"
    assert response["structural_state"] == "unknown"
    zero = _success_body("zero-probe")
    zero["data"] = {"shape": [0, 0], "columns": [], "preview_records": [], "route": "rule"}
    zero["text"] = "조건에 맞는 행이 없습니다."
    zero_response, zero_code = run_job(
        config,
        _request(sales, request_id="zero-probe"),
        launch=_probe(_body_line(zero) + "body['request_id']=req['request_id']\n"),
    )
    assert zero_code == 0
    assert zero_response["status"] == "success"
    assert zero_response["structural_state"] == "not_run"
    assert zero_response["data"]["shape"] == [0, 0]
    assert zero_response["artifacts"] == []


def _artifact_script(names: list[tuple[str, bytes]]) -> str:
    lines = [
        "import hashlib, pathlib",
        "target=pathlib.Path(req['output_directory'])",
        "target.mkdir(parents=True, exist_ok=True)",
        "arts=[]",
    ]
    for name, payload in names:
        lines.append(f"file=target/{name!r}")
        lines.append("file.parent.mkdir(parents=True, exist_ok=True)")
        lines.append(f"file.write_bytes({payload!r})")
        lines.append(
            "arts.append({'kind':'table','path':str(file),'size_bytes':len(file.read_bytes()),"
            "'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})"
        )
    lines.append("body['artifacts']=arts")
    return "\n".join(lines) + "\n"


def test_duplicate_basename_is_rejected(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    script = _body_line(_success_body("dup-name")) + "body['request_id']=req['request_id']\n"
    script += _artifact_script([("a/result.xlsx", b"one"), ("b/result.xlsx", b"two")])
    response, code = run_job(config, _request(sales, request_id="dup-name"), launch=_probe(script))
    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["code"] == "duplicate_artifact_name"
    assert response["artifacts"] == []
    assert list(config.publish_root.iterdir()) == []
    assert sales.is_file()


def test_publish_failure_removes_only_this_temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sales = _copy_fixture(tmp_path)
    other = tmp_path / "publish" / "kept"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("stay", encoding="utf-8")
    config = _config(tmp_path)
    script = _body_line(_success_body("copy-fail")) + "body['request_id']=req['request_id']\n"
    script += _artifact_script([("one.xlsx", b"one"), ("two.xlsx", b"two")])
    calls = {"n": 0}
    original = wrapper_run._stage_publish_file

    def _second_fails(dest: Path, payload: bytes) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("second copy failed")
        original(dest, payload)

    monkeypatch.setattr(wrapper_run, "_stage_publish_file", _second_fails)
    failed, failed_code = run_job(config, _request(sales, request_id="copy-fail"), launch=_probe(script))
    assert failed_code == CLI_EXIT_CODES["execution_failed"]
    assert failed["error"]["code"] == "publish_failed"
    assert failed["artifacts"] == []
    assert not any(path.name.endswith(".partial") for path in config.publish_root.iterdir())
    assert (other / "keep.txt").read_text(encoding="utf-8") == "stay"
    assert sales.is_file()

    def _rename_fails(source: Path, dest: Path) -> None:
        del source, dest
        raise PathRejected("publish_commit_failed", "공개 직전에 중단했다.")

    monkeypatch.setattr(wrapper_run, "_stage_publish_file", original)
    monkeypatch.setattr(wrapper_run, "_rename_publish", _rename_fails)
    script = script.replace("copy-fail", "rename-fail")
    blocked, blocked_code = run_job(config, _request(sales, request_id="rename-fail"), launch=_probe(script))
    assert blocked_code == CLI_EXIT_CODES["execution_failed"]
    assert blocked["error"]["code"] == "publish_commit_failed"
    assert list(path.name for path in config.publish_root.iterdir()) == ["kept"]
    assert sales.is_file()


def test_numeric_policy_rejects_invalid_values(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    base = json.loads(_config(tmp_path).source_path.read_text(encoding="utf-8"))
    zero = _config(tmp_path, grace_seconds=0, lock_wait_seconds=0)
    assert zero.grace_seconds == 0
    assert zero.lock_wait_seconds == 0
    omitted = dict(base)
    del omitted["default_timeout_seconds"]
    omitted["max_timeout_seconds"] = 10
    path = tmp_path / "omitted.json"
    path.write_text(json.dumps(omitted), encoding="utf-8")
    loaded = load_operator_config(path)
    assert loaded.default_timeout_seconds == 10
    assert loaded.max_timeout_seconds == 10

    def _bad(name: str, **overrides: object) -> None:
        body = dict(base)
        body.update(overrides)
        target = tmp_path / f"{name}.json"
        target.write_text(json.dumps(body, allow_nan=True), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_operator_config(target)

    _bad("nan", max_timeout_seconds=float("nan"))
    _bad("inf", max_timeout_seconds=float("inf"))
    _bad("ninf", grace_seconds=float("-inf"))
    _bad("bool", max_input_files=True)
    _bad("zero-files", max_input_files=0)
    _bad("negative-grace", grace_seconds=-1)
    _bad("default-over-max", default_timeout_seconds=50, max_timeout_seconds=10)
    config = _config(tmp_path, max_timeout_seconds=5)
    for raw in (float("nan"), float("inf"), True, 0):
        response, code = run_job(config, _request(sales, request_id=f"num-{raw!r}", timeout_seconds=raw))
        assert code == CLI_EXIT_CODES["invalid_request"]
        assert response["error"]["code"] == "invalid_timeout"
    above, above_code = run_job(config, _request(sales, request_id="num-above", timeout_seconds=6))
    assert above_code == CLI_EXIT_CODES["invalid_request"]
    assert above["error"]["code"] == "timeout_above_maximum"
    proc = subprocess.run(
        [sys.executable, "-m", "core.application.wrapper", "--config", str(tmp_path / "bool.json")],
        input=b"{}\n",
        capture_output=True,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 2
    assert b"Traceback" not in proc.stderr
    parsed = json.loads(proc.stdout.decode("utf-8"))
    assert parsed["status"] == "invalid_request"
    assert parsed["error"]["code"] == "invalid_config"


def test_timeout_and_cancel_record_hashes_and_unconfirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sales = _copy_fixture(tmp_path)
    before = hashlib.sha256(sales.read_bytes()).hexdigest()
    config = _config(tmp_path, grace_seconds=0.2, max_timeout_seconds=5)
    launch = wrapper_run.ProcessLaunch(argv=(sys.executable, "-c", "import time\ntime.sleep(30)\n"))
    timed, code = run_job(config, _request(sales, request_id="hash-timeout", timeout_seconds=0.3), launch=launch)
    assert timed["status"] == "timeout"
    assert code == 6
    integrity = timed["input_integrity"]
    assert integrity["state"] == "unchanged"
    assert integrity["authoritative"] is True
    item = integrity["items"][0]
    assert item["source_before"] == item["source_after"] == before
    assert item["copy_before"] == item["copy_after"]
    assert timed["child_returncode"] != timed["wrapper_exit_code"]
    meta = json.loads((config.workspace_root / timed["execution_id"] / "evidence" / "execution_meta.json").read_text())
    assert meta["child_returncode"] == timed["child_returncode"]
    assert meta["wrapper_exit_code"] == 6
    assert meta["input_integrity"]["state"] == "unchanged"

    _wait_cancel(config)
    cancelled, _cancelled_code = run_job(
        config,
        _request(sales, request_id="hash-cancel", timeout_seconds=5),
        launch=launch,
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["input_integrity"]["state"] == "unchanged"
    assert cancelled["input_integrity"]["items"][0]["source_after"] == before

    hidden = _config(tmp_path, grace_seconds=0.2, max_timeout_seconds=5)
    with _Alarm(8):
        unreadable, _unreadable_code = run_job(
            hidden,
            _request(sales, request_id="hash-hidden", timeout_seconds=0.3),
            launch=wrapper_run.ProcessLaunch(
                argv=(sys.executable, "-c", f"import os,time\nos.chmod({str(sales)!r}, 0)\ntime.sleep(30)\n")
            ),
        )
    os.chmod(sales, 0o644)
    assert unreadable["status"] == "timeout"
    assert unreadable["input_integrity"]["state"] == "unconfirmed"
    assert unreadable["input_integrity"]["authoritative"] is False
    assert unreadable["input_integrity"]["items"][0]["source_state"] == "unreadable"
    assert unreadable["input_integrity"]["state"] != "unchanged"

    monkeypatch.setattr(wrapper_run, "_scan_group", lambda *args, **kwargs: ([], False))
    blocked, blocked_code = run_job(
        config,
        _request(sales, request_id="hash-blind"),
        launch=_probe(_body_line(_success_body("hash-blind")) + "body['request_id']=req['request_id']\n"),
    )
    assert blocked_code == CLI_EXIT_CODES["execution_failed"]
    assert blocked["error"]["code"] == "cleanup_incomplete"
    assert blocked["artifacts"] == []
    assert blocked["input_integrity"]["state"] == "unconfirmed"
    assert blocked["input_integrity"]["authoritative"] is False
    assert all(
        row["source_state"] != "unchanged" and row["copy_state"] != "unchanged"
        for row in blocked["input_integrity"]["items"]
    )


def test_partial_prepare_keeps_copied_evidence(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    linked = tmp_path / "inputs" / "linked.xlsx"
    linked.symlink_to(sales)
    config = _config(tmp_path)
    response, code = run_job(
        config,
        {
            "contract_version": "1.0",
            "request_id": "partial-1",
            "user_prompt": "두 파일을 요약해줘",
            "inputs": [
                {"path": str(sales), "sheet": 0, "display_name": "sales.xlsx"},
                {"path": str(linked), "sheet": 0, "display_name": "linked.xlsx"},
            ],
        },
    )
    assert code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == "symlink_rejected"
    saved = json.loads(
        (config.workspace_root / response["execution_id"] / "evidence" / "input_integrity.json").read_text()
    )
    assert len(saved["items"]) == 1
    assert saved["items"][0]["source_before"]
    assert saved["items"][0]["copy_before"] == saved["items"][0]["source_before"]
    assert sales.is_file()


def test_stage_file_does_not_follow_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"safe")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(OSError):
        wrapper_run._stage_publish_file(link, b"changed")
    assert outside.read_bytes() == b"safe"
