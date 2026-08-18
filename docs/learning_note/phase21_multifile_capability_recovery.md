# Phase 21 — Multi-file Capability Recovery

## Goal

Phase 20 safety (`unsafe_execution=0%`)를 **절대 후퇴시키지 않으면서**
representation/alias, composite, over-ambiguity, dirty grain residual을 회복한다.

순서: **Safety → Capability** (역전 금지).

---

## Phase 20 baseline (live, 19 cases after `repeated_plan` deterministic_only)

| KPI | Phase 20 |
|-----|----------|
| unsafe_execution | **0%** |
| safe_outcome | **89.5%** |
| overall_ok | 42% |
| cannot_plan | 21% |
| unnecessary_cannot_plan | ~10% |
| wrong_composition | 0% |
| missing_operation | ~10.5% |

Deterministic: 100/100/0. Pytest: 474 passed, 2 skipped (Phase 20 end).

---

## Residual probe

저장: `benchmark_results/multi/phase21_probe.json`

### Semantic vs representation

| Type | Cases | 예 |
|------|-------|----|
| **structural_contract (alias)** | union_agg, filter_union_agg, join_agg, budget | ops 맞음, alias 누락 → `qty` vs `total_qty` |
| **result_invariant (grain)** | same_schema_union, dirty | union만 필요한데 aggregate 추가 → row_count↓ |
| **ambiguity / over-ambiguity** | compatible (LLM이 near_tie 날조), ambiguous (정상) | |
| **semantic / composite** | composite cannot_plan | fixture가 unique singleton이라 near_tied 유발 + Planner가 composite 미선택 |
| **parse** | three_file (간헐) | `step requires output` |

### 핵심 질문에 대한 답

1. 의미 계획 자체가 틀렸는가? — alias 계열은 **아니오** (composition OK). dirty/same_schema는 불필요 aggregate (grain).
2. ops 맞고 alias만? — **예** (다수).
3. intermediate schema 오예상? — select가 선언되지 않은 alias 참조 시 가능; shared contract로 정렬.
4. Validator≠Executor contract? — alias default는 양쪽 모두 source column명 (일치). Planner가 명시 alias를 안 줌.
5. composite evidence 미전달? — 전달됨. fixture가 singleton unique라 near_tied 발생 + Planner cannot_plan.
6. compatible을 ambiguity가 과도 차단? — observation `near_tied=false`인데 Planner가 near-tie를 **발명**.
7. dirty lineage 끊김? — rename→union까지 OK; 여분 aggregate가 grain 파괴.
8. retry가 failure type 미구분? — Phase 20은 대부분 동일 regenerate. Phase 21에서 분리.

---

## Alias contract

Shared module: `core/integrate/integration_contracts.py`

| 역할 | 규칙 |
|------|------|
| Planner | aggregate metric에 **명시 alias 권장**; 사용자 요청의 named total 사용 |
| Parser | alias 생략 시 `materialize_aggregate_metric` → structural default |
| Default | `default_aggregate_alias(column, fn) == column` (semantic 이름 생성 금지) |
| Validator | schema propagation에 `resolve_aggregate_alias` |
| Executor | 동일 helper로 output column 생성 |

Contract divergence 제거. `amount → 매출합계` 같은 semantic rewrite 금지.

---

## Composite recovery

1. **Fixture bug fix**: `sales_store`/`price_store`를 true composite로 재생성  
   (singleton uniqueness < 1, pair unique). expected_row_count 3→4 문서화.
2. Observation: `constituents_individually_unique`, `composite_improves_uniqueness`  
   — 이미 unique한 컬럼 쌍의 noisy composite 억제.
3. Planner guide: near_tied만 신뢰; composite observation + multi-column 요청 시 composite join 고려.
4. Validator: singleton near-tie 차단 **유지**; composite `len(keys)>=2` 예외 **유지**.

---

## Over-ambiguity

