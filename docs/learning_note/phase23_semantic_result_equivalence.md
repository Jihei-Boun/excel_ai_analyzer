# Phase 23 — Semantic Result Equivalence & Final-Grain Reliability

## Goal

Phase 22의 `safe_but_incorrect ≈ 31.58%`를

1. **평가 false mismatch** (값·metric identity는 맞는데 golden alias/표현만 다름)
2. **실제 semantic/grain/structural wrong result**

로 분리하고, 실제 엔진 문제만 최소 수정한다.

핵심 철학:

> golden alias와 다르다 ≠ 사용자가 요청한 결과가 틀렸다

---

## Phase 22 baseline

| KPI | P22 |
|-----|-----|
| unsafe | 0% |
| safe | 100% |
| overall_ok | 68.42% |
| unnecessary_cannot_plan | 0% |
| alias_contract_failure | 31.58% |
| correct_operation_wrong_result | 31.58% |
| safe_but_incorrect | 31.58% |
| composite success | 0% |
| three_file success | 0% |

---

## Semantic audit

저장: `benchmark_results/multi/phase23_semantic_audit.json`  
소스: Phase 22 live `20260811T092006Z` residual 6건을 plan schema로 재구성·실행.

| case | taxonomy | 근거 |
|------|----------|------|
| join_aggregate_001 | **representation_only** | sum(order_amount) 값 동일, alias만 `total_order_amount`≠`total_amount` |
| multifile_budget_001 | **representation_only** | sum(실행예산)/sum(집행액) 값 동일, alias 어순만 다름 |
| three_file_chain_001 | **structural** (+ alias) | metric 값은 name 매핑 시 동일하나 group key가 `customer_id`≠`customer_name` |
| lookup_join_001 | **structural** | join 후 select가 `product_id` 제거 |
| composite_key_join_001 | **grain** | composite join 후 불필요 aggregate → detail 컬럼 손실 (keys는 맞음) |
| dirty_multifile_001 | **grain** | 행 합치기 요청인데 aggregate로 1행 summary |

**분류 합계 (P22 residual 6):**

- representation-only: **3** (join_agg, budget, three_file*값*)
- structural: **2** (lookup, three_file*key presentation*)
- grain: **2** (composite, dirty)
- true semantic wrong values: **0** (재구성 기준)

\*three_file은 metric representation + structural group column이 공존.

---

## Evaluation levels

| Level | 의미 | overall_ok |
|-------|------|------------|
| L1 Safety | unsafe_execution | 필수 |
| L2 Plan ops | required/forbidden ops | 참고 |
| L3 Structural presentation | exact required_columns (legacy L4_execution) | **단독 fail 아님** |
| L4 Semantic | metric identity + values + grain | **필수** |
| L5 Presentation | alias wording | 기록만 (`representation_only_mismatch`) |

`overall_ok = status_ok ∧ safety_ok ∧ semantic_ok`

---

## Semantic equivalence 정의

동일 (source_column, aggregation fn) lineage로 metric을 매핑.
**문자열 유사도 금지.**

Pass 예:

- expected alias `total_amount`, actual `total_order_amount`
- plan metric: `sum(order_amount)` 양쪽 동일
- values / group keys 일치

Fail 예 (false-pass 방지):

- 같은 alias지만 `sum(qty)` vs `sum(amount)`
- 같은 값이지만 `mean` vs `sum`
- detail 요청인데 aggregate grain
- row count / values mismatch

구현: `tests/benchmark_multi/semantic_compare.py` (benchmark only)

---

## Evaluator vs Production 분리

| 변경 | 파일 | 성격 |
|------|------|------|
| semantic compare + overall_ok | `tests/benchmark_multi/semantic_compare.py`, `evaluate.py`, `metrics.py`, `schema.py` | **Evaluation** |
| final-grain / unnecessary aggregate self-check | `core/integrate/integration_planner.py` | **Production** (최소 prompt) |
| Executor / Result Validator | 변경 없음 | — |

Golden expected YAML **미변경** (alias exact를 semantic gate에서 완화).

Runtime-detectable vs benchmark-only:

- Runtime: Validator schema/contract, Result Validator invariants
- Benchmark-only: golden values / expected alias wording → **production retry 금지**

---

## KPI 상승 분해 (P22 → P23 live)

```text
Phase 22 overall_ok     68.42%
+ evaluator recovery    ~5.26pp   (join_aggregate representation)
+ production net        ~0.00pp   (grain residual 잔존; budget는 이번 live에서 failed로 변동)
= Phase 23 overall_ok   73.68%
```

이론상 P22 동일 plan을 새 evaluator로 재평가하면 representation 2건(join_agg+budget) → **+10.53pp (≈78.95%)**.
실제 live에서는 budget이 union 대신 invalid join 경로로 **failed**가 되어 evaluator 회복분이 1건으로 줄었다.

---

## Live 3-run (qwen2.5:7b, 19×3)

| metric | mean | min | max | std |
|--------|------|-----|-----|-----|
| unsafe | **0** | 0 | 0 | 0 |
| safe | 94.74 | 94.74 | 94.74 | 0 |
| overall_ok | **73.68** | 73.68 | 73.68 | 0 |
| unnecessary_cannot_plan | **0** | 0 | 0 | 0 |
| validator FP | **0** | 0 | 0 | 0 |
| semantic_result_accuracy | 69.23 | — | — | 0 |
| representation_only_mismatch | 5.26 | — | — | 0 |
| grain_mismatch | 10.53 | — | — | 0 |
| structural_mismatch | 10.53 | — | — | 0 |
| true_wrong_result | 0 | — | — | 0 |
| safe_but_semantically_wrong | 21.05 | — | — | 0 |
| composite_key_selection_success | **100** | — | — | 0 |
| composite_final_result_success | 0 | — | — | 0 |
| three_file_join_chain_success | **100** | — | — | 0 |
| three_file_final_result_success | 0 | — | — | 0 |

`semantic_result_accuracy` = success 케이스 중 semantic_equivalent 비율.

---

## Residuals

1. **Grain**: composite / dirty — connect·stack인데 aggregate
2. **Structural final select**: lookup `product_id` drop; three_file `customer_name` vs `customer_id`
3. **Budget variance**: live에서 join 시도 → failed (P22는 alias-only success)

가장 취약: **final grain discipline** + **final column selection**.

---

## Shadow readiness

| Criterion | |
|-----------|--|
| unsafe≈0 | ✅ |
| true semantic wrong values | ✅ 낮음 |
| composite key selection | ✅ |
| composite final result | ❌ |
| three-file join chain | ✅ |
| three-file final | ❌ |
| safe 안정 | ⚠️ 94.7 (failed variance) |

**권고: B — Phase 24 Semantic Reliability II** (route_multi shadow 보류).

---

## Tests / deterministic

- `tests/test_phase23_semantic_result_equivalence.py`
- pytest: **506 passed, 2 skipped**
- deterministic: **100 / 100 / 0**
