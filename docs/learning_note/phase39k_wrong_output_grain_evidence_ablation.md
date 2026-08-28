# Phase 39K — `wrong_output_grain` False-Fail Root Cause & Evidence Ablation

## 1. Executive Summary

Phase 39J live Shadow recorded verifier false-fails on valid rename+join cases **P39J-06** and **P39J-07** (`wrong_output_grain`), triggering **STOP-5 / Gate C**.

Phase 39K offline diagnosis finds:

1. **Deterministic V2.2 materialization is correct** for those plans: `final_schema` retains both side metrics, origins differ, evidence signatures differ, **aggregate op count = 0**, identical-signature metric groups are empty, and executed result columns match materialization.
2. The live verifier **invented unsupported structural claims** (e.g. final schema only has `total_stock` / `total_use_kwh`) that **contradict** materialization and result fingerprints.
3. Offline under frozen V2.2 (`final_schema_expr_partition`), P39J-05/06/07 are **PASS 5/5** — the live FF is **not stably reproducible** offline.
4. Evidence-salience ablations were tested harness-only. **A** and **B** alone each caused a **P39G-11 false-pass** (n=1). No production evidence patch is justified.

**V2.2 evidence sufficiency:** sufficient, but verifier reasoning can hallucinate collapse.

**Gate B.** Migration **not** approved. Shadow remains **OFF**. Next: instrument live verifier payloads and stress intermittency — not a production materialization change.

---

## 2. Baseline

See `benchmark_results/multi/phase39k/baseline_freeze.json`.

| Field | Value |
|---|---|
| materialization | **`final_schema_expr_partition` (V2.2)** confirmed |
| roles | R-ROLE-B (non-authoritative) |
| models | verifier/planner `qwen2.5:7b`; strong `qwen3:32b` |
| Legacy primary / Shadow OFF | yes |
| mid-phase production patch | none |
| regression (39H + 34) | PASS |

---

## 3. Reproduction

Offline suite (`reproduction_cases.json`): P39J-05/06/07, P39E-14, fake-dual (FD* + P39G-11), genuine same-origin (GS*), C2 collapse controls.

### Stability (n=5, V2.2 baseline)

| Case | Expected | Offline verdicts |
|---|---|---|
| P39J-05 | PASS | **pass 5/5** |
| P39J-06 | PASS (live FF) | **pass 5/5** |
| P39J-07 | PASS (live FF) | **pass 5/5** |
| P39G-11 | NON-PASS | fail 5/5 |
| FD1 | NON-PASS | fail 5/5 |
| GS1 | PASS | pass 5/5 |
| C2-W1 | NON-PASS | fail 5/5 |

**Live FF not offline-stable.** This is a different problem than a deterministic missing-evidence FF.

---

## 4. Root Cause

### Observed fact (deterministic)

For P39J-06 (representative):

- ops: `rename_columns`, `rename_columns`, `join`
- `aggregate_op_count = 0`
- `final_schema = [sku, depot_a_stock, depot_b_stock]`
- distinct origins / distinct evidence signatures for the two metrics
- identical-signature metric groups: empty
- result fingerprint columns match `final_schema`

P39J-07 analogous (`zone_id`, `use_kwh_s1`, `use_kwh_s2`).

P39J-05 same structural family; live and offline PASS.

### Planner claims

Non-authoritative; declare dual columns / entity grain. They do **not** claim a collapsed total.

### Verifier inference (live)

P39J-06 evidence claimed aggregation into `total_stock` and absence of side columns — **false**.

P39J-07 claimed collapse to `total_use_kwh` — **false**.

### Manual judgment

Shadow results for 06/07 were **YES** (valid rename+join duals).

### Classification

**A — V2.2 evidence sufficient; verifier reasoning unstable / hallucinated structural collapse.**

Not missing ancestry. Not alias/origin heuristic regression. Not genuine collapse.

---

