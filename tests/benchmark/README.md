# Phase 6 — Single-file AnalysisPlan Benchmark

범용성을 **주장하지 않고 측정**하기 위한 harness입니다.

## Layout

```text
tests/benchmark/
  cases/                 # YAML cases (dataset + question + expected)
  datasets/              # generated synthetic xlsx (reproducible)
  generate_datasets.py
  schema.py
  evaluate.py            # Level 1–4 checks (not exact plan JSON)
  metrics.py
  runner.py
  test_deterministic.py  # CI
  README.md
benchmark_results/       # live/deterministic JSON outputs (gitignored contents)
```

## Case schema (optional fields allowed)

```yaml
id: sales_group_sum_001
dataset: sales_basic.xlsx
domain: sales
profile: sales
question: "상품별 매출 합계를 알려줘"
fixed_plan: { ... }          # deterministic only
expected:
  route: analysis_plan       # system|retrieval|analysis_plan|legacy_fallback|pandasai|failure_safe
  required_operations: [aggregate]
  expected_columns:
    group_by: 상품
    metric: 매출
  expected_result:
    상품A: 225000
  allow_semantic_warning: true
  expect_safe_failure: true
  interpreter_grounding: true
```

Exact full-plan JSON match is **never** required.

## Evaluation levels

1. Routing correctness  
2. Plan correctness (required ops/columns — semantic/structural)  
3. Execution correctness (golden result)  
4. Interpretation grounding (heuristic; optional)

## Metrics

- routing_success_rate, plan_valid_rate, execution_success_rate / result_accuracy  
- analysis_plan_direct_rate, legacy_fallback_rate, pandasai_fallback_rate, fallback_rate  
- first_plan_success / retry_success / retry_exhausted / planner_retry_rate  
- semantic_warning_rate  
- failure categories (routing_error, wrong_column, …)

## Commands

```bash
# regenerate fixtures
python -m tests.benchmark.generate_datasets

# deterministic (CI / default pytest)
python -m tests.benchmark.runner --deterministic
pytest tests/benchmark/test_deterministic.py

# live Ollama (not in default pytest)
python -m tests.benchmark.runner --live --model qwen2.5:14b
BENCHMARK_LIVE=1 pytest -m benchmark_live

# compare two result JSONs
python -m tests.benchmark.runner --compare benchmark_results/<older>.json
```

## Policy

Benchmark failures are expected findings. Do **not** add question-specific hardcoding
to inflate scores. Use results to guide Planner / validator / hint improvements.
