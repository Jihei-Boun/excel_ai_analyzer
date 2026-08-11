"""Phase 19 multi-file Integration Pipeline benchmark package."""

from __future__ import annotations

from pathlib import Path

BENCHMARK_MULTI_ROOT = Path(__file__).resolve().parent
CASES_DIR = BENCHMARK_MULTI_ROOT / "cases"
DATASETS_DIR = BENCHMARK_MULTI_ROOT / "datasets"
RESULTS_DIR = BENCHMARK_MULTI_ROOT.parents[1] / "benchmark_results" / "multi"
