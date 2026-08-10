"""Deterministic Phase 6 benchmark (no live LLM).

Included in normal pytest. Live suite is separate (`benchmark_live` / runner --live).
"""

from __future__ import annotations

import pytest

from tests.benchmark.generate_datasets import ensure_datasets
from tests.benchmark.runner import run_suite
from tests.benchmark.schema import load_all_cases


@pytest.fixture(scope="module", autouse=True)
def _datasets() -> None:
    ensure_datasets(force=True)


def test_benchmark_datasets_exist() -> None:
    paths = ensure_datasets(force=False)
    assert len(paths) >= 8
    for path in paths.values():
        assert path.is_file()


def test_benchmark_cases_load() -> None:
    cases = load_all_cases()
    assert len(cases) >= 30
    domains = {c.domain for c in cases}
    for required in (
        "budget",
        "sales",
        "inventory",
        "hr",
        "survey",
        "sensor",
        "orders",
        "dirty",
        "negative",
        "ambiguous",
    ):
        assert required in domains


def test_deterministic_benchmark_suite() -> None:
    summary = run_suite(live=False, save=False)
    overall = summary["overall"]
    # CI contract: deterministic fixed_plan cases must not crash the harness
    assert summary["total_cases"] >= 20
    crashed = [
        c
        for c in summary["cases"]
        if c.get("failure_category") == "crash" or c.get("route_observed") == "crash"
    ]
    assert not crashed, f"crashes: {[c['case_id'] for c in crashed]}"
    # Most fixed_plan analytical cases should pass levels
    assert overall["overall_ok_rate"] >= 50.0


@pytest.mark.benchmark_live
def test_live_benchmark_opt_in() -> None:
    """Skipped unless -m benchmark_live and Ollama is available.

    Prefer: python -m tests.benchmark.runner --live
    """
    import os

    if not os.environ.get("BENCHMARK_LIVE"):
        pytest.skip("Set BENCHMARK_LIVE=1 to run live Ollama benchmark in pytest")
    summary = run_suite(live=True, save=True)
    assert summary["total_cases"] >= 1
