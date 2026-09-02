# Phase 40F — Contract Lineage Observability & Deterministic Checker Sufficiency

## 한 줄 결론

이미 선언된 grain 바인딩 E에 대해, Python은 **의미를 해석하지 않고** V2.2 `(source_id, origin_column_ref)` 조상과 연산 그래프로

```text
증명된 보존 → PRESERVED
증명된 소실 → CONTRADICTION
증거 부족 → INDETERMINATE
```

를 구분할 수 있다. 표시 이름 비교는 하지 않는다. `lineage 존재 ≠ grain 보존`.

네이티브 `final_grain` 필드는 없다. last-aggregate `group_by`를 rename/select로 전파하면 대부분의 경우를 도출할 수 있지만, aggregate 이후 join/union은 `FINAL_GRAIN_UNKNOWN`으로 남는다.

- **관측 판정: `SMALL_OBSERVATION_EXTENSION_REQUIRED`**
- **체커 판정: `FIX_OBSERVER_FIRST`**
- **다음: Outcome B — Phase 40G Narrow Contract-Lineage Observation Correction**
- 생산 계약 생성/체커 배선/DSL 변경 **없음**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF. `NO_PRODUCTION_CHANGE`.**

FALSE_CONTRADICTION = 0. FALSE_PRESERVED = 0. 동결 코퍼스에서 관측 공백을 모순으로 바꾸지 않았다.

## 진입

```text
Phase 40E SHA = 056ca4cb072c8dbf6534afc0d1bd68eb0631212a
Gate A, committed, working tree clean
Shadow OFF
Migration = NOT_APPROVED
planner / DSL / Validator / Executor / verifier / Legacy / V2.2 미변경
qwen2.5:7b V1, observe 5/24/4000, escalation 1
SemanticRequirementContract v1 = grain + grounded binding only
Shadow = OFF
NO_PRODUCTION_CHANGE
```

Phase 40F는 선언이 맞았는지를 묻지 않는다. 그건 40D의 문제다. 여기서는 **선언된 정체성이 계획 grain에 구조적으로 남는지**만 본다.

## 정체성

```text
canonical identity = (source_id, origin_column_ref)
```

V2.2 `final_column_origins`가 이미 rename을 통해 이 쌍을 복사한다. 의미 동치 클래스를 만들지 않는다.

`src_a.customer_id`와 `src_b.client_id`는 명시적 혈통이 없으면 다른 정체성이다.

## Grain vs evidence

집계 후 E가 metric 열로 남아도 grain이 아니다.

```text
group_by = extra
aggregate entity_key as n_keys
→ CONTRADICTION
```

조상에 E가 “어딘가에” 있다고 해서 최종 행 정체성인 것은 아니다. 다중 조상 grain 열은 `MULTI_ANCESTRY_AMBIGUOUS` → `INDETERMINATE`.

## 최종 grain 관측

IntegrationPlan `final_output_requirements.grain` (`detail|entity|group|summary`)는 계약 바인딩이 아니다.

네이티브 machine-readable final grain **없음**. 연구 관측기는:

1. 마지막 `aggregate`의 `group_by`
2. 이후 `rename_columns` / `select_columns` / `filter_rows`로 이름 전파
3. aggregate가 없으면 최종 스키마 전체를 row-level grain으로 본다
4. 이후 `join` / `union_rows` / 미지원 연산 → `FINAL_GRAIN_UNKNOWN` / `UNSUPPORTED_OPERATION`

코드 위치: 연구 전용 `observe_final_grain` (`tests/benchmark_multi/phase40f_research.py`). 생산 `schema_lineage.py`는 변경하지 않았다.

## 연산 규칙 (의미 없음)

| 연산 | 규칙 |
|---|---|
| filter | grain 정체성을 바꾸지 않음. 분기가 조상을 유지하면 PRESERVED |
| rename | 조상 복사. 표시 이름 비교 금지 |
| aggregate | G에 E의 단일 조상이 있으면 PRESERVED. G에 없고 조상이 모두 알려지면 CONTRADICTION. 아니면 INDETERMINATE |
| join | 카디널리티/M:N은 Validator 소유. 체커는 E가 최종 grain 정체성인지만 |
| union | 브랜치 조상이 합쳐져 단일 정체성을 증명할 수 없으면 INDETERMINATE |
| 미지원 op | INDETERMINATE |

