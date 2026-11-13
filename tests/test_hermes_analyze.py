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
    assert set(body) == _ANALYSIS_PUBLIC_KEYS | {"warnings", "fallback_unverified", "question_sent", "answer_table", "answer"}
    assert body["question_sent"] == question.strip()
    assert body["answer_table"] is None  # 가짜 래퍼는 빈 표를 돌려준다
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
    assert body["answer"].startswith("이 결과는 검증 단계를 거치지 않았으니 원본과 대조해 확인해 주세요.")
    assert body["answer_table"] in body["answer"]


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
    assert body["answer_table"] is None
    assert body["question_sent"] == QUESTION
    assert f"> {message}" in body["answer"]
    assert "질문을 고쳐 다시 물어 주세요." in body["answer"]


def test_skill_documents_analyze_rules() -> None:
    skill = (connect.Path(connect.__file__).parent / "SKILL.md").read_text(encoding="utf-8")
    for sentence in (
        "--task analyze",
        "질문은 사용자가 한 말을 그대로 `--question`에 넣는다.",
        "이 결과는 검증 단계를 거치지 않았으니 원본과 대조해 확인해 주세요.",
        "질문을 스스로 고쳐 다시 실행하지 않는다.",
        "100을 다시 곱하지 않는다.",
        "`answer_table`을 한 글자도 바꾸지 않고 그대로 붙인다.",
        "`question_sent`는 분석기에 실제로 보낸 질문이다.",
        "`answer` 전체를 한 글자도 바꾸지 않고 그대로 옮긴 것이다.",
        "표 뒤에 가장 높다·낮다, 순위, 증감 같은 비교나 판단을 덧붙이지 않는다.",
    ):
        assert sentence in skill, sentence


def test_answer_table_formats_without_changing_values(monkeypatch, tmp_path) -> None:
    _mock(
        monkeypatch,
        {
            "status": "success", "request_id": "t", "execution_id": "e", "text": "결과",
            "data": {
                "shape": [12, 4],
                "columns": ["부서", "예산", "집행률(%)", "비율"],
                "preview_records": [
                    {"부서": "영업|본부", "예산": 1234500.0, "집행률(%)": 41.23456789, "비율": 0.0012345678},
                    {"부서": "연구\n팀", "예산": 0, "집행률(%)": None, "비율": float("nan")},
                ],
            },
            "artifacts": [], "error": None, "structural_state": "not_run", "warnings": [],
        },
        0,
    )
    code, body = _invoke(monkeypatch, tmp_path, _args("t"))
    assert code == 0
    assert body["answer_table"].splitlines() == [
        "| 부서 | 예산 | 집행률(%) | 비율 |",
        "| --- | --- | --- | --- |",
        "| 영업\\|본부 | 1,234,500 | 41.23 | 0.0012 |",
        "| 연구 팀 | 0 |  |  |",
        "",
        "(전체 12행 중 앞 2행)",
    ]
    # 원본 값은 data에 그대로 남는다.
    assert body["data"]["preview_records"][0]["예산"] == 1234500.0


def _success(records, columns, shape=None) -> dict:
    return {
        "status": "success", "request_id": "p", "execution_id": "e", "text": "결과",
        "data": {"shape": shape or [len(records), len(columns)], "columns": columns, "preview_records": records},
        "artifacts": [], "error": None, "structural_state": "passed", "warnings": [],
    }


def test_percent_values_always_show_two_decimals(monkeypatch, tmp_path) -> None:
    _mock(
        monkeypatch,
        _success(
            [{"부서": "영업", "집행률(%)": 0.38281234}, {"부서": "연구", "집행률(%)": 20.0}],
            ["부서", "집행률(%)"],
        ),
        0,
    )
    _, body = _invoke(monkeypatch, tmp_path, _args("p"))
    assert body["answer_table"].splitlines()[2:] == ["| 영업 | 0.38 |", "| 연구 | 20.00 |"]


def test_percent_row_in_transposed_table_shows_two_decimals(monkeypatch, tmp_path) -> None:
    _mock(
        monkeypatch,
        _success(
            [{"항목": "예산", "영업": 1500.0, "연구": 0.5}, {"항목": "집행률(%)", "영업": 0.38281234, "연구": 20.0}],
            ["항목", "영업", "연구"],
        ),
        0,
    )
    _, body = _invoke(monkeypatch, tmp_path, _args("p"))
    assert body["answer_table"].splitlines()[2:] == ["| 예산 | 1,500 | 0.5000 |", "| 집행률(%) | 0.38 | 20.00 |"]


def test_answer_wraps_table_in_korean(monkeypatch, tmp_path) -> None:
    _mock(monkeypatch, _success([{"합계": 4321.0}], ["합계"]), 0)
    _, body = _invoke(monkeypatch, tmp_path, _args("p"))
    assert body["answer"] == "분석 결과입니다.\n\n| 합계 |\n| --- |\n| 4,321 |"


def test_answer_is_none_for_other_failures(monkeypatch, tmp_path) -> None:
    _mock(
        monkeypatch,
        {
            "status": "execution_failed", "request_id": "f", "execution_id": "e", "text": None,
            "data": None, "artifacts": [], "structural_state": "not_run",
            "error": {"code": "x", "message": "실패"}, "warnings": [],
        },
        1,
    )
    code, body = _invoke(monkeypatch, tmp_path, _args("f"))
    assert code == 1
    assert body["answer"] is None


def _with_profile(config, value) -> None:
    body = json.loads(config.read_text(encoding="utf-8"))
    body["analysis_profile"] = value
    config.write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.parametrize("task", ["analyze", "summary"])
def test_operator_config_sets_analysis_profile(tmp_path, task: str) -> None:
    config, records = _fake_wrapper(tmp_path)
    _with_profile(config, "budget")
    args = ["--task", task, "--file", str(FIXTURES / "budget.xlsx"), "--sheet", "0", "--request-id", f"p-{task}"]
    if task == "analyze":
        args += ["--question", QUESTION]
    completed = _run_connect(args, tmp_path, config, records)
    assert completed.returncode == 0, completed.stderr
    seen = json.loads((records / f"p-{task}.json").read_text(encoding="utf-8"))
    assert seen["payload"]["profile_name"] == "budget"


@pytest.mark.parametrize("value", ["", "../budget", "budget profile", 3])
def test_invalid_analysis_profile_does_not_spawn(tmp_path, value) -> None:
    config, records = _fake_wrapper(tmp_path)
    _with_profile(config, value)
    completed = _run_connect(
        ["--task", "analyze", "--file", str(FIXTURES / "budget.xlsx"), "--sheet", "0",
         "--question", QUESTION, "--request-id", "bad-profile"],
        tmp_path, config, records,
    )
    body = _single_json(completed.stdout)
    assert completed.returncode == 2
    assert body["error"]["code"] == "invalid_operator_config"
    assert list(records.iterdir()) == []
