# Phase 22 — Multi-file Contract Reliability & Complex Chain Stability

## Goal

Phase 20–21 safety / planning capability를 유지하면서
alias · intermediate schema · composite · three-file · unrelated→cannot_plan
**contract residual**을 안정화한다.

핵심 질문:

> Planner가 무엇을 해야 하는지는 맞게 결정했을 때,
> 그 계획이 여러 step을 거치면서도 동일한 column/schema contract를 유지하는가?
> 2-file에서 작동하는 contract가 3-file chain에서도 깨지지 않는가?

---

## Phase 21 baseline (live, qwen2.5:7b, 19×3)

| KPI | Phase 21 |
|-----|----------|
| unsafe_execution | **0%** |
| safe_outcome | **94.74%** |
| overall_ok | **61.4%** |
| cannot_plan | 10.53% |
| unnecessary_cannot_plan | **0%** |
| missing_operation | **0%** |
| wrong_composition | **0%** |
| alias_failure | ~33% |
| composite_failure | ~5.3% |

Deterministic: 100 / 100 / 0. Pytest: 487 passed, 2 skipped.

---

## Residual probe

저장: `benchmark_results/multi/phase22_probe.json`  
소스: `20260811T081653Z_qwen2.5:7b.json` + fresh pipeline traces.

### Findings

| Area | Root cause |
|------|------------|
| **Alias / overall_ok gap** | Ops는 맞는 경우가 많음. Planner가 golden과 다른 **명시 alias**를 발명 (`total_order_amount` vs `total_amount`). Executor는 보통 Planner 선언 alias를 그대로 materialize — Executor drift가 주범이 아님. |
| **True contract drift (P0 fix)** | Validator join collision: `{right.name}__{name}` vs Executor `(_left,_right)`. Result Validator: `alias or column` 수동 계산 (shared `resolve_aggregate_alias` 미사용). |
| **composite_key_join** | Composite keys는 맞는 경우가 있음. 불필요 `aggregate`가 detail 컬럼을 떨어뜨려 missing_columns. |
| **three_file** | Join chain OK; residual은 주로 final alias / select 컬럼. |
| **unrelated** | Relationship=`unrelated`인데 union 시도 → `union_incompatible_schema` 반복 → **failed**(exhausted). 기대는 `cannot_plan`. |
| **budget / join→agg** | Metric alias naming mismatch (golden `실행예산_합계` vs Planner 발명 이름). |

Failure taxonomy 분리: `alias_contract_error` ≠ `semantic_plan_error`.

---

## Alias source-of-truth audit

| Module | Before | After |
|--------|--------|-------|
| `integration_contracts.py` | aggregate alias only | + `JOIN_SUFFIXES`, `join_output_column_names`, `aggregate_output_column_names`, failure-type helpers |
| `integration_plan_types.py` | `materialize_aggregate_metric` | 유지 (shared) |
| `integration_planner.py` | prompt contract | alias/suffix/self-check 최소 보강 |
| `integration_plan_validate.py` | join simulate `{src}__col` | **`join_output_column_names`** + lineage `output_columns` |
| `integration_execute.py` | local `JOIN_SUFFIXES` | **import from contracts** |
| `integration_result_validate.py` | `alias or column` | **`resolve_aggregate_alias`** |
| `integration_pipeline.py` | basic retry | repeated structural contract 감지 + schema observability |

정책 **B 유지**: alias optional + shared default = **source column name** (semantic rename 금지).

이상적 흐름:

```
IntegrationPlan
  → shared alias / join-suffix contract
  → Validator expected schema
  → Executor materialization
  → Result Validator actual contract
```

---

## Intermediate schema propagation

- Validator `_simulate_output`가 op별 structural transform:
  rename / filter / select / union / **join(suffix)** / aggregate(alias)
- lineage에 `output_columns`, join 시 `join_suffixes` 기록
- Pipeline metadata: `expected_schema_by_step`, `actual_schema_by_step`
- Planner에게 정답 intermediate column을 **주입하지 않음** (structural feedback only)

---

## Composite / three-file / unrelated

| Topic | Change |
|-------|--------|
| Composite | Candidate bound 유지. Planner: joint evidence가 강할 때만. Retry: 동일 singleton 반복 금지 feedback (정답 키 지정 금지). |
| Three-file | Join suffix를 shared contract로 맞춤 → bare `status` 참조는 Validator가 catch. |
| Unrelated | `union_incompatible_schema` → ambiguity / `cannot_plan_hint`. Live에서 unrelated가 **cannot_plan 성공**으로 회복. |
| Retry | alias/structural → repair; ambiguity → cannot_plan_hint; repeated same codes → `repeated_structural_contract_failure`. |

---

## Executor / Result Validator

- Executor: shared `JOIN_SUFFIXES`만 (semantic recovery 없음).
- Result Validator: aggregate alias를 shared resolver로 통일.

---

## Hardcoding audit

Production integrate 경로에서 `비용코드` / `실행예산` / `customer_id` 등 **domain decision rule 없음**.  
docs/tests/few-shot abstract example만 허용.

