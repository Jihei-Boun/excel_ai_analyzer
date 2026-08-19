# Phase 29 — Silent Wrong-Success Diagnostics & Result Observability

## Executive summary

Silent wrong-success is **not one problem**. On frozen Phase 27/28 runs (16 silent instances):

| Observability | Count | Meaning |
|---------------|------:|---------|
| production-observable | 8 | Declared `detail`/`entity` grain + collapsing `aggregate` (already Plan Validator **WARNING**) |
| potentially observable | 2 | Declared required field set under-specified vs user intent |
| fundamentally unobservable | 6 | Internally consistent `group`+`aggregate` wrong intent |

Strong golden-independent candidate:

**`row_grain_with_collapsing_aggregate`** — TP=8, FP=0, FPR=0.0 on 60 valid successes (this corpus).

Production validators were **not** changed. Phase 28 escalation behavior unchanged.

## Silent failure inventory

### Family 1 — declared row grain vs collapsing aggregate

Typical pattern (`composite`, some `three_file` runs):

```text
join (often correct keys)
  → aggregate (collapses rows)
  → select
declared grain = entity
required columns = aggregate aliases (satisfiable)
```

- **Loss stage:** collapse after join under row-level declaration
- **Why validators passed:** `final_grain_contradiction` is WARNING only when required columns remain materializable (Phase 24 softened ERROR→WARNING to avoid FP). Result Validator does not re-check grain vs collapse.
- **Type:** **D** (implementation gap on existing contract signal)

### Family 2 — declared collapsed grain, wrong user intent

Typical pattern (`same_schema`, some `three_file` with grain=group):

```text
union/join → aggregate
declared grain = group
plan is self-consistent
```

- **Loss stage:** planner intent interpretation (user wanted detail-preserving output)
- **Why validators passed:** group+aggregate is internally consistent
- **Type:** **C** (fundamentally runtime-invisible without external semantic judge)

### Family 3 — required field set under-declaration

Some `three_file` runs: declared required columns omit fields expected by user-facing output (`customer_name` etc.) while remaining consistent with the declared set.

- **Type:** **B** (contract under-specification / wrong declaration)

## Failure taxonomy (generic)

1. `declared_row_grain_vs_collapsing_aggregate`
2. `declared_collapsed_grain_wrong_user_intent`
3. `required_field_set_under_declaration`

No scenario-named families.

## Observability matrix

See `benchmark_results/multi/phase29/observability_matrix.json`.

## Contract coverage audit

| Intent | Plan declares? | Plan Validator | Result Validator | Silent gap |
|--------|----------------|----------------|------------------|------------|
| final grain | yes | soft warning on row+agg | info only | soft gate |
| required columns | yes | error if unsatisfiable | presence only | wrong set OK |
| one_row_represents | yes | info | none | unused |
| join keys | yes | structural/safety | amp/unmatched | not the residual |
| aggregate necessity | no | no | no | core C gap |

## Candidate invariants

| Candidate | TP | FP | FN | TN | FPR | Verdict |
|-----------|---:|---:|---:|---:|----:|---------|
| row_grain_with_collapsing_aggregate | 8 | 0 | 8 | 60 | 0.0 | **strong** (narrow hardening candidate) |
| detail_grain_with_aggregate | 0 | 0 | 16 | 60 | 0.0 | weak |
| union_then_aggregate | 6 | 18 | 10 | 42 | 0.3 | reject |
| join_then_aggregate | 10 | 6 | 6 | 54 | 0.1 | reject (hits valid join→aggregate) |

Golden-independent: yes for all four (plan ops + declared grain only).

## Architecture audit

- No scenario/domain/column routing added
- No plan mutation / validator repair / executor inference
- No evaluator relaxation
- Escalation policy untouched
- `route_multi` untouched

## Regression

- New tests: `tests/test_phase29_silent_diagnostics.py`
- Diagnostic module: `core/integrate/result_diagnostics.py` (observe-only)
- Deterministic benchmark: 100/100/0
- Production Result Validator semantics: unchanged

## Artifacts

```text
benchmark_results/multi/phase29/
  silent_failure_traces.json
  failure_taxonomy.json
  contract_coverage_audit.json
  candidate_invariants.json
  invariant_counterexamples.json
  observability_matrix.json
  phase29_kpis.json
```

## Phase 30 recommendation

**Primary: A + D nuance → Result Validator / Plan Validator hardening (narrow)**

Promote `row_grain_with_collapsing_aggregate` from WARNING→blocking **only after** a dedicated FP stress pass (Phase 24 history: ERROR caused live FPs; current frozen corpus shows FPR=0 but re-validate live join→aggregate / enrichment→summary cases).

**Secondary: B — contract extension** for required-field / identity completeness is lower priority and riskier (must not invent fields from prompt).

**Not now: C** — do not invent Result Validator rules for internally consistent wrong `group` plans; needs semantic self-check / shadow comparison research.

Priority:

1. Narrow grain-collapse hardening experiment (observe→gate) with FP budget = 0
2. Keep Phase 28 escalation as-is
3. Defer Shadow / `route_multi` until observable residual family is gated or accepted as model-only
