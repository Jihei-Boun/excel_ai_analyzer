# Phase 27 — Planner Model Capability & Ceiling Evaluation

## Goal

점수 향상이 아니라 **병목 위치 증거** 확보.

독립변수 = **모델만**. Phase 26 production semantics freeze.

## Baseline freeze

| Item | Value |
|------|-------|
| git | `453afc5989ea0ae99cc07eb3bc8bce0f00fc7d71` |
| artifacts | `benchmark_results/multi/phase27/baseline_freeze.json` |
| prompt | ~14,470 chars (~3.6k tokens), 89 bullets, 22× Do-not |
| fair config | temp=0, format=json, max_retries=2, timeout=300 |

## Models compared (local Ollama)

| Role | Model | Params | Quant | Why |
|------|-------|--------|-------|-----|
| A baseline | `qwen2.5:7b` | 7.6B | Q4_K_M | Phase 26 |
| B mid | `qwen3:8b` | ~8B | (local) | stronger mid, installed |
| C strong | `qwen3:32b` | ~32B | (local) | strongest practical installed |

Available but not used: gemma, gemma4, qwen2.5-coder:32b, llama3.3 (size/lineage).

## Architecture feasibility

`tests/test_phase27_plan_feasibility.py` — fixed golden plans for composite / lookup / three-file / dirty **succeed** under current Validator+Executor.

**DSL can express correct finals** (join-only / join-only / join→join→agg / rename→union).

## Residual probe (9 cases × 3)

| KPI | 7b | 8b | 32b |
|-----|----|----|-----|
| overall | 55.56 | 77.78 | **100** |
| composite/lookup/3file/dirty final | 0/0/0/100 | **100**/100/0/100 | **100**/100/**100**/100 |
| unsafe | 0 | 0 | 0 |

## Full live 19 × 3

| KPI | qwen2.5:7b | qwen3:8b | qwen3:32b |
|-----|------------|----------|-----------|
| overall | **73.68** | 84.21 | **100** |
| safe | **89.47** | 94.74 | **100** |
| unsafe | **0** | **5.26** | **0** |
| composite final | 0 | **100** | **100** |
| lookup final | 0 | **100** | **100** |
| three-file final | 0 | 0 | **100** |
| dirty final | **100** | **0** | **100** |
| grain accuracy | 79.17 | 93.33 | 100 |
| column recall | 74.36 | 76.92 | 88.46 |
| understanding fail | 26.32 | 21.05 | 5.26 |
| preservation fail | 21.05 | 5.26 | 0 |
| retry exhausted | 21.05 | 0 | 0 |
| planner latency mean | ~9.6s | ~38.6s | ~140s |
| suite wall mean | ~501s | ~1568s | ~6852s |

Phase 26 baseline **재현** (7b): overall/safe/unsafe/finals exact match.

## Effect size

- 19-case: **1 case ≈ 5.26pp**
- 7b→8b overall +10.53pp ≈ 2 cases; but **unsafe +5.26** (incompatible_union executed)
- 7b→32b overall +26.32pp; residual finals 0→100; wall ~**14×**

## Judgment

### H1 — Model Capability Ceiling (primary)

동일 architecture/prompt에서:

- 32b가 composite/lookup/three-file final 전부 회복
- requirement understanding/preservation 대폭 개선
- retry_exhausted 0
- feasibility + 32b live plans = **architecture sufficiency YES**

### H3 nuance (8b)

- composite/lookup 회복 but three-file final 0, dirty regression, **unsafe overconfidence**
- mid-size ≠ production candidate without safety gate

## Failure fingerprints

| Model | Dominant |
|-------|----------|
| 7b | unnecessary aggregate (composite), select drops keys (lookup), weak retry diversity |
| 8b | three-file projection/alias; dirty wrong target names; incompatible_union unsafe |
| 32b | residual cleared; safety holds; higher cannot_plan on true-hard cases |

## Production code

**No semantic changes.** Harness only: `phase27_compare.py`, runner `results_dir`/`chat_json_fn`, tests, observability artifacts.

## Decision gate → **A (Model Strategy)** + safety caveat

- Architecture redesign **not** first-line (DSL sufficient)
- Prompt simplification optional secondary for 7b saturation
- Next: planner model upgrade / small→strong escalation / routing — **never** promote 8b while unsafe>0
- Shadow: 32b is semantic Shadow-candidate but latency impractical as default; escalate-on-residual better

## Artifacts

- `benchmark_results/multi/phase27/model_comparison_residual_probe.json`
- `benchmark_results/multi/phase27/model_comparison_full_19.json`
- `benchmark_results/multi/phase27/architecture_sufficiency_plans.json`
- `benchmark_results/multi/phase27/prompt_complexity_audit.json`
- `tests/test_phase27_*.py`
