# Phase 39F — Semantic Verifier Live Consistency Investigation

## 1. Executive Summary

P39E-14 (valid rename×2 + join dual-side) was a **deterministic verifier false-fail** (FAIL 5/5 pre-fix).

Root cause: the Independent Verifier under-trusted materialized `final_schema` and over-read join/entity grain as “collapse”, especially when sides were renamed rather than join-suffixed.

Fix (domain-free):

1. Prompt clarification: entity grain with multiple distinct side metrics ≠ collapse; rename survivors count; materialization outranks narrative.
2. Evidence V2 `final_schema_origins`: deterministic `final_column_origins` + `shared_singleton_origin_groups`.
3. Production default moved to **V2** (smallest mode that also blocks same-origin mislabel W6).

Offline + stability + tiny live confirmation: **false-fail 0**, **silent-wrong 0**, historical 39D protections intact.

**Gate A.** Migration still out of scope. Next: resume limited Shadow observation.

## 2. Phase 39E Freeze

See `benchmark_results/multi/phase39f/baseline_freeze.json`.

- Phase 39E Gate B; SILENT_WRONG 0; one false-fail (P39E-14)
- Candidate: Phase39D-V1 `final_schema` at freeze time
- P39E-15 / P39E-10 separated (out of scope)

## 3. P39E-14 Reproduction

Verifier-only on frozen plan/evidence, **5 runs, materialization=`final_schema`**:

| Run | Verdict | Reason |
|---:|---|---|
| 1–5 | fail | wrong_output_grain |

Classification: **A. deterministic false-fail**

Evidence claimed “only one kwh column” despite `final_schema = [site_id, kwh_p1, kwh_p2]`.

## 4. Exact False-Fail Anatomy

- Ops: `rename_columns` (kwh→kwh_p1) + `rename_columns` (kwh→kwh_p2) + `join` on site_id
- Manual: YES (both period metrics visible)
- Verifier: FAIL `wrong_output_grain` → unnecessary semantic 32B
- Not a planner error; not an executor error

## 5. Evidence Audit

Ground-truth structure:

- Sources: `p1_usage.xlsx[site_id,kwh]`, `p2_usage.xlsx[site_id,kwh]`
- Final schema: `site_id, kwh_p1, kwh_p2` (both sides present)
- Origins: `kwh_p1←p1.kwh`, `kwh_p2←p2.kwh`
- Unresolved refs: none; claimed absent: none

Planner claims/roles were consistent with materialization (not aspirational).

User intent: side-by-side period totals by site — **no delta required**.

## 6. Hypothesis Results

| H | Result |
|---|---|
| H1 Rename distrust | Partial — rename path more fragile than suffix join |
| H2 Join collapse confusion | **Supported** — “one row per site” misread as collapse |
| H3 Operation-history overweight | **Supported** — ignored final_schema facts |
| H4 final_schema insufficient alone | Partial — V1+prompt often enough for FF family; origins needed for same-origin mislabel |
| H5 output_roles | Not causal (FF6 no-roles also fixed) |
| H6 Prompt ambiguity | **Supported** — over-demanding grain interpretation |
| H7 Stochastic | **Rejected** for P39E-14 (5/5 fail) |

## 7. Generic Dual-Side Fixtures

FF1–FF8 (neutral schemas): rename+join, direct join suffixes, agg+agg+join, select/rename+join, join+select, roles absent/present, alt history.

After fix (ablation V1–V4 single pass): **all PASS**.

## 8. Matched Wrong Fixtures

W1–W7: one-side survive, join→agg collapse, aspirational aliases, combined metric, roles claiming missing sides, duplicated mislabel (same source), same-source both labels.

After V2: non-pass on W1–W5,W7; **W6 requires origins** (V1 still silent-wrong; V2 fails correctly).

## 9. Valid Non-Comparison Controls

C1–C6: union/total/append/groupby/enrichment/filter-after-agg → **PASS**, FP=0.

## 10. Evidence Ablation

| Variant | Mode | Dual FF | Wrong SW (pre-W6 note) | Notes |
|---|---|---|---|---|
| V1 | final_schema | 0 | 1 (W6) | Prompt fix alone clears P39E-14 family |
| V2 | final_schema_origins | 0 | 0 after shared-origin note | **Selected default** |
| V3 | lineage_origins | 0 | 1 (W6 in first pass) | No gain over V2 |
| V4 | full_lineage | 0 | W6 unstable | Richer ≠ better |

Prefer **smallest**: V2.

## 11. Output Roles Analysis

Roles remain **R-ROLE-B**. Valid dual-side PASSes without roles (FF6). Roles are supporting only.

## 12. Stochastic Stability

V2 `final_schema_origins`, n=5:

- P39E-14, FF1–FF3: PASS 5/5
- W1–W3: FAIL 5/5

W6 separate check under V2: FAIL 5/5.

## 13. Historical Regression

`pytest` Phase 33 / 35 / 39B / 39D: **PASS** (30 tests, exit 0).

## 14. Silent-Wrong Protection

Aspirational / collapse wrongs remain non-pass. Finance-style FP protection from 39D preserved by tests.

## 15. False-Fail Results

P39E-14 family false-fail rate → **0** under V2 (and V1 after prompt for this family).

## 16. Semantic Escalation Impact

P39E-14-class false FAIL had forced semantic 32B. Fixing FF removes that unnecessary escalation without reducing collapse recall on matched wrongs.

## 17. Latency

Verifier-only ~3–6s typical; no material latency regression vs 39D (~3.3–3.5s class).

## 18. Safety

unsafe = 0; no route_multi / Shadow migration; no keyword routing.

## 19. Architecture Audit

- Python: schema survival + origins only
- LLM: pass/fail / intent
- Roles unchanged
- Shadow / Legacy primary unchanged

## 20. Known Limitations

- 7B can still ignore rich V4 dumps (prefer compact V2)
- Full relational intent (P39E-15 “which increased”) still separate
- Telemetry gap P39E-10 still separate
- Broader Shadow not re-run in this phase (tiny confirm only)

## 21. Gate

### **Gate A**

False-fail root cause reproduced and generally fixed; silent-wrong protections preserved; stability + tiny live confirmation strong.

## 22. Recommended Next Phase

### **A. Resume limited Shadow observation**

Confirm V2 default under small production-like Shadow set (not migration).

## 23. Final Recommendation

Adopt **V2 `final_schema_origins`** as verifier materialization default. Do not migrate Shadow to primary. Close 39F frozen; next observe Shadow with the refined verifier.
