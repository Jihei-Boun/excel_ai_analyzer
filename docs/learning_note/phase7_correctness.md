# Phase 7 — Failure-driven correctness (same 42-case baseline)

Phase 6가 드러낸 **범용 결함**을 고치고, 동일 live benchmark로 개선을 측정한다.
점수를 맞추기 위한 case별 분기·컬럼 hardcoding·expected 완화는 하지 않는다.

## Live 결과 (`qwen2.5:7b`, 42 cases)

| metric | Phase 6 | Phase 7 | Δ |
|---|---:|---:|---:|
| overall_ok_rate | 45.2% | **64.3%** | +19.0 |
| analysis_plan_direct_rate | 45.2% | **69.0%** | +23.8 |
| fallback_rate | 42.9% | **26.2%** | −16.7 |
| pandasai_fallback_rate | 23.8% | **9.5%** | −14.3 |
| planner_retry_rate | 42.9% | **26.2%** | −16.7 |
| semantic_warning_rate | 0.0% | **4.8%** | +4.8 |

결과 JSON: `benchmark_results/2026-08-10_123412.json`  
baseline: `benchmark_results/2026-08-10_114121.json`

### 도메인별 (ok_rate)
- **inventory 20% → 100%** (가장 큰 개선)
- dirty 66.7% → 100%
- sensor 25% → 75%
- budget 42.9% → 57.1%
- ambiguous 0% → 0% (warning은 발생, 결과 선택은 여전히 취약)

## 수정 요약

### 7A — Aggregate executor
- `fn=mean|count|min|max|median|sum` 실제 적용
- `prefer_subtotals`는 sum 전용
- unsupported → `unsupported_aggregation`

### 7A — Quality routing
- `"문제"` substring 오탐 제거 (주문제외/문제상품)
- bounded token + 결측/중복 콤보

### 7B — Dirty Excel
- normalize: `1,000`, `%`, 공백 컬럼, 빈 열
- sanitize `_resolve_column` (canonicalize/match-key)
- high-level `operation=aggregate` compile + metric 컬럼 보존
- PandasAI int 컬럼명 KeyError 수정

### 7C/D — Ranking / semantic filter
- ranking = sort→limit; single-metric `top_n_difference`도 sort→limit로 컴파일
- column-vs-column filter + `value`에 컬럼명이 오면 right_column으로 해석
- inventory 전용 수식 rule 없음

### 7E — Semantic warning
- Phase 6 `0%`는 detector 미연결이 아니라 **schema sibling 검사 부재**
- `_ambiguous_sibling_column_warnings` 추가 → live 4.8%

### 7F — Fallback reasons
- `planner_generation_failed` / `plan_validation_exhausted` / `result_validation_exhausted` /
  `unsupported_operation` / `execution_error` / `retrieval_fallback` /
  `simple_groupby_fallback` / `pandasai_final_fallback`
- 최종 분포(Phase 7): pandasai_final 7, simple_groupby 4  
  prior: planner_generation_failed 6이 여전히 최대

## pytest
`284 passed, 1 skipped`

## 남은 취약점
- **wrong_operation** (ranking/rate/compare 계획 선택) — 가장 큰 failure category
- ambiguous 컬럼 선택 (warning은 뜨지만 결과가 기대와 불일치)
- 일부 mean/above-mean 케이스는 여전히 planner_generation_failed → fallback
