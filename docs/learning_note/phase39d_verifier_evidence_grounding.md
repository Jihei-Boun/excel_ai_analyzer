# Phase 39D — Independent Verifier Live Consistency & Evidence Grounding

## Purpose

Phase 39C Gate B held. Independent Verifier generalized offline (C2 16/16,
valid FP 0), but live showed opposite errors:

- **False Pass** (`LIVE-C2-finance`): aspirational column refs accepted as real
- **False Fail** (`LIVE-C2-energy`): valid dual-side join rejected as collapse

Roles remain **R-ROLE-B** (observability only). This phase hardens verifier
**evidence grounding** without planner/DSL/validator redesign.

## Phase 39C Freeze

See `benchmark_results/multi/phase39d/phase39c_freeze.json`.

- HEAD at freeze: `680d10a…`
- Gate B / R-ROLE-B unchanged policy
- No production migration / Shadow resume in this phase

## Confirmed Live Fixtures (canonical, tracked)

| ID | Path | Expected |
|---|---|---|
| LIVE-C2-finance FP | `tests/benchmark_multi/fixtures/phase39d/live_fp_finance.json` | non-pass |
| LIVE-C2-energy FF | `tests/benchmark_multi/fixtures/phase39d/live_ff_energy.json` | pass |

Plus consistency set:
`tests/benchmark_multi/fixtures/phase39d/live_consistency_set.json`
(dual-side / aspirational / valid non-comparison / roles-absent).

## Evidence Audit (pre-change)

| Evidence | Before 39D |
|---|---|
| raw user prompt | yes |
| plan ops/params | yes |
| planner claims / roles | yes (separated) |
| source schemas | no |
| intermediate simulated schemas | no |
| final materialized schema | no |
| unresolved column refs | no |
| actual result values | optional (avoided) |

Hypothesis confirmed:

> Verifier reasoned over textual plan + claims without grounded column
> materialization, so it could accept missing evidence and reject present evidence.

## Root Causes

### False Pass (finance)

Join yields `amount_left` / `amount_right`, but aggregate metrics reference
`actuals.xlsx.amount` / `budget.xlsx.amount` (not in join schema). Aliases
`actual_spend` / `budgeted_spend` are aspirational. Verifier trusted narrative
“join + aggregate actual vs budget” → PASS.

Plan Validator can catch executable missing columns when validation runs; live
Tier-2 path can still surface aspirational plans to the verifier. Verifier must
not treat ungrounded claims as proof.

### False Fail (energy)

Plan is a single join preserving `kwh_left` / `kwh_right`. Verifier invented
“aggregation collapse” and failed a distinction-preserving join.

## Design

Python (`core/integrate/schema_lineage.py`) computes deterministic:

- `step_outputs` / `final_schema`
- `unresolved_column_refs`
- `claimed_columns_absent_from_final`

LLM Verifier keeps all semantic judgment. No keyword/domain routing.

### Ablation modes

| Variant | Mode |
|---|---|
| V0 | none (structure + claims only) |
| V1 | final_schema grounding |
| V2 | intermediate lineage |
| V3 | lineage + explicit non-authoritative claims banner |

## Ablation Results

Source: `benchmark_results/multi/phase39d/ablation_v0_v3.json`

| Metric | V0 | V1 | V2 | V3 |
|---|---|---|---|---|
| LIVE FP → non-pass | **false** | **true** | **true** | **true** |
| LIVE FF → pass | true | true | true | true |
| Dual-side ok | 4/4 | 4/4 | 4/4 | 4/4 |
| Aspirational ok | 3/4 | **4/4** | **4/4** | **4/4** |
| Valid non-comparison | 4/4 | 4/4 | 4/4 | 4/4 |
| Roles-absent valid | 1/1 | 1/1 | 1/1 | 1/1 |
| Offline C2 | 16/16 | 16/16 | 16/16 | 16/16 |
| Offline valid | 18/18 | 18/18 | 18/18 | 18/18 |
| mean latency (s) | 3.41 | 3.54 | 3.36 | 3.44 |

**Selected default: V1 (`final_schema`)** — simplest variant matching V2/V3
primary metrics.

Note: V0 FF already pass after a shared prompt clarification that join
suffixes ≠ aggregation collapse. FP still requires materialization evidence.

## Stability (V1, FP/FF × 3)

`benchmark_results/multi/phase39d/stability_fp_ff_v1.json`:

- FP: fail × 3 / 3
- FF: pass × 3 / 3

Stable under V1.

## Roles Policy

Frozen **R-ROLE-B**. Roles not required for detection; roles-absent valid dual
join still PASSes under V1+.

## Safety / Architecture

- Phase 30 / 39B unit tests PASS
- No domain/keyword routing in `schema_lineage.py`
- No DSL / planner / output_roles ontology changes
- No strong-model bypass
- Shadow remains off

## Gate

### **Gate A**

Live FP/FF corrected; offline C2/valid preserved; unsafe=0 on measured suite;
latency ~3–4s practical.

Still **not** primary production migration — only readiness for limited Shadow
observation.

## Recommended Next Phase

### **A. Resume limited Shadow observation**

Optional follow-ups (not blocking): Planner role recall research (C), semantic
32B recovery (D), join-suffix robustness REC13 (E) as separate P1.
