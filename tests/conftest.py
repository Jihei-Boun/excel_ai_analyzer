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
    table_structure._CACHE.clear()
    yield
