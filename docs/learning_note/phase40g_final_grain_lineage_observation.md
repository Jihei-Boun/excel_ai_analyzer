# Phase 40G — Final Grain Lineage Observation Correction

## 한 줄 결론

Phase 40F가 요구한 좁은 공백은 **의미 추론이 아니라 first-class final-grain 관측**이었다.

생산 모듈 `core.integrate.schema_lineage`에 결정적 원시 연산을 추가했다.

```text
observe_final_grain_identities(plan, source_schemas, lineage=None)
→ {status, identities, reason}
```

정체성은 `(source_id, origin_column_ref)`만 담는다. 라벨/프롬프트/계약을 읽지 않는다.

알 수 없는 grain은 `known([])`가 아니다. `indeterminate`다. 전역 집계만 `known` + 빈 identities + `reason=global_aggregate`다.

- **관측 판정: `OBSERVER_CORRECTED`**
- **준비 판정: `READY_FOR_CONTRACT_OPERATIONAL_STRATEGY_RESEARCH`**
- 계약 생성 / 생산 ContractPlanChecker / Validator 배선 **없음**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**

`FALSE_KNOWN_GRAIN = 0`. `KNOWN_IDENTITY_MISMATCH = 0`. `MISSED_KNOWN_GRAIN = 0`.

## 진입

```text
Phase 40F SHA = 561138432aca0e6a88e5d33eaf1063bc4f76bac5
SMALL_OBSERVATION_EXTENSION_REQUIRED
FIX_OBSERVER_FIRST
Gate A, committed, working tree clean
Shadow OFF
Migration = NOT_APPROVED
planner / DSL / Validator / Executor / verifier / escalation / Legacy 미변경
qwen2.5:7b V1, observe 5/24/4000, escalation 1
```

## API

```json
{
  "status": "known | indeterminate | not_applicable",
  "identities": [{"source_id": "src_a", "origin_column_ref": "entity_key"}],
  "reason": null
}
```

`build_schema_lineage` 반환값에는 grain을 붙이지 않았다. verifier V1 payload / notes를 바꾸지 않기 위해서다.

## 상태 구분

| 상태 | 의미 |
|---|---|
| `known` | 최종 행 grain의 canonical identities가 증명됨. 전역 집계는 identities=`[]`, reason=`global_aggregate` |
| `indeterminate` | 증명 불가. identities는 항상 `[]` |
| `not_applicable` | `cannot_plan`. 빈 known grain이 아님 |

소스 테이블에 선언된 unique identity가 없으면 `indeterminate` (`source_has_no_declared_grain`). 모든 열을 grain으로 가정하지 않는다.

## 연산

| 연산 | 동작 |
|---|---|
| filter | 이전 grain 복사. predicate 해석 없음 |
| rename | canonical origin 유지. 표시 이름은 내부 추적만 |
| select | grain 표시 열이 모두 남으면 유지. 하나라도 빠지면 indeterminate |
| aggregate | `group_by`의 단일 조상 → known. 빈 group_by → known global. 조상 모호/부재 → indeterminate |
| join | 양쪽 known이고 동일 identity이며 join key가 그 grain을 덮을 때만 known. 그 외 indeterminate. M:N은 Validator 소유 |
| union | 모든 브랜치 known이고 identity tuple이 같을 때만 전파. 같은 이름 휴리스틱 없음 |
| 미지원 | indeterminate |

분기 grain은 테이블별로 유지한다. 원본 소스 메타로 되돌리지 않는다.

## 40F 코퍼스 재실행

78쌍 동결. 오라클은 구조적 grain identity만 (계약 의미 재평가 없음).

| 지표 | 값 |
|---|---|
| FALSE_KNOWN_GRAIN | 0 |
| KNOWN_IDENTITY_MISMATCH | 0 |
| MISSED_KNOWN_GRAIN | 0 |
| KNOWN_GRAIN_COVERAGE | 0.373 (28/75 applicable) |
| INDETERMINATE rate | 0.603 |

