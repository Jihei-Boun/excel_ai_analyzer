# Phase 39G — V2 Limited Shadow Re-observation

## 1. Executive Summary

Frozen V2 (`final_schema_origins`) was re-observed on **12** production-like Shadow requests.

- Known P39E-14-style **rename/join false-fail did not recur** on valid dual joins (P39G-03/04/05).
- **SILENT_WRONG = 1 (P39G-11)**: union → double-aggregate aliases of the same `kwh` produced fake dual sides; verifier **PASS**.
- STOP hit: **STOP-1, STOP-2, STOP-7**.
- unsafe = 0; Legacy isolation intact; no mid-pilot candidate change.
- **Gate C**. Next: **G. Further verifier / origin-evidence research**.

## 2. Phase 39F Freeze

See `benchmark_results/multi/phase39g/baseline_freeze.json`.

- Candidate: Independent Verifier + **V2 `final_schema_origins`**
- Roles: R-ROLE-B
- Escalation: Phase 35 unchanged
- Shadow default remains OFF after session

## 3. Observation Configuration

Session-only:

- `MULTI_SHADOW_ENABLED=true`, sample_rate=1.0
- `inline_for_tests=false`, concurrency=1
- Telemetry: `benchmark_results/multi/phase39g/telemetry/`
- After pilot: Shadow returned **OFF**

Pre-obs: targeted Phase 30–39D + full pytest PASS (Phase34 payload assertion updated for 39B independent shape); deterministic safety PASS.

## 4. Request Mix

| Group | N | IDs |
|---|---:|---|
| A valid integration | 2 | 01–02 |
| B dual-side (+ REC12-like) | 3 | 03–05 |
| C multi-hop | 2 | 06–07 |
| D non-comparison agg | 2 | 08–09 |
| E ambiguous | 1 | 10 |
| Anchors | 2 | 11–12 |
| **Total** | **12** | |

Mostly new prompts vs Phase 39E.

## 5. Shadow Coverage

| Metric | Value |
|---|---:|
| Total requests | 12 |
| Shadow eligible (≥2 files) | 11 |
| Shadow observed | 10 |
| Missing | 1 (P39G-09) |
| Coverage | **0.909** |

P39G-12 single-file: route_multi rejects (<2 files) → Shadow not scheduled (eligibility, not telemetry gap).

## 6. Legacy Correctness

YES 3 / PARTIAL 6 / NO 1 / N/A 2

## 7. Shadow Correctness

YES 5 / PARTIAL 1 / NO 1 / CORRECT_CANNOT_PLAN 1 / INDETERMINATE 4

## 8. Silent Wrong Review

### P39G-11 — **SILENT_WRONG (confirmed)**

User asked to keep W1 vs W2 node kWh totals visible.

Shadow plan:

1. `union_rows` of both windows (destroys side identity)
2. `aggregate` **twice** on the same post-union `kwh` as `total_kwh_w1` and `total_kwh_w2`
3. `select_columns`

Deterministic origins: **both** metric columns have **identical** multi-source provenance `{w1.kwh, w2.kwh}`.

Logical values: both sides equal the union sum (N1=22, N2=38) — fake dual.

Verifier: **PASS**, claiming distinct sources independently survived — **incorrect**.

`shared_singleton_origin_groups` did **not** fire (origins are multi-source sets, not singletons).

Frozen evidence: `silent_wrong_p39g11.json`.

## 9. Verifier False-Fail Review

**0** systematic rename/join false-fails in this set.

Valid dual joins P39G-03/04 (and distinction columns on 05) received PASS.

## 10. V2 Origin Evidence Review

- Helps genuine join dual-sides (left/right or renamed survivors).
- **Gap:** identical *multi-source* origin sets after union+dual-alias aggregate are not treated as same-origin collapse.
- Verifier over-trusted narrative roles / column names vs provenance equality.

## 11. Dual-Side / Comparison Review

- P39G-03/04: both sides preserved → PASS (good).
- P39G-05: sides preserved; “which increased” not answered → PARTIAL.
- P39G-11: apparent dual columns without real side separation → silent wrong.

## 12. Full Intent Satisfaction

Distinction ≠ full intent. P39G-05 marked PARTIAL for missing increase filter/rank. P39G-11 fails both distinction truth and intent.

## 13. 32B Invocation Analysis

| Type | N |
|---|---:|
| Failure escalation | 3 |
| Semantic escalation | 0 |
| Useful recovery | 0 |
| Unnecessary escalation | 3 |

No semantic-32B in this pilot. Failure-32B still appears on some correct dual paths.

## 14. Semantic Recovery

**SEMANTIC_RECOVERY_CONFIRMED = 0**

## 15. Failure-Escalation Recovery

Failure-32B observed but not cleanly proven as necessary recovery (paths already near-correct or operational). Tracked as cost, not verified value.

## 16. Operational Reliability

| Issue | N |
|---|---:|
| pipeline exception | 2 (P39G-06/07) |
| missing shadow record | 1 (P39G-09) |
| cannot_plan | 1 (P39G-10, appropriate) |
| capacity skip | 0 |
| shadow soft-timeout mark | 0 in final set |

## 17. Telemetry Reliability

One unexplained eligible missing record (P39G-09). STOP-6 (≥2) **not** hit. Still a follow-up (with P39E-10).

## 18. Latency

| Path | n | mean | p50 | p95/max |
|---|---:|---:|---:|---:|
| Legacy | 12 | 15.6s | 21.4s | max 41.6s |
| Shadow | 10 | 93.3s | 36.7s | p95/max 263.9s |

Shadow async — not user latency.

## 19. Resource Analysis

- Semantic 32B: 0 this pilot
- Failure 32B: 3
- Long tails remain on some dual-side paths
- Not optimized in 39G

## 20. Safety

unsafe = 0. Shadow did not alter Legacy replies. Production Shadow left OFF.

## 21. Phase 39E vs 39G

| | 39E | 39G |
|---|---|---|
| N | 15 | 12 |
| Coverage | 14/15 | 10/11 |
| Silent wrong | 0 | **1** |
| Verifier FF | 1 (P39E-14) | **0** observed |
| Failure 32B | 7 | 3 |
| Semantic 32B | 1 | 0 |
| Useful recovery | 0 | 0 |

Primary question answer:

> V2 removed the known rename/join FF family in this sample, **but introduced / exposed a new silent-wrong pattern** (union + aliased double aggregate).

Secondary: 32B cost remains relevant, but **semantic silent-wrong is now the dominant blocker**.

## 22. Architecture / Isolation Audit

- No mid-pilot candidate edits after observation start
- Harness wait/collection only adjusted before clean restart
- Legacy primary; Shadow observational
- No keyword/domain routing added

## 23. Known Limitations

- N=12 small
- Anchor2 single-file path not Shadow-schedulable
- Origin equality of multi-source sets not enforced as non-pass signal
- Pipeline exceptions / one telemetry gap

## 24. Gate

### **Gate C — Semantic blocker**

## 25. Recommended Next Phase

### **G. Further verifier research**

Focus: treat **identical final-column provenance sets** (not only singleton same-origin) as non-proof of dual-side survival; prevent PASS when two “sides” are aliased aggregates of the same post-union metric — without keyword routing and without weakening true join dual-sides / aspirational non-pass.

## 26. Final Recommendation

Close Phase 39G frozen on **Gate C**. Do **not** migrate. Do **not** expand Shadow until the union/alias fake-dual silent-wrong family is addressed offline with matched wrong/valid fixtures and regression protection for V2 join dual-sides.
