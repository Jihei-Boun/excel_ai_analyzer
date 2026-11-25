"""앱 스위치: EXCEL_ANALYZER_ENGINE=step 이면 분석 질문을 단계별 엔진이 맡는다. 가짜 모델로 확인한다."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from core.analysis import analyzer, step_engine
from core.routing import route_single

DF = pd.DataFrame({"부서": ["가", "나", "나"], "값": [1, 2, 3]})


def _fake_model(replies: list[dict]):
    queue = iter(json.dumps(r, ensure_ascii=False) for r in replies)

    def factory(model, base_url, **_):
        return lambda messages: next(queue)

    return factory


@pytest.fixture
def step_on(monkeypatch):
    monkeypatch.setenv(step_engine.ENGINE_ENV, "step")
    return monkeypatch


def _analyze(prompt: str = "부서별 합계"):
    return analyzer.run_analysis(DF, prompt, base_url="http://x", model="m")


def test_switch_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(step_engine.ENGINE_ENV, raising=False)
    assert not step_engine.enabled()


def test_answer_comes_back_with_group_labels_as_a_column(step_on) -> None:
    code = "result = tables['표'].groupby('부서')['값'].sum().to_frame()"
    step_on.setattr(
        step_engine,
        "ollama_chat",
        _fake_model(
            [
                {"action": "python", "why": "부서별 합계", "code": code, "save_as": "합계"},
                {"action": "answer", "table": "합계", "note": "부서별 합계입니다."},
            ]
        ),
    )
    table, reply, meta = _analyze()
    assert table.to_dict("list") == {"부서": ["가", "나"], "값": [1, 5]}
    assert reply == "부서별 합계입니다."
    assert meta["engine"] == "step" and meta["engine_stopped"] == "answered"
    assert code in meta["code"]


def test_ask_returns_the_question_and_no_table(step_on) -> None:
    step_on.setattr(step_engine, "ollama_chat", _fake_model([{"action": "ask", "message": "어떤 기준인가요?"}]))
    table, reply, meta = _analyze()
    assert table is None
    assert reply == "어떤 기준인가요?"
    assert meta["engine_stopped"] == "asked"


def test_step_limit_is_an_honest_failure(step_on) -> None:
    step_on.setattr(step_engine, "ollama_chat", _fake_model([{"action": "look", "table": "표"}] * step_engine.MAX_STEPS))
    table, reply, meta = _analyze()
    assert table is None
    assert reply == step_engine.NOT_FOUND_REPLY
    assert meta["engine_stopped"] == "step limit"


def test_route_shows_the_engine_answer_without_old_postprocessing(step_on) -> None:
    """옛 후처리는 '합계' 같은 질문 단어를 보고 합계 행을 빼거나 순서를 바꾼다. 엔진 답은 그대로 나가야 한다."""
    answer = "result = pd.DataFrame({'부서': ['합계', '나', '가'], '값': [6, 5, 1]})"
    step_on.setattr(
        step_engine,
        "ollama_chat",
        _fake_model(
            [
                {"action": "python", "code": answer, "save_as": "답"},
                {"action": "answer", "table": "답", "note": "끝"},
            ]
        ),
    )
    outcome = route_single._route_analysis(
        "부서별 값 목록",
        full_df=DF,
        source_df=DF,
        context_label=None,
        base_url="http://x",
        model="m",
        profile_name=None,
        prior_aggregate_df=None,
        prior_aggregate_prompt=None,
        prior_user_prompt=None,
        last_assistant_df=None,
    )
    assert outcome.dataframe.to_dict("list") == {"부서": ["합계", "나", "가"], "값": [6, 5, 1]}
    assert outcome.replace_selection is False and outcome.keep_as_filter is False
