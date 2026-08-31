# Phase 39P — Attempt-Aware Shadow Correctness & Escalation Revalidation

## 한 줄 결론

Phase 39O 라인리지를 켠 채 라이브 Shadow 관측을 수행했으나, **교차 요청 plan/result 오염**으로 attempt 귀속이 신뢰되지 않아 공식 verifier FF / silent-wrong / recovery 비율을 집계하지 않았다. **Gate C** (attribution integrity). Migration = NOT APPROVED. Shadow = OFF.

## 왜 이 Phase가 필요한가

39M은 “Final Correct + Verifier FAIL = FF”로 보였지만, 39N/39O는 verifier가 A1(fake-dual)을 평가하고 final은 A2였음을 보였다. 39P는 그 attempt 단위로 재측정하려 했다.

## 관측 설정

- Baseline / 39O SHA: `844ab09`
- Shadow: enabled, sample_rate=1.0, inline=false, concurrency=1 (세션 한정)
- Capture + attempt lineage ON
- Fixed set: 15 requests (A–F families), `pilot_request_set.json` 동결
- 관측 후 Shadow OFF 증명

## 원자료(비공식)

| 항목 | 값 |
|---|---|
| requests | 15 |
| raw success / timeout / failed | 7 / 6 / 2 |
| attempt rows | 13 |
| verifier-evaluated attempts (raw) | 8 |

이 숫자는 **백그라운드**이며 공식 분모가 아니다.

## STOP-4: Attribution integrity failure

성공으로 표시된 여러 행에서 `attempt_id` prefix는 맞아 보여도 **prompt domain과 final_plan/result columns가 불일치**했다.

예:

- P39P-05 (dock stock by sku) → result `hub_name, total_packages`
- P39P-09 (shift AM/PM) → result `sku, dock_a_stock, dock_b_stock`
- P39P-03 → `verified_attempt_id = req-14:A1:…` (타요청 prefix)

추정 원인: `shadow_timeout` 후에도 inflight worker가 남아 concurrency=1이어도 다음 요청과 결과가 섞임.

오염 flag case: P39P-03,05,06,09,10,11,14,15.

## 공식 메트릭 정책

```text
Attempt Manual Correct vs Verifier Verdict
```

를 오염된 binding으로 계산하면 39M과 같은 오판이 난다.

따라서:

- FALSE_FAIL_official = null
- SILENT_WRONG_official = null
- SEMANTIC_RECOVERY_CONFIRMED_official = null
- UNNECESSARY_ESCALATION_official = null
- final Shadow correctness_official = null

모든 수동 라벨은 **INDETERMINATE**, `official_metric_eligible=false`.

## Gate

**Gate C** — silent-wrong을 “관측했다”는 뜻이 아니라, **attribution이 깨져 관측이 무효**라는 뜻.

- STOP-4 attribution integrity
- STOP-3 shadow isolation suspect (timeout backlog)
- 안전/마이그레이션 금지 유지

## 회귀

관측 후 lineage/capture/provenance/verifier/escalation + shadow mode 테스트 PASS. 시맨틱 패치 없음.

## 다음 권고

1. Shadow worker drain / request-scoped result binding 수정 (관측 인프라)
2. inflight<=1 및 timeout 후 잔존 작업 금지 증명
3. 그 다음 **새로운 clean** attempt-aware observation Phase
4. **Migration 금지** / verifier redesign 금지 / V2.2 변경 금지

## 원칙 재확인

```text
Request
 ├── Attempt A1 → Verifier V1
 ├── Attempt A2 → Verifier V2?
 └── Final Attempt
```

Request 정답성 ≠ Attempt 정답성 ≠ Verifier 정답성 ≠ Escalation 가치.  
Attribution first. Correctness second. Recovery third. Migration later.
