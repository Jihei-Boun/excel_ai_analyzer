# Phase 39U — Filter-Aware Join Cardinality Validation

## 한 줄 결론

Phase 39T RC-F를 좁게 수정했다. join cardinality는 **원본 source uniqueness가 아니라, 선언된 연산 이후 join 경계의 실제 상태**를 본다. D01-like `filter → rename → join`은 VALID가 되고 Executor가 성공한다. 필터 후에도 양쪽 키가 중복이면 여전히 `many_to_many_join_risk`. Python은 파티션/join/union을 추론하지 않는다. **Gate A. Migration = NOT_APPROVED. Shadow = OFF.**

## 결함

```text
_v_join
→ 입력 이름 = 중간 산출물 (d1_renamed / d2_renamed)
→ pairwise_index는 source 이름만 가지고 있어 miss
→ _simulate_output(filter_rows)가 source uniqueness를 그대로 복사
→ well_id uniqueness = 0.5/0.5
→ many_to_many_join_risk
```

선언된 `day==D1` / `day==D2` 필터 이후 `well_id`는 각 분기에서 unique다. Validator가 그 상태를 보지 않았다.

## 구현

하이브리드 Option A+B / C-lite:

- `aggregate(group_by=K)` → K는 구성상 unique (symbolic)
- 선택적 `frames`가 있으면 선언된 연산을 Executor `_op_*`로 request-local copy에 적용하고, 선언된 join key의 uniqueness를 관측
- `integration_pipeline`이 `frames=sources`를 전달
- 미리보기가 실패하면 기존 source/symbolic uniqueness로 보수적 후퇴. 안전하다고 가정하지 않음

Python이 답하는 유일한 질문:

> LLM이 선언한 필터 F를 적용한 뒤, LLM이 선언한 키 K는 unique한가?

## Before / After (D01-like)

Before: 올바른 32B 계획 → `many_to_many_join_risk`  
After: 같은 계획 → 변환 후 uniqueness 관측 → VALID → Executor 2행 성공

planner/verifier/escalation/timeout 변경 없음.

## Negative controls

- 필터 후 양쪽 중복 → 여전히 거절
- 별도 파일 many-to-many → 여전히 거절
- 무관한 필터(행 집합이 그대로) → 우회 없음
- C fake-dual `final_grain_contradiction` 유지
- 자동 filter/aggregate/join-key/plan repair 없음

## 39T에서 남은 것

- RC-C (7B capability) 미해결
- RC-J (32B timeout) 미해결
- failure escalation 정책 미변경

다음: **Planner Model Strategy / Capability Routing Research**. 패밀리 하드코드 금지.
