# Phase 39R — Clean Attempt-Aware Shadow Revalidation After Request Isolation

## 한 줄 결론

39Q 신원 고정 위에서 **새로운** 15건 라이브 관측을 했다. 교차 요청 오염은 **0 / 15**. 공식 verifier 분모 8건에서 **FALSE_FAIL=0, SILENT_WRONG=0**, 시맨틱 복구 **3**. C(fake-dual)·D(same-origin) 패밀리는 32B 타임아웃으로 verifier 미평가. **Gate B**. Migration = NOT_APPROVED. Shadow = OFF.

## 왜 이 Phase가 필요한가

39P는 요청 신원 오염으로 공식 메트릭이 무효였다. 39Q Gate A는 인프라만 증명했다. 39R은 그 freeze 위에서 **첫 신뢰 가능한 attempt-aware 정확도**를 측정한다. 39P 숫자는 재사용하지 않는다.

## 진입

- 39Q SHA: `f0b5d7ae95787e0e48df8f35933f9df10d414fa8` (Gate A, committed/pushed)
- working tree: clean
- Shadow OFF → 관측 세션만 ON → 즉시 OFF
- 관측 전 회귀 PASS (39Q/O/L/H/D, 35, 28, 37, 38)

## 요청 세트 (신규)

15건, 개념 패밀리만 공유. 새 case_id `P39R-01..15`, 새 request_id `p39r-req-01..15`, 새 데이터.

완료 규칙: **identity_bound_finalization** — `record.request_id == submitted request_id`. `new_recs[-1]` 사용 안 함.

## 요청 격리 (시맨틱보다 먼저)

| 항목 | 값 |
|---|---|
| telemetry covered | 15 / 15 |
| attribution valid | 15 / 15 |
| caller timeout | 0 |
| late completion | 0 |
| cross-request flags | 0 |
| STOP | 없음 |

attempt/capture/final prefix는 모두 자기 `p39r-req-NN`. 39P 형태의 `req-14` 혼입은 재발하지 않았다.

## 공식 verifier 분모

```text
verifier-evaluated attempts = 9
attribution-valid = 9
judgeable = 9
official_metric_eligible (YES/NO only) = 8

CORRECT_PASS = 5
CORRECT_REJECTION = 3
FALSE_FAIL = 0
FALSE_PASS = 0
INDETERMINATE (PARTIAL+PASS) = 1
```

## 시맨틱 복구 (확정 3)

모두 같은 패턴: 부모 `union_rows+aggregate` 붕괴 → verifier FAIL `wrong_output_grain` → 시맨틱 32B → 자식 rename+join YES.

- P39R-04 A1 NO + FAIL → A2 YES
- P39R-05 A1 NO + FAIL → A2 YES
- P39R-07 A1 NO + FAIL → A2 YES

`UNNECESSARY_ESCALATION = 0`

## 최종 요청 정답성 (attribution-valid)

YES 8 / PARTIAL 1 (P39R-13 inner join 키 손실) / INDETERMINATE 5 (C·D 타임아웃) / CORRECT_CANNOT_PLAN 1 (P39R-15)

## 운영

- `shadow_timeout` 마킹 6건: 그중 1건(P39R-01)은 완료·라인리지 있음(600s 후 마킹). 5건은 final_attempt 없음.
- 레거시 `pandasai` 미설치 예외 다수. Shadow는 예외 후에도 스케줄됨(38 계약). 시맨틱 패치 없음.
- 타임아웃을 늘려 커버리지를 만들지 않음.

## Gate B 공백

라이브 verifier가 C(fake-dual 원형)·D(독립 파티션)를 평가하지 못했다. B패밀리 부모 붕괴에 대한 CORRECT_REJECTION은 있으나, P39G-11형 단일 테이블 fake-dual의 PASS/FAIL은 이번 분모에 없다.

## 다음

C/D를 겨냥한 **추가 bounded clean observation**. 타임아웃 팽창으로 가리지 말 것. **Migration 금지.**
