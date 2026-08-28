# Phase 39E — Limited Shadow Observation after Verifier Evidence Grounding

## 1. Executive Summary

Frozen Phase 39D-V1 (`final_schema` materialization) was observed on **15** production-like multi-file requests under session-only Shadow.

- **Silent wrong (wrong + verifier PASS): 0**
- **Verifier false-fail: 1** (P39E-14 Anchor B dual-side residual)
- Shadow coverage **14/15 (0.933)**; unsafe **0**; STOP conditions **not hit**
- Gate: **B** — promising, more observation / verifier investigation needed
- Next: **C. Semantic verifier investigation**
- Migration remains **out of scope**

## 2. Phase 39D Freeze

Artifact: `benchmark_results/multi/phase39e/baseline_freeze.json`

| Item | Value |
|---|---|
| git HEAD (at freeze) | `680d10a522d5adf575974752891ba2765086cfd8` |
| Phase 39D gate | A |
| Materialization mode | V1 `final_schema` |
| Roles | R-ROLE-B |
| Planner / verifier | qwen2.5:7b |
| Strong | qwen3:32b |
| Semantic / failure escalation | ON |
| Production Shadow default | `MULTI_SHADOW_ENABLED=false` |

Pre-observation wiring (before pilot, not mid-pilot): `semantic_escalation` passes live `source_schemas` into verifier so V1 grounding is active on Shadow path.

Pre-obs regression: Phase 30/33/35/37/38/39B/39D targeted pytest PASS; deterministic safety recorded (`pre_obs_regression.txt`, `deterministic_safety.txt`).

Candidate behavior was not modified after the first Phase 39E observation row.

## 3. Observation Configuration

| Item | Session value |
|---|---|
| `MULTI_SHADOW_ENABLED` | true (session only) |
| `MULTI_SHADOW_SAMPLE_RATE` | 1.0 |
| `inline_for_tests` | false |
| max concurrency | 1 |
| timeout | 600s |
| Telemetry | `benchmark_results/multi/phase39e/telemetry/` |
| Candidate tag | Phase39D-V1 |
| Fire-and-forget | yes |

Production default left OFF after the session. Legacy remained user-visible primary via `route_multi_prompt`.

## 4. Request Mix

Balanced pilot (not C2-only):

| Group | Target | Actual | IDs |
|---|---:|---:|---|
| A Valid integration | 3–4 | 3 | P39E-01..03 |
| B Distinction | 3–4 | 3 | P39E-04..06 |
| C Multi-hop | 2–3 | 2 | P39E-07..08 |
| D Non-comparison agg | 2–3 | 2 | P39E-09..10 |
| E Ambiguous / impossible | 2–3 | 2 | P39E-11..12 |
| Anchors A/B/C | ≥3 | 3 | P39E-13..15 |
| **Total** | ~15 | **15** | |

Request set: `pilot_request_set.json`.

## 5. Shadow Coverage

| Metric | Value |
|---|---:|
| Total requests | 15 |
| Shadow eligible | 15 |
| Shadow recorded | 14 |
| Shadow missing | 1 (P39E-10) |
| Capacity skipped | 0 |
| Coverage | **0.933** |

Missing reason (P39E-10): no new Shadow telemetry record after eligible request (coverage/telemetry gap; classified operational, not silent-wrong).

Exception-path observed: P39E-11 (`legacy_status=exception`, Shadow `cannot_plan` recorded).

## 6. Legacy Correctness

Manual judgment of user-visible Legacy result (not `legacy_success` telemetry):

| Judgment | N |
|---|---:|
| YES | 7 |
| PARTIAL | 4 |
| NO | 3 |
| N/A | 1 |

## 7. Shadow Correctness

Independent manual judgment:

| Judgment | N |
|---|---:|
| YES | 11 |
| PARTIAL | 0 |
| CORRECT_CANNOT_PLAN | 2 |
| NO | 0 |
| INDETERMINATE | 2 (P39E-10 missing; P39E-15 timeout) |

`success` ≠ correct; `cannot_plan` ≠ failure (E-group judged appropriate).

## 8. Silent Wrong Review

**Confirmed SILENT_WRONG: 0**

No case of semantically wrong final Shadow result with verifier PASS.

STOP-1 / STOP-6 not triggered.

## 9. Verifier False-Fail Review

**1 case — P39E-14 (Anchor B, energy-like)**

- Manual: valid dual-side rename×2 + join (`kwh_p1`/`kwh_p2`) — **correct**
- Verifier: **FAIL** (`wrong_output_grain`) — collapse claim incorrect
- Effect: semantic 32B escalation
- Classification: **VERIFIER_FALSE_FAIL** (Type F)
- STOP-2 (≥2 similar) **not** met → pilot continued to close

## 10. Materialization Grounding Review

- Anchor A (P39E-13 finance-like): planner materialized real `amount_left`/`amount_right`; verifier PASS justified; **no aspirational-column silent PASS**
- When schemas available, ungrounded dotted claims were not the failure mode in this pilot
- V1 did **not** prevent Anchor B false-fail (semantic misread of valid rename+join)
- Conclusion: grounding helps FP-class evidence; FF-class dual-side recognition remains open

