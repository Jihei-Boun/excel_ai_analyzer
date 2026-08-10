# Phase 9 — Operation composition 안정화

목표: 새 도메인 op 없이, Planner가 **이미 있는 atomic ops를 올바른 순서·조합**으로 고르게 한다.

기준 비교: Phase 8 single-run `benchmark_results/2026-08-10_132823.json`  
Phase 9: **3-run mean** (`benchmark_results/phase9/run{1,2,3}.json`, model `qwen2.5:7b`, 동일 42 cases)

---

## Live 3-run 요약 (mean 우선)

| metric | Phase 8 (1-run) | Phase 9 mean | min | max | std |
|---|---:|---:|---:|---:|---:|
| overall_ok_rate | 69.0% | **73.02%** | 69.05 | 76.19 | 2.97 |
| analysis_plan_direct_rate | 73.8% | **76.98%** | 73.81 | 78.57 | 2.24 |
| fallback_rate | 21.4% | **16.67%** | 14.29 | 21.43 | 3.37 |
| pandasai_fallback_rate | 14.3% | **15.08%** | 14.29 | 16.67 | 1.12 |
| wrong_operation | 7 | **5.33** | 5 | 6 | 0.47 |
| first_plan_success | 28 | **28.67** | 28 | 29 | 0.47 |
| retry_success | 3 | **3.67** | 3 | 4 | 0.47 |
| retry_exhausted | 6 | **4.33** | 3 | 6 | 1.25 |

pytest: **303 passed, 1 skipped**

Run3은 Phase 8과 비슷한 69%로 분산이 있음 → **단일 run 개선으로 단정하지 않음**. mean 기준으론 overall/direct↑, wrong_operation↓.

---

## 1. Phase 8 wrong_operation 7건 category

| case | category | Phase 8 selected | expected shape | fail stage | retry repeat? |
|---|---|---|---|---|---|
| ambiguous_sales_compare_001 | wrong_compare_shape | aggregate→sort→limit | aggregate→compare_groups | plan eval (missing compare) | soft retry 후도 compare 누락 |
| budget_execution_rate_001 | missing_ratio | aggregate only | ratio_of_aggregates | plan eval | first success로 통과→wrong ops |
| budget_top_rate_001 | misused_top_per_group (+ missing_ratio) | top_per_group (+sort) | ratio→sort→limit | plan eval | 없음 |
| inventory_max_value_001 | global_ranking_misclassified / missing_metric_before_sort | aggregate only | sort→limit (row metric) | plan eval | 없음 |
| inventory_stockout_risk_001 | unsupported_composition (col-vs-col) | filter_vs_mean | filter_rows left/right | plan eval | 없음 |
| sales_above_mean_rep_001 | missing_metric_before_sort (aggregate 선행) | filter_vs_mean only | aggregate→filter_vs_mean | plan eval | 없음 |
| sensor_max_temp_time_001 | global_ranking_misclassified | filter_vs_mean→sort (no limit) | sort→limit | plan eval | 없음 |

---

## 2–6. Composition contract / validator / decision guides

### Contract (`analysis_plan_contract.py`)
- Ranking decision tree: global = metric→sort→limit / group-wise = top_per_group / rate = ratio→sort→limit
- Ratio / compare guides: compare는 late step; rate는 explicit `name`
- Composition contracts: sort 의존성, limit 용도, top_per_group 전제, ratio name, compare metric 선행
- `COMPOSITION_CATEGORIES` + `composition_category_from_issues` / `plan_composition_category`

### Validator (`analysis_plan_validate.py`)
- `_validate_plan_composition`: missing ratio name, sort on missing derived, rate sort without ratio/derive, compare before metric, top_per_group+limit, global top에 top_per_group 오용, rate request without ratio, ranking missing limit, extremum via filter_vs_mean
- **정답 plan을 만들지 않음** — error feedback만

### Guides (prompt)
- Global vs group-wise: “별”만으로 판단 금지 → **그룹 내부 재순위 여부**
- Ratio: 대비/비율/률/rate/ratio/목표·예산 대비/실적률 — semantic guide only
- Compare: metric 생성 후 compare_groups

---

## 7. High-level op 정리

유지 (generic sugar): `group_comparison`, `top_n_per_group`, `top_n_difference`, `rate_vs_mean`, `find_items`, `split_by_difference`  
Legacy alias 유지·확대 금지: `execution_rate_compare`, `execution_rate_vs_mean`, `budget_change_split`  
신규 domain op 금지. `find_items` op=max/min → sort→limit redirect 추가.

---

## 8. Ratio output naming

- Contract: `ratio_of_aggregates` **required `name`**; 이후 sort/compare가 동일 name 참조
- Sanitize: name 생략 시 default `"비율"` (executor와 일치)
- Validator: empty name → `missing_ratio_name`
- Observability: ratio name을 selected_columns에 포함

---

## 9–10. Retry feedback / 반복 방지

- Composition-specific hints in `format_plan_validation_feedback`
- Pipeline: plan signature + **composition category** 비교 → `repeated_failure_category` metadata
- Retry에 “같은 composition 반복 금지” 힌트

---

## 11–15. Benchmark / inventory / residual

### Phase 8 → Phase 9에서 개선된 wrong_op (구조적으로 자주 사라짐)
budget_execution_rate, budget_top_rate, inventory_max_value, sales_above_mean_rep, sensor_max_temp  
inventory_stockout: wrong_operation → **wrong_result** (ops 형태는 가까워졌으나 결과 불일치)

### 잔여 wrong_operation (3-run 공통)
- `ambiguous_sales_compare_001` — wrong_compare_shape (sort/limit로 대체)
- `budget_above_mean_rate_001` — rate_vs_mean / filter_vs_mean 누락
- `orders_exclude_cancel_001`, `orders_top_customers_001`, `sales_top5_001` — 집계 후 ranking 누락 또는 aggregate 없이 detail sort

### planner_generation_failed
failure_category `plan_generation_error`는 3-run 모두 0 (exhaust 후 fallback으로 흡수).  
retry_log의 `empty_plan` attempt는 run당 3~4건 잔존.

### retry recovery
retry_success mean 3.67 (Phase 8: 3), retry_exhausted mean 4.33 (Phase 8: 6) → 소폭 개선.

### Inventory regression 판단
3-run inventory overall **60%로 고정** (분산 아님).  
- max_value: Phase 9에서 안정적 ok (composition 수정 효과)  
- below_safety: 3/3 fallback  
- stockout_risk: 3/3 wrong_result  
→ inventory 잔여는 **구조 문제**(col-vs-col / safety filter) + 일부 fallback, 단순 LLM 분산만은 아님.

### 가장 큰 잔여 failure
1. **wrong_operation** (mean 5.33) — 특히 compare 누락 + global ranking에서 aggregate 여부 혼동  
2. **fallback** (mean 4.33) — dirty/inventory/sales_compare 등  
3. **wrong_result** inventory_stockout 안정 실패

---

## 변경 파일
- `core/analysis/analysis_plan_contract.py`
- `core/analysis/analysis_plan_validate.py`
- `core/analysis/analysis_plan_compile.py` (extremum redirect, group_comparison note)
- `core/analysis/analysis_pipeline.py` (composition retry meta)
- `tests/test_phase9_composition.py`
- `tests/benchmark/metrics.py` (`_json_safe` — tuple key JSON 저장 오류 수정)
