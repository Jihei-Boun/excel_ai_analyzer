# Phase 8 — Planner reliability

목표: 새 operation을 늘리지 않고, 기존 atomic/generic compile을 Planner가 정확히 고르게 한다.

## Live 결과 (`qwen2.5:7b`, 동일 42 cases)

Baseline: Phase 7 `benchmark_results/2026-08-10_123412.json`  
Current: `benchmark_results/2026-08-10_132823.json`

| metric | Phase 7 | Phase 8 | Δ |
|---|---:|---:|---:|
| overall_ok_rate | 64.3% | **69.0%** | +4.8 |
| analysis_plan_direct_rate | 69.0% | **73.8%** | +4.8 |
| fallback_rate | 26.2% | **21.4%** | −4.8 |
| legacy_fallback_rate | 16.7% | **7.1%** | −9.5 |
| pandasai_fallback_rate | 9.5% | 14.3% | +4.8 |
| planner_retry_rate | 26.2% | **21.4%** | −4.8 |
| first_plan_success | 26 | **28** | +2 |
| retry_success | 3 | 3 | 0 |
| retry_exhausted | 8 | **6** | −2 |
| planner_generation_failed (prior) | 6 | **4** | −2 |
| wrong_operation | 6 | 7 | +1 |
| semantic_warning_rate | 4.8% | 4.8% | 0 |

pytest: **290 passed, 1 skipped**

## Phase 7 `planner_generation_failed` 6건 세분

| case | 세분 원인 | raw 형태 |
|---|---|---|
| budget_group_sum_001 | wrong_operation_shape | `metrics:[{"실행예산_합계":"sum"}]` |
| hr_dept_avg_salary_001 | wrong_operation_shape | group_comparison + denominator=`count` |
| sales_compare_regions_001 | missing_required_field | denominator=null |
| sensor_above_mean_temp_001 | wrong_operation_shape | find_items value=`mean(...)` |
| negative_missing_column_001 | invalid_column_reference (safe ok) | 없는 컬럼 |
| negative_missing_product_001 | wrong_operation_shape (safe ok) | 라벨 eq를 numeric_filters |

JSON parse / timeout은 해당 6건에서 없음 → sanitize/compile reject로 empty_plan.

## 변경 요약

1. **Contract** (`analysis_plan_contract.py`): operation required/optional + analysis-form decision guide + 5개 multi-domain few-shot
2. **Compile resilience**: `{col:fn}` / `*_합계` alias, mean-via-count → `fn=mean`, `mean()` filter → `filter_vs_mean`, null denominator compare
3. **fn required**: silent sum default 제거
4. **Ambiguous retry**: sibling ambiguity일 때만 semantic soft retry + role_hints
5. **Duplicate plan**: signature 비교
6. **Observability**: selected_operations/columns, retry_count, semantic_warnings, planner_failure_reason, final_path

## 개선된 case (Phase7 fail → Phase8 ok)

budget_group_sum, hr_dept_avg_salary, hr_high_perf, orders_product_amount,
sensor_above_mean_temp, survey_avg_sat, survey_age_sat

## 잔여 취약점

가장 큰 failure category는 여전히 **wrong_operation** (7):
ranking/rate에 `top_per_group` 오용, ratio 누락, above-mean에서 aggregate 선행 누락 등.

`planner_generation_failed` prior는 6→4로 줄었으나, inventory 일부는 LLM 분산으로 regress.
