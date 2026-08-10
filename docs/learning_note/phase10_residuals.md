# Phase 10 — Residual failure 안정화

목표: 새 기능 확장 없이 Phase 9 3-run에서 **반복 residual**을 범용적으로 줄인다.

Baseline: Phase 9 3-run mean (`benchmark_results/phase9/aggregate.json`)  
Current: Phase 10 3-run (`benchmark_results/phase10/run{1,2,3}.json`, `qwen2.5:7b`, 동일 42 cases)

---

## 1. Phase 8 PandasAI fallback 기준값 불일치

**원인: 보고 표기 혼동. metric 정의는 변경되지 않음.**

| source | pandasai_fallback_rate |
|---|---:|
| Phase 7 JSON `2026-08-10_123412.json` | **9.52%** (4/42) |
| Phase 8 JSON `2026-08-10_132823.json` | **14.29%** (6/42) |
| Phase 8 보고서 표 | Phase7=9.5% → Phase8=14.3% |

Phase 9 비교표의 Phase 8 = **14.3%가 올바른 baseline**.  
`tests/benchmark/metrics.py`의 `pandasai_fallback_rate` 계산식은 Phase 6~10 동일
(`pandasai_fallback` case 수 / total).

---

## 2. Residual probe (Phase 9 3-run 공통 패턴)

| case | 공통 패턴 | fail stage |
|---|---|---|
| ambiguous_sales_compare | soft retry가 compare→sort/limit로 변형 | semantic_soft_retry 후 wrong_op |
| sales_top5 / orders_top_* | entity ranking인데 sort→limit only | plan eval wrong_op |
| budget_above_mean_rate | `steps`에 nested `rate_vs_mean`이 drop | aggregate only |
| inventory_below_safety | nested `find_items` → empty_plan | plan_build |
| inventory_stockout | filter_vs_mean (semantic) → P004 누락 | wrong_result |
| dirty_filter_nonzero | value `">0"` / wrong equality | result/plan validation |
| sales_compare_regions | compare metrics dict → `str(dict)` 오탐 | plan_validation_exhausted |

---

## 3–10. 개선 요약

### wrong_compare_shape
- compare_groups metrics dict → column name sanitize
- composition `compare_before_metric`가 dict를 문자열로 오인하던 버그 수정
- groups ≥ 2 필수; metric-column 비교에 compare_groups 사용 금지
- ambiguous compare guide + soft retry에 **composition 유지** 지시
- `sales_compare_regions`: Phase 10에서 **3/3 ok**

### row vs entity ranking + grain hint
- inventory에 `unique_ratio`, `grain_hint` (`repeated_entity_candidate` / `row_id_like`)
- contract: row = sort→limit / entity = aggregate→sort→limit
- validator: entity ranking without aggregate → `entity_ranking_missing_aggregate`
- 효과: wrong_operation↓. 다만 top 케이스는 retry 실패 시 **fallback으로 이동** (direct↓)

### rate_vs_mean
- nested `operation=rate_vs_mean` in steps[] 확장
- composition: rate-vs-mean intent without filter_vs_mean → error
- filter_vs_mean on non-rate source when rate intent → error
- `budget_above_mean_rate`: **3/3 ok**

### inventory / column-vs-column
- nested `find_items` 확장 → `below_safety` **3/3 ok**
- left/right normalize in safety prefs
- embedded `">0"` / `"<안전재고"` parse; column_filters `">0"` → numeric
- filter_vs_mean for threshold/품절 intent → `column_vs_column_misclassified`
- stockout: wrong_result → fallback (재시도가 col-vs-col로 회복 못 함) — **구조적 잔여**

### nested high-level in steps[]
`expand_steps_high_level_ops`: find_items / group_comparison / rate_vs_mean 등이
`steps:[{operation:...}]`로 오면 empty_plan 대신 atomic으로 펼침.
→ below_safety, budget_compare/sales_compare, above_mean_rate, dirty 개선의 핵심.

### fallback 고정 case 분류 (Phase 9)
| case | prior |
|---|---|
| below_safety | planner_generation / empty_plan |
| dirty | result_validation → bad filter shape |
| sales_compare_regions | plan_validation (false compare_before_metric) |

### retry feedback
composition hint 확장 (entity ranking, rate_vs_mean, col-vs-col, compare 유지).

---

## 11. pytest
**312 passed, 1 skipped**

---

## 12–14. Live 3-run vs Phase 9

| KPI | P9 mean | P10 mean | min | max | std |
|---|---:|---:|---:|---:|---:|
| overall_ok_rate | 73.02 | **76.19** | 76.19 | 76.19 | **0.00** |
| analysis_plan_direct_rate | 76.98 | 71.43 | 71.43 | 71.43 | 0.00 |
| fallback_rate | 16.67 | 23.02 | 21.43 | 23.81 | 1.12 |
| pandasai_fallback_rate | 15.08 | **13.49** | 11.9 | 14.29 | 1.13 |
| wrong_operation | 5.33 | **2.67** | 2 | 3 | 0.47 |
| retry_success | 3.67 | 3.0 | 3 | 3 | 0.00 |
| retry_exhausted | 4.33 | 6.67 | 6 | 7 | 0.47 |

### Residual category (failed cases tagged, mean)
| category | P9 (approx) | P10 mean |
|---|---:|---:|
| wrong_compare_shape | high (ambiguous+sales_compare) | 2.67 (ambiguous 잔여) |
| global_ranking_misclassified as WO | sales_top/orders_top | **0** (→ fallback으로 이동) |
| missing_rate_vs_mean | budget_above_mean | **0** (3/3 ok) |
| column_vs_column_failure | below+stockout | **1.0** (stockout only; below fixed) |

---

## 15. 여전히 반복되는 failure (3/3)
- ambiguous_sales_compare (wrong_op / wrong_result)
- orders_product_amount, orders_exclude_cancel (wrong_op)
- sales_top5, orders_top_customers (fallback — entity ranking retry 미회복)
- inventory_stockout (fallback)
- budget_unexecuted_max, hr_compare_depts, sales_region_avg, survey_age_sat (fallback)

---

## 16. multi-file Planner 준비도

**아직 이르게 보는 편이 맞음.**

준비된 점: atomic composition contract, nested high-level 흡수, grain hint, residual category 관측.

부족: entity ranking retry가 정답 composition으로 회복되지 않고 fallback 증가; ambiguous compare; stockout semantic→col-vs-col 회복.  
multi-file 전에 **single-file retry recovery + ranking grain soft guidance**를 더 안정화하는 편이 안전.

---

## 해석
- **wrong_operation 대폭 감소 + overall mean↑ + std=0** → residual 문법 안정화는 성공 쪽.
- **direct↓ / fallback↑** → 더 엄격한 composition validation이 회복 실패를 fallback으로 밀어냄.
- overall만 보고 성공 단정하지 않음; Phase 10은 wrong_op/구조적 inventory/rate/compare에 초점.
