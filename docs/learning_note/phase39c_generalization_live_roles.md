# Phase 39C — Generalization, Live Planner Roles & End-to-End Recovery

## Purpose

Measure Phase 39B before changing it:

1. Does Independent Semantic Verifier generalize beyond REC12-shaped offline collapses?
2. Does live `qwen2.5:7b` Planner declare `output_roles` when material distinction is required?
3. Does it avoid over-declaring roles on combined-total / append prompts?
4. Do roles add incremental verifier value vs Independent Verifier alone (Variant B vs C)?
5. Does semantic FAIL → `qwen3:32b` replan recover real wrong plans end-to-end?

Production migration / `route_multi` changes are out of scope.

## Phase 39B Freeze

Recorded in `benchmark_results/multi/phase39c/phase39b_freeze.json`:

- HEAD: `680d10a522d5adf575974752891ba2765086cfd8`
- Commits: optional `output_roles` → structural validators → planner instruction → independent verifier → fixtures/tests/note
- Models: planner/verifier `qwen2.5:7b`; strong `qwen3:32b`
- Principle: **Measure before changing**

No Phase 39B production logic was patched during 39C.

## Evidence Tiers (not mixed)

| Tier | Artifact | Purpose |
|---|---|---|
| 1 Offline | `tier1_offline.json` | Verifier discrimination + ablation B/C |
| 2 Live planner | `tier2_live_planner.json` | Role recall/precision + plan composition |
| 3 E2E | `tier3_e2e_escalation.json` | Semantic escalation recovery + latency |
| Targeted | `rec12_targeted_live.json` | REC12-shaped live recheck |

Harness: `tests/benchmark_multi/phase39c_eval.py`  
Fixtures: `tests/benchmark_multi/fixtures/phase39c/offline_generalization.json`

## Datasets

### Tier 1 — Offline

- Phase 39B frozen: 6 C2 + 6 valid (+ REC12 collapsed plan in suite)
- Phase 39C new: **10 C2** (patterns A–H across energy / ML / finance / ops / saas / logistics) + **12 valid controls** (union totals, append, single-source, dual-side correct, join-no-contrast, summary)
- Historical C1: 1 canonical Phase 34 fixture via `load_canonical_historical_fixture()`

### Tier 2 — Live (synthetic neutral frames)

- 5 distinction-required prompts
- 5 negative controls (combine / overall / append / integrated total / combined mean)
- `live-repeats=1` (stochastic stability 3× **not** completed — cost/time)

### Tier 3 — E2E subset

- LIVE-C2-energy, LIVE-C2-finance, LIVE-C2-saas, LIVE-VC-combine-total
- Semantic escalation config unchanged in spirit; harness used `reverify_strong=True` for measurement

## Offline C2 Generalization (Tier 1)

| Slice | Variant | N C2 | C2 non-pass recall | N valid | Valid FAIL FP | Uncertain | mean latency |
|---|---|---:|---:|---:|---:|---:|---:|
| 39C-only | B (roles stripped) | 10 | **1.0** | 12 | **0** | 0 | 2.98s |
| 39C-only | C (roles kept) | 10 | **1.0** | 12 | **0** | 0 | 2.90s |
| all (39B+39C) | B | 16 | **1.0** | 18 | **0** | 0 | 3.24s |
| all | C | 16 | **1.0** | 18 | **0** | 0 | 2.96s |

Latency (Variant C all): mean ≈ 2.96s, p50 ≈ 2.94s, p95 ≈ 3.80s.

**Q1 answer:** Yes for frozen offline collapse patterns — high recall, zero valid FP on expanded controls.

False-pass / false-fail counts (Variant C offline labels): **FP=0, FF=0**.

## Ablation — Variant B vs C

Across all measured offline metrics, **B ≈ C**.

Roles did **not** improve C2 recall, C1 non-pass, or valid FP on frozen plans.

## Live Planner Role Quality (Tier 2)

