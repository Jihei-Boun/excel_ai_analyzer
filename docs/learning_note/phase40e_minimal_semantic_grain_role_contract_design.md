# Phase 40E — Minimal Semantic Grain/Role Contract Architecture Design

## 한 줄 결론

Phase 40D가 부분적으로 유망했던 지점은 **Python이 사용자 의미를 읽는 것**이 아니라, LLM이 선언한 grain 정체성이 계획에 남는지다.

v1은 그것만 남긴다.

```text
SemanticRequirementContract v1
= grounding_status
+ required_grain[role_id, binding, required_for_answerability]
```

`semantic_label`은 진단용이다. Python은 읽지 않는다.  
`partially_grounded`, output/function, distinction, relationship은 v1에서 뺀다.

**설계만. 생산 배선 없음.**

- **옵션: B (grain + grounded binding)**
- **독립성: `SEMANTIC_REQUIREMENT_INDEPENDENCE` / D1**
- **체커: 구조 Validator 이후, 3값+α (`SATISFIED|CONTRADICTION|INDETERMINATE|NOT_APPLICABLE|INVALID_CONTRACT|OPERATIONAL_FAILURE`)**
- **rename: V2.2 `final_column_origins`로 조상 추적. 표시 이름 비교 금지**
- **다음: Outcome A 다음 B — 관측 증명 후 운영 비용. 구현 시작 금지**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF. `NO_PRODUCTION_CHANGE`.**

## 진입

```text
Phase 40D SHA = b1382d0fad1656aa0e5328885cede2a73b060620
Gate A, committed, working tree clean
Shadow OFF
Migration = NOT_APPROVED
planner / DSL / Validator / Executor / verifier 미변경
qwen2.5:7b V1, observe 5/24/4000, escalation 1
```

### 40D 지표 정리 (동결)

| | 의미 오탐 | 관측 공백 오탐 | 자기정당화 |
|---|---|---|---|
| 7B I0 | 0.04 | 0.08 | 0.06 (campus) |
| 32B I0 | **0** | 0.15 | **0** (timeout은 UNUSABLE) |

관측 공백: rename 표시 이름, cannot_plan vs 빈 스키마. 계약 의미 실패가 아니다.

## 계약이 의미하는 것

`required_grain` = 최종 결과에서 **구분되어 남아야 하는 의미 정체성**.

다음이 아니다: 현재 group_by, 입력의 모든 키, Python이 고른 grain, Executor grain.

LLM이 역할을 고르고, 스키마 식에 묶는다. Python은 그 묶음이 존재하는지만, 그리고 혈통이 남았는지만 본다.

## SemanticRequirementContract v1

```json
{
  "contract_version": "1",
  "grounding_status": "grounded",
  "required_grain": [
    {
      "role_id": "g1",
      "semantic_label": "requested grouping entity",
      "binding": {"source_id": "src_a", "column_ref": "entity_key"},
      "grounding_status": "grounded",
      "required_for_answerability": true
    }
  ]
}
```

제거: `partially_grounded`, `cannot_determine`, `required_outputs`, `function`, `required_distinctions`, `required_relations`.

`partially_grounded`는 체커 행동이 모호해서 삭제했다. 역할 단위 `grounded|cannot_ground`면 충분하다.

## 바인딩

```text
binding = {source_id, column_ref} | null
```

`source_id`는 CrossFileUnderstanding의 기존 식별자다. 새 식 언어를 만들지 않는다.

- 존재 여부: 스키마 재고의 정확한 튜플
- 보존 여부: V2.2 `final_column_origins`의 `(source, column)` 조상
- 틀린 바인딩: `SEMANTIC_BINDING_ERROR`. Python이 고치지 않는다. Verifier 영역이다.

`semantic_label`은 파서가 체커 입력에서 버린다.

## cannot_ground

스키마에 구분 증거가 없으면 `binding: null`, `grounding_status: cannot_ground`. 날조 금지.

**cannot_ground ≠ cannot_plan.**

`required_for_answerability`는 LLM이 쓴 의미 선언이다. Python은 다음 사실만 관측한다.

