"""Shared test setup."""

from __future__ import annotations

import os

import pytest

from core.io import table_structure


@pytest.fixture(autouse=True)
def _no_llm_table_structure(monkeypatch):
    """Unit tests never call the LLM to read table layouts.

    Tests that exercise the LLM step pass their own fake `ask`. The live benchmark
    (BENCHMARK_LIVE=1) keeps the real step on.
    """
    if os.environ.get("BENCHMARK_LIVE") != "1":
        monkeypatch.setenv(table_structure.ENABLE_ENV, "off")
        # 질문 종류 판정(LLM)도 끈다: 판정 없이는 분석 경로로 간다.
        # 정해진 처리기를 시험하는 테스트는 request_kind를 직접 넘긴다.
        monkeypatch.setenv("EXCEL_ANALYZER_REQUEST_KIND", "off")
    table_structure._CACHE.clear()
    yield


@pytest.fixture
def route_kind(monkeypatch):
    """질문 종류 판정(LLM)을 정해 둔 답으로 바꾼다.

    route_kind("summary") 는 모든 질문을, route_kind({"질문": "schema"}) 는 적힌 질문만
    그 종류로 보낸다. 적히지 않은 질문은 analysis.
    """

    def _set(kinds):
        if isinstance(kinds, str):
            # 하위 프로세스(wrapper)에도 전해지도록 환경 변수로도 고정한다.
            monkeypatch.setenv("EXCEL_ANALYZER_REQUEST_KIND", kinds)
        def _fake(prompt, *_a, **_k):
            if isinstance(kinds, str):
                return kinds
            return kinds.get(prompt, "analysis")

        monkeypatch.setattr("core.routing.route_single.classify_request", _fake)
        monkeypatch.setattr("core.routing.route_multi.classify_request", _fake)

    return _set
