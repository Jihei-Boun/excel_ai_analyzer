# Phase 28 — Evidence-based Planner Model Strategy & Escalation

## 1. Phase 27 결론

- DSL / Validator / Executor는 residual final을 표현·검증할 수 있음
- 7B residual의 주병목은 Planner capability
- 32B는 capability 충분, default로는 latency (~140s) 과다
- 8B는 unsafe=5.26% → production escalation 후보 제외

## 2. Escalation probe

`benchmark_results/multi/phase28/escalation_probe.json`

7B 실패 taxonomy (case×run):

| Class | Count | Notes |
|-------|------:|-------|
| A first-pass success | 27 | |
| B retry success | 6 | |
| G expected cannot_plan | 9 | ambiguous / unrelated / impossible |
| C retry_exhausted → 32B recoverable | 9 | lookup, rename_join |
| C wrong-result success → 32B recoverable | 6 | composite, three_file, same_schema |
| D cannot_plan → 32B recoverable | 0 | |

## 3. Fast failure taxonomy (운영 관점)

**Escalation-visible (runtime evidence):**

- `status=failed` + `join_key_dropped_in_final_projection` (+ repeat family)
- 예: lookup, rename_join

**Escalation-invisible (silent wrong success):**

- `status=success` but evaluator overall_ok=False
- 예: composite, three_file, same_schema
- Production은 golden을 모르므로 **escalate 불가** (설계상 FN)

## 4. Escalation evidence (허용)

- plan/result/execution failure codes in `retry_log`
- `exhausted`, validation failure counts
- duplicate / same-family repeat
- `repeated_final_contract_failure`

금지: case_id, domain, prompt keyword, golden, overall_ok

## 5. cannot_plan safety

- `cannot_plan` → **never escalate**
- dominant `union_incompatible_schema` / unsafe join gates without projection triggers → **never escalate**
- Prefer safe failure over forcing strong model

## 6. Escalation policy

`core/integrate/planner_model_strategy.py`

```
fast path (7B, max_retries=2)
→ if status=failed AND trigger codes (join_key_dropped_…)
→ strong path (32B, same Validator/Executor/Result Validator)
→ else keep fast outcome
```

Policy = **A/B hybrid**: full fast retries, then escalate only on recoverable exhausted failures.

## 7. Retry vs escalation

- Fast retries first (diversity / final-contract regenerate 유지)
- Escalation은 plan을 수정하지 않음 — 모델만 교체
- Strong 입력: generic failure summary only (ops 지시 금지)

## 8. Strong planner input

`build_escalation_feedback()` — stages/codes/repeat signals + cannot_plan safety reminder.

## 9. Offline simulation (Phase 27 frozen)

`benchmark_results/multi/phase28/offline_strategy_simulation.json`

| Strategy | Overall | Safe | Unsafe | 32B % | Est. planner latency |
|----------|--------:|-----:|-------:|------:|---------------------:|
| 7B only | 73.68 | 89.47 | 0 | 0 | 9.6s |
| 32B only | 100 | 100 | 0 | 100 | 140s |
| Escalation | **84.21** | **100** | **0** | **10.53** | **24.3s** |

- FP escalation = 0%
- FN escalation = 15.79% (silent wrong-success: composite/three_file/same_schema)
- lookup final 0→100, dirty 100 유지, composite/three-file final 여전히 0

## 10. Live benchmark

Runner: `--live --escalate --model qwen2.5:7b --strong-model qwen3:32b --runs 3`

Results: `benchmark_results/multi/phase28/live_escalation/` + `live_escalation_summary.json`

| KPI (3-run mean) | Live |
|------------------|-----:|
| overall | **84.21** |
| safe | **96.49** |
| unsafe | **0** |
| escalation / strong invocation | **10.53** |
| escalation success | **10.53** (all escalations succeeded) |
| lookup final | **100** |
| dirty final | **100** |
| composite / three-file final | **0 / 0** |

Escalated cases (every run): `lookup_join_001`, `rename_join_001` → `strong_escalation_success`.

Live ≈ offline simulation (overall/unsafe/escalation rate match).

## 11. Safety results

- Live unsafe=0 across 3 runs
- ambiguous/unrelated not escalated
- Escalation never bypassed validators

## 12. Latency / cost

Offline est. planner latency ≈ 24.3s vs 7B 9.6s / 32B 140s.

Live wall: ~3 suites with occasional 32B calls (log timestamps ~01:35 → 02:40 UTC ≈ 65 min for 3 runs).

## 13. Hardcoding audit

`tests/test_phase28_model_strategy.py` — no scenario/domain/keyword routing in strategy module.

## 14. Phase 27 → 28 KPI

| KPI | P27 7B | P27 32B | P28 Escalation live |
|-----|-------:|--------:|--------------------:|
| overall | 73.68 | 100 | **84.21** |
| safe | 89.47 | 100 | **96.49** |
| unsafe | 0 | 0 | **0** |
| composite final | 0 | 100 | 0 |
| lookup final | 0 | 100 | **100** |
| three-file final | 0 | 100 | 0 |
| dirty final | 100 | 100 | **100** |
| strong invocation | 0 | 100 | **10.53** |
| latency | 9.6s | 140s | ~24s est. |

## 15. Shadow readiness

**Not ready** for `route_multi` Shadow:

- unsafe=0 ✅
- safe ≥ 7B ✅
- lookup recovered ✅
- composite/three-file still 0 ❌
- strong rate 10.53% ✅
- latency << 32B-only ✅

## 16. Phase 29 recommendation

**Decision B** (with partial A): evidence-based escalation is **practical and safe** for validation-exhausted recoveries (+10.53pp at 10.53% 32B), but Shadow is blocked by silent wrong-success residuals.

Next options:

1. Targeted result-contract coverage for silent wrong-success (careful — no domain rules)
2. Keep escalation layer; separately diagnose composite/three-file with observability-only probes
3. Do **not** switch `route_multi` yet