| Metric | Value |
|---|---:|
| Role recall (required cases) | **0.2** (1/5) |
| Role precision (among declared comparison roles) | **1.0** |
| Over-declaration rate (negative controls) | **0.0** (0/5) |
| Wrong binding (observed) | not systematically counted; incomplete declarations dominate |

Required-case composition (heuristic):

| Class | Count |
|---|---:|
| Correct dual-side (dual-agg+join) | 0 |
| Wrong collapse (union+single agg) | 1 |
| Other wrong / partial | 4 |
| Cannot-plan / parse fail | 0 |

Negative controls: 4 combined-agg + 1 append — all without comparison roles.

**Q2:** Live 7B under-declares roles heavily when distinction is needed.  
**Q3:** Live 7B avoids unnecessary comparison roles on combine/append/total prompts (good precision / low over-declaration).

Important: role declaration ≠ semantic plan correctness. One case (`LIVE-C2-ml`) declared A/B roles but still composed a broken union→self-join plan; verifier failed both B and C.

## Live Plan Quality & Verifier on Live Plans

Manual notes (do not treat heuristic composition as ground truth):

1. **LIVE-C2-energy (Tier2):** classic union→agg collapse → verifier **FAIL** (good).
2. **LIVE-C2-finance (Tier2):** join→agg with suspicious `actuals.xlsx.amount` column refs → verifier **PASS** — treat as **live false-pass risk** (aspirational dual metrics).
3. **LIVE-C2-saas (Tier2):** join + `gt` left/right filter + both sides in select — composition may be **semantically acceptable**; PASS may be correct (not counted as offline-style C2 collapse).
4. **LIVE-C2-change:** incomplete roles (entity_key only); verifier FAIL.
5. Negative controls: verifier PASS — good (no invented contrast).

## End-to-End Escalation (Tier 3)

| Case | Pipeline status | final_path | semantic_32b | elapsed_s | Manual read |
|---|---|---|---|---:|---|
| LIVE-C2-energy | success | semantic_escalation_success | True | 446 | 7B/32B both produced dual-side **join** with roles; verifier still **FAIL** → **false-fail on correct-looking join** + unnecessary semantic escalation |
| LIVE-C2-finance | success | strong_escalation_success | False | 159 | Failure escalation (not semantic); rename×2+join dual-side; verifier PASS |
| LIVE-C2-saas | success | strong_escalation_success | False | 509 | Failure escalation; join+filter with roles; verifier PASS |
| LIVE-VC-combine-total | success | fast_success | False | 15 | Correct combined agg; no roles; verifier PASS |

**Q5 answer (cautious):** Clean “wrong collapsed 7B → verifier catch → 32B correct recovery” was **not** cleanly demonstrated on this subset.

- Useful recovery of a clear C2 collapse via semantic path: **not observed** in Tier3 rows
- Unnecessary semantic escalation driven by verifier false-fail: **observed** (energy)
- Failure-path 32B still useful for validator/execution recovery (finance/saas)

## REC12

- Offline collapsed fixture: still **FAIL** under Variant B and C (stable non-pass)
- Targeted live (`rec12_targeted_live.json`, synthetic july/august frames, ~874s):
  - Final path: `strong_escalation_success` (**failure** escalation, not semantic)
  - Final plan: `join` → `rename_columns` with `output_roles` sides A/B (`july_sales` / `august_sales`)
  - Final semantic verifier: **PASS**
  - **Bad path** (collapsed union→agg + verifier PASS): **0** in this targeted run
  - Caveat: plan preserves both sides but does **not** filter “which increased”; distinction preserved ≠ full answer completeness
  - Original 7B path still needed strong recovery for execution/validation — not a pure “7B correct dual-side first try” story

## Output Roles Incremental Value

### Decision: **R-ROLE-B**

Roles do **not** improve Independent Verifier detection on offline evidence (B==C),  
but remain useful as **Planner declaration / structural observability** when present.

Do **not** remove yet solely due to complexity; also do **not** treat roles as required correctness proof.

