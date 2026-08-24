# Phase 39B — Observable Roles & Independent Semantic Verifier

## Problem

Phase 38 Shadow observation REC12:

- User asked for a multi-side regional contrast (which regions increased across periods).
- Candidate plan: `union_rows → aggregate(region) → total_sales`.
- Planner labeled `one_row_represents = "sales comparison by region"`.
- Semantic Verifier returned **pass / satisfied** → silent wrong-success.

Generic failure class (Type C2):

> User requires a material distinction across multiple sides, but the plan
> collapses that distinction before the requested contrast can be performed,
> while declaring the collapsed result as if it satisfied the request.

## Phase 38 Evidence

- Telemetry: `data/shadow_telemetry/shadow_20260824.jsonl` line 11
- Frozen fixture: `tests/benchmark_multi/fixtures/phase39b_rec12_collapsed_plan.json`
- Baseline git: `1bd720497a82462a9a434c267f0975333ae46515`
- Baseline re-run (pre-change V1 verifier): **pass / satisfied**

## Phase 39A Decision

**C — Planner observable contract + independent verifier**

| Layer | Owns |
|---|---|
| Planner | semantic plan + optional observable `output_roles` |
| Plan/Result Validator | declared structural consistency only |
| Semantic Verifier | independent user-intent check from raw prompt + ops |
| Escalation | existing Phase 35 path on FAIL/UNCERTAIN |

## Implementation

### Contract (`FinalOutputRequirements`)

Additive optional field:

```json
"output_roles": [
  {"role": "entity_key", "columns": ["..."]},
  {"role": "comparison_side", "side_id": "A", "columns": ["..."]},
  {"role": "comparison_side", "side_id": "B", "columns": ["..."]}
]
```

- Roles limited to `entity_key` | `comparison_side`
- `side_id` is opaque (Python never maps A/B to months/domains)
- Absent `output_roles` remains valid (backward compatible)

### Validators

Structural only:

- role schema validity
- `comparison_side` requires ≥2 distinct `side_id`
- declared role columns present in simulated/final schema
- role columns ⊆ `required_columns` when required_columns present

Forbidden: reading user prompt to invent required roles.

### Planner prompt

Minimal instruction:

- If answering requires preserving ≥2 material sides, declare `output_roles` and materialize them
- If user only wants combined totals / append, do **not** invent comparison sides
- `output_roles` must describe actual final columns, not aspirational labels

### Independent Verifier

- Payload split: `plan_structure` vs `planner_claims`
- System protocol: reconstruct requirements from raw prompt first; claims never ground truth
- Generic op effects (union/aggregate/join/select/rename/filter)
- Explicit: union→aggregate not inherently wrong; wrong only when it destroys required distinction
- Explicit: do not invent contrast for combine/stack/overall-total requests

## Ablation / Metrics

Offline frozen-plan harness: `tests/benchmark_multi/phase39b_ablation.py`  
Artifact: `benchmark_results/multi/phase39b/phase39b_ablation.json`

| Variant | C2 recall (non-pass) | Valid fail FP | Notes |
|---|---:|---:|---|
| Baseline (pre-39B, REC12 only) | 0 (pass) | n/a | REC12 silent pass |
| Independent verifier (B) | **1.0** (6/6) | **0.0** (0/6) | REC12 fail |
| Legacy mixed payload | 1.0 (6/6) | 0.0 (0/6) | Same prompt hardening |

Primary decision rule met:

- C2 silent-wrong recall improved (REC12 + 5 generics)
- Valid union/append/total/dual-side controls remain accepted
- No keyword/filename/domain routing introduced

## Regressions

- `tests/test_phase33_semantic_verifier.py`, `test_phase35_semantic_escalation.py`, `test_phase26_requirement_semantics.py`, `test_phase39b_observable_roles.py`: pass
- unsafe invariant: unchanged (no executor/validator bypass)
- Shadow isolation: unchanged (observational path only; additive contract)

## Failure Analysis

Early independent prompt over-rejected VC3 (“combine two tables”) by inventing a distinction requirement. Fixed with generic instruction: stack/combine/overall total does not imply contrast. C2/REC12 remained fail.

Variant A (planner roles only) was not separately scored with live planner calls in this phase; offline proof focuses on verifier detection of frozen wrong plans (the silent-pass blocker).

## Final Recommendation

**A — Phase 39B validated; proceed to Phase 39C/generalization**

Follow-ups (not blockers):

- Live planner declaration completeness measurement on C2 prompts
- Broader historical Type-C corpus re-score under independent verifier
- Optional telemetry of `output_roles` (additive only)
