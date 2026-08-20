# Phase 36 — Semantic Escalation Reliability & Pre-Shadow Gate

## Executive summary

Frozen Phase 35 architecture is **characterized, not tuned**. Correctness/safety support **parallel Shadow observation** (not user-facing replacement). Mean latency ~103s is **tail-driven** (32B paths), while p50 ~24s. Live Type-C intermittency is **explained** by layered detectors (validator first vs silent success → verifier). Historical harmful FP remains 0. **Recommendation: B — Conditional Shadow Readiness** (ops must accept KPI run-to-run span and 32B resource load); **production replacement: not ready**.

---

## Frozen architecture

Confirmed unchanged from Phase 35:

| Component | Freeze |
|-----------|--------|
| Fast / strong planners | qwen2.5:7b / qwen3:32b |
| Verifier | 7B V1 Prompt+Plan |
| Validators / Executor | unchanged |
| Failure escalation | Phase 28/30 |
| Semantic budget | 1 |
| route_multi | not wired |
| Evaluator | frozen |

No accuracy optimization, no new router, no Python/verifier repair.

---

## Request path distribution (Phase 35 live 57)

| Path | Count | Rate | Mean | P50 | P95 | Max |
|------|------:|-----:|-----:|----:|----:|----:|
| A Fast + verifier PASS | 28 | 49.1% | 22.2s | 21.9s | 26.1s | 33.5s |
| B Failure-based strong | 11 | 19.3% | 389.1s | 258.8s | 1148.5s | 1317.5s |
| C Semantic strong | 4 | 7.0% | 154.2s | 145.4s | 188.6s | 194.5s |
| D Verifier not reached | 14 | 24.6% | 25.7s | 24.2s | 41.2s | 43.5s |

Double-strong (failure 32B + semantic 32B): **0 / 57**.

---

## Latency distribution (n=57)

```text
mean  103.14s
p50    23.98s
p75   131.66s
p90   220.20s
p95   313.75s
max  1317.47s

<30s   68.42%
<60s   73.68%
>120s  26.32%
```

Verifier component: mean **3.89s**. Semantic strong planner component: mean **112.76s**.

### Tail attribution

Top tails are almost entirely **Path B (failure 32B)** outliers (e.g. rename_join ~1317s), then Path C (~130–190s). Fast+PASS majority stays ~22s. **Mean inflation is minority 32B tails, not architecture-wide slowdown.**

Instrumentation limit: Phase 35 recorded total + verifier + semantic-strong; understanding/fast/failure-strong are not separately timed — Path B totals used as failure-strong proxy.

---

## Strong invocation breakdown

| Source | Count | Rate |
|--------|------:|-----:|
| failure 32B | 11 | 19.3% |
| semantic 32B | 4 | 7.0% |
| double 32B | 0 | 0% |
| any 32B | 15 | 26.3% |

---

## Verifier reachability

| | n |
|--|--:|
| total | 57 |
| status=success (eligible) | 41 |
| invoked | 41 |
| not reached | 16 |

`invoked + not_reached = total` ✓

**Why 71.93% ≠ 100%:** blanket verify only after **deterministic success**. `cannot_plan` (11) + failed/`retry_exhausted` (5) never reach verifier. Some Path B failures also never succeed → no verify (included in not-reached).

---

## Type-C reachability (`same_schema_union_001`, 3 live runs)

| Run | Deterministic success | Verifier | Escalation | Final ok | Fate |
|-----|----------------------|----------|------------|----------|------|
| 0 | No | No | No | No | plan_validation_failure → remaining failed |
| 1 | Yes | FAIL | semantic 32B | Yes | semantic_recovered |
| 2 | Yes | FAIL | semantic 32B | Yes | semantic_recovered |

Not reaching verifier is **not automatically an architecture failure** — when Plan Validator trips first, semantic layer correctly stays idle. Gap: run 0 also lacked failure-based 32B recovery (observational; not fixed in Phase 36).

---

## Detector interaction

**Complementary layered coverage** (not a merged heuristic):

- Deterministic validators → declared contract / safety (Type D + unsafe structure)
- Semantic verifier → Plan ↔ request on successes (Type C)
- Model strategy → separate `failure_*` vs `semantic_*` attribution

Same request family can hit validator in one sample and Type-C silent success in another depending on planner output — **competition for which evidence appears first**, responsibilities remain distinct.

---

## False escalation stress

Single pass over **35** VALID candidates (21 historical + 14 synthetic):

| Dataset | Verified | false FAIL | false UNCERTAIN | false esc | Harmful |
|---------|---------:|-----------:|----------------:|----------:|--------:|
| Historical real | 21 | 0 | 0 | 0 | **0** |
| Synthetic | 14 | 2 | 1 | 3 | **0** |
| Overall | 35 | 2 | 1 | 3 (8.6%) | **0** |

All 3 false escalations: 32B replan → **remains correct** (harmless under replacement policy).