```text
required_for_answerability = true
AND grounding_status = cannot_ground
→ FACT: REQUIRED_OBLIGATION_UNGROUNDED
```

이 사실만으로 Python은 `cannot_plan`, semantic retry, strong-model escalation, 특정 planner outcome을 결정하지 않는다.  
`LLM이 필요성을 선언`하는 것과 `파이프라인이 충족되지 않은 필요성에 무엇을 할지`는 Phase 40E에서 섞지 않는다. 후자는 별도 semantic/pipeline policy이며 `OUT_OF_SCOPE_FOR_IMPLEMENTATION`이다.

## rename / 혈통

V2.2는 이미 rename에서 `source_origins`를 새 표시 이름으로 복사한다. 40D 오탐은 연구 체커가 `column_ref == final_schema` 문자열을 비교한 결과다.

설계 불변:

```text
rename은 표시 이름만 바꾼다. 조상은 그대로다.
조상을 증명할 수 없으면 INDETERMINATE. 거부가 아니다.
```

집계: 선언된 조상이 group_by 정체성(표시 이름으로 매핑된 뒤)에 없으면 CONTRADICTION일 수 있다. “의미상 같은 열”은 쓰지 않는다.

필터는 혈통이 남으면 grain을 깨지 않는다. join/union도 구조 전파만 본다.

## cannot_plan 경로

`planned` / `cannot_plan` / `planner_failure`를 섞지 않는다.  
cannot_plan에는 물질화된 final schema가 없다. 빈 테이블처럼 grain 검사를 돌리지 않는다 → `NOT_APPLICABLE`.

timeout / 파서 붕괴는 `OPERATIONAL_FAILURE` 또는 `INVALID_CONTRACT`. cannot_ground나 cannot_plan으로 바꾸지 않는다.

## 결과 대수

`SATISFIED | CONTRADICTION | INDETERMINATE | NOT_APPLICABLE | INVALID_CONTRACT | OPERATIONAL_FAILURE`

참/거짓 하나로는 관측 공백을 표현할 수 없다.

세 층:

1. 구조적 유효성 — Python
2. 의미 선언이 맞는지 — LLM/Verifier/수동
3. 계획이 선언을 지켰는지 — Python (혈통)

## 독립성

```text
SEMANTIC_REQUIREMENT_INDEPENDENCE
```

선언 단계는 IntegrationPlan, Validator, Executor, verifier를 보지 않는다. 40D I1은 7B 재현을 0.53→0.18로 떨어뜨렸다.

추가 LLM 호출 비용은 숨기지 않는다. 이 Phase에서 호출을 승인하지 않는다. plan+contract 병합도 아직 안 한다.

### D1 vs D2

| | |
|---|---|
| D1 플래너에 계약 비공개 | 독립 최대. 불일치가 관측된다 |
| D2 플래너에 계약 제공 | 목표 공유. 틀린 계약이 계획에 전파 |

v1 추천은 **D1**. 권위: 계약=요구 선언, 계획=실행 제안. Python이 의미 승자를 고르지 않는다. 불일치는 CONTRADICTION. 재계획 정책은 `OUT_OF_SCOPE_FOR_IMPLEMENTATION`.

## 파이프라인 (설계, 미구현)

```text
User + CrossFileUnderstanding
  → SemanticRequirementContract   [LLM, I0]
  → IntegrationPlan               [LLM]
  → Structural Validator
  → ContractPlanChecker           [Python]
  → Executor → Result Validator → Semantic Verifier
```

체커는 **Validator 뒤(Option B)**. 혈통은 구조적으로 유효한 planned 그래프가 필요하다.

허용 검사: `DECLARED_BINDING_EXISTS`, `DECLARED_GRAIN_PRESERVED`.  
금지: 프롬프트 해석, 단어→열, fuzzy, join/union 선택, 메트릭 동치, 바인딩 수리, group_by로 grain 채우기.

## 범위 / 불변성

계약은 **요청(request)** 수준의 requirement artifact이며 planner attempt와 분리된다.