- Relationship prompt: `near_tied==true`일 때만 ambiguous; 약한 겹침≠ambiguity; high schema_sim → same/compatible.
- Planner: observation의 `near_tied`만 신뢰, 공유 컬럼으로 near-tie 발명 금지.
- near-tie absolute strength 유지 (`uniqueness≥0.95`, strong score).

---

## Dirty / grain

- Final grain guide: 행 합치기만 요청 시 union(+rename)만; 합계 요청 시에만 aggregate.
- Representation normalization (whitespace/case) vs semantic rename 분리 유지.

---

## Missing operation / retry

- Planner completeness self-check.
- Failure types: `semantic` / `structural_contract` / `ambiguity` / `result_invariant`.
- Modes: repair (structural) / regenerate (semantic/result) / cannot_plan_hint (ambiguity).
- Feedback는 evidence만 — 정답 key/alias 미지정.

---

## Safety invariant

Phase 20 ambiguous near-tied singleton join block — regression test 고정.  
Capability를 위해 near-tie rule 완화하지 않음.

---

## Hardcoding audit

`core/integrate` production: customer_id/product_id/budget/비용코드 등 **decision rule 없음**.

---

## Executor / Result Validator

- Executor: alias resolve를 shared helper로 정렬 (semantic 변경 없음).
- Result Validator: 변경 없음.

---

## Benchmark notes

- `repeated_plan` deterministic_only 유지.
- composite fixture 정정 + expected_row_count 4 (scenario fidelity; 완화 아님).
- 기존 20 case 삭제 없음.

---

## Tests

`tests/test_phase21_multifile_capability_recovery.py`  
+ Phase 20 ambiguous regression 유지.

---

## Deterministic

overall_ok 100% / safe 100% / unsafe 0% 유지 확인.

---

## Live 3-run (final)

조건: `qwen2.5:7b`, 19 live cases (`repeated_plan` deterministic_only), 3 runs.

| KPI | Phase 20 | Phase 21 final |
|-----|----------|----------------|
| **unsafe_execution** | **0%** | **0.0%** |
| **safe_outcome** | **89.5%** | **94.74%** |
| overall_ok | 42% | **61.4%** (min 57.9 / max 63.2 / std 2.5) |
| cannot_plan | 21% | **10.5%** |
| unnecessary_cannot_plan | ~10% | **0%** |
| wrong_composition | 0% | **0%** |
| missing_operation | ~10.5% | **0%** |
| alias_failure_rate | (high) | ~33% (ops ok, name mismatch residual) |
| composite_failure_rate | high cannot_plan | 5.3% (safe success but wrong key/grain) |
| validator_false_positive | ~5% proxy | **0%** |

Scenario highlights:

- ambiguous_key: cannot_plan / safe / ok (safety 유지)
- compatible / same_schema / union_aggregate / filter_union / dirty: **ok↑**
- composite: safe but wrong_join_key residual
- budget / three_file / join_aggregate: alias naming residual
- unrelated: failed (union 시도) vs expected cannot_plan — safe delivery 없음이나 status mismatch

요약: `benchmark_results/multi/live_3run_summary.json`

---

## Safety incident mid-phase

1차 live에서 `impossible_aggregate`가 string `합산`을 `count`로 우회 → unsafe 5.26%.  
수정: additive 요청 + count-on-string에 evidence feedback 후 반복 시 `cannot_plan` (op/key 선택 아님).  
재측정 후 unsafe **0%** 회복.

---

## route_multi readiness

**권장: B — Phase 22 추가 reliability**

근거:

- Safety invariant 충족 (unsafe=0, safe↑)
- 핵심 2-file composition/union 다수 회복
- 그러나 alias residual ~33%, composite join key, three-file naming, unrelated calibration 남음
- production route 전환 전 alias contract를 Planner가 더 안정적으로 지키는지와 composite 성공률 추가 개선 권장

Shadow/canary는 alias residual이 더 줄면 검토.

---

## Tests

`tests/test_phase21_multifile_capability_recovery.py`  
+ Phase 20 ambiguous regression 유지.

Pytest: **487 passed, 2 skipped**.
