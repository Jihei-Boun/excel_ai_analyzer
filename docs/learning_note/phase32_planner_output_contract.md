# Phase 32 — Planner Output-Contract Declaration Improvement

## 1. Executive Summary

Generic **answer-field vs mechanics** prompt sharpening (**Candidate A**) did **not** improve Type-B under-declaration on 7B. Offline control metrics were essentially flat. With live-style CrossFileUnderstanding, baseline already declared `customer_name` but omitted aggregate; Candidate A caused **`planner_parse_failed` (2/2)**. Production prompt remains **baseline**. Full live 19×3 was **not** run (candidate not promising). **Decision: D — Prompt insufficient** (Candidate A rejected).

## 2. Prompt Change

### Baseline
Existing Phase 30/31 Final-output-aware planning prompt (~14.5k chars).

### Candidate A (compact delta, +~1.0k chars / ~+254 approx tokens)
1. **Answer fields vs mechanics** — `required_columns` = reader-facing answer fields; join keys ≠ automatic required; match what user asked to see; complete-but-minimal.  
2. **ANSWER COMPLETENESS** self-check before `planned`.

No scenario / domain / column / file-count / fixture hardcoding (verified in tests).

Production: **`_PLANNER_SYSTEM = baseline`** (candidate kept as `candidate_a` variant for experiments only).

## 3. Declaration Quality (7B offline, fixed relationships)

| Metric | Baseline | Candidate A |
|--------|---------:|------------:|
| precision | 0.594 | 0.609 |
| recall | 0.864 | 0.864 |
| F1 | 0.681 | 0.689 |
| under-decl rate | 27.27% | 27.27% |
| over-decl rate | 72.73% | 72.73% |
| Type-B | cannot_plan | cannot_plan |

No material Type-B lift. Precision delta is noise-level.

## 4. Type-B Trace

### Fixed-relationship probe (3× each)
Both variants: **100% cannot_plan** — no declaration to compare.

### Live-understanding probe (closer to production)

| Variant | Status | required_columns | Ops |
|---------|--------|------------------|-----|
| baseline ×2 | planned | includes **customer_name**, category_name (+ id/region) | join→join (**no aggregate**) |
| candidate_a ×2 | cannot_plan | none | `planner_parse_failed` |

Interpretation: Type-B is not stably “missing name in required_columns” only — 7B may declare name yet still fail composition (missing aggregate / wrong grain). Lengthening the prompt made JSON planning worse.

## 5. Full Benchmark

| Metric | Phase 30 | Phase 32 |
|--------|---------:|---------:|
| overall | 89.47 | **unchanged (candidate not live-promoted)** |
| safe | 96.49 | unchanged |
| unsafe | 0 | unchanged |
| 32B invocation | 17.54 | unchanged |
| latency | ~34s | unchanged |

Live E2E for Candidate A: **skipped** — not promising; risk of regression via parse failures.

## 6. Residuals

- **Type B:** unresolved; entangled with composition errors + 7B parse capacity  
- **Type C:** out of scope; unchanged  
- **Type D:** Phase 30 recovery preserved (prompt not adopted)

## 7. Safety

Offline safety cases: ambiguous / many-to-many / incompatible / unrelated still **planned** under both prompts (pre-existing tendency; validators/escalation handle live). `impossible_aggregate` remains cannot_plan. Candidate did not improve safe refusal.

## 8. Architecture Audit

| Check | Result |
|-------|--------|
| scenario / domain / column hardcoding | PASS |
| Python semantic inference / Plan mutation | PASS |
| Validator / Executor / Result Validator / escalation change | PASS |
| evaluator / route_multi | PASS |
| production prompt change | PASS (retained baseline) |

## 9. Regression

- pytest: pass  
- deterministic: **100 / 100 / 0**  
- production prompt = baseline

## 10. Recommendation

### **D. Prompt insufficient** (Candidate A → **Reject**)

근거:
1. Type-B recall/under-declaration not improved offline  
2. Live understanding: Candidate A → parse failures; baseline already can declare `customer_name`  
3. Further prompt lengthening risks 7B capability regression  
4. Do **not** continue benchmark-tuned prompt search  

### Phase 33 gate

```text
Phase 33 — Semantic Wrong-Success Verification Research
```

Type-C + remaining silent wrongs (and Type-B when declaration is complete but composition/result still wrong). Optionally revisit model strategy — **not** more generic prompt inflation.

## Artifacts

```text
benchmark_results/multi/phase32/
  baseline_freeze.json
  prompt_candidates.json
  baseline_prompt.txt / candidate_a_prompt.txt
  declaration_probe.json
  declaration_diff.json
  type_b_repeat_probe.json
  type_b_live_understanding_probe.json
  live_candidate.json
  phase30_comparison.json
```
