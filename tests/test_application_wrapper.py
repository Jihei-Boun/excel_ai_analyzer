"""DEV-2 실행 래퍼. Linux에서만 실행한다."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from core.application.contracts import CLI_EXIT_CODES
from core.application.wrapper_config import load_operator_config
from core.application.wrapper_paths import is_inside
from core.application.wrapper_run import ProcessLaunch, request_cancel, run_job
from core.constants import MERGES_DIR, active_merges_dir
from core.display.chart_utils import charts_dir
from core.integrate.integrate_pipeline import _export_merges_dir
from core.io.export_utils import export_sheets_xlsx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "development" / "dev0" / "fixtures"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="DEV-2 래퍼는 Linux만 지원한다")


def _config(tmp_path: Path, **overrides: object) -> object:
    body = {
        "python_path": sys.executable,
        "repo_cwd": str(ROOT),
        "allowed_input_roots": [str(tmp_path / "inputs")],
        "workspace_root": str(tmp_path / "work"),
        "publish_root": str(tmp_path / "publish"),
        "model_base_url": "http://127.0.0.1:11434",
        "model_name": "qwen2.5:7b",
        "allowed_profiles": ["generic"],
        "max_input_files": 8,
        "max_file_bytes": 5_000_000,
        "max_total_bytes": 10_000_000,
        "max_timeout_seconds": 30,
        "default_timeout_seconds": 10,
        "grace_seconds": 0.4,
        "lock_wait_seconds": 2,
        "max_stdout_bytes": 1_000_000,
        "max_stderr_bytes": 1_000_000,
        "allowed_extensions": [".xlsx", ".xls", ".xlsm", ".csv"],
    }
    body.update(overrides)
    if body["default_timeout_seconds"] > body["max_timeout_seconds"] and "default_timeout_seconds" not in overrides:
        body["default_timeout_seconds"] = body["max_timeout_seconds"]
    (tmp_path / "inputs").mkdir(exist_ok=True)
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return load_operator_config(path)


def _request(path: Path, *, request_id: str = "req-1", prompt: str = "이 파일의 주요 내용을 요약해줘", **extra: object) -> dict:
    payload = {
        "contract_version": "1.0",
        "request_id": request_id,
        "user_prompt": prompt,
        "inputs": [{"path": str(path), "sheet": 0, "display_name": path.name}],
    }
    payload.update(extra)
    return payload


def _success_body(request_id: str, **overrides: object) -> dict:
    body = {
        "contract_version": "1.0",
        "request_id": request_id,
        "status": "success",
        "text": "ok",
        "data": {"shape": [0, 0], "columns": [], "preview_records": [], "route": "probe"},
        "artifacts": [],
        "warnings": [],
        "safety": {
            "validation_status": "passed",
            "unsafe_output_blocked": False,
            "validation": {
                "structural": {
                    "state": "not_run",
                    "scope": "integration_result_structure",
                    "covers_semantic_correctness": False,
                }
            },
        },
        "timing": {"elapsed_ms": 1},
    }
    body.update(overrides)
    return body


def _body_line(body: dict) -> str:
    return "body=json.loads(" + json.dumps(json.dumps(body, ensure_ascii=False)) + ")\n"


def _probe(code: str) -> ProcessLaunch:
    script = "import json,sys\nreq=json.load(sys.stdin)\n" + code + "\nprint(json.dumps(body, ensure_ascii=False))\n"
    return ProcessLaunch(argv=(sys.executable, "-c", script))


def _copy_fixture(tmp_path: Path, name: str = "sales.xlsx") -> Path:
    dest = tmp_path / "inputs" / name
    dest.parent.mkdir(exist_ok=True)
    dest.write_bytes((FIXTURES / name).read_bytes())
    return dest


def test_default_export_dirs_stay_shared_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXCEL_ANALYZER_MERGES_DIR", raising=False)
    monkeypatch.delenv("EXCEL_ANALYZER_CHARTS_DIR", raising=False)
    assert active_merges_dir() == MERGES_DIR
    assert _export_merges_dir() == MERGES_DIR
    assert charts_dir() == ROOT / "exports" / "charts"


def test_env_redirects_merge_and_chart_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    merges = tmp_path / "merges"
    charts = tmp_path / "charts"
    monkeypatch.setenv("EXCEL_ANALYZER_MERGES_DIR", str(merges))
    monkeypatch.setenv("EXCEL_ANALYZER_CHARTS_DIR", str(charts))
    before = {path.name for path in MERGES_DIR.glob("*")}
    saved = export_sheets_xlsx({"통합": pd.DataFrame({"값": [1]})}, filename="dev2-isolation-probe.xlsx")
    try:
        assert saved.parent == merges
        assert saved.is_file()
        assert "dev2-isolation-probe.xlsx" not in {path.name for path in MERGES_DIR.glob("*")}
        assert {path.name for path in MERGES_DIR.glob("*")} == before
        assert _export_merges_dir() == merges
        assert charts_dir() == charts
    finally:
        leaked = MERGES_DIR / "dev2-isolation-probe.xlsx"
        if leaked.is_file():
            leaked.unlink()


def test_prefix_lookalike_is_outside(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    evil = tmp_path / "allowed-evil"
    root.mkdir()
    evil.mkdir()
    assert is_inside(root / "file.xlsx", root) is False or not (root / "file.xlsx").exists()
    assert is_inside(evil / "file.xlsx", root) is False


def test_rule_summary_publishes_nothing_and_keeps_inputs(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    before = sales.read_bytes()
    config = _config(tmp_path)
    response, code = run_job(config, _request(sales, request_id="summary-1"))
    assert code == 0
    assert response["status"] == "success"
    assert response["structural_state"] == "not_run"
    assert response["semantic_correctness_guaranteed"] is False
    assert response["artifacts"] == []
    assert sales.read_bytes() == before
    meta = json.loads((config.workspace_root / response["execution_id"] / "evidence" / "execution_meta.json").read_text())
    assert meta["returncode"] == 0
    assert meta["source"] == "direct"
    assert meta["git_head"]
    assert meta["input_hashes_before"][str(sales.resolve())] == meta["input_hashes_before"][str(sales.resolve())]
    assert response["source_identity"]["diff_sha256"]


def test_schema_table_is_published_after_hash_check(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    response, code = run_job(
        config,
        _request(sales, request_id="schema-1", prompt="컬럼 목록을 보여줘"),
    )
    assert code == 0
    assert response["status"] == "success"
    assert len(response["artifacts"]) == 1
    published = Path(response["artifacts"][0]["path"])
    assert published.is_file()
    assert is_inside(published, config.publish_root)
    assert published.stat().st_size == response["artifacts"][0]["size_bytes"]
    import hashlib

    assert hashlib.sha256(published.read_bytes()).hexdigest() == response["artifacts"][0]["sha256"]
    loaded = pd.read_excel(published)
    assert list(loaded.columns) == ["행 수", "열 수", "컬럼 목록"]


def test_zero_row_stays_success_without_artifact(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    response, code = run_job(
        config,
        _request(sales, request_id="zero-1", prompt="결측값이 있는 행만 보여줘"),
    )
    assert code == 0
    assert response["status"] == "success"
    assert response["structural_state"] == "not_run"
    assert response["data"]["shape"] == [0, 0]
    assert response["artifacts"] == []


def test_closed_port_matches_exit_code(tmp_path: Path) -> None:
    left = _copy_fixture(tmp_path, "customers.xlsx")
    right = tmp_path / "inputs" / "grades.xlsx"
    right.write_bytes((FIXTURES / "grades.xlsx").read_bytes())
    config = _config(tmp_path, model_base_url="http://127.0.0.1:9", max_timeout_seconds=15, default_timeout_seconds=10)
    response, code = run_job(
        config,
        {
            "contract_version": "1.0",
            "request_id": "closed-1",
            "user_prompt": "선택한 파일들을 통합해줘",
            "inputs": [
                {"path": str(left), "sheet": 0, "display_name": "customers.xlsx"},
                {"path": str(right), "sheet": 0, "display_name": "grades.xlsx"},
            ],
        },
    )
    assert response["status"] == "model_unavailable"
    assert code == CLI_EXIT_CODES["model_unavailable"] == 5
    assert response["exit_code"] == 5
    assert response["artifacts"] == []
    assert response["data"]["preview_records"] == []
    assert response["structural_state"] == "not_run"
    meta = json.loads((config.workspace_root / response["execution_id"] / "evidence" / "execution_meta.json").read_text())
    assert meta["returncode"] == 5


def test_outside_input_and_forbidden_output_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes((FIXTURES / "sales.xlsx").read_bytes())
    config = _config(tmp_path)
    called = {"value": False}

    def _no(*_a: object, **_k: object) -> None:
        called["value"] = True

    response, code = run_job(config, _request(outside, request_id="out-1"))
    assert code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == "outside_root"
    denied, _denied_code = run_job(
        config,
        _request(_copy_fixture(tmp_path), request_id="out-2", output_directory="/tmp"),
    )
    assert denied["error"]["code"] == "forbidden_request_field"
    assert called["value"] is False


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    hidden = secret / "hidden.xlsx"
    hidden.write_bytes((FIXTURES / "sales.xlsx").read_bytes())
    (tmp_path / "inputs").mkdir(exist_ok=True)
    link_dir = tmp_path / "inputs" / "via"
    link_dir.symlink_to(secret, target_is_directory=True)
    direct = tmp_path / "inputs" / "link.xlsx"
    direct.symlink_to(hidden)
    config = _config(tmp_path)
    via, _via_code = run_job(config, _request(link_dir / "hidden.xlsx", request_id="link-1"))
    assert via["error"]["code"] == "outside_root"
    linked, _linked_code = run_job(config, _request(direct, request_id="link-2"))
    assert linked["error"]["code"] == "symlink_rejected"


def test_same_filename_and_duplicate_request_id(tmp_path: Path) -> None:
    first = tmp_path / "inputs" / "a"
    second = tmp_path / "inputs" / "b"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "sales.xlsx").write_bytes((FIXTURES / "sales.xlsx").read_bytes())
    (second / "sales.xlsx").write_bytes((FIXTURES / "sales.xlsx").read_bytes())
    config = _config(tmp_path)
    launch = _probe(
        _body_line(_success_body("same-1"))
        + "body['request_id']=req['request_id']\nbody['text']='|'.join(item['path'] for item in req['inputs'])\n"
    )
    payload = {
        "contract_version": "1.0",
        "request_id": "same-1",
        "user_prompt": "두 파일을 요약해줘",
        "inputs": [
            {"path": str(first / "sales.xlsx"), "sheet": 0, "display_name": "sales.xlsx"},
            {"path": str(second / "sales.xlsx"), "sheet": 0, "display_name": "sales.xlsx"},
        ],
    }
    response, code = run_job(config, payload, launch=launch)
    assert code == 0
    copies = response["text"].split("|")
    assert len(copies) == 2
    assert copies[0] != copies[1]
    assert Path(copies[0]).name != Path(copies[1]).name or copies[0] != copies[1]
    assert all(is_inside(Path(item), config.workspace_root / response["execution_id"] / "inputs") for item in copies)
    again, again_code = run_job(config, payload, launch=launch)
    assert again_code == 0
    assert again["execution_id"] != response["execution_id"]
    assert again["duplicate_request_id"] is True
    assert (config.workspace_root / response["execution_id"] / "evidence" / "wrapper_response.json").is_file()
    assert (config.publish_root / response["execution_id"]).exists() is False or response["artifacts"] == []


def test_two_processes_serialize_and_lock_releases_on_death(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    slow_config = _config(tmp_path, lock_wait_seconds=3, grace_seconds=0.3)
    busy_path = tmp_path / "busy.json"
    json.loads(slow_config.source_path.read_text())
    busy_body = json.loads(slow_config.source_path.read_text())
    busy_body["lock_wait_seconds"] = 0.2
    busy_path.write_text(json.dumps(busy_body), encoding="utf-8")
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json,sys,time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from core.application.wrapper_config import load_operator_config\n"
        "from core.application.wrapper_run import ProcessLaunch, run_job\n"
        "config = load_operator_config(sys.argv[2])\n"
        "payload = json.loads(sys.argv[3])\n"
        "script = sys.argv[4]\n"
        "response, code = run_job(config, payload, launch=ProcessLaunch(argv=(sys.executable, '-c', script)))\n"
        "sys.stdout.write(json.dumps({'code': code, 'status': response['status']}))\n",
        encoding="utf-8",
    )
    slow_script = (
        "import json,sys,time\n"
        "req=json.load(sys.stdin)\n"
        "time.sleep(1.2)\n"
        "body={'contract_version':'1.0','request_id':req['request_id'],'status':'success','text':'slow',"
        "'data':{'shape':[0,0],'columns':[],'preview_records':[]},'artifacts':[],'warnings':[],"
        "'safety':{'validation_status':'passed','unsafe_output_blocked':False,"
        "'validation':{'structural':{'state':'not_run','scope':'integration_result_structure','covers_semantic_correctness':False}}},"
        "'timing':{'elapsed_ms':1}}\n"
        "print(json.dumps(body))\n"
    )
    fast_script = slow_script.replace("time.sleep(1.2)\n", "")
    payload_a = json.dumps(_request(sales, request_id="lock-a"))
    payload_b = json.dumps(_request(sales, request_id="lock-b"))
    first = subprocess.Popen(
        [sys.executable, str(runner), str(ROOT), str(slow_config.source_path), payload_a, slow_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        child.is_dir() and len(child.name) == 32 for child in slow_config.workspace_root.iterdir()
    ):
        time.sleep(0.05)
    second = subprocess.Popen(
        [sys.executable, str(runner), str(ROOT), str(busy_path), payload_b, fast_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out_b, err_b = second.communicate(timeout=10)
    out_a, err_a = first.communicate(timeout=10)
    assert second.returncode == 0, err_b
    parsed_b = json.loads(out_b)
    assert parsed_b["status"] == "busy"
    assert parsed_b["code"] == 8
    assert first.returncode == 0, err_a
    parsed_a = json.loads(out_a)
    assert parsed_a["status"] == "success"
    holder = subprocess.Popen(
        [sys.executable, "-c", "import fcntl,os,time; fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o644); fcntl.flock(fd, fcntl.LOCK_EX); time.sleep(30)", str(slow_config.workspace_root / ".wrapper.lock")],
        start_new_session=True,
    )
    time.sleep(0.2)
    os.kill(holder.pid, 9)
    holder.wait(timeout=5)
    response, code = run_job(slow_config, _request(sales, request_id="lock-c"), launch=_probe(
        _body_line(_success_body("lock-c")) + "body['request_id']=req['request_id']\n"
    ))
    assert code == 0
    assert response["status"] == "success"


def test_timeout_kills_process_group(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.3, max_timeout_seconds=5)
    launch = ProcessLaunch(
        argv=(
            sys.executable,
            "-c",
            "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n",
        )
    )
    response, code = run_job(config, _request(sales, request_id="time-1", timeout_seconds=0.4), launch=launch)
    assert response["status"] == "timeout"
    assert code == CLI_EXIT_CODES["timeout"]
    assert response["cleanup"]["state"] == "complete"
    assert "SIGKILL" in response["cleanup"]["signals"]
    assert response["artifacts"] == []
    pid = int(response["cleanup"]["pid"])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancel_stops_process(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, grace_seconds=0.3, max_timeout_seconds=10, default_timeout_seconds=8)

    def _watch() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for child in config.workspace_root.iterdir():
                if child.is_dir() and len(child.name) == 32:
                    request_cancel(config, child.name)
                    return
            time.sleep(0.05)

    threading.Thread(target=_watch, daemon=True).start()
    launch = ProcessLaunch(argv=(sys.executable, "-c", "import time\ntime.sleep(30)\n"))
    response, code = run_job(config, _request(sales, request_id="cancel-1", timeout_seconds=8), launch=launch)
    assert response["status"] == "cancelled"
    assert code == CLI_EXIT_CODES["cancelled"]
    assert response["cleanup"]["state"] == "complete"
    assert response["artifacts"] == []
    with pytest.raises(ProcessLookupError):
        os.kill(int(response["cleanup"]["pid"]), 0)


def test_malformed_stdout_and_status_mismatch(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    bad, bad_code = run_job(
        config,
        _request(sales, request_id="bad-json"),
        launch=ProcessLaunch(argv=(sys.executable, "-c", "import sys; sys.stdin.read(); print('not-json')")),
    )
    assert bad_code == CLI_EXIT_CODES["execution_failed"]
    assert bad["error"]["code"] == "malformed_engine_output"
    assert bad["artifacts"] == []
    mismatch, mismatch_code = run_job(
        config,
        _request(sales, request_id="mismatch-1"),
        launch=ProcessLaunch(
            argv=(
                sys.executable,
                "-c",
                "import json,sys\n"
                "req=json.load(sys.stdin)\n"
                + "body=json.loads("
                + json.dumps(json.dumps(_success_body("mismatch-1")))
                + ")\n"
                "body['request_id']=req['request_id']\n"
                "print(json.dumps(body))\n"
                "raise SystemExit(5)\n",
            )
        ),
    )
    assert mismatch_code == CLI_EXIT_CODES["execution_failed"]
    assert mismatch["error"]["code"] == "status_exit_mismatch"
    assert mismatch["artifacts"] == []


def test_bad_artifacts_are_not_published(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    outside, _outside_code = run_job(
        config,
        _request(sales, request_id="art-out"),
        launch=_probe(
            _body_line(_success_body("art-out"))
            + "body['request_id']=req['request_id']\n"
            "body['artifacts']=[{'kind':'table','path':'/etc/hosts','size_bytes':1,'sha256':'abc'}]\n"
        ),
    )
    assert outside["error"]["code"] == "artifact_outside_workspace"
    assert list(config.publish_root.glob("*")) == []
    missing, _missing_code = run_job(
        config,
        _request(sales, request_id="art-missing"),
        launch=_probe(
            _body_line(_success_body("art-missing"))
            + "body['request_id']=req['request_id']\n"
            "body['artifacts']=[{'kind':'table','path':req['output_directory']+'/missing.xlsx','size_bytes':1,'sha256':'abc'}]\n"
        ),
    )
    assert missing["error"]["code"] == "artifact_missing"
    wrong, _wrong_code = run_job(
        config,
        _request(sales, request_id="art-hash"),
        launch=_probe(
            "import pathlib\n"
            "target=pathlib.Path(req['output_directory'])\n"
            "target.mkdir(parents=True, exist_ok=True)\n"
            "file=target/'result.xlsx'\n"
            "file.write_bytes(b'abc')\n"
            + _body_line(_success_body("art-hash"))
            + "body['request_id']=req['request_id']\n"
            "body['artifacts']=[{'kind':'table','path':str(file),'size_bytes':3,'sha256':'0'*64}]\n"
        ),
    )
    assert wrong["error"]["code"] == "artifact_hash_mismatch"
    assert wrong["artifacts"] == []
    size, _size_code = run_job(
        config,
        _request(sales, request_id="art-size"),
        launch=_probe(
            "import pathlib\n"
            "target=pathlib.Path(req['output_directory'])\n"
            "target.mkdir(parents=True, exist_ok=True)\n"
            "file=target/'result.xlsx'\n"
            "file.write_bytes(b'abc')\n"
            + _body_line(_success_body("art-size"))
            + "body['request_id']=req['request_id']\n"
            "body['artifacts']=[{'kind':'table','path':str(file),'size_bytes':9,'sha256':'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}]\n"
        ),
    )
    assert size["error"]["code"] == "artifact_size_mismatch"
    assert size["artifacts"] == []


def test_nonsuccess_strips_preview_and_artifacts(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    launch = ProcessLaunch(
        argv=(
            sys.executable,
            "-c",
            "import json,sys\n"
            "from pathlib import Path\n"
            "req=json.load(sys.stdin)\n"
            "target=Path(req['output_directory']); target.mkdir(parents=True, exist_ok=True)\n"
            "file=target/'result.xlsx'; file.write_bytes(b'abc')\n"
            "body={"
            "'contract_version':'1.0','request_id':req['request_id'],'status':'validation_failed',"
            "'text':'검증 실패','data':{'shape':[1,1],'columns':['값'],'preview_records':[{'값':1}]},"
            "'artifacts':[{'kind':'workbook','path':str(file),'size_bytes':3,'sha256':'abc'}],"
            "'warnings':[],'safety':{'validation_status':'failed','unsafe_output_blocked':False,"
            "'validation':{'structural':{'state':'failed','scope':'integration_result_structure','covers_semantic_correctness':False}}},"
            "'timing':{'elapsed_ms':1},'error':{'code':'structural_validation_failed','message':'검증 실패','stage':'structural_validation','retryable':False}"
            "}\n"
            "print(json.dumps(body))\n"
            "raise SystemExit(4)\n",
        )
    )
    response, code = run_job(config, _request(sales, request_id="fail-preview"), launch=launch)
    assert code == CLI_EXIT_CODES["validation_failed"]
    assert response["status"] == "validation_failed"
    assert response["artifacts"] == []
    assert response["data"]["preview_records"] == []
    assert response["structural_state"] == "failed"
    saved = json.loads(
        (config.workspace_root / response["execution_id"] / "evidence" / "engine_response.json").read_text()
    )
    assert saved["data"]["preview_records"] == [{"값": 1}]


def test_wrapper_process_receives_request_export_dirs(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    launch = _probe(
        "import os\n"
        + _body_line(_success_body("paths-1"))
        + "body['request_id']=req['request_id']\n"
        "body['text']=os.environ['EXCEL_ANALYZER_MERGES_DIR']+'|'+os.environ['EXCEL_ANALYZER_CHARTS_DIR']\n"
    )
    response, code = run_job(config, _request(sales, request_id="paths-1"), launch=launch)
    assert code == 0
    merges, charts = response["text"].split("|")
    execution = config.workspace_root / response["execution_id"]
    assert is_inside(Path(merges), execution / "intermediate" / "merges")
    assert is_inside(Path(charts), execution / "intermediate" / "charts")
    assert MERGES_DIR != Path(merges)


def test_copy_mutation_blocks_publish(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    original = sales.read_bytes()
    config = _config(tmp_path)
    launch = _probe(
        "import os\n"
        + _body_line(_success_body("mutate-1"))
        + "body['request_id']=req['request_id']\n"
        "path=req['inputs'][0]['path']\n"
        "os.chmod(path, 0o644)\n"
        "open(path,'ab').write(b'x')\n"
    )
    response, code = run_job(config, _request(sales, request_id="mutate-1"), launch=launch)
    assert code == CLI_EXIT_CODES["execution_failed"]
    assert response["error"]["code"] == "input_changed"
    assert response["artifacts"] == []
    assert sales.read_bytes() == original


def test_timeout_above_maximum_is_rejected(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path, max_timeout_seconds=5)
    response, code = run_job(
        config,
        _request(sales, request_id="too-long", timeout_seconds=6),
        launch=ProcessLaunch(argv=(sys.executable, "-c", "raise SystemExit(99)")),
    )
    assert code == CLI_EXIT_CODES["invalid_request"]
    assert response["error"]["code"] == "timeout_above_maximum"
    assert response["execution_id"] is None


def test_copy_is_read_only_but_not_a_sandbox(tmp_path: Path) -> None:
    sales = _copy_fixture(tmp_path)
    config = _config(tmp_path)
    response, _code = run_job(
        config,
        _request(sales, request_id="mode-1"),
        launch=_probe(
            _body_line(_success_body("mode-1"))
            + "body['request_id']=req['request_id']\n"
        ),
    )
    copies = list((config.workspace_root / response["execution_id"] / "inputs").iterdir())
    assert copies
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o444
