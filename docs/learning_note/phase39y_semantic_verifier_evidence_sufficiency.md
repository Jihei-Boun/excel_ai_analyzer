# Phase 39Y — Semantic Verifier Evidence Sufficiency & Result-Awareness

## 한 줄 결론

프로덕션 verifier는 여전히 `result=None` + variant **V1**이다. 블라인드 miss 2건의 원인은 같지 않다.

- **M1 join-instead-of-union = E1 `RESULT_EVIDENCE_MISSING`.** V0 PASS 5/5, V1 FAIL 5/5. 유효 join/union lookalike는 유지.
- **M2 wrong-group-grain = E5(+E4) `VERIFIER_REASONING_FAILURE`.** 프롬프트·plan `group_by=tid`·결과(3행 tid)가 보여도 7B는 V0–V3 모두 PASS.

결과 인식은 **부분적으로 도움이 되고**, 추론 잔여는 남는다. 이번 Phase에서 프로덕션 페이로드/프롬프트는 바꾸지 않는다.

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**  
**Production = `NO_PRODUCTION_CHANGE`**  
**Next = Outcome B / MIXED_NEXT_PHASE (E)**

## 진입

Phase 39X SHA `decb584ab169aa659f0920c2a6ac514624d38a1f`. Shadow OFF. core 미변경.

## 페이로드 감사 (코드)

```text
success → semantic_escalation
→ run_semantic_verification(result=None, variant=V1, V2.2)
→ build_verifier_payload  # observed_result는 V2/V3만
→ qwen2.5:7b T=0
→ _should_semantic_escalate
```

V0에 없는 것: 실행 결과 객체, row_count, sample, CrossFileUnderstanding(V1).  
있는 것: user_prompt, plan_structure, planner_claims, V2.2 lineage.

## 코퍼스

17 attempts. Wrong 7 (M1/M2 + 39X 포착 5). Lookalike 8. cannot_plan 2.  
Fidelity: CANONICAL_EQUIVALENT_REPLAY.

## V0 베이스라인 (불변)

Silent wrong: M1, M2. 나머지 wrong 5 = CORRECT_REJECTION. Lookalike 8/8 CORRECT_PASS.

## 결과 인식 V1

| | |
|---|---|
| RESULT_EVIDENCE_CORRECTIONS | **1 (M1)** |
| RESULT_EVIDENCE_FALSE_FAILS | **0** |
| NET | **+1** |
| 안정성 M1 V0/V1 | pass×5 / fail×5 |
| lookalike V1 | pass×5 join, pass×5 union |

성분: M1에서 **V1A (columns+row_count) = FAIL**, **V1B (sample만) = PASS**.  
row_count=1이 적재 실패를 드러내고, 조인된 한 행 샘플은 오히려 `units_left/right`로 “쌓인 것처럼” 오해할 수 있다.

## Planner claims V2

공식 V1 prefix로 재실행. M1 V2 = **PASS** (V0와 동일).  
CLAIM_REMOVAL_NET = 0. **`CLAIMS_NEUTRAL`.** 앵커링이 원인 아님.

V3 (결과+claims 제거): M1 FAIL — 결과는 필요하고 claims 제거는 아님.

## M1 / M2

**M1 E1 (high).** 결과 행이 없어 join을 적재로 오해. V1이 `row_count=1` inner join을 봄.  
8B는 **V0부터 FAIL** → 계획만으로도 가능하나 7B는 결과가 필요 (`BOTH`).

**M2 E5 주원인, E4 기여 (E7).** `group_by`가 plan에 있고 결과는 tid 3행. 7B는 결과를 보고도 `tid=agent`로 읽음.  
8B는 V0 PASS, **V1 FAIL** → 증거는 충분, 현재 모델 추론이 약함 (`CURRENT_MODEL_LIMIT`).

## 권고

```text
RESULT_EVIDENCE_MATERIALLY_HELPFUL
CLAIMS_NEUTRAL
MIXED_RESIDUAL
MIXED_NEXT_PHASE
```

결과 인식 구현은 **별도 Phase**. 프롬프트 튜닝도 이 Phase에서 하지 않음.  
계약 일반화(C)는 M2 grain에 도움이 될 수 있으나 Python이 group-by를 추론하면 안 됨.

## 하지 않은 것

프로덕션 verifier, 프롬프트, threshold, 결과 배선, 의미 Python, Shadow ON.
