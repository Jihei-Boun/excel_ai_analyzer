# Phase 25 — Final-Output-Aware Planning & Semantic Recovery

## Goal

Planner가 선언한 `final_output_requirements`와 `steps[]`의 consistency를 높이고,
final grain / required fields를 보존하며, retry가 같은 semantic mistake를 반복하지 않게 한다.

## Phase 24 baseline

| KPI | P24 |
|-----|-----|
| unsafe | 0% |
| safe | 89.47% |
| overall_ok | 73.68% |
| semantic_acc | 76.92% |
| grain / structural | 5.26 / 10.53 |
| composite key/join/final | 100 / 100 / 0 |
| three_file join/final | 100 / 0 |
| lookup / dirty final | 0 / 0 |
| retry success / exhausted | 10.53 / 21.05 |

## Residual probe

`benchmark_results/multi/phase25_final_output_probe.json`

| case | primary | root |
|------|---------|------|
| composite | both | join OK → unnecessary aggregate; often declares group to match bad plan |
| lookup | both | select drops product_id; often omitted from declared required |
| three_file | both | join OK; wrong final group columns vs expected labels |
| dirty | understanding-heavy | wrong grain / missing rename |
| unrelated | understanding | forced union → exhausted (should cannot_plan) |
| budget | control | representation-only OK |

## Design

### Contract
- Keep optional `final_output_requirements` (`grain`, `required_columns`)
- **No `identity_columns`** — probe evidence insufficient; keep contract minimal

### Backward dependency reasoning
- Planner prompt: Final-Requirement-First → backward field origin → minimum forward chain → consistency check
- Python does not invent required fields / grain

### Validator
- `required_field_permanently_lost`, `required_field_not_materializable`
- Strong `final_grain_contradiction` ERROR when row-level grain + collapsing aggregate + required fields missing
- Soft grain mislabel remains WARNING when required fields present

### Retry
- `final_requirement_preservation` feedback (no prescribed ops/columns)
- repeated final contract → regenerate diversity
- repair vs regenerate hints

### Executor
- unchanged

### Evaluator
- Phase 23 semantic equivalence unchanged (no relaxation)
- New observability: understanding / preservation / recall / survival
- Validator FP definition tightened (structural rejection ≠ FP)

### Mid-phase note
First live after unrelated-hardening caused three_file `cannot_plan` (join chain 0%).
Prompt balanced: evidence-supported multi-file joins must still be planned.
Final live restores three_file join = 100% and unrelated safe = 100%.

## Live 3-run (final)

`qwen2.5:7b`, 19×3 — `phase25_live_3run_summary.json`

| KPI | mean |
|-----|------|
| unsafe | **0** |
| safe | **94.74** |
| overall_ok | **78.95** |
| semantic_acc | 76.92 |
| true_wrong | **0** |
| grain / structural | 5.26 / 10.53 |
| composite key/join/final | 100 / 100 / **0** |
| three_file join/final | **100** / **0** |
| lookup / dirty final | 0 / 0 |
| unrelated safe | **100** |
| unnecessary_cannot_plan | **0** |
| validator FP | **0** |
| retry success / exhausted | **21.05** / **10.53** |
| req grain accuracy | 86.67 |
| req column recall | 71.79 |
| understanding fail rate | 26.32 |
| preservation fail rate | 15.79 |

### Attribution

```text
P24 overall 73.68
+ production: safe recovery (unrelated cannot_plan), retry recovery
+ production: requirement contract enforcement / feedback
+ LLM variance (dirty still fails; finals not recovered)
+ evaluator change ≈ 0
= P25 overall 78.95
```

## Residuals / Shadow

가장 취약: composite post-join aggregate (understanding declares group),
lookup select drop (preservation), three-file final group columns,
dirty rename omission.

**권고: B — Phase 26 Multi-file Semantic Reliability IV**

이유: overall/safe/retry 개선은 있으나 composite/lookup/three-file/dirty **final = 0** 유지.
Shadow gate의 final reliability 미충족.

## Tests / deterministic

pytest: **528 passed, 2 skipped** (phase25 tests 포함)  
deterministic: **100 / 100 / 0**
