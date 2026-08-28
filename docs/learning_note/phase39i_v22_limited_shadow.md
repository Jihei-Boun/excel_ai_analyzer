# Phase 39I — V2.2 Limited Shadow Re-observation

## 1. Executive Summary

Frozen **V2.2 `final_schema_expr_partition`** was observed on **13** production-like Shadow requests (session-only Shadow; Legacy primary).

- **SILENT_WRONG = 0**
- **Fake dual did not PASS** (P39I-12 blocked at plan validation)
- **Valid dual accepted** on live case P39I-05; **P39E-14-family anchor incomplete** (pipeline exception)
- **Same-origin partitioned dual incomplete** (P39I-07 timeout / cannot_plan)
- **Verifier false-fail = 1** (isolated: P39I-09 rename+join misread as collapsed grain)
- **unsafe = 0**; isolation intact; no mid-run candidate changes
- Coverage **13/13**; Shadow restored **OFF** after session
- **Gate B**. Next: **A. Another limited Shadow observation** (anchors + same-origin + FF watch). Migration **not** approved.

## 2. Phase 39H Freeze

See `benchmark_results/multi/phase39i/baseline_freeze.json`.

| Field | Value |
|---|---|
| git HEAD | `680d10a522d5adf575974752891ba2765086cfd8` |
| materialization | **`final_schema_expr_partition`** (confirmed) |
| semantic escalation materialization | same V2.2 |
| planner / verifier | `qwen2.5:7b` |
| strong | `qwen3:32b` |
| output_roles | R-ROLE-B |
| Shadow production default | `MULTI_SHADOW_ENABLED=false` |
| Phase 39H | Gate A; offline 13/13; regression 36/36; tiny-live silent_wrong=0 |

Observation did **not** start under V2/V2.1.

## 3. Observation Configuration

Session-only:

- `MULTI_SHADOW_ENABLED=true`
- `MULTI_SHADOW_SAMPLE_RATE=1.0`
- `inline_for_tests=false`
- `concurrency=1`
- Telemetry: `benchmark_results/multi/phase39i/telemetry/`
- Soft Shadow timeout marker: 600s (cooperative; does not hard-kill LLM)

After pilot: **Shadow returned OFF**.

No mid-run changes to planner/verifier prompts, evidence, roles, validator, executor, escalation, retry, Shadow scheduling, `route_multi`, or labels.

## 4. Request Mix

| Group | N | IDs |
|---|---:|---|
| A normal multi-file integration | 2 | 01–02 |
| B aggregate after integration | 3 | 03–04, 13 |
| C genuine dual (rename / agg / same-origin) | 3 | 05–07 |
| D fake-dual pressure | 2 | 08–09 |
| E cannot-plan / ambiguous | 1 | 10 |
| F anchors (P39E-14 / P39G-11 families) | 2 | 11–12 |
| **Total** | **13** | |

Natural prompts only (no provenance/verifier jargon). Not a replay of Phase 39H fixture wording.

## 5. Shadow Coverage

| Metric | Value |
|---|---:|
| Eligible | 13 |
| Observed records | 13 |
| Missing | 0 |
| Capacity skip | 0 |
| Coverage | **1.0** |

## 6. Legacy Correctness

| Label | N |
|---|---:|
| YES | 5 |
| PARTIAL | 6 |
| NO | 0 |
| N/A | 1 |
| INDETERMINATE | 0 |

Legacy remained user-facing primary. Several PARTIAL cases reflect incomplete Legacy joins/columns vs full intent.

## 7. Shadow Correctness

| Label | N |
|---|---:|
| YES | 5 |
| PARTIAL | 0 |
| NO | 2 |
| CORRECT_CANNOT_PLAN | 1 |
| INDETERMINATE | 4 |
| N/A | 1 |

YES: 01, 02, 03, 05, 08, 09 (09 recovered after FF escalation).  
NO: 04 (inappropriate cannot_plan), 13 (invalid derived metric / failed).  
INDETERMINATE: 06, 07, 11 (+ operational gaps).  
CORRECT_CANNOT_PLAN: 10.

## 8. Silent Wrong Review

**SILENT_WRONG = 0**

No case with manual Shadow **NO** + verifier **PASS**.

Closest scrutiny:

- **P39I-08** (D pressure): PASS, but realized **genuine** dual join — not fake dual.
- **P39I-12** (fake-dual family): never reached verifier PASS.

## 9. Verifier False-Fail Review

**Count = 1 (isolated)**

### P39I-09 — verifier FF