Evidence is **not** R-ROLE-A. Incomplete live declaration keeps some uncertainty (not enough for R-ROLE-D given clear B≈C offline).

## Safety / Historical Non-Regression

- `pytest` Phase 30 grain + Phase 31 diagnostics + Phase 39B tests: **PASS**
- Type D `final_grain_contradiction` remains in escalation triggers
- Type B under-declaration: **not claimed fixed**
- `unsafe` production Shadow not re-run broadly; `MULTI_SHADOW_ENABLED` left false
- Deterministic safety tests: no new unsafe signal in measured suite

Historical C1 offline: 1/1 non-pass under independent verifier (kept).

## Latency (if this pipeline were primary today)

| Path | Observed |
|---|---|
| Verifier only | ~3.0–3.5s mean; p95 ~3.8–4.6s |
| Fast semantic-success (valid combine) | ~15s end-to-end (Tier3 VC) |
| Semantic / strong paths | ~160–510s on measured C2 subset (32B dominant) |

Do not conflate with Shadow background UX claims.

## Architecture Audit

Responsibility boundary held:

- No validator prompt intent inference added
- No executor semantic inference / keyword / domain / fixture routing added
- No `route_multi` / Legacy / PandasAI changes
- No DSL arithmetic / comparison ops added
- Shadow default remains off

## Data Leakage / Evaluator Integrity

- Case IDs / expected labels not injected into Planner/Verifier prompts
- No production code branches on fixture IDs
- Offline expected labels unchanged; live failures documented rather than relabeled
- Heuristic composition classifier is **diagnostic only**, not an evaluator softener

## Known Limitations

1. Live role recall low; stability repeats (3×) not completed
2. Verifier strong on frozen union→agg collapses; weaker / inconsistent on live join dual-side plans (false-fail) and aspirational column refs (false-pass risk)
3. Tier3 N is small; 32B recovery rate not statistically stable
4. Historical suite sampled thinly (1 C1 fixture in harness path)
5. “Increased/decreased” without arithmetic DSL remains partially aspirational even when sides are preserved

## Failure Taxonomy (measure-first; no patch in 39C)

| Code | Observation |
|---|---|
| C2-offline-collapse | Covered well by independent verifier |
| ROLE-UNDER | Dominant live failure for distinction prompts |
| ROLE-OK-PLAN-BAD | Declared roles with non-materializing plan |
| VF-FA-JOIN | Verifier fails plausible dual-side join |
| VF-PA-ASPCOL | Verifier passes plans with non-executable dual metric column refs |
| ESC-UNNECESSARY | Semantic 32B invoked due to VF-FA-JOIN |

## Migration Gate

### **Gate B**

Promising offline generalization, but live role quality and live verifier consistency are insufficient for renewed Shadow-as-migration-signal.

Not Gate A: live role recall too low; REC12/live dual-side path not cleanly green; escalation recovery not proven as primary metric.

Not Gate C by default: no confirmed offline silent-wrong family expansion; blockers are live/consistency, not “39B offline collapsed.”

Not Gate D yet: contract still diagnostically useful (R-ROLE-B); redesign premature.

## Recommended Next Phase

### **C. Refine Independent Verifier** (primary)

Focus on:

- False-fail on dual-side join / rename+join plans that preserve both sides
- False-pass on aspirational dual metrics / non-executable column refs
- Keep protocol: no keyword exceptions / no domain routing

Secondary track if needed: **B. Refine Planner role declaration** (raise live role recall without raising over-declaration).

Do **not** migrate production; do **not** simplify away `output_roles` yet.

## Final Recommendation

Phase 39B Independent Verifier **generalizes** on frozen C2 collapses with **valid FP=0** offline, and roles add little detection lift (**R-ROLE-B**).  
Live 7B **under-declares** roles; end-to-end semantic recovery is **not yet evidence-backed**.  
**Hold migration; Gate B; next = verifier live consistency (+ planner role completeness).**
