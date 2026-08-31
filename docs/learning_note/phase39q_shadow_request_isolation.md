# Phase 39Q — Shadow Request Isolation & Async Lineage Integrity

## 한 줄 결론

Phase 39P의 교차 요청 오염은 **타임아웃 자체**가 아니라, (1) 워커가 호출자 반환 후에도 계속 돌고, (2) 라인리지/캡처가 **프로세스 전역 env / `_last_record`** 를 늦게 읽으며, (3) 39P 하니스가 **완료 순서**로 레코드를 붙이면서 발생했다. 요청 신원을 스냅샷에 고정하고 식별자 기반 귀속을 강제했다. **Gate A**. Migration = NOT APPROVED. Shadow = OFF.

## 왜 이 Phase가 필요한가

39O는 “verdict는 평가한 attempt에 귀속”을 요구했다. 39P는 그 아래 층이 깨져 있었다: **attempt 자체가 다른 request에 붙을 수 있다.** 그 상태에서 verifier FF를 다시 세면 39M과 같은 오판이 난다.

## 진입 조건

- Phase 39P SHA: `f3aee2ec3d6a27594339866352e69cd135a2c1e3` (Gate C, 커밋·보존)
- working tree: 구현 시작 시 clean
- Shadow default: OFF (`MULTI_SHADOW_ENABLED` unset)
- 39P 아티팩트는 덮어쓰지 않음

## 관측된 사실 (OBSERVED FACT)

- `RequestAttemptLineage(request_id=env_request_id())` 가 워커 **내부·후반**에서 호출됨.
- 캡처 `case_id` 는 항상 live env. `request_id` 는 lineage 없으면 다시 env.
- `update_last_*` 와 verifier 연결이 모듈 전역 `_last_record` / `get_last_record_for_tests()` 에 의존.
- `schedule_shadow` 는 Future를 버리지 않고, `future.cancel()` 을 호출하지 않음. `timeout_sec` 는 파이프라인 **종료 후 마킹**.
- 39P 하니스: `new_recs[-1]`, `case_caps = filter or all`, `ar["request_id"] = rid`.
- 39P 증거: P39P-03 `verified_attempt_id = req-14:...`; P39P-05 prompt vs hub 결과 불일치.

## 가설 (HYPOTHESIS)

- 6건 하니스 타임아웃이 겹침 창을 키웠다. **인과로 단정하지 않음.**
- concurrency=1 이어도 호출자 스레드의 env 덮어쓰기는 실행 중 워커와 겹친다.

## 재현된 메커니즘 (REPRODUCED MECHANISM)

1. env=`req-A`로 스냅샷 생성 → env=`req-B`로 덮기 → 스냅샷은 A, live env는 B (구 라인리지 소스).
2. 파이프라인에 `request_id=req-A` 를 넘기면 env가 B여도 lineage/final은 A.
3. A 지연 + B 진행: B.verified_attempt_id 가 A1이 되지 않음 (39P 오염 형태 차단).
4. A 파이널라이즈가 B 캡처를 덮지 않음 (`refused_rebind`).
5. 완료 순서 C,A,B 에서도 identity map 은 유지.

## 수정 (FIXED INVARIANT)

Pattern A: late completion 허용. 출력은 원래 스냅샷 신원에만 기록. LLM 취소를 강제하지 않음.

- 스냅샷 생성 시 request_id/case_id freeze (호출자 스레드).
- runner → experimental pipeline 으로 명시 전달. 엔트리에서 재freeze. live env 재독 없음.
- 캡처 인덱스는 attempt_id. 업데이트는 신원 일치 시에만.
- 부모/자식/final attempt 는 같은 request_id. 불일치는 기록하고 재바인딩하지 않음.
- 컬렉터 헬퍼: `bind_records_by_request_id` (완료 순서 zip 금지).

시맨틱/프롬프트/verifier/escalation/V2.2 변경 없음. 타임아웃을 늘리지 않음.

## 메트릭 (운영/프로비넌스만)

- reproduction: success
- root-cause: **K (mixed)**
- deterministic isolation tests: 23 PASS
- stress: **0 / 100** cross-request contamination
- S1–S8: 전부 PASS
- Legacy isolation: PASS
- Shadow default OFF: PASS
- verifier accuracy: **not calculated**

## Gate

**Gate A** — request-isolation infrastructure is ready for a **new** clean attempt-aware observation.

의미하지 않는 것: verifier 정확도, migration.

## 다음 권고

1. 39Q isolation freeze 위에서 **새로운** bounded attempt-aware Shadow observation
2. 39P 공식 메트릭 재사용 금지 (무효화된 격리 결함 증거로 보존)
3. 39O lineage + V2.2 유지, verifier redesign 금지
4. **Migration 금지**

## 원칙

```text
Request Identity
    ↓
Candidate Attempt Identity
    ↓
Plan / Result Identity
    ↓
Verifier Invocation Identity
    ↓
Verdict
```

Request Identity 가 신뢰되지 않으면 그 위 모든 정확도 분모는 무효다.
