"""Hermes 연결 스크립트의 자유 질문(analyze) 작업. 모델과 Hermes는 부르지 않는다."""

from __future__ import annotations

import json
import sys

import pytest

from tests.test_hermes_excel_bridge import (
    FIXTURES,
    _ANALYSIS_PUBLIC_KEYS,
    _fake_wrapper,
    _invoke,
    _run_connect,
    _single_json,
    connect,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="운영 래퍼는 Linux만 지원한다")

QUESTION = "부서별 예산 합계 대비 집행 합계 집행률을 구해줘"


def test_analyze_sends_question_unchanged(tmp_path) -> None:
    config, records = _fake_wrapper(tmp_path)
    source = FIXTURES / "budget.xlsx"
    question = "  " + QUESTION + " $HOME `id` 'x' "
    completed = _run_connect(
        ["--task", "analyze", "--file", str(source), "--sheet", "0",
         "--question", question, "--request-id", "analyze-1"],
        tmp_path, config, records,
    )
    assert completed.returncode == 0, completed.stderr
    body = _single_json(completed.stdout)
    assert set(body) == _ANALYSIS_PUBLIC_KEYS | {"warnings", "fallback_unverified"}
    assert body["task"] == "analyze"
    assert body["fallback_unverified"] is False
    seen = json.loads((records / "analyze-1.json").read_text(encoding="utf-8"))
    assert seen["payload"] == {
        "contract_version": "1.0",
        "request_id": "analyze-1",
        "user_prompt": question.strip(),
        "inputs": [{"path": str(source), "sheet": 0, "display_name": "budget.xlsx"}],
        "profile_name": "generic",
        "analysis_mode": "single",
    }


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--task", "analyze"], "missing_question"),
        (["--task", "analyze", "--question", "   "], "missing_question"),
        (["--task", "analyze", "--question", "가" * 501], "question_too_long"),
        (["--task", "analyze", "--question", "합계\x07"], "invalid_question"),
        (["--task", "summary", "--question", QUESTION], "forbidden_argument"),
        (["--task", "analyze", "--question", QUESTION, "--address", "A1"], "forbidden_argument"),
    ],
)
def test_analyze_argument_failures_do_not_spawn(monkeypatch, tmp_path, args: list[str], code: str) -> None:
    def _blocked(*_a, **_k):
        raise AssertionError("잘못된 인자는 래퍼를 호출하면 안 된다")

    monkeypatch.setattr(connect.subprocess, "run", _blocked)
    exit_code, body = _invoke(
        monkeypatch,
        tmp_path,
        [*args, "--file", "/tmp/a.xlsx", "--sheet", "0", "--request-id", "bad"],
    )
    assert exit_code == 2
    assert body["status"] == "invalid_request"
    assert body["error"]["code"] == code


def _mock(monkeypatch, response: dict, returncode: int) -> None:
    class _Done:
        stdout = json.dumps(response, ensure_ascii=False).encode()
        stderr = b""

    _Done.returncode = returncode
    monkeypatch.setattr(connect.subprocess, "run", lambda *_a, **_k: _Done())


def _args(request_id: str) -> list[str]:
    return ["--task", "analyze", "--file", "/tmp/a.xlsx", "--sheet", "0",
            "--question", QUESTION, "--request-id", request_id]


def test_unverified_fallback_is_flagged(monkeypatch, tmp_path) -> None:
    _mock(
        monkeypatch,
        {
            "status": "success", "request_id": "u", "execution_id": "e", "text": "PandasAI 결과",
            "data": {"shape": [1, 2], "columns": ["부서", "집행률"], "preview_records": [{"부서": "영업", "집행률": 0.5}]},
            "artifacts": [], "error": None, "structural_state": "not_run",
            "warnings": [
                {"code": "unverified_fallback", "message": "not validated", "stage": "analysis"},
                {"code": "bad"},
                "junk",
            ],
        },
        0,
    )
    code, body = _invoke(monkeypatch, tmp_path, _args("u"))
    assert code == 0
    assert body["fallback_unverified"] is True
    assert body["warnings"] == [{"code": "unverified_fallback", "message": "not validated"}]
    assert body["data"]["columns"] == ["부서", "집행률"]


def test_cannot_plan_passes_clarification(monkeypatch, tmp_path) -> None:
    message = "비율을 무엇으로 나눠 구할지 정하지 못했습니다."
    _mock(
        monkeypatch,
        {
            "status": "cannot_plan", "request_id": "c", "execution_id": "e", "text": message,
            "data": None, "artifacts": [], "structural_state": "not_run",
            "error": {"code": "rate_needs_operands", "message": message},
            "warnings": [{"code": "unverified_fallback", "message": "x", "stage": "analysis"}],
        },
        3,
    )
    code, body = _invoke(monkeypatch, tmp_path, _args("c"))
    assert code == 3
    assert body["status"] == "cannot_plan"
    assert body["error"] == {"code": "rate_needs_operands", "message": message}
    assert body["text"] is None and body["data"] is None
    assert body["warnings"] == [] and body["fallback_unverified"] is False


def test_skill_documents_analyze_rules() -> None:
    skill = (connect.Path(connect.__file__).parent / "SKILL.md").read_text(encoding="utf-8")
    for sentence in (
        "--task analyze",
        "질문은 사용자가 한 말을 그대로 `--question`에 넣는다.",
        "이 결과는 검증 단계를 거치지 않았으니 원본과 대조해 확인해 주세요.",
        "질문을 스스로 고쳐 다시 실행하지 않는다.",
        "100을 다시 곱하지 않는다.",
    ):
        assert sentence in skill, sentence