- Plan: rename `cost`→`batch1_cost` / `batch2_cost` + join on `sku_id`
- Result columns: `sku_id`, `batch1_cost`, `batch2_cost` — **manual YES**
- Verifier: **FAIL** `wrong_output_grain` claiming costs collapsed to a single metric (false description of the plan/schema)
- Effect: **unnecessary semantic 32B**; final path `semantic_escalation_success` with still-correct dual plan
- Family: rename+join dual (P39E-14-adjacent)
- **STOP-2 not met** (need ≥2 same-family FF)

P39I-06 FAIL was **correct** rejection of collapsed `total_hours` — not FF.

## 10. Fake Dual-Side Review

| Case | Realized structure | Verifier | Outcome |
|---|---|---|---|
| P39I-08 | Genuine join of two windows | PASS | Appropriate (not fake) |
| P39I-09 | Genuine rename+join dual | FAIL (FF) | Sides independent; FF on grain wording |
| P39I-12 | union → single agg + duplicate side roles | (not invoked) | Plan validation NON-PASS |

**Critical question:** V2.2 continued to avoid a **PASS** on the P39G-11-style fake dual in this set. Distinction was enforced here primarily by **plan validation** on role/column mismatch, with no silent PASS.

## 11. Same-Origin Partitioned Dual Review

**P39I-07** (tx_history P1 vs P2 + accounts): required case.

- Path: `strong_escalation_cannot_plan` after ReadTimeout
- Verifier verdict: none
- Manual: **INDETERMINATE**

**Cannot confirm** that V2.2 accepts same-origin independently partitioned comparison under live Shadow in this pilot. No systematic rejection observed either (STOP-4 not hit).

## 12. P39E-14 Valid Dual Anchor

**P39I-11** (lane load R1 vs R2):

- `shadow_pipeline_exception`: MergeError duplicate `lane_id_left` / `lane_id_right` suffixes
- No verifier verdict
- Manual: **INDETERMINATE**

Live non-anchor **P39I-05** (shift A/B rename-style join dual): manual **YES** + verifier **PASS** — valid dual still accepted outside the broken anchor.

## 13. P39G-11 Fake-Dual Anchor

**P39I-12** (interval I1/I2 chamber use):

- Plan: `union_rows` → aggregate single `total_use_kwh` with both comparison sides claiming `use_kwh`
- `plan_validation_status=failed` (`output_role_column_missing`, `output_role_not_in_required_columns`)
- Verifier not invoked; **not PASS**
- **STOP-3 not hit**

## 14. Full Intent Satisfaction Review

Side distinction ≠ full intent.

- Comparison cases that keep both side values without “which increased?” style ops were judged on the stated prompt (keep both visible) — not auto-downgraded.
- P39I-13 asked for `qty * price` line_value; DSL/plan failed — full intent not met (**NO**), separate from verifier silent-wrong.
- Full-intent relational semantics remain **out of scope for fixes** in 39I.

## 15. 32B Invocation Analysis

| Type | Cases (approx) | Classification |
|---|---|---|
| failure_32b | P39I-01, 07, 08 | mix of UNNECESSARY_ESCALATION / NO_RECOVERY |
| semantic_32b | P39I-06, 09 | NO_RECOVERY (06); UNNECESSARY_ESCALATION (09) |

- **USEFUL_RECOVERY** (failure path recovering wrong fast to correct): 0 counted under strict definition
- **SEMANTIC_RECOVERY_CONFIRMED**: **0** (09 fast was already correct; 06 did not recover)

Do not change escalation policy in this phase (unchanged).

## 16. Semantic Recovery Evidence

**SEMANTIC_RECOVERY_CONFIRMED = 0**

No case met all four: demonstrably wrong fast → correct verifier reject → semantic 32B → manually correct strong.

## 17. Failure-Escalation Recovery

Failure-32B paths produced:

- P39I-01: eventual strong success (correct join) after long run / soft timeout mark
- P39I-07: no recovery (timeout cannot_plan)
- P39I-08: strong success on already-valid dual structure

Not counted as semantic recovery.

## 18. Operational Reliability

| Class | N / notes |
|---|---|
| shadow_timeout marked | 3 (01, 06, 07) — soft marker; long 32B/LLM |
| pipeline exception | 1 (P39I-11 MergeError) |
| telemetry missing | 0 |
| capacity skip | 0 |
| correct cannot_plan | 1 (P39I-10) |
| inappropriate cannot_plan | 1 (P39I-04) |
| legacy exception | 1 (P39I-10) |
| Shadow exception | 1 (P39I-11) |
| ReadTimeout (Ollama) | 06, 07 strong/semantic paths |

