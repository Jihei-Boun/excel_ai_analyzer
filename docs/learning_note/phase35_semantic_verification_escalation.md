# Phase 35 — Semantic Verification-Triggered Escalation

## Executive summary

Frozen 7B V1 semantic FAIL (and UNCERTAIN) → **one** generic-evidence 32B replan recovers Type-C end-to-end without prescribing repairs.

- **Historical targeted (primary):** Type-C 9/9 detected → 9/9 recovered; valid FP 0; harmful FP 0.
- **Full live 19×3:** overall **89.47% → 96.49%**, safe **96.49% → 98.25%**, unsafe **0%**; semantic 32B **7.02%**; total 32B **26.32%**.
- **Latency:** mean **~103s** (p50 **~24s**) vs Phase 30 **~34s** / 32B-only **~140s**. Correctness strong; mean cost is the main tradeoff.

**Recommendation: B — Useful but limited** (strong recovery evidence; pre-shadow latency / intermittency gate before production wiring).

---

## Experimental architecture

```text
7B Planner → Plan Validator → Executor → Result Validator
  → deterministic success only
  → 7B V1 Semantic Verifier (Prompt + Plan)
      PASS → accept candidate
      FAIL / UNCERTAIN → one qwen3:32b replan (generic evidence)
          → same Plan Validator → Executor → Result Validator
          → END (no default re-verify of strong)
```

Module: `core/integrate/semantic_escalation.py`  
Harness: `tests/benchmark_multi/phase35_semantic_escalation.py`  
**Not** wired to `route_multi`.

Roles unchanged:

| Component | Role |
|-----------|------|
| Verifier | judge only |
| Strong planner | replan |
| Plan / Result validators | validate |
| Executor | execute |

---

## Verifier freeze

Identical to Phase 34:

| Knob | Value |
|------|-------|
| model | qwen2.5:7b |
| variant | V1 |
| input | User Prompt + IntegrationPlan |
| temperature | 0 |
| verdict | PASS \| FAIL \| UNCERTAIN |

Prompt not modified for Phase 35 recovery outcomes.

---

## Semantic escalation policy

| Verdict | Primary policy |
|---------|----------------|
| PASS | accept |
| FAIL | escalate (budget 1) |
| UNCERTAIN | escalate (configurable `uncertain_policy`) |
| parse_failed | do **not** escalate |

Offline FAIL-only vs FAIL-or-UNCERTAIN on targeted set: **identical** (UNCERTAIN count = 0).

Live: one UNCERTAIN (`three_file_chain_001`) escalated and recovered successfully.

`max_semantic_escalations = 1`. No verifier loop on strong result in primary experiment.

---

## Targeted recovery (primary evidence)

| Dataset | Type-C det. | Recovered | Failed recovery | False esc. | Harmful FP |
|---------|------------:|----------:|----------------:|-----------:|-----------:|
| Historical real | 9/9 | **9/9 (100%)** | 0 | 0 | **0** |
| Synthetic | 5/6 | 4/5 (80%) | 1 | 1 harmless | **0** |
| Overall | 14/15 | 13/14 (92.9%) | 1 | 1 | **0** |

All 9 historical Type-C cases were `same_schema_union_001` silent wrongs; 32B recovered with `union_rows`.

False escalation (synthetic `rename_join_001`): verifier FAIL → 32B replan → **final still correct** (harmless under replacement policy).

---

## Full live 19×3

| Metric | Phase 30 Baseline | Phase 35 |
|--------|------------------:|---------:|
| overall | 89.47 | **96.49** |
| safe | 96.49 | **98.25** |
| unsafe | 0 | **0** |
| verifier invocation % | 0 | 71.93 |
| failure 32B % | 17.54 | 19.30 |
| semantic 32B % | 0 | **7.02** |
| total 32B % | 17.54 | **26.32** |
| latency mean | ~34s | **103.14s** |
| latency p50 | — | **23.98s** |
| same_schema overall ok | (weak / silent wrong) | **66.67% (2/3)** |

### Live semantic escalations (4/57)

| Case | Verdict | Final ok | Ops after |
|------|---------|----------|-----------|
| same_schema_union_001 | fail | True | union_rows |
| same_schema_union_001 | fail | True | union_rows |
| three_file_chain_001 | uncertain | True | join×2 + aggregate |
| rename_join_001 | fail | True | join |