Original-vs-strong auto-select **not** implemented (deferred).

---

## Repeatability (Phase 35 live ×3 = frozen KPI stability)

| KPI | min | max | span |
|-----|----:|----:|-----:|
| overall | 89.47 | 100 | 10.53 |
| safe | 94.74 | 100 | 5.26 |
| unsafe | 0 | 0 | 0 |
| verifier % | 68.42 | 73.68 | 5.26 |
| failure 32B % | 10.53 | 26.32 | 15.79 |
| semantic 32B % | 0 | 15.79 | 15.79 |
| total 32B % | 21.05 | 31.58 | 10.53 |
| latency mean | 63.5 | 123.6 | 60.1 |
| latency p50 | 22.2 | 25.3 | 3.1 |

Overall span is largely explained by Type-C reachability on run 0 — not unexplained evaluator drift.

### Offline Type-C ×3 (9 historical plans × 3)

```text
recovery = 27/27 (100%)
verdict_stable = 100%
outcome_stable = 100%
harmful = 0
```

---

## Type coverage

| Family | Status |
|--------|--------|
| Type D | Deterministic Plan Validator — preserved |
| Type C | Verifier + one strong replan — promising / intermittent live reachability |
| Type B | **Unresolved** |

---

## Resource estimate (per 100 candidate requests)

| Call | Expected |
|------|----------:|
| semantic verifier | ~72 |
| failure 32B | ~19 |
| semantic 32B | ~7 |
| any 32B | ~26 |

- **User-facing suitability:** not ready (mean ~103s)
- **Shadow suitability:** observable if parallel capacity absorbs ~26% 32B + ~72% verifier (capacity UNKNOWN)

---

## Architecture audit

| Check | Result |
|-------|--------|
| scenario/domain/column/file-count/op/complexity routing | PASS |
| Python semantic inference / Plan mutation / Validator repair | PASS |
| Verifier repair | PASS |
| strong-model bypass / evaluator relaxation | PASS |
| benchmark leakage | PASS |
| route_multi / Shadow activation | PASS (none) |

Layer roles unchanged: Understanding / Planner / Validators / Executor / Semantic Verifier / Model Strategy.

Safety cases (ambiguous / many-to-many / incompatible union / unrelated / cannot_plan) remain on deterministic path — verifier does not bypass safety rejection (only runs after success).

---

## Pre-Shadow gate

| Gate | Result | Evidence |
|------|--------|----------|
| unsafe = 0 | PASS | all runs 0 |
| Type-C recovery stable | PASS | offline 27/27; live 2/3 semantic recover |
| harmful historical FP ≈ 0 | PASS | 0/21 |
| verifier stable | PASS | 100% on Type-C ×3 |
| strong replan stable | PASS | 100% outcome stable |
| detector interaction understood | PASS | complementary_layered |
| latency tail understood | PASS | Path B/C tails |
| Shadow resource cost observable | PASS/UNKNOWN capacity | invocation rates known |
| no semantic router | PASS | |
| Type B limitation explicit | PASS | |

### Reliability questions

| ID | Answer |
|----|--------|
| R1 unsafe=0 | YES |
| R2 Type-C stable | YES (offline 100%; live reachability intermittent but explained) |
| R3 no harmful hist FP | YES |
| R4 verifier stable | YES |
| R5 strong replan stable | YES |
| R6 interaction explained | YES |
| R7 latency tail explained | YES |
| R8 latency worth shadow measure | YES (shadow ≠ user-facing) |
| R9 Type B unresolved | YES |
| R10 no semantic router | YES |

---

## Recommendation

### **B — Conditional Shadow Readiness**

**Blocker for Shadow ops sign-off:** live overall KPI span (~10pp) from Type-C not-reached intermittency — understood, but ops must accept variance before Shadow infra.

**Not blockers for Shadow (but blockers for production):** mean latency ~103s / heavy 32B tails; Type B unresolved.

- **Shadow observation:** viable with resource + variance acceptance
- **Production replacement:** **not ready**
- Do **not** auto-activate Shadow without ops sign-off

### Phase 37 candidates

- If ops accepts B → **Multi-file Shadow Mode Infrastructure** (observe only; never change production response)
- Else prefer → **Escalation Cost / Latency Architecture Research** (no scenario router)
- Harmful FP did not appear → Candidate Preservation not urgent

---

## Artifacts

```text
benchmark_results/multi/phase36/
  baseline_freeze.json
  request_path_traces.json
  latency_distribution.json
  latency_tail_analysis.json
  strong_call_breakdown.json
  verifier_reachability.json
  type_c_reachability.json
  detector_interaction.json
  false_escalation_stress.json
  harmful_false_escalation.json
  type_c_repeatability.json
  benchmark_repeatability.json
  shadow_resource_estimate.json
  pre_shadow_gate.json
```

## Principle held

> Do not optimize what has not yet been fully characterized.

> Shadow-ready ≠ Production-ready.
