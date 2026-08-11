# Multi-file Integration Pipeline Benchmark (Phase 19)

범용성을 **주장하지 않고 측정**하는 harness입니다. `route_multi`는 전환하지 않습니다.

## Layout

```text
tests/benchmark_multi/
  cases/                 # YAML cases
  datasets/              # generated synthetic xlsx
  generate_datasets.py
  schema.py
  evaluate.py            # Level 1–6
  metrics.py
  runner.py
  test_deterministic.py
  test_evaluator.py
  README.md
benchmark_results/multi/
```

## Commands

```bash
python -m tests.benchmark_multi.generate_datasets
python -m tests.benchmark_multi.runner --deterministic
python -m tests.benchmark_multi.runner --live --model qwen2.5:7b --runs 3
pytest tests/benchmark_multi/
BENCHMARK_MULTI_LIVE=1 pytest -m benchmark_multi_live
```

## Outcomes

- `success` — safe integrated result
- `cannot_plan` — correct refusal (often the right answer)
- `failed` — exhausted / blocked without safe plan

**KPI:** `unsafe_execution_rate ≈ 0` (wrong integration delivered as success).

No legacy / PandasAI / merge_engine fallback in this harness.
