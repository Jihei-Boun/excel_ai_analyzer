# Phase 20 — Multi-file Safety & Composition Reliability

## Goal

Phase 19 live baseline 이후 **기능을 추가하지 않고** 실제 failure를 domain-neutral하게 개선한다.

우선순위:

1. **P0 Safety** — `unsafe_execution_rate` → 0%에 근접
2. **P1 Composition** — `wrong_composition` / `missing_operation` 감소
3. **P2 Relationship quality** — label 과신 방지, observation 강화
4. **P3 Retry diversity** — 동일 integration family 반복 감지

원칙 유지: **LLM decides / Validator checks / Executor executes**.  
`route_multi` 미전환. domain hardcoding / key 자동선택 / plan 자동수정 금지.

---

## Phase 19 baseline (live, qwen2.5:7b, 3-run)

| KPI | Phase 19 |
|-----|----------|
| pipeline_success | 65% |
| safe_outcome | 75% |
| unsafe_execution | **10%** |
| overall_ok | 60% |
| first_plan_success | 70% |
| retry_success | 10% |
| cannot_plan | 15% |
| wrong_composition / missing_operation | ~30% |

취약: `ambiguous_key`(unsafe 100%), union→agg, filter→union→agg, budget, three_file, dirty

---

## Probe 결과

저장: `benchmark_results/multi/phase20_probe.json`

### Unsafe 10% root cause (ambiguous_key)

| 질문 | 답 |
|------|----|
| Planner가 처음부터 잘못 이해? | 부분 — 사용자 “연결해줘” + join_candidate label에 맞춰 단일 key join 선택 |
| CrossFileUnderstanding evidence 잘못? | **핵심** — observation상 `customer_id`/`account_id` near-tie인데 LLM label이 `join_candidate`(과신) |
| sanitizer/parser drop? | 아니오 |
| Validator가 잡아야 할 unsafe를 통과? | **예** — label이 `ambiguous`가 아니면 observational near-tie를 검사하지 않음 |
| retry가 같은 전략 반복? | 해당 case는 첫 plan이 바로 success(unsafe)로 종료 |

구성 실패 요약:

- same_schema 쌍이 `join_candidate`로 과라벨 → join→agg 선택 (union→agg 필요)
- three_file: aggregate 누락
- dirty: rename 후 union 대신 잘못된 grain
- filter_union: unnecessary cannot_plan 일부

---

## Ambiguous vs Composite

| | Ambiguous singleton | Composite |
|--|---------------------|-----------|
| 의미 | 서로 다른 singleton key가 비슷한 evidence | 여러 컬럼을 **함께** 써야 unique |
| Observation | `key_ambiguity_observation.near_tied` + `tied_pairs` + `evidence_gap` | `composite_key_observations` uniqueness/cardinality |
| Python 금지 | best key 선택 | “composite join 하라” 강제 |
| Validator | singleton join이 tied set에 있으면 ERROR `ambiguous_key_selection` | composite(`len(keys)>=2`)는 singleton ambiguity로 취급하지 않음; per-column uniqueness만으로 many_to_many 추정 금지 |

Phase 20 보정: singleton plausibility에 **한쪽 uniqueness ≥ 0.95** 요구  
→ composite part 컬럼(낮은 uniqueness + 높은 overlap)을 near-tie로 오인하지 않음.

---

## CrossFileUnderstanding 변경

- `PairwiseObservation.key_ambiguity_observation`
- `PairwiseObservation.composite_key_observations`
- Relationship LLM prompt: near-tied면 `ambiguous` 선호; `join_candidate`≠must join; schema 정렬 시 same/compatible 선호
- **금지 필드 없음 유지**: best_join_key / recommended_operation / should_join

---

## Planner 변경

- Relationship label = hint only (과신 방지 문구)
- domain-neutral composition decision guide (union→agg, filter→union→agg, join→agg, multi-join)
- 추상 few-shot만 (domain 파일명/키 금지)
- `key_ambiguity_observation.near_tied` 미해결 시 `cannot_plan`
- compact understanding에 ambiguity/composite observation 포함
- `operation_family` meta 기록

---

## Plan Validator 변경

- Observational near-tied singleton join → ERROR (`ambiguous_key_selection`) — LLM label과 독립
- Composite uniqueness lookup (키 순서 무관)
- Composite일 때 per-column many_to_many 추론 스킵
- Feedback: evidence만, 정답 key/op 미지정

**Validator false-positive**: master_detail / compatible union / composite join 단위 테스트로 회귀 방지.

---

## Composition 개선

