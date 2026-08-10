# Phase 11 — Planner Recovery & Fallback Reduction

목표: **엄격한 composition validation 유지**하면서, 실패한 plan을 Planner retry에서 더 잘 회복시킨다.
(validator 전체 완화 / fallback을 direct로 집계 / case hardcoding 금지)

Baseline: Phase 10 3-run mean  
Current: Phase 11 3-run (`benchmark_results/phase11/`, `qwen2.5:7b`, 동일 42 cases)

---

## KPI (3-run mean, std=0)

| KPI | Phase 10 | Phase 11 | Δ |
|---|---:|---:|---:|
| overall_ok_rate | 76.19 | **85.71** | +9.52 |
| analysis_plan_direct_rate | 71.43 | **85.71** | +14.28 |
| fallback_rate | 23.02 | **9.52** | −13.50 |
| pandasai_fallback_rate | 13.49 | **9.52** | −3.97 |
| wrong_operation | 2.67 | 3.0 | +0.33 |
| retry_success | 3.0 | 2.0 | −1.0 |
| retry_exhausted | 6.67 | **2.0** | −4.67 |
| first_plan_success | 27.0 | **34.0** | +7.0 |

추가 KPI (retry_log / warnings 재집계):

| KPI | mean |
|---|---:|
| repair_retry_success | 0 |
| regenerate_retry_success | 1 |
| semantic_ambiguity (warning 포함 case) | 2 |
| entity_ranking_fallback | **0** |
| validator_false_positive (추정) | **0** |

---

## 1. Phase 10 retry_exhausted category (11A)

Phase 10 run1 exhausted 전수:

| case | repeated pattern | category | final path |
|---|---|---|---|
| sales_top5 / orders_top / survey / budget_unexecuted | aggregate→sort but `매출_합계`/`*_sum` alias | `same_composition_repeated` / aggregate_output_alias | plan_validation_exhausted → PandasAI |
| hr_compare_depts | compare shape 반복 | `same_composition_repeated` | exhausted → PandasAI |
| inventory_stockout | filter_vs_mean 반복 | `wrong_operation_family` / column_vs_column | exhausted → PandasAI |
| sales_region_avg | execution → missing_metric | `partial_fix_but_new_error` | execution_error → fallback |

핵심 발견: entity ranking exhausted는 **aggregate 누락이 아니라** executor가 유지하는 소스 metric명과 불일치하는 **출력 alias** (`X_합계`/`X_sum`) 반복이었다.

---

## 2–3. Retry feedback (11B)

`format_plan_validation_feedback`에 명시:

- Failure category / retry_mode / Invariant / Previous invalid plan / Validation errors
- 짧은 Hint (정답 plan 전체 강제 금지)
- alias invariant: aggregate 이후 sort/select는 **소스 metric명** 사용

---

## 4–5. repair vs regenerate (11C)

| mode | 선택 조건 | 동작 |
|---|---|---|
| `repair` | missing_sort/select/compare field, alias 계열 | 기존 op 시퀀스 유지, 잘못된 field만 수정 요청 |
| `regenerate` | entity ranking / col-vs-col / ratio / empty_plan 등 | 다른 composition으로 재생성 |

Python이 plan을 patch하지 않음. LLM만 수정.

Live: regenerate_retry_success≈1/run (`negative_missing_column`), repair 성공 카운트 0  
(대부분 ranking 회복은 **첫 plan sanitize alias rewrite**로 first_plan_success↑).

---

## 6–7. Entity ranking recovery (11D/E grain)

- sanitize: `X_합계`/`X_sum`/`X_mean` → 소스 `X` rewrite
- grain_hint 문구 명확화 (repeated entity → aggregate before sort; row_id_like → direct sort)

결과:

- `sales_top5_001`, `orders_top_customers_001`: **3/3 direct ok** (entity_ranking_fallback=0)
- Phase 10에서 fallback으로 밀렸던 ranking 계열이 direct로 복귀

---

## 8–9. Ambiguous compare (11E)

