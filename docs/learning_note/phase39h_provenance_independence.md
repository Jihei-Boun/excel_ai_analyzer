# Phase 39H — Provenance Independence & Fake Dual-Side Investigation

## 1. Executive Summary

Phase 39G’s P39G-11 silent-wrong (union → double-aggregate aliases presented as dual sides) was investigated offline.

**Winner: V2.2 `final_schema_expr_partition`** — deterministic `evidence_signature` (expression + row-population/filter ancestry) plus `equivalent_evidence_signature_groups` / `identical_evidence_signature_column_sets`. Python exposes observable equivalence; the LLM verifier judges semantic adequacy.

- Fake dual family: stable **NON-PASS**
- Genuine same-origin partitioned dual: stable **PASS**
- Phase 39F valid dual / aspirational / C2 / non-comparison: **36/36** regression OK
- Tiny live (8): silent-wrong 0, verifier FF 0, unsafe 0
- **Gate A**. Next: **A — Resume limited Shadow observation**

## 2. Phase 39G Freeze

See `benchmark_results/multi/phase39h/baseline_freeze.json`.

- Candidate entering 39H: Independent Verifier + V2 `final_schema_origins`
- Roles: R-ROLE-B (non-authoritative)
- Legacy primary; Shadow observational only
- P39G-11 canonical fixture: `tests/benchmark_multi/fixtures/phase39h/p39g11_canonical.json`
- No dependence on local Shadow logs for regression tests

## 3. P39G-11 Reproduction

Verifier-only under frozen V2 origins, n=5:

| Verdict | Count |
|---|---:|
| fail | 5 |
| pass | 0 |

Classification: **C — not reproducible as PASS** in this offline session.

Phase 39G live Shadow still recorded PASS once with evidence claiming “distinct sources”. Offline FAIL today shows **cross-session intermittency**, not proof the family is gone. Structural evidence was still required for stable NON-PASS.

Artifact: `reproduction.json`.

## 4. Silent-Wrong Anatomy

**Genuine dual-side**

- Side A and Side B preserve independently derived evidence
- May share a source file if partition/filter ancestry differs
- Example: `filter(period=A)→SUM(value)` vs `filter(period=B)→SUM(value)` then join

**Fake dual-side (P39G-11 family)**

- Same underlying expression + same row population
- Duplicated/aliased into two final columns
- Roles/names claim A/B; Semantic Verifier previously PASSed
- Example: `union` → `SUM(kwh) AS total_w1` + `SUM(kwh) AS total_w2` on the combined stack

## 5. V2 Provenance Evidence Audit

Under V2 `final_schema_origins`, each final column exposes origin sets.

For P39G-11:

- Both metrics share multi-source origins `{w1.kwh, w2.kwh}`
- `shared_singleton_origin_groups` empty (not singleton)
- No aggregate expression fingerprint
- No filter/partition ancestry
- No deterministic “same expression / same population” fact

**Minimum missing evidence:** expression lineage (agg fn + input) **and** row-population/filter ancestry.

Audit: `v2_provenance_audit_p39g11.json`.

## 6. Hypothesis Results

| ID | Claim | Result |
|---|---|---|
| H1 | Origin-set equivalence gap | **Supported** — multi-source identical origins invisible to singleton grouping |
| H2 | Expression-lineage gap | **Supported** — identical `SUM(kwh)` aliases not observable |
| H3 | Partition-context gap | **Supported** — V2.1 without filters false-fails genuine duals |
| H4 | Alias over-trust | **Supported** — distinct names treated as sides |
| H5 | output_roles over-trust | **Supported** — roles accepted despite equivalent lineage |
| H6 | Operation-history under-grounding | **Partial** — expression+partition sufficient; full lineage not needed |

## 7. Fake Dual-Side Fixture Family

Tracked under `tests/benchmark_multi/fixtures/phase39h/fake_dual_family.json` + `p39g11_canonical.json` (FD8).

| ID | Pattern | Expected |
|---|---|---|
| FD1 | same source → two SUM aliases | NON-PASS |
| FD2 | union → same agg input → two aliases | NON-PASS |
| FD3 | incomplete duplicate/rename claim | NON-PASS |
| FD4 | same agg expr → join/select aliases | NON-PASS |
| FD5 | roles declare A/B; equivalent lineage | NON-PASS |
| FD6 | different names; identical lineage | NON-PASS |
| FD7 | intermediate duplicate then rename | NON-PASS |
| FD8 | P39G-11 canonical | NON-PASS |

Under V2.2: all NON-PASS in ablation; FD1/2/5/8 stable fail ×5.

## 8. Genuine Same-Origin Comparison Fixtures

`genuine_same_origin_dual.json`:

| ID | Pattern | Expected |
|---|---|---|
| GS1 | filter period A/B → agg → join | PASS |
| GS2 | filter status before/after → agg → join | PASS |
| GS3 | union + retained discriminator → filter → agg → join | PASS |
| GS4 | disjoint category filters | PASS |
| GS5 | same origin set after union; side-specific partition before agg | PASS |

