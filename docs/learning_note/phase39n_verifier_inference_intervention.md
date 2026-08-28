# Phase 39N — Verifier Inference Intervention Learning Note

## Entry
- Phase 39M SHA: `185231fbbbbda4b6962f1cd12f2ec870d3a09bf6` (Gate C)
- HEAD at freeze: `185231fbbbbda4b6962f1cd12f2ec870d3a09bf6`
- Shadow: OFF · Migration: NOT_APPROVED

## Critical finding
P39M-07/08 exact captured verifier payloads are **not** valid rename+join plans.

| Artifact | P39M-07/08 |
|---|---|
| Exact capture plan | `union_rows` → `aggregate` with two aliases of the same sum over the same union |
| identical_evidence_signature_column_sets | groups the two side metrics |
| Observation final_plan | `rename_columns` → `join` |
| Result content | matches **correct join** values, not fake-dual equal totals |

Therefore Phase 39M “verifier false-fail on valid rename+join” was a **misattribution**:
the verifier correctly failed a rejected fast-path fake-dual plan; escalation later produced the good rename+join result that manual review scored YES.

These captures are isomorphic to **P39G-11** fake dual. Expected corrected label: **NON-PASS**.

## RQ answers (short)
1. V2.2 “hallucination” framing is incomplete — FAIL is evidence-supported; wording may over-claim “columns collapsed away”.
2. PASS vs FAIL captures differ by ops + identical signature sets + capture≠final plan.
3. Class **F (mixed)** — primary misattribution; secondary unsupported claim wording.
4–6. Prompt grounding/self-check may improve wording; must not convert these captures to PASS.

## Interventions
- I0 baseline: stable FAIL on exact captures (no STOP-BASELINE-DRIFT).
- I1–I3: research-only; I4/I5 skipped.
- Gate **B**: Root cause substantially clarified (misattribution + claim wording). Prompt interventions are not Gate-A candidates because FF oracles should remain NON-PASS. Shadow validation of a "fix" is not justified.

## Safety
Any intervention that makes exact P39M-07/08 PASS fails R1 relative to fake-dual semantics.

## Architecture
LLM = semantic decision · Python = deterministic observation. Unchanged. No production patch.
