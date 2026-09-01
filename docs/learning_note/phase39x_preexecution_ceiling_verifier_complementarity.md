# Phase 39X — Pre-Execution Signal Ceiling & Verifier Complementarity

## 한 줄 결론

Phase 39W의 7건 VALID-오답은 **실행 전 결정적 Python이 사용자 의미를 해석하지 않고는 잡을 수 없다.** 동결 semantic verifier는 같은 구역에서 **5/7을 거부**하고 유효 lookalike **8/8을 통과**시킨다. 조기 라우팅을 넓힐 자리가 아니라, 이미 있는 층 분담이 맞다.

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**  
**Pre-exec: `PARTIAL_PREEXEC_OBSERVABILITY`**  
**Verifier: `STRONG_COMPLEMENT`**  
**Production: `NO_PRODUCTION_SEMANTIC_CHANGE`**  
**Next: Outcome A** — 조기 라우팅은 좁게 유지. 다음 관심은 강한 플래너 운영(RC-J)과 남은 verifier miss 2건.

이 비율은 **Phase 39X 코퍼스 천장 추정치**이다. 프로젝트 보편 비율이 아니다.

## 진입

- Phase 39W SHA: `d25c87a36a4409035c8ca78e68938ad81a894373`
- working tree 진입 시 clean, Shadow OFF
- planner / verifier / escalation / timeout / DSL / V2.2 / 프로덕션 라우팅 변경 없음
- `PHASE39V_RULE_V1` 미수정

## 가설과 분류

가설: 실행 전 결정적 증거는 구조/증거 모순만 잡고, 구조 VALID 의미 오답은 결과·요청을 의미적으로 비교해야 한다.

25 attempts (39W 재사용, 최소 22 충족):

| 역할 | n |
|---|---|
| CLASS B 블라인드 (VALID+exec+FAST NO) | 7 |
| 유효 lookalike | 8 |
| CLASS A 실행 전 관측 가능 오답 | 6 |
| genuine m2m | 1 |
| 올바른 cannot_plan | 3 |

7건 블라인드 모두:

```text
python_without_meaning = NO
counterfactual = SEMANTIC_INFERENCE_REQUIRED
contract = SEMANTIC_REQUIREMENT_NOT_STRUCTURALLY_DECLARED
planner_under_declaration = true
```

조인 vs union, 어느 필터, 어느 group-by, 어느 사이드를 유지할지는 **명시 계약이 없으면 Python이 판단하면 안 된다.**

CLASS A 7건(역할 붕괴, grain 모순, 없는 열/키, fake-dual, 스키마 불일치, m2m)은 기존 validator 증거로 관측 가능하다. m2m은 `SAFELY_BLOCKED_WITHOUT_STRONG_RECOVERY`.

## 천장 (이 코퍼스만)

```text
pre_execution_observable_wrong / all wrong = 7/14 = 0.50
architecture_safe / recoverable = 5/12 ≈ 0.42
```

**`PARTIAL_PREEXEC_OBSERVABILITY`**

39W 동결 규칙이 7건 VALID-오답을 전부 놓친 것은 규칙이 덜 tuned 되어서가 아니라 **관측 가능 집합 밖**이기 때문이다.

## 프로덕션 verifier 페이로드 (코드 기준)

`semantic_escalation`은 여전히:

- `variant = V1`
- `result=None`
- `materialization_mode = final_schema_expr_partition` (V2.2)
- model `qwen2.5:7b`, temperature 0, timeout 300s

`build_verifier_payload`는 `observed_result`를 **variant V2/V3에서만** 붙인다. 따라서 39L 이후에도 **이중 맹점**이 유지된다: 호출이 `result=None`이고, V1은 결과가 있어도 생략한다.

T0: plan + validator codes + V2.2 lineage.  
T1: row_count / columns / bounded sample.  
T2 프로덕션: user_prompt + plan_structure + planner_claims + V2.2. **결과 행은 없음.**

이 Phase에서 고치지 않는다.

## Verifier 보완 (동결, 라이브 CANONICAL_EQUIVALENT_REPLAY)

블라인드 7, lookalike 8. 프롬프트/모델/threshold 변경 없음.

| | |
|---|---|
| VERIFIER_RECALL_ON_PREEXECUTION_BLIND_REGION | **0.7143 (5/7)** |
| FALSE_FAIL on lookalikes | **0/8** |
| SILENT_WRONG | `w2-join-instead-of-union`, `w2-wrong-group-grain` |

잡은 5건: 붕괴, 한쪽 런, liters 누락, union-when-compare, 잘못된 사이트 필터. 설명은 대체로 요청 vs plan_structure/final_schema.

놓친 2건:

- join 적재 요청을 join이 만족했다고 오해 (`units_left/right`를 지어냄 — 결과 없음).
- “agent별” 요청을 `group_by tid`와 혼동 (추론 오류).

## Ablation (하네스 전용)

**V1 결과 관찰:** 블라인드 7 중 1건만 변경. `w2-join-instead-of-union` V0 `pass` → V1 `fail`. lookalike 오탐 없음.  
**V2 planner_claims 제거:** 블라인드 7 전부 V0와 동일.

결과 맹점이 join-vs-union miss **일부**를 설명한다. grouping miss는 결과로도 안 바뀐다. **프롬프트를 먼저 고치지 말 것.** 증거 결여 vs 추론 오류가 혼재한다.

## 안정성 n=5

| 시도 | 5회 |
|---|---|
| w2-collapse-no-roles | fail ×5 |
| w2-filter-wrong-site | fail ×5 |
| w1-union-total | pass ×5 |
| w1-filter-then-agg | pass ×5 |

이 부분집합은 안정.

## 회복 사슬

Verifier가 거부한 5건은 39W 오라클로 32B 회복. `VERIFIER_TO_STRONG_RECOVERY_RATE = 1.0` (거부 조건 하). 라이브 32B 없음. RC-J 분리. 운영 실패 0.

Verifier가 통과시킨 2건은 현재 경로에서 32B로 안 넘어간다.

## 책임 구역

| Region | 처리 |
|---|---|
| R1 실행 전 모순 | Validator / failure 경로 |
| R2 구조 VALID 의미 오답 | Semantic verifier |
| R3 verifier 거부 + 회복 | 현재 semantic escalation |
| R4 verifier miss | 2건. 결과 맹점 1 + 추론 1 |
| R5 32B 운영 | 이 코퍼스 0 |

Python에 `if 비교요청 and 결과한쪽` 같은 의미 미러를 넣지 않는다. 계약이 없으면 의미 층이다.

## 아키텍처

증거는 다음을 지지한다.

```text
Early routing / validator: 관측 가능한 구조·증거 모순
Semantic verifier: 구조적으로 타당한 의미 실패
Strong planner: verifier가 확인한 fast 부족의 재계획
```

조기 라우팅 구현 Phase는 열지 않는다.

## 다음

**Outcome A.** 조기 라우팅은 좁게.  
남은 R4 2건과 `result=None`은 후속 verifier 증거 연구에서 다룰 수 있으나, 이번 Phase는 페이로드를 바꾸지 않는다.  
32B 지연/타임아웃(RC-J)은 별도.

## 하지 않은 것

프로덕션 라우팅, verifier 프롬프트, threshold, planner, validator, timeout, DSL, V2.2, Shadow ON, 새 라우팅 피처, 의미 Python 규칙.
