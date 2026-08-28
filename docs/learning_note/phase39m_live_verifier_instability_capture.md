# Phase 39M — Focused Live Verifier Instability Capture & Exact Replay

## 1. Executive Summary

Phase 39M ran a **controlled live Shadow observation** on the frozen Phase 39L capture stack.

| Metric | Result |
|---|---|
| Observations | **15** |
| Valid rename+join exposure | **7** (6 with verifier verdict) |
| Verifier FF (manual YES ∩ fail) | **2** — `P39M-07`, `P39M-08` |
| Silent wrong | **0** |
| Exact capture for FFs | **Yes** |
| Exact replay | **EXACT_REPLAY**, both FFs **LIVE_FAIL_REPLAY_FAIL_STABLE** (fail 10/10) |
| Unsupported structural claims | **2** (manual, Korean notes) |
| **Gate** | **C** |
| Migration | **NOT APPROVED** |
| Shadow final | **OFF** |

Primary outcome:

> The valid rename+join verifier false-fail **recurred live**, and the exact captured payloads **stably reproduce FAIL under EXACT_REPLAY**.

This closes the Phase 39J evidence gap: we now have replayable live invocations.

---

## 2. Baseline Freeze

- Phase 39L SHA / HEAD: `675f3aea2745724713991b694fb22df77e7d6063` (Gate A, clean tree)
- Materialization: V2.2 `final_schema_expr_partition`
- Verifier: `qwen2.5:7b`, temperature 0, JSON format
- Capture: `MULTI_VERIFIER_CAPTURE_DIR`
- Shadow default OFF before/after observation (`shadow_off_proof.json`)
- Pre-observation regression: Phase 39L/H/34/33/35/37/38 suites PASS

No semantic/evidence/prompt changes during the run.

---

## 3. Request Set

15 cases (`pilot_request_set.json`):

| Family | Count |
|---|---|
| ordinary_multi | 4 |
| valid_rename_join (+ historical anchor) | 7 |
| fake_dual | 2 |
| same_origin_partitioned | 1 |
| ambiguous | 1 |

Request targeting is harness-only. Production code has no case-id special casing.

---

## 4. Capture Coverage

| | Count |
|---|---|
| Shadow recorded | 13/15 |
| Verifier verdict present | 9/15 |
| Exact captures | 9/15 (18 JSONL lines incl. retries) |
| Missing capture when verdict present | **0** |

Operational gaps (no verifier invocation / capture): P39M-03 cannot_plan, P39M-06 failed, P39M-11/12 fake-dual no usable shadow record, P39M-13 timeout, P39M-14 cannot_plan.

For the two FFs of interest: capture integrity held (STOP-4 not triggered).

---

## 5. Target Family — valid rename+join

| Case | Shadow correct | Verifier | Capture |
|---|---|---|---|
| P39M-04 | YES | pass | yes |
| P39M-05 | YES | pass | yes |
| P39M-06 | operational fail | — | no |
| **P39M-07** | **YES** | **fail `wrong_output_grain`** | **yes** |
| **P39M-08** | **YES** | **fail `wrong_output_grain`** | **yes** |
| P39M-09 | YES | pass | yes |
| P39M-10 | YES | pass | yes |

Exposure with verdict: 6. FF: 2. Family recurrence confirmed.

---

## 6. Live FF Findings

### P39M-07
- Final columns: `gate_id, lane_x_cars, lane_y_cars` (both sides present)
- Verifier claim: aggregates both lanes into a single total / not side-by-side
- Classification: **UNSUPPORTED_STRUCTURAL_CLAIM**
- Exact payload hash captured

### P39M-08
- Final columns: `product_id, store_m_sales, store_n_sales`
- Verifier claim: store metrics are totals across both stores rather than side-by-side
- Classification: **UNSUPPORTED_STRUCTURAL_CLAIM**
- Exact payload hash captured

Raw model JSON contains the claims; parser preserves them (raw≠parsed mismatch not observed).

---

## 7. Exact Replay

| Case | Fidelity | n | Distribution | Class |
|---|---|---|---|---|
| P39M-07 | EXACT_REPLAY | 10 | fail 10/10 | LIVE_FAIL_REPLAY_FAIL_STABLE |
| P39M-08 | EXACT_REPLAY | 10 | fail 10/10 | LIVE_FAIL_REPLAY_FAIL_STABLE |

Interpretation (**Pattern A**):

> Live FAIL + exact replay FAIL repeatedly ⇒ failure is **payload-associated / stable** under frozen runtime config — not a one-off sampling fluke on these payloads.

This differs from Phase 39K reconstructed offline PASS 5/5: reconstructed ≠ exact live payload.

---

## 8. Unsupported Claim Review

See `manual_claim_review.json` (notes in Korean).

Both FFs assert collapse/aggregation that contradicts deterministic `final_schema` / observed columns. Claims appear in raw response and survive parsing.

---

## 9. Fake-Dual / Grain Safety

P39M-11/12 produced **no verifier PASS**, but also **no usable verifier invocation** (operational gap).  
Cannot claim clean NON-PASS proof for fake-dual in this run; can claim **no fake-dual PASS recurrence**.

---

## 10. Same-Origin Evidence

P39M-13: shadow_timeout, no verifier capture → remains **INDETERMINATE** operationally.

---

## 11. 32B

Escalation occurred on some paths (32B loaded during run). FFs of interest are about verifier judgment on the fast path result, not “semantic recovery confirmed.” Unnecessary escalation classification left as secondary; not used to excuse FFs.

---

## 12. Operational Failures

Separately counted: cannot_plan, shadow_timeout, shadow failed, missing fake-dual shadow records. Not labeled as semantic FAIL.

---

## 13. Isolation

Legacy remained user-visible path. Shadow session-only. After run: Shadow OFF confirmed (`shadow_off_proof.json`).

---

## 14. Gate

**Gate C**

Evidence:

- ≥2 independent valid rename+join live FFs (`STOP-7`)
- exact captures available
- EXACT_REPLAY stably FAIL
- unsupported structural claims on both
- silent wrong = 0, unsafe = 0, no fake-dual PASS

Migration remains prohibited.

---

## 15. Next Recommendation

Run a **verifier inference-stability intervention research Phase** that:

1. freezes exact payloads `P39M-07` / `P39M-08` as regression oracles
2. studies interventions (prompt/decoding/evidence presentation/model) **offline against exact replay**
3. does **not** migrate Candidate until Gate evidence changes

Do **not** patch mid-observation. Do **not** declare the historical problem solved by non-recurrence elsewhere — recurrence is now demonstrated with exact evidence.

**Capture succeeded. Replay succeeded. Characterization: payload-stable FAIL. Fix later.**
