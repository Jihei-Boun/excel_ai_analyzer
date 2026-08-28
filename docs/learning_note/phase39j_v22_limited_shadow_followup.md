# Phase 39J — V2.2 Another Limited Shadow Observation

## 1. Executive Summary

Frozen **V2.2 `final_schema_expr_partition`** was re-observed on **10** targeted production-like Shadow requests to fill Phase 39I evidence gaps.

- **SILENT_WRONG = 0**, **unsafe = 0**, Shadow isolation intact, Shadow restored **OFF**
- **STOP-5 hit**: rename+join valid-dual verifier false-fail in **two independent 39J cases** (P39J-06, P39J-07)
- RQ1 same-origin partitioned: still **INDETERMINATE** (timeout ×2)
- RQ2 P39E-14-family: again **INDETERMINATE** (MergeError)
- RQ3 P39I-09 FF: **recurrence confirmed** (P39J-06); P39J-05 in same family **PASS**
- RQ4 P39G-11 fake dual: **not realized** (planner emitted genuine dual; verifier FF on it)
- Coverage 10/10 telemetry; **Gate C**. Next: **E. further verifier / evidence research**. Migration **not** approved.

## 2. Freeze Verification

See `benchmark_results/multi/phase39j/baseline_freeze.json`.

| Field | Value |
|---|---|
| git HEAD | `680d10a522d5adf575974752891ba2765086cfd8` |
| dirty tree | yes (pre-existing 39H–39I work retained; not overwritten) |
| materialization | **`final_schema_expr_partition`** (confirmed unchanged) |
| roles | R-ROLE-B (non-authoritative) |
| planner/verifier | `qwen2.5:7b` |
| strong | `qwen3:32b` |
| pre-obs regression | Phase 39H + Phase 34 PASS |
| mid-run candidate changes | **none** |
| Shadow after session | **OFF** (`shadow_off_proof.json`) |

## 3. Observation Configuration

Session-only:

- `MULTI_SHADOW_ENABLED=true`, sample_rate=1.0
- `inline_for_tests=false`, concurrency=1
- telemetry: `benchmark_results/multi/phase39j/telemetry/`
- After pilot: Shadow **OFF** (proof recorded)

No mid-run changes to verifier, planner, escalation, roles, executor, or labels.

## 4. Request Set

Frozen in `pilot_request_set.json` (10 cases):

| ID | Category | Purpose |
|---|---|---|
| P39J-01 | ordinary integration | production-like filler |
| P39J-02 | ordinary agg-after-join | production-like filler |
| P39J-03 | P39E-14 valid dual | RQ2 |
| P39J-04 | same-origin partitioned | RQ1 |
| P39J-05 | P39I-09 rename+join | RQ3 |
| P39J-06 | P39I-09 rename+join | RQ3 (2nd independent) |
| P39J-07 | P39G-11 fake-dual pressure | RQ4 |
| P39J-08 | same-origin partitioned backup | RQ1 |
| P39J-09 | ordinary union | filler |
| P39J-10 | ambiguous cannot-plan | refusal control |

## 5. Shadow Coverage

| Metric | Value |
|---|---:|
| Planned | 10 |
| Executed | 10 |
| Shadow records | 10 |
| Missing telemetry | 0 |
| Coverage | **1.0** |
| Interpretable semantic | 6 |
| INDETERMINATE | 4 |

Coverage ≠ accuracy.

## 6. Legacy Correctness

YES 2 / PARTIAL 5 / NO 1 / N/A 2

Legacy remained Primary. Several PARTIAL/empty results on hard joins are separate from Shadow semantics.

## 7. Shadow Correctness

| Label | N | Cases |
|---|---:|---|
| YES | 5 | 02, 05, 06, 07, 09 |
| CORRECT_CANNOT_PLAN | 1 | 10 |
| INDETERMINATE | 4 | 01, 03, 04, 08 |
| NO / PARTIAL | 0 | — |

## 8. Critical Anchor Results

### RQ1 — Same-origin independently partitioned

- **P39J-04**, **P39J-08**: both `strong_escalation_cannot_plan` after Ollama **ReadTimeout**
- Manual: **INDETERMINATE**
- **Unresolved.** Do not treat as PASS or FAIL.

### RQ2 — P39E-14 valid dual

- **P39J-03**: `shadow_pipeline_exception` **MergeError** (`bay_id_left` / `bay_id_right` duplicate suffixes)
- Same operational family as Phase 39I P39E-14 anchor failure
- Manual: **INDETERMINATE**
- **Unresolved** for semantic acceptance.

### RQ3 — P39I-09 false-fail recurrence

| Case | Result | Verifier | FF? |
|---|---|---|---|
| P39J-05 | rename+join YES | PASS | no |
| P39J-06 | rename+join YES | FAIL `wrong_output_grain` | **yes** |

**Recurrence confirmed** (not merely historical). Within 39J, one PASS and one FF on the family → intermittent but real.

### RQ4 — P39G-11 fake dual

