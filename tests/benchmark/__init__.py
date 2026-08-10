"""Phase 6 single-file AnalysisPlan benchmark package."""

from __future__ import annotations

from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
CASES_DIR = BENCHMARK_ROOT / "cases"
DATASETS_DIR = BENCHMARK_ROOT / "datasets"
RESULTS_DIR = BENCHMARK_ROOT.parents[1] / "benchmark_results"
