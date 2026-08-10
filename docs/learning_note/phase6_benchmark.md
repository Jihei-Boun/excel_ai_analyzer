# Phase 6 — Benchmark baseline notes

측정 목적의 single-file AnalysisPlan benchmark.
실패를 고치기 위한 question hardcoding은 하지 않는다.

## Commands

```bash
python -m tests.benchmark.generate_datasets
python -m tests.benchmark.runner --deterministic
python -m tests.benchmark.runner --live --model qwen2.5:7b
python -m tests.benchmark.runner --compare benchmark_results/<older>.json
pytest tests/benchmark/test_deterministic.py
```

## Baseline (captured during Phase 6 implementation)

Deterministic (fixed_plan): see `benchmark_results/*` mode=deterministic  
Live model: `qwen2.5:7b` — see latest live JSON under `benchmark_results/`

Key live observation: result accuracy alone is misleading when
`pandasai_fallback_rate` / `legacy_fallback_rate` are high.
