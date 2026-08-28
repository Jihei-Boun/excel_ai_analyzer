# Phase 39O — Verifier Attempt Attribution Learning Note

## 한 줄 결론

Phase 39M의 “최종 Shadow 정답 + verifier FAIL = false-fail” 해석은 **시도(attempt) 단위 귀속 실패**였고, Phase 39O에서 verifier invocation ↔ candidate attempt 라인리지를 넣어 그 오해를 재현 불가능하게 막았다.

## 핵심 교훈

1. **요청 단위 정답성 ≠ verifier 단위 정답성**  
   한 request 안에 거절된 fast attempt와 이후 strong final attempt가 공존할 수 있다.

2. **Verifier는 자신이 본 plan/result에 대해서만 평가받는다**  
   `final_plan`과 capture plan이 다르면, capture 쪽 시도를 기준으로 판정해야 한다.

3. **Claim quality와 verdict correctness는 분리**  
   P39M-07/08처럼 FAIL 판정은 맞고 설명 문구만 약한 경우가 있다. UNSUPPORTED_STRUCTURAL_CLAIM ≠ FALSE_FAIL.

4. **관측(telemetry)은 의미를 바꾸지 않는다**  
   attempt_id / fingerprint 기록 실패는 semantic 경로를 바꾸면 안 된다.

## Attempt 정의 (이 저장소)

- **새 attempt**: deterministic success에 도달한(또는 strong-path 결과가 된) IntegrationPlan 후보.
- Planner format/validation retry: 같은 attempt 생산 과정의 내부 호출.
- Verifier parse/model retry: 같은 `attempt_id`, 다른 `verifier_invocation_id`.
- Semantic 32B replan / Failure 32B escalation: **자식 attempt** (trigger로 구분).

## Phase 39M 재귀속

| Case | Verifier가 본 것 | Verdict | Attempt 수동 정답 | Final | 결론 |
|---|---|---|---|---|---|
| P39M-07 | fake-dual union→agg | FAIL | NO | rename+join YES | CORRECT_VERDICT; ATTRIBUTION_CORRECTED |
| P39M-08 | 동일 패턴 | FAIL | NO | rename+join YES | 동일 |

## Gate

**Gate A** — attempt-level attribution 인프라 준비 완료.  
Migration = NOT APPROVED. Shadow = OFF.

## 다음

작은 **attempt-aware Shadow observation**으로 FF / silent-wrong / recovery / unnecessary escalation을 재계산. Migration 금지.