Critical counterexample to F1 (`same origin == invalid`). Under V2.2: all PASS.

## 9. Phase 39F Dual-Side Regression

Valid duals (rename×2+join, suffix join, agg×2+join, roles absent/present, P39E-14): **PASS** under V2.2. Stability P39E-14: pass ×5.

## 10. Aspirational Protection

Phase 39D/39F aspirational / wrong fixtures remain **NON-PASS**. No weakening of materialization grounding.

## 11. C2 Collapse Protection

Phase 39B C2 + REC12-style + generalized wrong fixtures: **NON-PASS**. Silent-wrong recall not regressed (36/36 regression bundle).

## 12. Valid Non-Comparison Controls

Union total / append / overall total / join enrichment / single-source groupby / aggregate-after-union: **PASS**. Repeated provenance is not automatically an error when comparison is not requested.

## 13. Evidence Model Ablation

| Mode | Fake dual | Genuine partitioned | Notes |
|---|---|---|---|
| V2 origins | partial FP (FD4) | some FF (GS2/GS4) | insufficient |
| V2.1 expr (filters stripped) | FP≈0 | **all GS FF** | too strong equivalence |
| **V2.2 expr+partition** | **0 FP** | **0 FF** | **selected** |

V2.1 proves filter ancestry is mandatory for H3.

## 14. Evidence Signature Design

Deterministic fingerprint per final column:

- `op_family`
- `aggregate.{function,input_column}`
- `group_by`
- `row_population.{kind,inputs,filters}`
- `source_origins`

Python groups identical fingerprints into `equivalent_evidence_signature_groups` / `identical_evidence_signature_column_sets`.

**Python does not decide** whether the user needs independent sides or whether the plan is adequate. LLM judges using signatures as structural facts.

Unknown/unsafe equivalences are not guessed.

## 15. Output Roles Analysis

R-ROLE-B retained. FD5: roles A/B + equivalent signatures → NON-PASS under V2.2. Roles remain non-ground-truth.

Authority order enforced in prompt:

1. user intent  
2. deterministic materialization/provenance  
3. operation structure  
4. planner claims / roles  

## 16. Stochastic Stability

V2.2, n=5:

| Case | Distribution | Stable |
|---|---|---|
| FD1/FD2/FD5/FD8 | fail×5 | yes |
| GS1/GS2 | pass×5 | yes |
| P39E-14 | pass×5 | yes |

## 17. Historical Regression

Phase 39F dual + wrong + non-comparison + Phase 39D live FP/FF + Phase 39B C2: **36/36 OK** under V2.2. Unit tests for 39D grounding + 39H signatures PASS.

## 18. Tiny Live Confirmation

8 verifier-path cases (4 fake + 4 genuine), no mid-run fixes:

- silent_wrong = 0  
- verifier_false_fail = 0  
- unsafe = 0  
- all_ok = true  

Artifact: `tiny_live_confirmation.json`. Broad Shadow not resumed.

## 19. Escalation Side Effects

Out of scope. No 32B / retry / timeout changes. Secondary observation only: fewer false semantic PASS on fake duals should reduce unnecessary “satisfied” paths that previously skipped recovery; not measured in this phase.

## 20. Latency

Verifier-only typical 3–8s per call on qwen2.5:7b. Signature packaging is local/deterministic and negligible vs LLM latency.

## 21. Safety

- unsafe = 0  
- no domain/keyword routing  
- no blind `same origin == invalid`  
- no Python intent / PASS/FAIL engine  
- roles non-authoritative  

## 22. Architecture Audit

| Boundary | Status |
|---|---|
| Python exposes lineage facts | Yes |
| LLM decides semantic adequacy | Yes |
| IntegrationPlan DSL sufficient for FD/GS family | Yes (filter_rows + aggregate + join/union) |
| Full expression theorem prover | Explicitly avoided |
| Shadow / migration | Untouched |

## 23. Known Limitations

1. Offline V2 FAIL×5 ≠ proof 39G live PASS cannot recur under V2 without signatures.  
2. Signature equality is observational DSL equivalence only — not algebraic simplification.  
3. Full-intent relational asks (“which increased?”) still out of scope.  
4. Pivot-as-first-class op not required; GS3 approximated via union+filter.  
5. Tiny live was verifier-path confirmation, not a new 12-request Shadow pilot.

## 24. Gate

**Gate A** — General provenance-independence distinction demonstrated; fake dual NON-PASS; genuine dual PASS; historical protections intact; tiny live stable.

## 25. Recommended Next Phase

**A. Resume limited Shadow observation** with V2.2 default.

Not yet: migration-readiness (needs Gate A + another clean Shadow). Escalation cost, telemetry, full-intent semantics remain queued after Shadow re-observation.

## 26. Final Recommendation

Adopt **V2.2 `final_schema_expr_partition`** as verifier materialization default.

Ship evidence signatures + equivalent-signature groups as non-authoritative structural facts. Keep R-ROLE-B. Do not ban same-origin comparison. Close Phase 39H frozen; next re-observe Shadow under the refined verifier.