```text
같은 semantic-evidence snapshot 안의 planner attempts/retries 동안 contract는 immutable
planner 실패 때문에 contract를 다시 쓰지 않음
completion-order가 아니라 identifier로 request에 귀속
```

새 `semantic_contract_version`은 향후 CrossFileUnderstanding 또는 semantic evidence가 **명시적으로 re-resolve**되는 아키텍처가 도입될 때만 만들 수 있다. 새 version은 기존 객체를 mutation하지 않고, 이전 계약을 가리키는 lineage를 가진 **새 immutable artifact**다.

Phase 40E에서 re-resolution은 구현하지 않는다.

## 잔여

체커가 못 하는 것: 틀린 바인딩, 생략된 의무, 검사 불가 의미, campus형 키 오인.  
**Verifier는 그대로 필요하다.** 계약은 검증기를 대체하지 않는다.

32B 계약 기본값, 생산 라우팅, 재시도 정책은 이 Phase 밖이다.

## 구현 전제

1. origin 기반 체커가 40D 동결 코퍼스에서 rename/cannot_plan 관측 공백을 닫는지 증명
2. 공백 제거 후 Manual YES 의미 차단율
3. 호출 통합 시 I0 독립성 유지 증거
4. 모델 전략은 별도 비용 연구
5. 그 전 DSL/planner/Validator 배선 금지

**다음: Outcome A 다음 B.** 구현 Phase 자동 시작 없음.

## 경영 보고

1. Phase 40D SHA? `b1382d0fad1656aa0e5328885cede2a73b060620`
2. 수정 귀속? 의미 오탐 vs 관측 공백 분리. SJ에서 timeout/빈 계약 제외. 32B I0 SJ=0, 의미 오탐=0
3. v1 필드? `contract_version`, `grounding_status`, `required_grain{role_id, semantic_label, binding, grounding_status, required_for_answerability}`
4. 제거? partially_grounded, cannot_determine, required_outputs, function, distinctions, relations
5. required_grain? 최종에서 구분되어야 하는 의미 정체성. group_by가 아님
6. 바인딩? LLM이 `{source_id, column_ref}` 선언. Python은 존재+조상만
7. Python이 semantic_label을 보는가? **아니오**
8. 바인딩 불가? `cannot_ground`, binding null. 날조 금지
9. cannot_ground가 자동 cannot_plan인가? **아니오**. Python 최대 결론은 `REQUIRED_OBLIGATION_UNGROUNDED` 사실. 파이프라인 정책은 별도 설계.
10. rename? V2.2 origins. 표시 이름 비교 금지
11. 필요 혈통? `final_column_origins` + evidence signatures (기존 V2.2)
12. 증거 부족? `INDETERMINATE` (거부 아님)
13. cannot_plan? `NOT_APPLICABLE`. 빈 스키마 ≠ 모순
14. backend/parser? `OPERATIONAL_FAILURE` / `INVALID_CONTRACT`
15. I0를 유지하는 이유? 40D I1 앵커. 선언이 계획을 보면 안 됨
16. 플래너가 계약을 보는가? **D1 (숨김)** 을 v1 기본으로
17. 범위? 같은 evidence snapshot 안 request-level immutable. planner 실패로 재작성 금지. re-resolve 시에만 새 version (미구현).
18. 허용 검사? binding 존재, grain 보존
19. 금지 추론? 프롬프트/라벨/fuzzy/연산 선택/자동완성
20. 왜 autocomplete가 아닌가? 빠진 필드를 채우지 않음
21. 체커가 못 하는 잔여? 오바인딩, 생략, 키 정체성 오인
22. Verifier 필요? **예**
23. 추가 LLM 비용? 독립 선언이면 추가 호출. 미승인
24. 먼저 고칠 관측? 체커가 origins를 쓰게 할 것. V2.2 자체는 rename 조상을 이미 가짐
25. 구현 전제? 위 1–5
26. 생산 변경? `NO_PRODUCTION_CHANGE`
27. Gate? **A**
28. Migration? `NOT_APPROVED`
29. Shadow? **OFF**