커버리지가 낮은 주된 이유는 40F 코퍼스의 다수가 **집계 없는 소스/rename/join**이기 때문이다. unique grain이 선언되지 않았으면 indeterminate가 맞다.

40F `FINAL_GRAIN_UNKNOWN` 5건:

| 케이스 | 40G |
|---|---|
| `i-branch-agg-join` (동일 grain 두 집계 후 join) | **known** `(src_a, entity_key)` |
| `i-post-agg-join` (집계 + 미소스가 있는 쪽 join) | indeterminate `join_grain_unprovable` |
| `i-unknown-op` / `i-math-as-grain` | indeterminate |
| `i-incomplete-final` | indeterminate `missing_final` |

증명 가능한 post-agg join만 닫았다. 카디널리티를 추측하지 않았다.

40D 재현: rename 조상은 aggregate가 있으면 known으로 유지. cannot_plan → `not_applicable`. compare-tod 양측 키 조인 → indeterminate (동치 추론 없음).

## 생산 경계

변경 파일: `core/integrate/schema_lineage.py`만 (함수 추가).

Validator 판정 / Executor 출력 / planner / DSL / verifier / escalation / Legacy 미변경.

요청 국소: 전역 상태 없음. 동일 plan+schema면 동일 출력. LLM 없음.

런타임: mean 0.11ms, p95 0.40ms, max 0.55ms.

상태 비용: 스텝 워킹 중 테이블당 grain dict. lineage evidence를 변이하지 않음.

## 다음

계약 생성·체커 구현은 시작하지 않는다.

다음이 허용되는 경우에만:

**Phase 40H — Semantic Contract Generation Operational Strategy Research**

추가 LLM 호출 비용, 7B 신뢰성, 강한 모델 의존을 측정한다. 구현 결정이 아니다.

## 경영 보고

1. Phase 40F SHA? `561138432aca0e6a88e5d33eaf1063bc4f76bac5`
2. 생산 변경 파일? `core/integrate/schema_lineage.py` (`observe_final_grain_identities` 추가)
3. API? `observe_final_grain_identities(plan, source_schemas, *, lineage=None)`
4. canonical identity? `(source_id, origin_column_ref)`
5. UNKNOWN? `status=indeterminate`, identities=`[]` (known 빈 목록과 구분)
6. cannot_plan? `not_applicable` / `cannot_plan`
7. filter? 이전 grain 복사
8. rename? origin 보존
9. aggregate? group_by 단일 조상으로 grain 설정
10. composite? group_by 순서의 identity 목록
11. global aggregate? `known` + `[]` + `global_aggregate`
12. join? 동일 증명된 grain + 키가 그 grain을 덮을 때만 전파. 아니면 indeterminate
13. union? 브랜치 identity가 구조적으로 같을 때만 전파
14. same-source branch? 테이블 로컬 grain. 소스 메타로 collapse 없음
15. unsupported? indeterminate
16. 40F 코퍼스? 78
17. 이전 FINAL_GRAIN_UNKNOWN? 5
18. 안전하게 known으로 닫힌 수? 1 (`i-branch-agg-join`)
19. FALSE_KNOWN_GRAIN? **0**
20. KNOWN_IDENTITY_MISMATCH? **0**
21. MISSED_KNOWN_GRAIN? **0**
22. KNOWN_GRAIN_COVERAGE? 0.373
23. 남은 INDETERMINATE rate? 0.603 (소스 unique 미선언 + 보수적 join이 대부분)
24. 40D rename/cannot_plan? rename origin 유지, cannot_plan = N/A, 다중조상 조인 = IND
25. 런타임? mean 0.11ms
26. Validator 판정 의미? 미변경
27. Executor 출력? 미변경
28. planner/verifier/DSL/escalation? 미변경
29. Legacy? 미변경
30. 계약 운영전략 연구 준비? **READY_FOR_CONTRACT_OPERATIONAL_STRATEGY_RESEARCH** (구현 승인 아님)
31. Gate? **A**
32. Migration? `NOT_APPROVED`
33. Shadow? **OFF**