`ambiguous_sales_compare_001`: 3/3 **wrong_operation** (direct 실행됨).

- expected: `compare_groups`
- observed: aggregate → ratio → sort → limit
- warnings에 `semantic_ambiguity` / metric alternatives 존재
- soft-retry를 억지로 돌리기보다 ambiguity warning을 남기는 방향은 맞으나, benchmark expected가 compare_groups라 현재는 wrong_operation으로 집계

→ user intent 자체가 ambiguous인 residual. golden 하나에 맞추기보다 semantic_ambiguity 진단이 장기적으로 적합.

---

## 10–11. Stockout / below_safety (11F)

| case | P10 | P11 | 원인 |
|---|---|---|---|
| inventory_below_safety | 3/3 ok (filter_rows left/right) | 3/3 exhausted | Planner가 `filter_vs_mean(재고수량)`만 반복 → validator 정상 거부 → **same_plan_repeated / duplicate_plan** |
| inventory_stockout_risk | exhausted | exhausted | 동일 패턴 |

구분:

- executor/result 문제 **아님** (plan이 validation에서 차단)
- **semantic operation 선택 + regenerate 회복 실패**
- inventory 전용 공식은 넣지 않음; quantity↔threshold role hint + regenerate feedback만 유지
- Phase 10 대비 below_safety는 **회귀** (첫 plan 품질 변동 + regenerate가 동일 서명 반복)

---

## 12. Fallback taxonomy (11G)

Phase 11 fallback 4/42 (=9.52%) 분해 (3-run 동일):

| bucket | count/run | 비고 |
|---|---:|---|
| planner_exhausted → PandasAI | 2 | below_safety, stockout |
| expected_negative → PandasAI | 2 | negative_* (의도된 safe path) |
| retrieval / simple groupby / sanitize | 0 | — |

**Planner가 처리해야 하는 fallback** = inventory 2건. negative 2건은 줄일 대상 아님.

---

## 13. Validator false positive (11H)

- entity ranking / derived metric / nested compile에서 **정상 plan 차단 증거 없음**
- inventory 거부는 filter_vs_mean 오용 → true positive
- validator 완화하지 않음

---

## 14. Nested plan drop (11I)

Phase 10 `expand_steps_high_level_ops` 유지. Phase 11 exhausted에 empty_plan/nested-drop 패턴 **재발 없음**.

---

## 15–16. Benchmark

- 42-case / expected 변경 없음
- `qwen2.5:7b` × 3, overall/direct/fallback **std=0**

pytest: **317 passed, 1 skipped** (Phase 11 추가 테스트 포함)

---

## P10 → P11 주요 케이스 이동

개선: sales_top5, orders_top, survey_age_sat, sales_region_avg, budget_unexecuted, hr_compare_depts  
회귀: inventory_below_safety, hr_high_perf (wrong_result, row_count)  
잔여: ambiguous_sales_compare, orders_product_amount, orders_exclude_cancel, stockout

---

## 가장 큰 residual

1. **inventory col-vs-col semantic + regenerate duplicate** (below_safety 회귀 + stockout)
2. **ambiguous compare** (intent 모호 / wrong_operation)
3. orders aggregate 누락 계열 wrong_operation

---

## Single-file 종료 / multi-file

- single-file AnalysisPlan 경로의 recovery 핵심(alias sanitize, repair/regenerate, feedback)은 동작
- multi-file Planner는 **아직 불필요** — 잔여는 semantic recovery(특히 col-vs-col regenerate)와 ambiguous intent 정책
- multi-file 전환 전 우선순위: inventory regenerate가 동일 plan을 반복하지 않도록 feedback/diversity 강화 (validator 완화 없이)

---

## 해석

Phase 11 핵심 목표(direct↑, exhausted↓, fallback↓, wrong_operation 낮은 수준 유지)는 충족.
overall만으로 성공 판단하지 않더라도, direct=overall=85.71이며 fallback의 절반은 expected negative이다.
남은 과제는 **틀린 첫 plan을 두 번째 시도에서 col-vs-col로 바꾸는 regenerate 품질**이다.