---

## Tests

신규: `tests/test_phase22_multifile_contract_reliability.py`

- shared join suffix SoT / Executor 정렬
- explicit·default aggregate alias propagation
- aggregate→select dependency
- future-key survival (select drops key → join ERROR)
- structural repair feedback (정답 alias 미지정)
- unrelated cannot_plan contract
- repeated alias contract in pipeline
- near-tie unsafe regression
- three-file suffix chain

전체 pytest: **497 passed, 2 skipped**

---

## Deterministic benchmark

```
overall_ok = 100%
safe_outcome = 100%
unsafe_execution = 0%
```

---

## Live benchmark

조건: `qwen2.5:7b`, common 19 cases × 3 runs.

최종 artifact: `benchmark_results/multi/phase22_live_3run_summary.json`  
(runs: `20260811T090832Z`, `T091415Z`, `T092006Z`)

| metric | mean | min | max | std |
|--------|------|-----|-----|-----|
| unsafe_execution | **0.0** | 0 | 0 | 0 |
| safe_outcome | **100.0** | 100 | 100 | 0 |
| overall_ok | **68.42** | 68.42 | 68.42 | 0 |
| pipeline_success | 73.68 | 73.68 | 73.68 | 0 |
| cannot_plan | 15.79 | 15.79 | 15.79 | 0 |
| unnecessary_cannot_plan | **0.0** | 0 | 0 | 0 |
| first_plan_success | 68.42 | 68.42 | 68.42 | 0 |
| retry_success | 15.79 | 15.79 | 15.79 | 0 |
| alias_contract_failure | 31.58 | 31.58 | 31.58 | 0 |
| intermediate_schema_failure | 5.26 | 5.26 | 5.26 | 0 |
| composite_key_success | 0.0 | 0 | 0 | 0 |
| three_file_success | 0.0 | 0 | 0 | 0 |
| join_aggregate_contract_success | 0.0 | 0 | 0 | 0 |
| correct_operation_wrong_result | 31.58 | 31.58 | 31.58 | 0 |
| safe_but_incorrect | 31.58 | 31.58 | 31.58 | 0 |
| validator FP | **0.0** | 0 | 0 | 0 |
| missing_operation | **0.0** | 0 | 0 | 0 |
| wrong_composition | **0.0** | 0 | 0 | 0 |

### Phase 21 → 22 delta (final)

| KPI | P21 | P22 | Δ |
|-----|-----|-----|---|
| unsafe | 0 | **0** | 유지 |
| safe | 94.74 | **100** | +5.26 |
| overall_ok | 61.4 | **68.42** | +7.02 |
| unnecessary_cannot_plan | 0 | **0** | 유지 |
| missing_operation | 0 | **0** | 유지 |
| wrong_composition | 0 | **0** | 유지 |
| unrelated | failed | **cannot_plan OK** | 회복 |
| alias | ~33 | ~31.6 | 소폭 |
| composite / 3-file / join→agg / budget | fail | fail (주로 golden alias/grain) | 잔존 |

중간 측정(soften 전)에서 `retry_recovery` unnecessary cannot_plan / dirty wrong_composition이 잠깐 회귀했으나,
`near_tied=false`면 connect→join 허용 안내 보강 후 최종 3-run에서 해소.

---

## Residuals (Phase 23 후보)

1. **Golden alias mismatch** — ops 맞음에도 `total_amount` vs `total_order_amount` (expected 변경 금지 하에서 LLM naming 변동성).
2. **composite** — 불필요 aggregate / grain.
3. **three_file** — intermediate alias + select column survival.
4. **budget** — `*_합계` naming.
5. **unnecessary cannot_plan** on connect-only when `near_tied=false` (prompt soften으로 완화 시도).

---

## Shadow readiness

| Criterion | Status |
|-----------|--------|
| unsafe ≈ 0 | ✅ |
| safe 안정 | ✅ (100%) |
| alias contract failure 낮음 | ❌ (~32%) |
| validator FP ≈ 0 | ✅ |
| unnecessary cannot_plan 낮음 | ✅ (0%) |
| 2-file composition 안정 | ✅ (wrong_composition/missing_op = 0) |
| composite 기본 안정 | ❌ |
| 3-file 최소 안정 | ❌ |

**권고: B — Phase 23 추가 reliability** (route_multi shadow 보류).

Contract layer(Validator↔Executor join suffix / aggregate alias)는 정렬됨.
overall/safe는 목표 방향으로 상승. 남은 갭은 주로 **Planner golden naming / composite grain / 3-file alias chain**.

---

## 핵심 철학 정리

- Phase 20: 잘못 합치지 않는다.
- Phase 21: 합칠 수 있는 것을 불필요하게 포기하지 않는다.
- Phase 22: 맞게 세운 계획을 중간 이름/schema contract 때문에 실패시키지 않는다.

이번 Phase는 Planner를 더 창의적으로 만든 것이 아니라
**Planner → Validator → Executor 계약을 일관되게** 맞춘 단계이다.