## 5. V2.2 Evidence Sufficiency

**Choice: sufficient but reasoning unstable / hallucinated.**

Supporting points:

- Materialization already encodes the distinction needed to reject invented totals.
- System prompt already forbids inventing aggregates absent from plan and prefers materialization over narrative collapse when sides remain in `final_schema`.
- Offline trials follow that evidence; live trials sometimes ignore it.

Representation may still benefit from **salience**, but that is not the same as missing facts.

---

## 6. Candidate Evidence (harness-only)

| ID | Observation | Intent |
|---|---|---|
| A_op_boundary | ops sequence, aggregate/union counts, metric columns, signature-group overlap, salient schema/result match | Make anti-collapse facts harder to miss |
| B_no_planner_claims | withhold planner_claims | Test claim contamination |
| A_plus_B | both | Combined |

Properties: deterministic, domain-neutral, no intent inference, harness-injected only.

---

## 7. Ablation Results (n=1 matrix)

| Mode | ok | false_pass | false_fail |
|---|---:|---|---|
| V2.2_baseline | 11/11 | [] | [] |
| A_op_boundary | 10/11 | **[P39G-11]** | [] |
| B_no_planner_claims | 10/11 | **[P39G-11]** | [] |
| A_plus_B | 11/11 | [] | [] |

Interpretation:

- Offline baseline already has **no P39J FF**.
- A or B alone **reopens fake-dual false-pass** (STOP-1 risk if productized carelessly).
- A_plus_B looks clean once, but does not repair a reproducible offline defect; **not** a Gate A production candidate.

---

## 8. Grain Safety

C2 collapse control remained NON-PASS under V2.2 stability (fail 5/5). No Phase 30 grain-hardening reopen observed under baseline.

---

## 9. Provenance / Fake-Dual Safety

P39G-11 / FD1 remained NON-PASS under V2.2 stability (fail 5/5).

Caution: harness candidates A and B each allowed a P39G-11 PASS once — evidence that salience/claim edits can trade FF reduction for FP risk.

---

## 10. Stability

- Valid rename+join anchors: stable PASS offline.
- Live Shadow: intermittent FF (observed in 39J, not reproduced offline).
- Therefore: treat as **attention/hallucination intermittency**, not deterministic evidence gap.

---

## 11. Regression

`tests/test_phase39h_provenance_independence.py` + `tests/test_phase34_generalization.py`: **PASS**.

No production code change shipped in 39K.

---

## 12. Architecture Review

Preserved:

> Python observes structure; LLM judges semantics.

Harness ablations only expose additional **observations**. No Python semantic intent engine. No Shadow enablement. No migration.

---

## 13. Gate

### **Gate B**

Root cause is substantially understood, but:

- live FF not stably reproduced offline
- no justified generalizable production evidence patch
- A/B candidates show fake-dual FP risk

Not Gate A (no ready Shadow-validation candidate). Not Gate C (no safety reopen under frozen V2.2; diagnosis progressed).

---

## 14. Next Recommendation

**Smallest next diagnostic experiment (not migration):**

1. Instrument live Semantic Verifier to persist the **exact payload** (plan_structure + materialization_evidence + planner_claims) alongside verdict.
2. Re-run a **tiny offline stress** (higher n / varied seeds if available) and, if needed, a **future limited Shadow** only after payload diffs explain live vs offline.
3. If a candidate is revisited, require **n≥5** on fake-dual + collapse + P39J family before any freeze.

Do **not** weaken `wrong_output_grain` globally.  
Do **not** migrate.  
Do **not** enable production Shadow in this phase.

---

## Artifacts

`benchmark_results/multi/phase39k/`

- `baseline_freeze.json`
- `reproduction_cases.json` / `reproduction_results.json`
- `root_cause_trace.json`
- `structural_audit_p39j.json`
- `stability_results.json`
- `ablation_matrix.json`
- `regression_results.json`
- `phase39k_summary.json`
