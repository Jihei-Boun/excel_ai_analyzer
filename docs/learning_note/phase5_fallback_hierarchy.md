# Phase 5 — Single-file fallback hierarchy

최종 single-file 분석 흐름:

```text
User Prompt
  → System/Data Router (요약·스키마·결측·품질·컬럼의미)
      ├─ Chart-only display (명확한 차트 명령만)
      └─ Analytical request
            → AnalysisPlan Pipeline
                 → Planner
                 → Plan Validator
                 → Executor
                 → Result Validator (+ semantic soft retry 1회)
                 → Interpreter
            → Lightweight deterministic retrieval
                 → value_match / list_seed
            → legacy_simple_groupby_fallback
                 → 단순 "X별 Y 합계/평균"만
            → PandasAI
```

## Legacy 기능 분류

| 기능 | 분류 | Production 역할 |
|------|------|----------------|
| `build_groupby_aggregate_table` | B | `legacy_simple_groupby_fallback` 래퍼로만 사용 |
| `build_context_aggregate_table` | A / chart helper | single-file analytical path 제거. `route_helpers` 차트용만 |
| `try_condition_row_filter` | A | single-file analytical path 제거. multi-file·단위 테스트만 |
| value_match | C | exact retrieval |
| list_seed | C | 단순 목록 retrieval |
| chart fallback | D | chart-only display; 분석+차트는 Planner 후 렌더 |

A=주경로 제거, B=fallback-only, C=retrieval, D=display

## 제거된 production path

- Router → groupby/context 선점 (Phase 2에서 이미 제거)
- Analyzer analytical path의 `try_condition_row_filter` / `build_context_aggregate_table`
- Analyzer가 `build_groupby_aggregate_table`을 직접 import·호출
- `enable_force_prefs_rewrite` API
- `apply_*_prefs` 의미 rewrite

## `analysis_column_prefs.py`

유지: `apply_safety_column_normalization` (whitespace/case/canonical exact)

제거: operation/numerator/denominator/group rewrite

## Phase 6 준비

- multi-file Planner는 아직 미구현 (multi는 기존 fallback 유지)
- benchmark harness는 다음 Phase에서 구축
