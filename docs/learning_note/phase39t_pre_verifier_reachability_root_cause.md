# Phase 39T — Operational Pre-Verifier Reachability Root Cause

## 한 줄 결론

39S C/D verifier 공백의 주원인은 **verifier가 아니다**. Pattern A는 `fast cannot_plan`이 아니라 **7B가 구조적으로 기각되는 계획을 낸 뒤 실패 32B가 돌고, 32B가 명시적 cannot_plan이거나 300s 타임아웃으로 cannot_plan이 되는 경로**다. 단일파일 D는 DSL로 표현 가능하나 검증기가 필터 이후 uniqueness를 소스 기준으로 보고 `many_to_many`로 기각한다. 두 파일 D는 7B가 `union+agg`를 안정적으로 내고 32B는 **같은 입력만으로** `rename+join`을 낸다. **Gate A (진단 닫힘, 미수정). Migration = NOT_APPROVED. Shadow = OFF.**

## 39S 압축 레이블 보정 (라벨 자체는 유지)

39S 공식 정답 라벨은 바꾸지 않는다. 다만 Pattern A의 `cannot_plan` 주체는 **fast가 아니다**.

정책 `skip_cannot_plan`이 있으므로 32B가 돌아갔다면 `fast.status == failed`다. 최종 `cannot_plan`은 merge된 **strong 경로** 결과다.

## cannot_plan 기원

| Case | 최종 cannot_plan 기원 |
|---|---|
| C03, C04 | EXPLICIT_MODEL_CANNOT_PLAN (32B JSON) |
| C01, D01, D03 | BACKEND_FAILURE (ReadTimeout 300s → `planner_parse_failed`) |
| C02/D02/D04 | 최종 cannot_plan 아님 |

Parser가 의미를 잃은 사례는 지배적이지 않다.

## 입력 / DSL

- **C:** 요청한 side 구분 열이 없다. 실행 가능한 올바른 dual plan은 없다. `cannot_plan`이 맞다 (`SUFFICIENT_FOR_CANNOT_PLAN`).
- **단일파일 D:** `day`/`season` 샘플이 있다. `filter_rows`→(agg|rename)→`join`은 기존 연산으로 **EXPRESSIBLE**.
- **두 파일 D:** 파일 신원과 `same_schema` 힌트가 있다. `rename+join` **EXPRESSIBLE**.

## 재현 (RECONSTRUCTED_REPLAY, n=5)

| Cell | 결과 |
|---|---|
| C03 7B | 5/5 fake-dual `join+agg` → validator `final_grain_contradiction` |
| C03 32B | 5/5 명시적 cannot_plan |
| D01 7B | 5/5 잘못된 branch 계획 → validator 다수 오류 |
| D01 32B | 5/5 의미상 올바른 분기 계획 → validator `many_to_many_join_risk` (필터 전 uniqueness) |
| D02 7B | 5/5 `union+agg` (구조 VALID) |
| D02 32B | 5/5 `rename+join` (실패 내러티브 없이, A5) |

D01 32B 지연 ~277s vs LLM timeout 300s → 39S 타임아웃과 맞닿아 있다.

## 실패 에스컬레이션

Pattern A: `LOW_VALUE` (타임아웃 서브타입) / C03류는 비싼 끝에 올바른 cannot_plan. 정책 변경은 39T에서 하지 않는다.

Semantic 32B (D02/D04): **CLEARLY_USEFUL**.

## Verifier

**주 블로커 아님 (NO).** Pattern A는 invocation 0. Pattern B에서 FAIL은 같은 attempt CORRECT_REJECTION.

## 다음

Python에 side/join/union 의미를 넣지 말 것. 다음 후보:

1. 동등 입력에서 7B vs 32B capability routing 연구 (패밀리 하드코드 금지)
2. 필터 이후 uniqueness를 보는 validator 범위 조사 (의미 추론 없이)
3. 타임아웃-변환 cannot_plan 실패 에스컬레이션 정책 증거 Phase (선택)

## 파이프라인 (구체 모듈)

```text
core/integrate/integration_planner.py          # 7B/32B 호출, parse, cannot_plan 변환
core/integrate/planner_model_strategy.py       # skip_cannot_plan, final_grain_contradiction 에스컬레이션
core/integrate/integration_plan_validate.py    # 구조 검증 (many_to_many_join_risk = 필터 전 uniqueness)
core/integrate/planner_invocation_capture.py   # 39T 관측 전용, default OFF
core/shadow/runner.py                          # 39S telemetry는 retry_log/fast_path_status 누락
core/llm_client.py                             # LLM timeout = 300s
```

## Gate / Migration / Shadow

- Gate = **A** (진단 닫힘. 문제는 고치지 않음)
- Migration = **NOT_APPROVED**
- Shadow = **OFF** (`MULTI_SHADOW_ENABLED` unset, `load_shadow_config().enabled is False`)
- 라이브 Shadow 확인 = 하지 않음 (오프라인 재현으로 충분)
- 프로덕션 시맨틱 수정 = 없음
- 회귀 = 147 passed / 0 failed

## 다음 Phase 트리

- **Outcome A** (두 파일 D): 7B vs 32B capability routing 연구. 패밀리 하드코드 금지.
- **Outcome B** (C 양쪽 실패가 아님): C는 32B가 올바른 cannot_plan. 7B만 fake-dual.
- **Outcome C**: 결정적 관측 누락은 지배적이지 않음.
- **Outcome D**: DSL 확장 불필요 (세 패밀리 모두 EXPRESSIBLE).
- **Outcome E**: 타임아웃-변환 cannot_plan에 대해 Failure Escalation Policy Evidence Phase.
- **Outcome F**: 필터 이후 uniqueness validator 범위 (의미 추론 없이).

39T에서 프로덕션 시맨틱 수정 없음.
