# Phase 26 — Requirement Semantics & Final Projection Reliability

## Goal

Planner가 최종 결과의 의미(grain / required fields / projection)를 더 정확히 이해하고,
IntegrationPlan 전체에서 끝까지 보존하도록 한다.

## Phase 25 baseline

| KPI | P25 |
|-----|-----|
| overall_ok | 78.95% |
| safe | 94.74% |
| unsafe | 0% |
| composite/lookup/three-file/dirty final | 0/0/0/0 |
| grain accuracy / column recall | 86.67 / 71.79 |
| understanding / preservation fail | 26.32 / 15.79 |
| final-contract retry | ≈0 |

## Probe

`benchmark_results/multi/phase26_requirement_semantics_probe.json`

| case | primary |
|------|---------|
| composite | understanding (group/entity+aggregate after correct join) |
| lookup | preservation (select drops join key) |
| three_file | understanding (wrong final group columns) |
| dirty | understanding (rename omitted) → **fixed in live** |

### identity_columns

**Not introduced.** Residual is wrong grain/projection, not identity-vs-required ambiguity.

Optional **`one_row_represents`** added (Planner phrase; Validator observability only).

## Production changes

| Area | Change |
|------|--------|
| Planner | one-row grain meaning; projection check; join keys in required for entity/detail; rename-before-union; no reverse-engineering requirements |
| Contract | optional `one_row_represents` |
| Validator | `join_key_dropped_in_final_projection` ERROR (detail/entity select drops join keys) |
| Retry | Declared/Observed/Invariant feedback; projection/grain/field families; union rename hint |
| Executor | unchanged |
| Evaluator | observability only (no relaxation) |

## Live 3-run (final)

`qwen2.5:7b`, 19×3 — `phase26_live_3run_summary.json`

| KPI | mean |
|-----|------|
| unsafe | **0** |
| safe | 89.47 (−5.27 vs P25) |
| overall_ok | 73.68 (−5.27) |
| dirty_final | **100** (P25: 0) |
| composite/lookup/three-file final | 0 / 0 / 0 |
| three_file join / composite key·join | 100 / 100 |
| unrelated safe | 100 |
| unnecessary_cannot_plan | 0 |
| validator FP | 0 |
| final-contract retry success | **5.26** (P25: ≈0) |
| column recall | 74.36 |
| declared rate | 84.21 |
| retry_exhausted | 21.05 |

### Attribution

```text
P25 overall 78.95
+ dirty rename+union recovery (production)
+ final-contract retry > 0
− lookup/rename_join failed under join_key_dropped ERROR (retry not repairing)
− same_schema unnecessary aggregate variance
− evaluator ≈ 0
= P26 overall 73.68
```

## Residuals / Shadow

- **Improved:** dirty final 0→100
- **Still 0:** composite, lookup, three-file final
- **Regression:** safe/overall (stricter projection gate; LLM retry weak)
- **Most fragile:** composite (declares entity+aggregate totals), lookup (select drops keys, retry loops)

**Shadow Gate: C — Planner model limitation 검증 필요**
(+ B reliability still needed for remaining finals)

근거: dirty recovery proves contract direction works, but composite/lookup/three-file
remain 0 under qwen2.5:7b despite stronger prompts/validation. Recommend Phase 27
model comparison before more prompt stacking.