- **P39J-07** intended as fake-dual pressure
- Planner produced **genuine** rename+join (`use_kwh_s1` / `use_kwh_s2`) — valid dual
- Verifier **FAIL** with false collapse claim → **verifier FF on valid dual**, not a fake-dual NON-PASS test
- Fake-dual verifier behavior **unresolved** this run

## 9. Silent Wrong / Safety

- **SILENT_WRONG = 0**
- **unsafe = 0**
- Shadow did not replace Legacy; no user-facing mutation observed
- STOP-1 / STOP-2 / STOP-3 not hit

## 10. Verifier False-Fails

### P39J-06 — FF (rename+join dual)

- Final columns: `sku`, `depot_a_stock`, `depot_b_stock` — manual **YES**
- Verifier FAIL claiming collapsed `total_stock` (contradicted by fingerprint)
- Triggered **unnecessary semantic 32B**

### P39J-07 — FF (rename+join dual; intended fake-dual cell)

- Final columns: `zone_id`, `use_kwh_s1`, `use_kwh_s2` — manual **YES**
- Verifier FAIL claiming single total — false
- Second independent 39J observation in same structural family → **STOP-5**

Family label: **rename_join_valid_dual** (P39I-09 / P39E-14-adjacent).

## 11. Operational Reliability

| Class | N | Notes |
|---|---:|---|
| shadow_timeout / ReadTimeout cannot_plan | 3 | 01, 04, 08 |
| MergeError pipeline exception | 1 | 03 (P39E-14 family) |
| telemetry missing | 0 | |
| capacity skip | 0 | |

Operational INDETERMINATE ≠ semantic verifier FAIL.

## 12. 32B Cost / Value

| Class | N |
|---|---:|
| failure_32b | 3+ |
| semantic_32b | 2 (06, 07) |
| USEFUL_RECOVERY | 1 (05, failure path) |
| UNNECESSARY_ESCALATION | 2 (06, 07) |
| NO_RECOVERY | 3 (timeout cannot_plan) |
| SEMANTIC_RECOVERY_CONFIRMED | **0** |

Do not count failure-escalation recovery as semantic recovery.

## 13. Regression Results

Pre-observation: Phase 39H provenance + Phase 34 generalization **PASS**.

No candidate patch during/after official run → no post-diff expected from 39J itself.

## 14. Phase 39I vs 39J

| Metric | 39I | 39J |
|---|---|---|
| N | 13 | 10 |
| coverage | 13/13 | 10/10 |
| silent_wrong | 0 | 0 |
| verifier FF | 1 isolated | **2 same family (STOP-5)** |
| same-origin | INDETERMINATE | INDETERMINATE |
| P39E-14 | MergeError | MergeError |
| fake-dual PASS | 0 | n/a (not realized) |
| Gate | B | **C** |

## 15. Architecture / Isolation Audit

- Legacy Primary / Shadow observational: intact
- R-ROLE-B non-authoritative: intact
- Materialization remained `final_schema_expr_partition`
- No mid-run semantic patch
- STOP-6 not hit

## 16. Known Limitations

1. Same-origin partitioned still never completed under live Shadow
2. P39E-14-family repeatedly blocked by MergeError before verifier
3. Fake-dual pressure often realizes as genuine dual when files are already side-split
4. Verifier intermittently invents collapsed-grain narratives against correct dual schemas
5. High Shadow latency / 32B contention → soft timeouts

## 17. Gate Decision

### **Gate C**

Critical correctness blocker under STOP-5:

> same valid structural family (rename+join dual) produced verifier false-fail in **≥2 independent Phase 39J observations** (P39J-06, P39J-07).

Not Gate A/B: anchors RQ1/RQ2 remain INDETERMINATE, but systematic valid-family FF already blocks.

Gate C ≠ automatic redesign of unrelated subsystems; it blocks migration and marks V2.2 live reliability as insufficient for the next evidence-expansion Gate A path without addressing the FF family.

## 18. Recommended Next Phase

### **E. Further verifier / provenance-evidence research**

Focus:

1. Why verifier emits `wrong_output_grain` / collapse claims against correct dual schemas (P39I-09 / P39J-06 / P39J-07)
2. Keep V2.2 frozen until a **new** candidate is explicitly proposed offline
3. Optionally separate operational track for MergeError / timeout reliability — **do not** conflate with semantic redesign unless evidence requires it

Do **not** migrate. Do **not** mid-hoc patch production on these cases alone.

## 19. Final Recommendation

Phase 39J answered the core question negatively for live reliability of the frozen V2.2 candidate on the rename+join dual family: the isolated 39I FF **recurred** and became **systematic within 39J** (STOP-5). Critical 39I gaps (same-origin partitioned acceptance; P39E-14 clean completion; fake-dual verifier NON-PASS) remain **unfilled** due to operational indeterminacy / non-realization.

**Freeze remains V2.2 for history; Gate C; next = E (verifier research). Migration not approved.**
