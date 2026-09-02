# Phase 40D — Planner Semantic Requirement Contract Generalization

## 한 줄 결론

LLM이 선언한 **작은 의미 의무(grain + optional function)** 는 Python이 사용자 의미를 해석하지 않고도 일부 플래너 오류를 구조적으로 노출할 수 있다. 그러나 **rename 관측 공백으로 오탐이 생기고**, 7B는 campus 키 정체성에서 의무를 빼먹으며, 계약 생성은 **추가 LLM 호출**이다.

- **계약 판정: `CONTRACT_PARTIALLY_PROMISING`**
- **아키텍처 판정: `RESEARCH_MINIMAL_GRAIN_ROLE_CONTRACT`**
- **생성 방식: I0(계획 비공개)이 I1(계획 공개)보다 낫다**
- **생산 변경: 없음. 계약은 연구 sidecar만.**
- **다음: Outcome D — Phase 40E Minimal Grain/Role Contract Generalization (설계만)**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**

## 진입

```text
Phase 40C SHA = a1f57c2f6a764b4c39e47e6166a0b8745ffb06e7
Gate A, committed
working tree: 연구 파일만 추가, core/ 미변경
Shadow OFF (MULTI_SHADOW_ENABLED unset)
variant V1, qwen2.5:7b
observe 5/24/4000, MAX_SEMANTIC_ESCALATIONS=1
planner / Validator / Executor / DSL / V2.2 / Legacy / verifier 미변경
40C 결론 KEEP_7B_DEFAULT 재오픈 없음
```

## 연구 질문

> LLM 플래너가 도메인 중립 의미 의무를 충분히 믿을 수 있게 써서, Python이 사용자 의미를 읽지 않고도 중요한 플래너 실수를 구조적으로 볼 수 있는가?

답은 **부분적으로 예**. 가정하지 않고 측정했다.

## 경계

```text
LLM  → 의미 해석, 의무 선언, 스키마 바인딩
Python → 선언된 토큰 vs plan/result 구조만 비교
```

Python은 프롬프트·열 이름 의미·벤치마크 가족을 읽지 않는다. 빠진 `required_grain`을 group_by로 채우지 않는다.

## 연구 계약 (생산 DSL 아님)

스키마 동결 후 holdout 평가.

```text
grounding_status, cannot_determine
required_grain[]     {role_id, semantic_role, binding}
required_outputs[]   {role_id, semantic_role, binding, function}
required_distinctions[] {left_role_id, right_role_id}
binding = {source, column} | null
```

프롬프트 sha `3d2d66538f304824b9947ebc1973aff977a00fdf8ac61ad72e4db61ec7cb465a`  
temperature 0, timeout 300s, 재시도 0. 생산 플래너 프롬프트 미변경.

체커: K1 grain 보존, K2/K4 output·function 물질화, K3 구분, K0 cannot_plan.  
입력은 선언된 계약 + 결정적 plan 관측뿐.

## 코퍼스

연구 분포이며 생산 트래픽이 아니다.

| | |
|---|---|
| n | 43 (≥36) |
| YES / NO / IND | 26 / 17 / 0 = 60.5% / 39.5% / 0% |
| DEV / HOLD | 23 / 20 |
| 구성 | 40B 앵커 10, 40C 앵커 10, M2 1, 신규 22 |

유효 lookalike를 포함했다 (building/campus grain, mean vs sum, sides, stack, abstain).

## 실험

분리 생성: 기존 IntegrationPlan + 별도 계약 호출.

| | 계획 비공개 I0 | 계획 공개 I1 |
|---|---|---|
| 7B | `qwen2.5:7b` | 동일 |
| 32B | `qwen3:32b` | 동일 |

공동 plan+contract 출력은 1차에서 생략 (`joint_output_ablation.ran=false`).

## 결과 요약

| | 선언 정확 | 바인딩 정확 | 올바른 계약 커버 | 검사가능 올바른 커버 | NO 재현 | 의미 오탐 | 관측공백 오탐 | 자기정당화 | 평균 지연 |
|---|---|---|---|---|---|---|---|---|---|
| 7B I0 | 0.74 | 0.74 | 0.74 | 0.70 | 9/17=0.53 | 1/26=0.04 | 2/26=0.08 | 1/17=0.06 | 8.0s |
| 7B I1 | 0.63 | 0.63 | 0.63 | 0.63 | 3/17=0.18 | 0.04 | 0.08 | 1/17=0.06 | 5.6s |
| 32B I0 | 0.93 | 0.93 | 0.93 | 0.93 | 14/17=0.82 | **0/26=0** | 4/26=0.15 | **0/17=0** | 115s |
| 32B I1 | 0.91 | 0.91 | 0.91 | 0.91 | 13/17=0.76 | 0 | 0.12 | **0** | 128s |

HOLD 32B I0: 선언 0.95, 재현 0.75. 스키마/체커는 holdout 전에 동결.

