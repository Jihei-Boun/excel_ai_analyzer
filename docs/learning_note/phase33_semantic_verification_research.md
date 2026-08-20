# Phase 33 — Semantic Wrong-Success Verification Research

## 1. Executive Summary

Offline LLM semantic verification is **partially viable**: **7B + Prompt+Plan (V1)** detects **Type C** (wrong grain/intent with internally consistent group+aggregate) with **recall 100% on Type C / 50% overall silent**, **precision 100%**, **valid FP = 0**, parse fail = 0, latency ~**3.6s**. Adding result/understanding context (**V2/V3**) **destroys 7B discrimination** (recall→0). **32B** matches Type-C detection but adds **~8% valid FP** and **~50–70s** latency without better Type B/D coverage. Not a blanket every-success gate; **Type B/D remain undetected**. Production pipeline unchanged.

## 2. Dataset

Probe (golden labels offline only):

| Class | Count |
|-------|------:|
| VALID_SUCCESS | 12 |
| SILENT_WRONG | 8 |
| Type B | 2 |
| Type C | 4 |
| Type D (control) | 2 |

Leakage prevention: verifier payload = `user_prompt` + compact plan (+ optional result schema/count + rebuilt understanding). No scenario id, expected_*, golden, overall_ok.

## 3. Verifier Contract

```json
{ "verdict": "pass|fail|uncertain", "reason_code": "...", "evidence": ["..."] }
```

Judge-only; no repair/replan. Generic reason codes only.

## 4. Context Comparison

| Variant | Best use |
|---------|----------|
| **V1 Prompt+Plan** | **Best** — Type C visible from declared grain+ops |
| V2 +Result | 7B collapses to pass-all; 32B similar to V1 but slower |
| V3 +Understanding | No Type B/D lift; more FP/uncertain on 32B |

Smallest useful context: **Plan-only**.

## 5. Model Comparison

| Model | Best config | Silent recall | Precision | Valid FP | Latency mean |
|-------|-------------|-------------:|----------:|---------:|-------------:|
| 7B | V1 | 50% (C:100%) | 100% | 0% | 3.6s |
| 32B | V1 | 50% (C:100%) | 80% | 8.3% | 49.7s |

Same-capability 7B→7B does **not** rubber-stamp Type C. Strong 32B is **not** better for this task on FP/cost.

## 6. Confusion Matrix (promising)

### 7B V1
`TP=4 FP=0 TN=12 FN=4` (FN = all Type B+D)

### 32B V1
`TP=4 FP=1 TN=11 FN=4`

## 7. Type-Level Detection

| Type | 7B V1 | 32B V1 |
|------|-------|--------|
| C wrong grain/intent | **4/4 FAIL** | **4/4 FAIL** |
| B under-declaration | 0/2 (PASS) | 0/2 |
| D grain+agg (control) | 0/2 (PASS) | 0/2 |

Verifier catches **plan-level intent/grain mismatch**, not undeclared fields or post-P30 residual Type D patterns when plan looks coherent.

## 8. Stability / Parse

- Parse fail rate: **0%** all configs  
- Temperature 0; multi-instance same `case_id` Type C verdicts stable FAIL under V1  
- UNCERTAIN rare (32B V3 only, 5%)

## 9. Latency / Cost

| Config | mean | p50 | p95 | +every-success vs ~34s baseline |
|--------|-----:|----:|----:|--------------------------------:|
| 7B V1 | 3.6 | 3.3 | 5.0 | ~38s |
| 32B V1 | 49.7 | 48.7 | 69.6 | ~84s |
| 32B V3 | 68.7 | 60.2 | 97.6 | ~103s |

Every-success 32B verification is impractical; even 7B needs selective invocation research.

## 10. Optional Recovery Simulation

Not wired. Hypothetical: 7B V1 FAIL → escalate replan would target Type C only; Type B/D would not trigger; FP escalations = 0 under 7B V1.

## 11. Architecture Audit

| Check | Result |
|-------|--------|
| scenario/domain/column routing | PASS |
| golden leakage in verifier input | PASS |
| Plan mutation / production wiring | PASS |
| Validator/Executor/escalation/evaluator/`route_multi` | PASS (unchanged) |

## 12. Regression

- pytest: pass (incl. `test_phase33_*`)  
- deterministic: **100 / 100 / 0**  
- Phase 30 baseline / production planner prompt: **unchanged**

## 13. Recommendation

### **B (limited) — 7B Plan-only verifier viable for Type C**

with explicit limits → treat overall as **C-adjacent** if requiring full silent-wrong coverage:

- Do **not** deploy every-success verification  
- Do **not** prefer 32B verifier for this discrimination task  
- Type B/D need other approaches (capability / different evidence)

### Phase 34

```text
Phase 34 — Cheap / Selective Semantic Verification Strategy
```

Focus: when to invoke **7B V1** without scenario routers; interaction with Phase 28 escalation; still no Shadow/`route_multi`.

## Artifacts

```text
benchmark_results/multi/phase33/
  verification_dataset.json
  verifier_context_probe.json
  verifier_model_comparison.json
  verifier_confusion_matrix.json
  verifier_stability.json
  verifier_latency.json
  hypothetical_cost_simulation.json
  optional_recovery_simulation.json
```
