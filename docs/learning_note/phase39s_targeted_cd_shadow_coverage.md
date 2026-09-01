# Phase 39S — Targeted C/D Family Shadow Coverage Completion

## 한 줄 결론

39R에서 비었던 C/D 라이브 verifier 노출을 8건으로만 다시 봤다. 격리는 **8/8, 오염 0**. C verifier 노출 **1/4** (CORRECT_REJECTION 1, FALSE_PASS 0). D verifier 노출 **2/4**는 모두 붕괴 A1에 대한 CORRECT_REJECTION이며, **valid partitioned D attempt의 verifier 평가 = 0**. 단일파일 비교 형상 5건은 `cannot_plan → 실패 32B`. **Gate B**. `SYSTEMATIC_OPERATIONAL_EXPOSURE_GAP`. Migration = NOT_APPROVED. Shadow = OFF.

## 왜 이 Phase인가

39R Gate B의 공백은 정확도가 아니라 **C(fake-dual)·D(독립 파티션) verifier 도달**이었다. 39S는 그 패밀리만 관측한다. 파이프라인을 바꿔 노출을 만들지 않는다.

## 진입

- 39R SHA: `dbea8c4999296bfed1242139c644dda1d97dc1f8` (Gate B, committed/pushed)
- 39Q isolation / 39O lineage / V2.2: unchanged (`f0b5d7a`)
- Shadow OFF → 세션만 ON → 즉시 OFF
- 타임아웃 팽창 없음 (600s / caller 1800s)
- `PYTHONPATH=.` 스모크 후 공식 실행

## 요청 세트 (신규 4C+4D)

| ID | 형상 | 결과 한 줄 |
|---|---|---|
| P39S-C01 | 단일 메트릭, 측 구분 없음 | 32B cannot_plan, verifier 없음 |
| P39S-C02 | face/back 요청, 구분 열 없음 | A1 이중 alias FAIL = CR |
| P39S-C03 | Alpha/Beta 요청, 구분 없음 | 32B cannot_plan |
| P39S-C04 | weekday/weekend 요청, 구분 없음 | 32B cannot_plan |
| P39S-D01 | 같은 origin, day 파티션 | caller timeout + 32B cannot_plan |
| P39S-D02 | dawn/dusk 파일 분할 | A1 union+agg CR → A2 rename+join YES |
| P39S-D03 | season 파티션 | 32B cannot_plan |
| P39S-D04 | W1/W2 파일 분할 | A1 union+agg CR → A2 rename+join YES |

## 격리 (시맨틱보다 먼저)

telemetry 8/8 (D01은 caller timeout 후 identity late bind). attribution 8/8. 교차 오염 0. STOP 없음.

## 노출 vs 정답성

```text
C verifier exposure = 1   (target 2)  → unmet
C CORRECT_REJECTION = 1
C FALSE_PASS = 0

D verifier exposure = 2
D valid partitioned verifier = 0   (target 2)  → unmet
D collapsing A1 CORRECT_REJECTION = 2
D FALSE_FAIL = 0
D CORRECT_PASS = 0
```

C02 A1: `identical_evidence_signature_column_sets = [[face_total, back_total]]`. Manual NO + FAIL.

D02/D04 A1은 valid D가 아니다. 나중에 성공한 A2를 부모 FAIL의 FF로 읽지 않는다.

## 운영 공백

3/4 C와 2/2 단일파일 D가 같은 길:

`fast cannot_plan → failure 32B cannot_plan (± shadow_timeout 마킹)`

두 파일로 이미 갈라진 D02/D04만 fast 계획이 실행되어 verifier에 닿았다. 그것도 붕괴 부모였다. valid 자식은 `reverify_strong=False`라 verifier를 다시 타지 않는다.

동일 세트를 반복해도 같은 운영 경로가 재현될 가능성이 높다. **다음 Phase는 추가 C/D 라이브 반복이 아니라 pre-verifier reachability 원인 조사.**

## Gate B

격리·무 silent wrong·무 반복 D FF·Shadow OFF·회귀 clean. 그러나 C/D 노출 목표 미달 + 체계적 운영 공백.

Migration 금지.