- Family signature: `union_then_aggregate`, `filter_union_aggregate`, `join_then_aggregate`, `multi_join_chain`, …
- Prompt guide로 composition 선택률 개선 (live: wrong_composition ~30% → ~2%)
- Residual: aggregate **alias 이름** mismatch로 L4 golden 실패 (ops 순서는 맞는 경우가 많음)

---

## Dirty multi-file

- Observation의 normalized name overlap은 representation 수준
- semantic rename 자동 금지 (nonexistent_column로 차단)
- Planner가 명시한 `rename_columns`만 실행 (Executor 변경 없음)

---

## Retry diversity

- `integration_operation_family_signature` + `repeated_integration_family_feedback`
- Pipeline: signature 중복 + family 반복 감지 → evidence-only feedback
- 정답 plan 생성 금지

---

## Hardcoding audit

`core/integrate` production 검색 (`customer_id`, `product_id`, `budget`, `비용코드`, `집행`, `재고`):

- decision rule 없음
- `integration_execute.py` 주석에 “안전재고”는 **자동 semantic rename 금지** 설명용

---

## Executor / Result Validator

- **Executor**: 변경 없음 (Phase 19 deterministic 100% — bottleneck 증거 없음)
- **Result Validator**: 변경 없음

---

## Benchmark case note

`repeated_plan_001`에 `deterministic_only: true` 추가.

이유: live에서 fixed bad plan을 쓰지 않아, 정상 master_detail join success가 “expected=failed → unsafe”로 집계되던 **benchmark artifact**(Phase 19에도 unsafe 5% 기여).  
기존 20 case 삭제/완화 아님 — live 집계에서만 제외.

---

## Deterministic regression

| | Phase 19 | Phase 20 |
|--|----------|----------|
| overall_ok | 100% | **100%** |
| safe_outcome | 100% | **100%** |
| unsafe_execution | 0% | **0%** |

---

## Live 3-run (qwen2.5:7b) — final

Live cases = **19** (`repeated_plan_001` is `deterministic_only`).

| KPI | Phase 19 (20 cases) | Phase 20 final (19) |
|-----|---------------------|---------------------|
| **unsafe_execution** | **10%** | **0.0%** |
| **safe_outcome** | **75%** | **89.47%** |
| pipeline_success | 65% | 63.16% |
| overall_ok | 60% | 42.11% |
| first_plan_success | 70% | 68.42% |
| retry_success | 10% | 15.79% |
| cannot_plan | 15% | 21.05% |
| wrong_composition | ~30% | **0.0%** |
| missing_operation | ~30% | (residual, mostly cannot_plan) |
| validator_false_positive_rate | — | ~5% (proxy; see residual) |

`ambiguous_key` (3/3 runs): **cannot_plan, safe, overall_ok** — unsafe success 없음.

`repeated_plan` live artifact 제거 후 unsafe=0. near-tie uniqueness 보정으로 composite singleton 오탐 완화(단 LLM은 여전히 composite를 자주 cannot_plan).

요약 파일: `benchmark_results/multi/live_3run_summary.json`

---

## Residual failure

1. Aggregate metric **alias** (`total_qty` vs `qty`) → L4 wrong_result (ops composition은 맞는 경우 다수)
2. `compatible_schema_union` — LLM이 `ambiguous` 과라벨 → unnecessary cannot_plan
3. `composite_key_join` — relationship이 `same_schema`로 가고 Planner가 composite join을 못 고름 → cannot_plan
4. Dirty / budget / three_file — grain·alias·column naming

가장 취약 (capability): dirty / budget alias / composite planning / compatible over-ambiguity.

---

## Phase 20 종료 판단

| 기준 | 결과 |
|------|------|
| 1. unsafe_execution 감소 | **10% → 0%** ✅ |
| 2. Validator FP ≈ 0 | 단위 테스트 OK; live proxy ~5% (주로 보수적 cannot_plan) ⚠️ |
| 3. safe_outcome 유지/상승 | **75% → 89%** ✅ |
| 4. composition failure 감소 | wrong_composition **~30% → 0%** ✅ |
| 5. retry exhausted 감소 | 소폭 개선 |
| 6. unnecessary cannot_plan 과다 증가 없음 | 소폭 증가(compatible/composite) — 추적 대상 |

**Safety 목표 달성.** overall_ok 하락은 alias/golden mismatch residual이며 직접 목표가 아님.

---

## route_multi

**전환 보류.**  
unsafe=0·safe↑·composition↑는 확인. 다만 composite/compatible capability residual과 overall_ok(alias) 개선이 Phase 21 과제.

