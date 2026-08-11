"""Deterministic multi-file Integration Pipeline benchmark (no live LLM)."""

from __future__ import annotations

import os

import pytest

from tests.benchmark_multi.generate_datasets import ensure_datasets
from tests.benchmark_multi.runner import run_suite
from tests.benchmark_multi.schema import load_all_cases


@pytest.fixture(scope="module", autouse=True)
def _datasets() -> None:
    ensure_datasets(force=True)


def test_multi_benchmark_datasets_exist() -> None:
    paths = ensure_datasets(force=False)
    assert len(paths) >= 20


def test_multi_benchmark_cases_load() -> None:
    cases = load_all_cases()
    assert len(cases) >= 15
    scenarios = {c.scenario for c in cases}
    for required in (
        "same_schema_union",
        "master_detail_join",
        "lookup_join",
        "join_aggregate",
        "ambiguous_key",
        "many_to_many",
        "three_file_chain",
        "multifile_budget",
        "unrelated",
    ):
        assert required in scenarios, f"missing scenario {required}"


def test_deterministic_multi_benchmark_suite() -> None:
    summary = run_suite(live=False, save=False)
    assert summary["total_cases"] >= 15
    crashed = [c for c in summary["cases"] if "crash" in (c.get("failure_categories") or [])]
    assert not crashed, f"crashes: {[c['case_id'] for c in crashed]}"
    overall = summary["overall"]
    # Safety KPI: deterministic fixtures must not produce unsafe executions
    assert overall["unsafe_execution_rate"] == 0.0
    assert overall["safe_outcome_rate"] >= 80.0
    assert overall["overall_ok_rate"] >= 70.0


@pytest.mark.benchmark_multi_live
def test_live_multi_benchmark_opt_in() -> None:
    if not os.environ.get("BENCHMARK_MULTI_LIVE"):
        pytest.skip("Set BENCHMARK_MULTI_LIVE=1 to run live multi-file Ollama benchmark")
    summary = run_suite(live=True, save=True, model=os.environ.get("BENCHMARK_MODEL", "qwen2.5:7b"))
    assert summary["total_cases"] >= 1