## 코퍼스

78 contract-plan 쌍. 구조적 오라클만 (의미 재평가 없음). PRESERVED = 43 (55%).

| 오라클 | n |
|---|---|
| PRESERVED | 43 |
| CONTRADICTION | 14 |
| INDETERMINATE | 9 |
| NOT_APPLICABLE | 5 |
| INVALID_CONTRACT | 7 |

| 오라클 | n |
|---|---|
| PRESERVED | ≥50% (표시명 변경 lookalike 포함) |
| CONTRADICTION | 집계 붕괴, 잘못된 group, select drop, M2 agent vs tid |
| INDETERMINATE | 혼합 union, post-agg join, 조인 키 조상 병합, 미지원 op |
| NOT_APPLICABLE | cannot_plan, cannot_ground |
| INVALID_CONTRACT | 없는 source/column, 동일 이름 타소스 탐색 금지, 중복 바인딩 |

실측 분포는 `benchmark_results/multi/phase40f/phase40f_summary.json`.

M2 앵커: 선언 `tickets.xlsx.agent` vs group `tid` → CONTRADICTION. lookalike group `agent` → PRESERVED.

## Phase 40D 관측 공백 재현

| 케이스 | 40D 연구 체커 | 40F 조상 체커 |
|---|---|---|
| rename 표시명 | 거짓 모순 | **PRESERVED** |
| sides join+rename | 거짓 모순 | **PRESERVED** |
| cannot_plan 빈 스키마 | 거짓 모순 | **NOT_APPLICABLE** |
| compare-tod 양측 node 조인 | 거짓 모순 | **INDETERMINATE** (am.node와 pm.node를 동치로 보지 않음) |

의미 거짓 모순은 재현되지 않았다.

## 안전 지표 (동결 코퍼스)

```text
FALSE_CONTRADICTION = 0
FALSE_PRESERVED = 0
contradiction recall = 1.0 (오라클 CONTRADICTION을 모두 맞춤)
INDETERMINATE = 보수적. 부재의 증명 실패를 모순으로 바꾸지 않음
```

미래 생산 체커 안전 막대 `FALSE_CONTRADICTION = 0`은 이 코퍼스에서 만족한다. 그러나 네이티브 grain 필드가 없어 구현 전제는 아직 닫히지 않았다.

## V2.2 충분성

**`SUFFICIENT_WITH_SMALL_OBSERVATION_EXTENSION`**

AVAILABLE: source schema/ID, final schema, `final_column_origins`, evidence signatures, alias mapping events, operation graph.

PARTIAL: 변환 분기 상태 (`step_column_origins`는 있으나 체커가 최종 grain으로 직접 쓰지는 않음), **final grain** (도출 가능, 필드 아님).

MISSING: 계약용 first-class `final_grain_identities`.

제안 확장 (미구현):

```text
observe_final_grain_identities(plan, lineage)
→ {complete, identities: [(source_id, column_ref)], gap}
```

조건: 결정적, 의미 없음, request-local, 비변이, 연산 일반. 계약 전용이 아니어야 한다. Phase 40F에서 생산 코드에 넣지 않았다.

## 관측기 / 체커 분리

```text
Relational/Lineage Observer (V2.2 + 도출 grain)
    → 결정적 사실만
Contract Checker
    → 선언 바인딩 vs 그 사실
```

체커는 데이터를 실행하지 않는다. 두 번째 Executor를 만들지 않는다. `build_schema_lineage` 메타데이터 시뮬레이션만 재사용한다.

Python은 `semantic_label`과 user prompt를 읽지 않는다. 같은 열 이름 타소스 탐색 없음. fuzzy 없음.

## 격리 / 불변