Run 1 `same_schema`: deterministic `plan_validation_error` / `retry_exhausted` **before** verifier → no semantic path (shows Type-C silent-success is intermittent live).

---

## Type coverage

| Family | Status |
|--------|--------|
| **Type D** declared contradiction | Deterministic Plan Validator (Phase 30). `final_grain_contradiction` still observed in live traces. **Preserved.** |
| **Type C** Plan ≠ intent | Verifier + one strong replan. **Candidate — strong targeted recovery; live intermittent occurrence.** |
| **Type B** undeclared requirement | **Unresolved** (do not claim all silent wrongs solved). |

---

## Latency breakdown

| Component | n | mean | p50 | p95 |
|-----------|--:|-----:|----:|----:|
| End-to-end case | 57 | 103.14 | 23.98 | 313.75 |
| Semantic verifier | 41 | 3.89 | 3.57 | 5.19 |
| Semantic strong replan | 4 | 112.76 | 112.14 | 137.68 |

Path means (live): fast ~23s; failure-32B ~389s; semantic-32B ~154s.

Targeted strong replan mean ~130s when triggered.

Still **below** ~140s 32B-only mean, but mean inflation vs Phase 30 is material (tail-dominated).

---

## Escalation source attribution

| Source | Count (of 57) |
|--------|--------------:|
| none | 42 |
| failure | 11 |
| semantic | 4 |

Keep families separate — do not merge into a single heuristic score.

Architecture audit note: failure escalation remains evidence-based recoverable path; semantic FAIL is a **separate evidence family** (`semantic_verifier_fail` / `uncertain`). Avoid growing either into scenario/error-code routers.

---

## Architecture audit

| Check | Result |
|-------|--------|
| scenario routing | PASS |
| domain routing | PASS |
| column routing | PASS |
| selective semantic router | PASS (blanket on deterministic success) |
| Python semantic repair | PASS |
| Verifier auto-repair / new_plan | PASS |
| strong-model bypass | PASS |
| Plan mutation by Python | PASS |
| evaluator relaxation | PASS |
| route_multi change | PASS |

---

## Regression

- Phase 35 unit tests: PASS
- Deterministic multi benchmark: **100%** ok / safe, unsafe 0
- Full pytest: see CI run in session
- Production planner / validators / evaluator / Phase 28 failure policy: unchanged

---

## Hard gates

| Gate | Status |
|------|--------|
| unsafe_execution > 0 | PASS (0) |
| harmful false escalation meaningful | PASS (~0) |
| valid success material regress | PASS (safe improved) |
| 32B → 100% | PASS (26%) |
| latency → 32B-only | PASS but watch mean (103 < 140) |
| scenario/domain exception required | PASS (none) |

---

## Recommendation

### **B — Useful but limited**

Why not A: mean latency ~3× Phase 30; live Type-C path intermittency (some runs never reach verifier); synthetic FP family still present (harmless so far).

Why not C: recovery is strong when escalation fires (historical 100%; live semantic cases all final ok).

Why not D: no harmful FP, unsafe 0, overall/safe improved, no routing/repair violations.

### Phase 36 direction

**Semantic Escalation Reliability / Pre-Shadow Gate**

Focus:

1. Latency mean vs p50 (failure-tail + semantic cost accounting)
2. Cases that fail deterministically instead of becoming Type-C silent success
3. Optional false-escalation handling research (original vs strong) — **not** primary yet
4. Still **no** automatic Shadow / `route_multi` switch

---

## Artifacts

```text
benchmark_results/multi/phase35/
  baseline_freeze.json
  semantic_escalation_targeted.json
  semantic_replan_traces.json
  false_escalation_analysis.json
  historical_recovery.json
  synthetic_recovery.json
  full_live_semantic_escalation.json
  latency_breakdown.json
  latency_breakdown_targeted.json
  escalation_source_breakdown.json
  phase34_comparison.json
  live_semantic_escalation_cases.json
  uncertain_policy_comparison.json
```

## Final principle (held)

> Detection evidence may trigger replanning; it must not prescribe the repair.

> A semantic verifier is useful only if its failures can be recovered without harming valid successes.