Operational failures ≠ automatic semantic failures; several INDETERMINATE labels follow from this.

## 19. Telemetry Reliability

- eligible 13 / observed 13 / missing 0 / coverage **1.0**
- P39E-10 / P39G-09 family gap **not** observed in this set
- Absolute telemetry dir used for the session

## 20. Latency

**Legacy (user-visible):** mean ≈ 10.5s, p50 ≈ 0.1s, max ≈ 34.5s (many near-instant Legacy replies).

**Shadow (background):**

| | s |
|---|---:|
| mean | 313.7 |
| p50 | 48.1 |
| p95 | 1154.3 |
| max | 1272.5 |

By path (illustrative): fast_success ~23s; semantic/failure escalations hundreds–1000+s.

Shadow remains asynchronous: background cost ≠ user-visible latency.

## 21. Resource Analysis

- concurrency=1 still saw multi-hour wall time under 32B + soft timeouts
- Soft timeout marks did not always bound wall clock when LLM calls continued
- No capacity skips at N=13

## 22. Safety

**unsafe = 0**

Confirmed for this session:

- Shadow never replaced Legacy
- Shadow did not alter user-facing response path
- Exception observation did not change UI behavior
- No strong-model trust bypass
- No executor repair added
- Candidate not patched mid-run

## 23. Phase 39G vs 39I

| Metric | 39G (V2 origins) | 39I (V2.2 expr_partition) |
|---|---|---|
| N | 12 | 13 |
| coverage | 10/11 | **13/13** |
| silent wrong | **1** | **0** |
| verifier FF | 0 | **1** (isolated) |
| fake-dual outcome | PASS (P39G-11) | NON-PASS (P39I-12) |
| valid dual | accepted | accepted (05); anchor 11 indeterminate |
| same-origin partitioned | n/a | **indeterminate** |
| semantic 32b | 0 | 2 |
| failure 32b | 3 | 3 |
| useful recovery | 0 | 0 |
| timeout marked | 0 | 3 |
| pipeline exception | 2 | 1 |
| missing telemetry | 1 | 0 |
| Shadow mean / p50 / max | 93 / 37 / 264 | 314 / 48 / 1273 |
| unsafe | 0 | 0 |
| Gate | C | **B** |

Small-N: silent_wrong clearance is **directionally** positive vs 39G’s blocker, but incomplete anchors prevent Gate A / migration claims.

## 24. Architecture / Isolation Audit

- Legacy primary + Shadow observational: intact
- R-ROLE-B non-authoritative: intact
- Materialization remained `final_schema_expr_partition` (no V2/V2.1)
- Python materialization ≠ semantic judge contract: no mid-run bypass
- Isolation STOP-6 / STOP-7: **not** hit

## 25. Known Limitations

1. Same-origin partitioned dual not completed under live Shadow (timeout)
2. P39E-14-family anchor hit executor MergeError — anchor gap
3. Isolated rename+join verifier FF (P39I-09)
4. High Shadow latency / soft-timeout under 32B contention
5. D-category “pressure” often realized as genuine dual when files are already side-split
6. Full-intent relational ops still out of verifier contract scope

## 26. Gate

### **Gate B**

No critical semantic blocker (no silent wrong; fake dual did not PASS; unsafe=0; isolation intact), but:

- evidence insufficient on same-origin partitioned acceptance
- P39E-14 anchor incomplete
- unresolved isolated verifier FF
- heavy operational INDETERMINATE / timeout mass on critical C/F cells

Gate B ≠ migration. Gate A not assigned.

## 27. Recommended Next Phase

### **A. Another limited Shadow observation**

Focus set (still ≤15):

1. Same-origin independently partitioned dual (retry P39I-07-family)
2. P39E-14-family valid dual anchor (avoid known MergeError shape or use proven join path)
3. At least one forced fake-dual realization reaching verifier (not only validator)
4. Watch rename+join dual for repeat FF (P39I-09 family)

Do **not** choose migration. Optional parallel track later: B (escalation cost) or E (verifier research) if FF repeats.

## 28. Final Recommendation

Freeze remains **V2.2 `final_schema_expr_partition`**. Treat Phase 39I as a **clean-enough but incomplete** re-observation: silent_wrong stayed 0 and the prior fake-dual PASS did not recur, yet critical cells (same-origin partitioned + P39E-14 anchor) did not produce decisive PASS evidence, and one isolated verifier FF appeared.

**Next:** limited Shadow re-observation (A). **Do not migrate.**