체커 contradiction 전체(raw YES 오탐)와 **의미 계약 실패**를 섞지 않는다.

32B 운영 실패: timeout/빈 계약 3건 (I0). `UNUSABLE_CONTRACT`로 분리. 자기정당화에 넣지 않는다.

## I0 vs I1

I0이 선언 품질과 NO 재현에서 앞선다. 특히 7B I1은 재현이 0.53→0.18로 붕괴한다 (계획의 잘못된 group_by에 앵커, 또는 과다 선언).

32B 자기정당화율은 정리 후 I0=I1=0이다. I1이 독립 가치를 더하지는 않는다.

미래 설계는 **계획을 보기 전에 의무를 선언**하는 쪽이 맞다. 40D에서는 구현하지 않는다.

## 최소 유용 계약

32B I0 차원 절제 (holdout 미재튜닝):

| | NO 재현 | YES 오탐 |
|---|---|---|
| G (grain만) | 0.41 | 0.04 |
| G+R (grain+output/function) | 0.76 | 0.15 |
| G+R+D / FULL | 0.82 | 0.15 |

**최소 유용 집합: `required_grain` + optional `function`.**  
`required_distinctions` / 관계 필드는 복잡도 대비 이득이 작다. 온톨로지는 만들지 않았다.

32B I0 체커 contradiction 4건은 모두 `PLAN_OBSERVATION_GAP`이며 **의미 선언/바인딩 실패가 아니다**.

- rename 후 최종 열 이름이 선언 source column과 다름 (compare-tod, sides, rename)
- cannot_plan인데 site grain이 빈 final_schema와 비교됨 (abstain-inlet)

32B I0 의미 오탐 = 0.  
7B I0 의미 오탐 1건 (`d40-y-sides`, 의무 생략). 나머지 7B I0 오탐 2건은 관측 공백.

## M2

요청: agent별 합계. 스키마에 `agent`와 `tid`가 모두 있다. 오답 계획은 `tid`로 group.

1. 계약이 요청 grain을 agent로 식별하는가? **예** (7B·32B I0, n=5 안정)
2. 바인딩이 `tickets.xlsx.agent`인가? **예**
3. fast plan이 같은 식을 쓰는가? **아니오** (`tid`)
4. 결정적 검사가 불일치를 보는가? **예** (K1)
5. 7B 선언 올바른가? **예**
6. 32B 선언 올바른가? **예**
7. 계획 공개가 오답을 정당화하는가? **이 케이스에서는 아니오** (I1도 노출)

M2는 계약이 검증기보다 명확히 새 관측을 제공하는 앵커다.

## 키 정체성

판정: **`STRONG_MODEL_ONLY_CONTRACT`** (campus).

| 케이스 | 7B verifier | 8B verifier | 7B 계약 I0 | 32B 계약 I0 |
|---|---|---|---|---|
| campus (crm로 집계) | MISS | MISS | 생략 → 자기정당화 | EXPOSE |
| vessel | MISS | MISS | EXPOSE | EXPOSE |
| ward | MISS | MISS | EXPOSE | EXPOSE |
| building (신규) | NA | NA | EXPOSE | EXPOSE |
| M2 tid vs agent | MISS (40A) | 불안정 | EXPOSE | EXPOSE |

요청 단어와 스키마 열이 같으면 7B도 grain을 잘 묶는다. **별칭(campus vs crm)은 7B 계약도 검증기와 같은 한계**를 보인다. 32B는 campus를 안정적으로 묶는다 (n=5).

32B 계약 기본값으로 올리지 않는다. 40C와 같은 모델-비용 문제의 재현이다.

## 증분 노출

`CONTRACT_INCREMENTAL_EXPOSURE` = 7B verifier silent-wrong ∩ 32B I0 계약 모순 = **10건**.

campus, vessel, ward, depot, mean-not-total, mean-span, union-not-compare, drop-name, agg-events, M2.

7B 계약 I0만으로도 vessel/ward/depot/M2/drop-name 등은 잡히지만 campus·mean 계열은 32B가 더 잡는다.

검증기를 완전히 대체하지는 못한다. **보완**이다.

## 기권

C형 스키마 부재 (inlet/outlet 없음):

- `d40-abstain-inlet`: 32B·7B 모두 `cannot_ground` (안정 5/5)
- `d40-n-fake-inlet`: 32B I0도 올바로 기권. 다만 “기권인데 실행 계획이 있다”는 체커 규칙이 없어 오답 계획을 모순으로 표시하지 않음 (holdout 이후 규칙 추가 금지)

날조 바인딩으로 inlet 열을 만든 경우는 관측되지 않았다.

## 자기정당화

진짜 `SELF_JUSTIFYING_CONTRACT` = Manual NO + 사용 가능한 의미 계약이 틀림 + 그 계약이 틀린 계획과 내부 일치.