## 11. Comparison / Dual-Side Review

- P39E-04..06, P39E-13: both sides preserved; verifier PASS
- P39E-14: both sides preserved in plan/output roles but verifier rejected → FF
- No requirement for explicit delta unless prompt asks

## 12. Full Intent Satisfaction Review

- Distinction-preserved ≠ full intent satisfied
- P39E-15 (“which regions increased”): Shadow had join/filter intent but timed out / strong failed → **INDETERMINATE**; not auto-YES from dual columns alone
- Legacy often PARTIAL on distinction/agg asks (file-wise totals)

## 13. 32B Invocation Analysis

| Metric | N |
|---|---:|
| Failure escalation | 7 |
| Semantic escalation | 1 (P39E-14) |
| Useful recovery (confirmed) | 0 |
| Unnecessary escalation | 1 (FF-driven) |
| Failed recovery / timeout aftermath | present in timeout cases |
| Safe abstention | N/A clear |

Failure vs semantic escalations counted separately.

## 14. Semantic Recovery Evidence

**SEMANTIC_RECOVERY_CONFIRMED: 0**

No clean observed path: 7B wrong → verifier non-pass → semantic 32B → final correct.

Evidence still missing relative to Phase 39C gap (do not manufacture prompts to force it).

## 15. Operational Failures

| Class | N | Notes |
|---|---:|---|
| shadow_timeout | 3 | P39E-01, 08, 15 |
| Missing shadow record | 1 | P39E-10 |
| cannot_plan | 2 | P39E-11, 12 (correct) |
| Known join-suffix (REC13) | 0 | out of scope; none observed |
| pipeline exception (Shadow) | 0 clear separate from timeout |
| Capacity skip | 0 |

Operational ≠ semantic wrong.

## 16. Exception Coverage

P39E-11: Legacy exception; Shadow record exists (`cannot_plan`); Legacy UI path unchanged (no Shadow bleed observed). Did not intentionally corrupt the app.

## 17. Latency

Shadow is asynchronous — **not** user latency.

| Path | n | mean | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| Legacy | 15 | 16.1s | 17.5s | — | 41.5s |
| Shadow | 14 | 266.4s | 55.0s | 652.8s | 692.4s |

Heavy tails driven by failure/semantic 32B and timeouts.

## 18. Resource / Saturation

- Frequent failure-32b (7/15)
- Semantic 32b (1)
- Long-running Shadow up to ~11+ minutes
- Timeout markers present
- Worker concurrency=1; no capacity skips, but host load makes broader observation costly
- Do not optimize in 39E — note practicality only

## 19. Safety

| Check | Result |
|---|---|
| unsafe | **0** |
| Shadow replaces Legacy | No |
| Reply / fallback / route changed by Shadow | No |
| Production default still OFF | Yes |

STOP-3 / STOP-4 not hit.

## 20. New Failure Taxonomy

| Type | Hits |
|---|---|
| A understanding | — |
| B planner under-declaration | — |
| C1 / C2 | — (no new silent collapse+PASS) |
| D grain contradiction | verifier misapplied on P39E-14 |
| E materialization mismatch | — (aspirational FP not observed) |
| **F verifier false-fail** | **P39E-14** |
| G execution/contract | timeouts |
| H unknown/new | missing telemetry P39E-10 |

## 21. Legacy vs Shadow Summary

| Dimension | Legacy | Shadow (candidate) |
|---|---|---|
| User-visible | Yes (primary) | No (observe only) |
| Manual YES | 7 | 11 |
| Manual wrong | 3 | 0 |
| Ambiguous handling | Mixed / exception | cannot_plan OK (2) |
| Semantic verifier | N/A | PASS 10 / FAIL 1 / none 4 |
| Latency role | User path | Async cost |

Structural `comparison.structural` treated as metadata only.

## 22. Architecture / Isolation Audit

- Fire-and-forget Shadow preserved
- No mid-pilot patch to planner/verifier/roles/escalation/telemetry semantics
- No `route_multi` migration
- No keyword/domain routing added
- Roles remain R-ROLE-B observability
- Isolation verified for pilot set (STOP-4 clear)

## 23. Known Limitations

- N=15 small sample
- Stochastic model variance
- Shadow p95/max impractical for large pilots on this host
- Residual dual-side verifier FF (39D family)
- No confirmed semantic 32B recovery
- P39E-10 missing record weakens coverage claim slightly
- REC13 join-suffix still out of scope

## 24. Gate

### **Gate B — Promising but more observation needed**

Not A: sample small; FF residual; weak recovery evidence; operational cost.  
Not C: no silent-wrong / systematic FF (≥2) blocker.  
Not D alone: evaluation still possible despite timeouts.

## 25. Recommended Next Phase

### **C. Semantic verifier investigation**

Focus: valid dual-side rename/join false-fails without new keyword routing.  
Do **not** migrate primary. Optional later: more Shadow after FF mitigation (track A).

## 26. Final Recommendation

Close Phase 39E frozen. Phase 39D-V1 showed **no silent wrong** on this pilot and Anchor A aspirational FP did not recur, but **do not trust for migration**. Residual verifier false-fail + timeouts/32B cost require verifier investigation before any broader Shadow trust claim.
