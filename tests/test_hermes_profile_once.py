"""검증 프로필의 Hermes가 excel-analyzer를 등록된 도구로만 다루는지 한 번 확인한다.

스키마를 먼저 한 번 실행하고, 성공했을 때만 요약·품질·없는 파일을 한 번씩 이어서 실행한다.
미등록 도구 이름은 stream-json에 tool_use로 나오지 않고 세션 기록에만 남으므로 state.db도 읽는다.
Hermes 호출 전에 프로필 config.yaml의 skills.auto_load가 profile-autoload.yaml과 같은지 확인한다.
저장된 시스템 프롬프트에 skill 본문의 `connect.py --task`가 없으면 전달 실패,
있는데 skill_view·connect.py가 없으면 종단 실패다.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from tests.test_hermes_excel_bridge import (
    FIXTURES,
    HERMES_MODEL,
    HERMES_TIMEOUT_SECONDS,
    ROOT,
    _bridge_output,
    _command_tokens,
    _hermes_executable,
    _hermes_home,
    _sha256,
    _terminal_result,
    connect,
)

_REPO_SKILL = ROOT / "hermes" / "excel-analyzer"
_SKILL_BODY_MARKER = "connect.py --task"
_UNREGISTERED = frozenset({"excel-analyzer", "excel_analyzer"})
_STDERR_TOOL_NAME = re.compile(r"(?:Unknown tool|Auto-repaired tool name:)\s*'([^']*)'")
_QUERIES = {
    "schema": "{path} 파일의 컬럼 목록을 시트 0 기준으로 알려줘. 요청 ID는 {request_id}로 해줘.",
    "summary": "{path} 파일을 시트 0 기준으로 요약해줘. 요청 ID는 {request_id}로 해줘.",
    "quality": "{path} 파일의 데이터 품질을 시트 0 기준으로 진단해줘. 요청 ID는 {request_id}로 해줘.",
    "missing": "{path} 파일을 시트 0 기준으로 요약해줘. 요청 ID는 {request_id}로 해줘.",
}


def _run(hermes: str, home: Path, workdir: Path, name: str, query: str) -> tuple[int, list[dict], str]:
    query_file = workdir / f"{name}.query.txt"
    query_file.write_text(query, encoding="utf-8")
    dropped = {connect.CONFIG_ENV, "HERMES_INFERENCE_MODEL", "HERMES_INFERENCE_PROVIDER", "FORCE_COLOR", "PYTHON_COLORS"}
    env = {key: value for key, value in os.environ.items() if key not in dropped}
    env["HERMES_HOME"] = str(home)
    env["PATH"] = os.pathsep.join([str(Path(hermes).parent), env.get("PATH", "")])
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    completed = subprocess.run(
        [
            hermes, "chat", "--oneshot", "--yolo", "--reasoning", "none",
            "-t", "terminal,skills", "--max-turns", "12",
            "--format", "stream-json", "--query-file", str(query_file),
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        timeout=HERMES_TIMEOUT_SECONDS,
        check=False,
    )
    (workdir / f"{name}.stream.jsonl").write_bytes(completed.stdout)
    (workdir / f"{name}.stderr.txt").write_bytes(completed.stderr)
    events: list[dict] = []
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "type" in item:
            events.append(item)
    return completed.returncode, events, completed.stderr.decode("utf-8", errors="replace")


def _state_query(home: Path, query: str, params: tuple) -> list[tuple]:
    db = home / "state.db"
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)) as conn:
            return conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        # WAL 데이터베이스는 -shm 파일이 없으면 읽기 전용으로 열리지 않을 수 있다.
        with closing(sqlite3.connect(f"file:{db}", uri=True, timeout=10)) as conn:
            return conn.execute(query, params).fetchall()


def _session_rows(home: Path, session_id: str) -> list[tuple]:
    query = "SELECT role, tool_calls, tool_name FROM messages WHERE session_id = ? ORDER BY rowid"
    return _state_query(home, query, (session_id,))


def _system_prompt(home: Path, session_id: str) -> str:
    query = (
        "SELECT COALESCE(p.prompt, s.system_prompt, '') FROM sessions s "
        "LEFT JOIN system_prompts p ON p.hash = s.system_prompt_hash WHERE s.id = ?"
    )
    rows = _state_query(home, query, (session_id,))
    return str(rows[0][0] or "") if rows else ""


def _session_calls(home: Path, session_id: str) -> tuple[list[tuple[str, object]], set[str]]:
    calls: list[tuple[str, object]] = []
    tool_rows: set[str] = set()
    for role, raw_calls, tool_name in _session_rows(home, session_id):
        if role == "tool" and tool_name:
            tool_rows.add(str(tool_name))
        if role != "assistant" or not raw_calls:
            continue
        try:
            items = json.loads(raw_calls)
        except (TypeError, json.JSONDecodeError):
            items = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            called = str(function.get("name") or item.get("name") or "")
            calls.append((called, function.get("arguments", item.get("arguments"))))
    return calls, tool_rows


def _connect_args(command: str) -> dict[str, str]:
    try:
        tokens = _command_tokens(command)
    except ValueError:
        return {}
    return {
        token: tokens[index + 1]
        for index, token in enumerate(tokens[:-1])
        if token in ("--task", "--file", "--sheet", "--request-id")
    }


def _skill_view_succeeded(events: list[dict], at: int) -> bool:
    call_id = events[at].get("tool_call_id")
    for event in events[at + 1:]:
        if event.get("type") != "tool_result" or event.get("name") != "skill_view":
            continue
        if call_id and event.get("tool_call_id") not in (None, call_id):
            continue
        output = str(event.get("output") or "")
        return not event.get("is_error") and bool(re.search(r'"success"\s*:\s*true', output[:200]))
    return False


def _profile_auto_load(config: Path) -> object:
    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    skills = loaded.get("skills") if isinstance(loaded, dict) else None
    return skills.get("auto_load") if isinstance(skills, dict) else None


def _show(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 500 else text[:500] + "..."


def _case(
    hermes: str, home: Path, workdir: Path, name: str, *, task: str, source: Path, expect_missing: bool
) -> tuple[list[str], str]:
    request_id = f"once-{name}-{uuid.uuid4().hex}"
    query = _QUERIES[name].format(path=source, request_id=request_id)
    returncode, events, stderr = _run(hermes, home, workdir, name, query)
    problems: list[str] = []

    init = next((event for event in events if event.get("type") == "system" and event.get("subtype") == "init"), {})
    result = next((event for event in events if event.get("type") == "result"), {})
    model = str(init.get("model") or "")
    if model.split(":")[0] != HERMES_MODEL:
        problems.append(f"에이전트 모델이 다르다: {model!r}")

    session_id = str(result.get("session_id") or init.get("session_id") or "")
    calls: list[tuple[str, object]] = []
    tool_rows: set[str] = set()
    delivered = "확인 불가"
    if session_id:
        try:
            calls, tool_rows = _session_calls(home, session_id)
        except sqlite3.Error as exc:
            problems.append(f"세션 기록을 읽지 못해 미등록 호출 여부를 확인할 수 없다: {exc}")
        try:
            prompt = _system_prompt(home, session_id)
        except sqlite3.Error as exc:
            problems.append(f"저장된 시스템 프롬프트를 읽지 못해 skill 본문 전달 여부를 확인할 수 없다: {exc}")
        else:
            delivered = "있음" if _SKILL_BODY_MARKER in prompt else "없음"
            if _SKILL_BODY_MARKER not in prompt:
                problems.append(
                    f"전달 실패: 저장된 시스템 프롬프트({len(prompt)}자)에 skill 본문의 {_SKILL_BODY_MARKER!r}가 없다"
                )
    else:
        problems.append("session_id가 없어 미등록 호출 여부를 확인할 수 없다")
    streamed = [(str(event.get("name")), event.get("input")) for event in events if event.get("type") == "tool_use"]
    if session_id and not calls and streamed:
        problems.append("세션 기록에 도구 호출이 없는데 스트림에는 있다")

    names = {called for called, _ in calls + streamed} | tool_rows | set(_STDERR_TOOL_NAME.findall(stderr))
    names |= {str(event.get("name")) for event in events if event.get("type") == "tool_result"}
    unregistered = sorted(called for called in names if called.strip().lower() in _UNREGISTERED)
    if unregistered:
        problems.append(f"미등록 함수 이름을 호출했다: {unregistered}")

    skill_at = next(
        (
            index
            for index, event in enumerate(events)
            if event.get("type") == "tool_use"
            and event.get("name") == "skill_view"
            and isinstance(event.get("input"), dict)
            and event["input"].get("name") == "excel-analyzer"
        ),
        None,
    )
    if skill_at is None:
        problems.append("skill_view로 excel-analyzer를 읽지 않았다")
    elif not _skill_view_succeeded(events, skill_at):
        problems.append("skill_view excel-analyzer 결과가 성공이 아니다")

    connect_at = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "tool_use"
        and event.get("name") == "terminal"
        and "connect.py" in str((event.get("input") or {}).get("command", ""))
    ]
    if len(connect_at) != 1:
        problems.append(f"terminal의 connect.py 실행 수가 1이 아니다: {len(connect_at)}")
    else:
        at = connect_at[0]
        command = str(events[at]["input"]["command"])
        if skill_at is not None and at < skill_at:
            problems.append("connect.py가 skill_view보다 먼저 실행됐다")
        args = _connect_args(command)
        expected = {"--task": task, "--file": str(source), "--sheet": "0"}
        if any(args.get(flag) != value for flag, value in expected.items()):
            problems.append(f"connect.py 인자가 {expected}와 다르다: {command}")
        try:
            bridge, terminal_exit = _bridge_output(name, _terminal_result(name, events, at))
        except AssertionError as exc:
            problems.append(str(exc))
        else:
            if expect_missing:
                if (bridge.get("error") or {}).get("code") != "missing_file" or bridge.get("wrapper_exit_code") != 2:
                    problems.append(f"missing_file·종료 코드 2가 아니다: {bridge.get('status')} {bridge.get('error')} "
                                    f"wrapper_exit_code={bridge.get('wrapper_exit_code')}")
                if isinstance(terminal_exit, int) and terminal_exit != 2:
                    problems.append(f"터미널 종료 코드가 2가 아니다: {terminal_exit}")
            else:
                if bridge.get("status") != "success" or bridge.get("wrapper_exit_code") != 0:
                    problems.append(f"래퍼가 성공하지 않았다: {bridge.get('status')} {bridge.get('error')} "
                                    f"wrapper_exit_code={bridge.get('wrapper_exit_code')}")
                if isinstance(terminal_exit, int) and terminal_exit != 0:
                    problems.append(f"터미널 종료 코드가 0이 아니다: {terminal_exit}")

    shown = calls or streamed
    lines = [
        f"[{name}] 요청: {query}",
        f"hermes 종료 코드 {returncode}, result exit_code {result.get('exit_code')}, error {result.get('error')!r}",
        f"세션 {session_id or '(없음)'}, 시스템 프롬프트의 skill 본문({_SKILL_BODY_MARKER}): {delivered}",
        "선택된 도구와 인자:",
        *([f"  - {called} {_show(arguments)}" for called, arguments in shown] or ["  (없음)"]),
        "오류:",
        *[f"  - {problem}" for problem in problems],
        f"stderr 끝:\n{stderr[-1500:]}",
    ]
    return problems, "\n".join(lines)


@pytest.mark.skipif(
    os.environ.get("HERMES_EXCEL_E2E") != "1",
    reason="HERMES_EXCEL_E2E=1일 때만 실제 Hermes를 호출한다",
)
def test_schema_once_then_remaining(tmp_path: Path) -> None:
    home = _hermes_home()
    hermes = _hermes_executable()
    for repo_file, installed in (
        (_REPO_SKILL / "SKILL.md", home / "skills" / "excel-analyzer" / "SKILL.md"),
        (_REPO_SKILL / "profile-soul.md", home / "SOUL.md"),
    ):
        assert installed.is_file(), f"{installed}가 없다"
        assert _sha256(repo_file) == _sha256(installed), f"{installed}가 {repo_file}와 다르다"
    assert _SKILL_BODY_MARKER in (_REPO_SKILL / "SKILL.md").read_text(encoding="utf-8")
    expected_auto_load = yaml.safe_load((_REPO_SKILL / "profile-autoload.yaml").read_text(encoding="utf-8"))
    assert expected_auto_load == {"auto_load": ["excel-analyzer"]}
    config = home / "config.yaml"
    assert config.is_file(), f"{config}가 없다"
    auto_load = _profile_auto_load(config)
    assert auto_load == expected_auto_load["auto_load"], (
        f"{config}의 skills.auto_load가 {expected_auto_load['auto_load']}가 아니다: {auto_load!r}"
    )
    workdir = tmp_path / "hermes"
    workdir.mkdir()
    sales = FIXTURES / "sales.xlsx"
    quality = FIXTURES / "quality.xlsx"
    missing = FIXTURES / f"hermes-once-missing-{uuid.uuid4().hex}.xlsx"
    assert not missing.exists()

    problems, report = _case(
        hermes, home, workdir, "schema", task="schema", source=quality, expect_missing=False
    )
    assert not problems, report

    failures: list[str] = []
    for name, task, source, expect_missing in (
        ("summary", "summary", sales, False),
        ("quality", "quality", quality, False),
        ("missing", "summary", missing, True),
    ):
        problems, report = _case(
            hermes, home, workdir, name, task=task, source=source, expect_missing=expect_missing
        )
        if problems:
            failures.append(report)
    assert not failures, "\n\n".join(failures)