- 7B I0: **1/17** (`b40-n-campus` grain 생략)
- 32B I0: **0/17**
- timeout / 빈 계약 / 파서 실패는 `UNUSABLE_CONTRACT` (32B I0 3건). 자기정당화에 넣지 않음

자기선언 계약(B)을 지금 기각할 정도는 아니나, 7B I1 재현 붕괴는 **계획과 같이 쓰게 하면 7B가 계획을 따라간다**는 경고다.

## 비용

계약 생성은 두 번째 LLM 호출이다. 체커는 밀리초.

- 7B: ~8s
- 32B: ~115s (약 14×)
- 페이로드: 프롬프트 ~400 토큰 + schema inventory. 역할 1–3개.

공짜가 아니다. 40C의 8B verifier 비용 문제와 별도로, 32B 계약 기본은 추천하지 않는다.

## 복잡도

파서: 필드 존재·enum·리스트 형태만. 의미 보정 없음.  
체커: 멤버십/동등 비교 4종. 도메인 규칙 이름 없음.  
유출 감사: 필드·체커 모두 벤치마크 도메인이 바뀌어도 성립.

## 전략 비교 (구현 없음)

| | |
|---|---|
| A 현재 | planner → validator → executor → 7B verifier |
| B 자기선언 | 비용은 낮으나 7B I1이 계획을 따라감 |
| C 독립 계약 | I0이 더 낫다. 추가 LLM 호출 |
| D 계약 없음 | 39Z 유지 |

추천은 **C의 생성 순서 + 최소 grain/role 스키마**를 40E에서 설계만 하는 것. 생산 배선 없음.

## 생산

```text
Migration = NOT_APPROVED
Shadow = OFF
NO_PRODUCTION_CHANGE
KEEP_7B_DEFAULT
IntegrationPlan DSL 동결
생산 planner 프롬프트 동결
Validator / Executor / verifier 동결
```

## 다음

**Outcome D.** Phase 40E — Minimal Grain/Role Contract Generalization.

할 일(설계만):

- 생산 필드 후보를 `required_grain` + optional function으로 한정
- 독립(I0) 선언을 기본 순서로 둘지 비용과 함께 평가
- rename/alias를 Python이 의미를 짐작하지 않고 관측하는 방법 (V2.2 lineage의 선언된 식)
- cannot_determine vs 실행 계획
- 32B 라우팅 금지. 필요하면 별도 비용 연구

Outcome C(32B만 된다)는 campus에만 해당한다. 전체 계약을 32B로 올리지 않는다.

## 경영 보고 (§86)

1. Phase 40C SHA? `a1f57c2f6a764b4c39e47e6166a0b8745ffb06e7`
2. 코퍼스? 43, YES 26, NO 17, IND 0
3. 스키마 후보? 위 5필드 sidecar. 온톨로지/연산 enum 기각
4. 최소 유용 계약? `required_grain` + optional function
5. 7B 선언 정확? I0 0.74 / I1 0.63
6. 32B 선언 정확? I0 0.93 / I1 0.91
7. 7B 바인딩 정확? I0 0.74 / I1 0.63
8. 32B 바인딩 정확? I0 0.93 / I1 0.91
9. 올바른 계약 커버? 7B I0 0.74, 32B I0 0.93
10. 검사가능 올바른 커버? 7B I0 0.70, 32B I0 0.93
11. Manual NO 재현? 7B I0 0.53, 32B I0 0.82
12. Manual YES 오탐? raw 체커 contradiction 7B I0 0.12 / 32B I0 0.15. **의미 오탐** 7B I0 0.04 / 32B I0 **0**. **관측 공백** 7B I0 0.08 / 32B I0 0.15
13. 7B verifier miss 증분 노출? **10**
14. 자기정당화? 7B I0 0.06 (campus). 32B I0 **0**. timeout/빈 계약은 UNUSABLE로 분리
15. I0이 I1보다 나은가? **예**
16. 키 정체성 잔여를 푸는가? **부분.** vessel/ward/M2는 7B도. campus는 32B만. `STRONG_MODEL_ONLY_CONTRACT`
17. 32B가 필요한가? campus와 mean 계열에서만 크게. 기본 라우팅 금지
18. C형 기권? **예** (날조 없음). cannot_plan 오탐 1건은 관측 공백
19. 가치 있는 필드? grain, function
20. 가치 없는 필드? distinctions, relations
21. Python이 사용자 의미 없이 검사 가능한가? **선언된 바인딩에 한해 예**
22. 검증기 이상의 가치? **예, 증분 10건.** 대체는 아님
23. 생산 변경? **없음**
24. 1차 판정? `CONTRACT_PARTIALLY_PROMISING`
25. 아키텍처? `RESEARCH_MINIMAL_GRAIN_ROLE_CONTRACT`
26. Gate? **A**
27. Migration? `NOT_APPROVED`
28. Shadow? **OFF**
