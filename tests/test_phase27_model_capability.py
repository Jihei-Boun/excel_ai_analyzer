"""Phase 27: Model capability harness unit tests (no live LLM)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.benchmark_multi.phase27_compare import (
    COMPARE_KPI_KEYS,
    RESIDUAL_CASES,
    build_comparison_table,
    model_slug,
    write_baseline_freeze,
)
from tests.benchmark_multi.runner import run_suite


def test_model_slug() -> None:
    assert model_slug("qwen2.5:7b") == "qwen2.5_7b"


def test_baseline_freeze_written(tmp_path: Path) -> None:
    path = tmp_path / "baseline_freeze.json"
    freeze = write_baseline_freeze(path)
    assert path.is_file()
    assert freeze["phase"] == 27
    assert "planner_system_sha256" in freeze
    assert freeze["fair_comparison"]["temperature"] == 0
    assert freeze["fair_comparison"]["max_retries"] == 2


def test_comparison_table_schema() -> None:
    fake = [
        {
            "model": "a",
            "metrics": {k: {"mean": 1.0, "min": 1.0, "max": 1.0, "std": 0.0} for k in COMPARE_KPI_KEYS},
            "latency": {"planner_call": {"mean": 0.1}, "suite_wall_seconds": {"mean": 1.0}},
        },
        {
            "model": "b",
            "metrics": {k: {"mean": 2.0, "min": 2.0, "max": 2.0, "std": 0.0} for k in COMPARE_KPI_KEYS},
            "latency": {"planner_call": {"mean": 0.2}, "suite_wall_seconds": {"mean": 2.0}},
        },
    ]
    table = build_comparison_table(fake)
    assert set(table.keys()) == {"a", "b"}
    assert table["a"]["overall_ok_rate"] == 1.0
    assert table["b"]["overall_ok_rate"] == 2.0


def test_runner_results_dir(tmp_path: Path) -> None:
    summary = run_suite(
        live=False,
        save=True,
        results_dir=tmp_path,
        case_ids=["lookup_join_001"],
    )
    assert summary.get("saved_to")
    assert Path(summary["saved_to"]).parent == tmp_path
    assert summary["overall"]["overall_ok_rate"] == 100.0


def test_residual_case_ids_stable() -> None:
    assert "composite_key_join_001" in RESIDUAL_CASES
    assert "lookup_join_001" in RESIDUAL_CASES
    assert "three_file_chain_001" in RESIDUAL_CASES
    assert "dirty_multifile_001" in RESIDUAL_CASES


def test_no_production_semantic_patch_in_phase27_harness() -> None:
    """Harness must not rewrite planner/validator semantics."""
    root = Path(__file__).resolve().parents[1]
    harness = (root / "tests/benchmark_multi/phase27_compare.py").read_text(encoding="utf-8")
    assert "remove_aggregate" not in harness
    assert "domain ==" not in harness
    assert "case_id ==" not in harness
    # Only infrastructure imports of production integrate modules for freeze hashes
    assert "validate_integration_plan(" not in harness
