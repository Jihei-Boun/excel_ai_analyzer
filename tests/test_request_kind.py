"""질문 종류 판정: LLM이 고르고, 코드는 정해진 종류인지만 확인한다."""

from __future__ import annotations

import pandas as pd
import pytest

from core.routing.request_kind import (
    KIND_ANALYSIS,
    REQUEST_KINDS,
    classify_request,
    parse_request_kind,
)
from core.routing.route_single import route_single_prompt

DF = pd.DataFrame({"부서": ["A", "B"], "매출": [10, 20]})


def _chat_returning(raw):
    seen: dict = {}

    def _chat(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return raw

    return _chat, seen


@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_each_known_kind_is_accepted(kind: str) -> None:
    chat, _ = _chat_returning({"kind": kind})
    assert classify_request("질문", DF, base_url="u", model="m", chat=chat) == kind


@pytest.mark.parametrize("raw", [{"kind": "pivot"}, {"kind": 3}, {}, ["summary"], None])
def test_unknown_answer_goes_to_analysis(raw) -> None:
    chat, _ = _chat_returning(raw)
    assert classify_request("질문", DF, base_url="u", model="m", chat=chat) == KIND_ANALYSIS


def test_llm_error_goes_to_analysis() -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("ollama down")

    assert classify_request("질문", DF, base_url="u", model="m", chat=_boom) == KIND_ANALYSIS


def test_question_and_columns_are_shown_to_the_llm() -> None:
    chat, seen = _chat_returning({"kind": "schema"})
    other = pd.DataFrame({"매출": [1], "지점": ["x"]})
    classify_request("어떤 열이 있어?", [DF, other], base_url="u", model="m", chat=chat)
    assert "어떤 열이 있어?" in seen["prompt"]
    assert "부서, 매출, 지점" in seen["prompt"]  # 겹치는 열은 한 번만


def test_parse_is_case_and_space_tolerant() -> None:
    assert parse_request_kind({"kind": " Summary "}) == "summary"


def test_env_switch_forces_a_kind(monkeypatch) -> None:
    monkeypatch.setenv("EXCEL_ANALYZER_REQUEST_KIND", "quality")
    assert classify_request("아무 질문", DF, base_url="u", model="m") == "quality"
    monkeypatch.setenv("EXCEL_ANALYZER_REQUEST_KIND", "off")
    assert classify_request("파일을 요약해줘", DF, base_url="u", model="m") == KIND_ANALYSIS


def test_analysis_kind_reaches_the_planner_even_with_summary_words(monkeypatch, route_kind) -> None:
    """'요약한 표'처럼 요약이라는 말이 있어도 분석으로 판정되면 분석기가 받는다."""
    route_kind("analysis")
    calls: list[str] = []

    def _fake_run_analysis(df, prompt, **_k):
        calls.append(prompt)
        return pd.DataFrame({"부서": ["A", "B"], "매출": [10, 20]}), "ok", {}

    monkeypatch.setattr("core.routing.route_single.run_analysis", _fake_run_analysis)
    out = route_single_prompt(
        "부서별 매출을 요약한 표 만들어줘",
        full_df=DF,
        source_df=DF,
        context_label=None,
        base_url="u",
        model="m",
    )
    assert calls == ["부서별 매출을 요약한 표 만들어줘"]
    assert out.meta.get("request_kind") == KIND_ANALYSIS


def test_system_kind_is_recorded_on_the_answer(route_kind) -> None:
    route_kind("schema")
    out = route_single_prompt(
        "어떤 열들이 있어?",
        full_df=DF,
        source_df=DF,
        context_label=None,
        base_url="u",
        model="m",
    )
    assert out.meta.get("request_kind") == "schema"
    assert out.reply