픽스처는 `request_id`, `semantic_contract_id`, `attempt_id`를 가진다. 전역 상태 없음. 동일 계약을 두 planner attempt에 재사용해 결과가 계획 때문에만 바뀌는 것을 확인했다. versioned re-resolution은 설계상 새 아티팩트 ID로만 표현. 미구현.

`required_for_answerability=true AND cannot_ground`는 사실 `REQUIRED_OBLIGATION_UNGROUNDED` (`pipeline_action=None`). cannot_plan/retry/escalation으로 매핑하지 않는다.

## 성능

결정적 체커 비용은 LLM 호출 대비 무시 가능. 실측 mean/p95/max는 `performance_results.json`.

## 구현 전제 (아직 미충족)

1. canonical identity 해석 — 충족
2. rename 조상 안전 — 충족
3. **final grain 결정적 관측 — 부분 (도출 / post-agg combine 공백)**
4. cannot_plan = NOT_APPLICABLE — 충족
5. FALSE_CONTRADICTION = 0 — 충족
6. semantic_label 미검사 — 충족
7. fuzzy 없음 — 충족
8. branch/request 격리 — 충족
9. 보수적 INDETERMINATE — 충족
10. observer/checker 분리 — 충족

3번 때문에 생산 체커 구현 Phase를 시작하지 않는다.

## 다음

**Outcome B.** Phase 40G는 좁은 결정적 grain 관측 보정만. 계약 생성 LLM과 동시에 하지 말 것. 보정 후 이 안전 코퍼스를 재실행.

계약 생성 운영 전략(추가 LLM 비용)은 관측이 닫힌 뒤에.

## 경영 보고

1. Phase 40E SHA? `056ca4cb072c8dbf6534afc0d1bd68eb0631212a`
2. 구조 코퍼스 크기? 78
3. 오라클 분포? PRESERVED 43 / CONTRADICTION 14 / INDETERMINATE 9 / N/A 5 / INVALID 7
4. V2.2가 canonical binding identity를 해석하는가? **예** (`final_column_origins`)
5. rename이 origin identity를 보존하는가? **예**
6. alias 깊이가 혈통을 잃는가? **아니오** (0–3 rename 모두 PRESERVED)
7. final grain을 결정적으로 관측하는가? **부분**. 도출 가능. 네이티브 필드 없음. post-agg join/union은 INDETERMINATE
8. 집계 붕괴를 의미 추론 없이 증명하는가? **예** (G의 조상 vs E)
9. join은 안전한가? **예**. 카디널리티는 만지지 않음. 키 조상 병합은 INDETERMINATE
10. union은 안전한가? **예**. 혼합 조상은 INDETERMINATE. 같은 조상은 PRESERVED
11. same-source 분기가 격리되는가? **예**. 분기 alias를 쓰며 원본 소스 이름으로 되돌리지 않음
12. 40D 관측 공백 재현? rename/sides → PRESERVED, cannot_plan → N/A, compare-tod 양측 키 → INDETERMINATE
13. FALSE_CONTRADICTION? **0**
14. FALSE_PRESERVED? **0**
15. contradiction recall? **1.0**
16. INDETERMINATE rate? 0.115 (9/78; INVALID_PLAN 포함 정규화)
17. 남은 관측 공백? `FINAL_GRAIN_UNKNOWN` (post-agg combine), `MULTI_ANCESTRY_AMBIGUOUS`, `UNSUPPORTED_OPERATION`
18. 결정적 관측 확장이 필요한가? **예**, 좁게
19. 무슨 확장? `final_grain_identities` 관측기 (의미 없음)
20. 의미 없는가? **예**
21. 체커 런타임? mean 0.13ms, p95 0.47ms, max 1.25ms
22. request/attempt 격리? **예**
23. Python이 라벨/프롬프트 없이 검사하는가? **예**
24. 미래 생산 전제 모두 충족? **아니오** (native/complete final grain)
25. 관측 판정? `SMALL_OBSERVATION_EXTENSION_REQUIRED`
26. 체커 판정? `FIX_OBSERVER_FIRST`
27. 생산 변경? `NO_PRODUCTION_CHANGE`
28. Gate? **A**
29. Migration? `NOT_APPROVED`
30. Shadow? **OFF**
