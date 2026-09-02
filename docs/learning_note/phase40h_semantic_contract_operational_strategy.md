# Phase 40H — Independent Semantic Contract Operational Strategy Research

## 한 줄 결론

독립 I0 v1 의미 계약은 **일부 grain 오답을 검증기가 놓친 자리에서 노출**한다. 그러나 7B는 측도를 grain으로 과다 선언해 YES를 막는 비율이 너무 높고, 32B는 그 오탐을 줄여도 지연·타임아웃·잔여 오탐이 운영 장벽을 넘지 못한다.

병렬 실행은 I0을 깨지 않고 7B 순차 오버헤드를 거의 없앨 수 있지만, **오탐이 큰 전략을 싸게 만드는 최적화일 뿐**이다. 기존 pre-plan LLM 호출에 계약을 얹는 재사용은 안전하지 않다.

- **전략 판정: `NO_SAFE_OPERATIONAL_STRATEGY`**
- **프론티어 승자: `S0 — CURRENT PRODUCTION BASELINE`**
- **다음: Outcome E — 현재 생산 아키텍처 유지. Phase 40I 구현/설계 진행하지 않음**
- **생산 변경: `NO_PRODUCTION_CHANGE`. 독립 계약 생성을 production에 추가하지 않음. I1은 latency workaround로 채택하지 않음**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**

승인 고정:

```text
Strategy verdict = NO_SAFE_OPERATIONAL_STRATEGY
Operational frontier winner = S0 — CURRENT PRODUCTION BASELINE
Gate = A
Migration = NOT_APPROVED
Shadow = OFF
Production change = NO_PRODUCTION_CHANGE
Phase 40I = 진행하지 않음
I1 plan-aware merge = 채택하지 않음
independent contract generation = production에 추가하지 않음
ACTUAL_STRONG_RECOVERY = NOT_MEASURED
Phase 40D 43 = OBSERVER_REANALYSIS_ONLY
operational corpus = fresh holdout 40
```

질문 “계약이 의미적으로 작동하는가?”는 40D–40G에서 이미 부분적으로 예였다.  
40H의 질문은 **“독립 의미 계약을 운영할 가치가 있는가?”** 이고, 이 코퍼스에서는 **아니오**다.

## 진입

```text
Phase 40G SHA = ac819329a3fec3737285f4c4b83d33cd66023ea6
OBSERVER_CORRECTED
READY_FOR_CONTRACT_OPERATIONAL_STRATEGY_RESEARCH
Gate A, committed, working tree: 연구 파일만 추가, core/ 미변경
Shadow OFF (MULTI_SHADOW_ENABLED unset)
Migration = NOT_APPROVED
생산 유일 변경분은 40G observe_final_grain_identities (이번 Phase에서 미수정)
qwen2.5:7b V1, observe 5/24/4000, MAX_SEMANTIC_ESCALATIONS=1
```

동결하고 재오픈하지 않은 것:

```text
40D: I0 > I1 (plan-aware anchoring). 최소 의미 의무는 grain.
40E: v1 = required_grain + grounded binding. outputs/functions/distinctions/ontology 없음.
40F: Python은 선언된 바인딩만 검사.
40G: 결정적 final-grain 관측기. 관측기 오탐 기대 0.
```

## 연구 질문

> 독립 의미-계약 LLM 호출의 증분 정확도가, 그 지연·운영 실패·추가 모델 비용을 정당화하는가?

증거로 답한다. 생산 구현은 하지 않았다.

## 경계

```text
LLM  → I0 의미 선언 (user_prompt + schema_inventory / CrossFileUnderstanding 상당)
Python → 40E 구조 파서 + 40G observe_final_grain_identities
수동 라벨 → 정답 오라클. 32B / 강 플래너 / 검증기는 오라클이 아님
```

I1(계획 공개)과 plan+contract 병합은 추천 전략이 아니다. 40D 앵커링 증거로 기각한다.

## 전략 (시뮬레이션만)

| ID | 내용 |
|---|---|
| S0 | 현재 생산. 계약 생성 없음 |
| S1 | 독립 7B v1 I0 → 결정적 계약-계획 검사 |
| S2 | 독립 32B v1 I0 → 동일 검사 (능력/비용 참조. 기본 추천 아님) |
| S3 | 기존 호출 재사용 — 감사 결과 `NO_SAFE_EXISTING_CALL_REUSE` |

모델 동결: `qwen2.5:7b` / `qwen3:32b`, temperature 0, timeout 300s (32B를 좋게 보이려고 올리지 않음), retries 0, Ollama `http://localhost:11434`. prompt sha `1427af5a27a88aac013ed2ec32ea6de1fc495dca80160660bfb9edbc5bf87141`.

## 코퍼스

운영 모델 품질 결론의 근거는 **fresh holdout 40건**이다. 연구 분포이며 생산 트래픽이 아니다.

| | n | YES | NO | IND | 용도 |
|---|---|---|---|---|---|
| Fresh holdout (`h40-*`, 40D ID 비재사용) | **40** | 26 (65%) | 12 (30%) | 2 (5%) | 7B/32B v1 선언·바인딩·오탐·지연·운영 결론 |
| Phase 40D 역사 43건 | 43 | 26 | 17 | 0 | **`OBSERVER_REANALYSIS_ONLY`** |

Fresh 구성: 단파일/다파일, filter, aggregate, join, union, rename, multi-stage, 올바른 cannot_plan, 스키마 부재 기권, valid lookalike, 구조적으로 유효한 의미 오답.

### Phase 40D 43건 제한

이 43건은 SemanticRequirementContract v1을 **새로 생성하지 않았고**, Phase 40G checker만 재적용했다.

사용할 수 있는 것:

* Phase 40G observer/checker regression
* historical YES contradiction 0
* observation-gap correction 유지 여부

포함하지 않는 것:

* fresh 7B/32B contract declaration generalization accuracy
* production operational reliability estimate
* new-model contract generation prevalence

40D 재분석 결과: YES CONTRADICTION **0**. 40G 관측기 보정 후 observation-gap 오탐이 이 재실행에서 재발하지 않았다.

## 선언 품질 (fresh, corpus-specific)

`DECLARATION_CORRECT` = 사용 가능 계약이 gold grain을 포함 (또는 gold가 없으면 바인딩 없음).  
과다 선언은 별도. exact = gold와 바인딩 집합이 일치.

| | 7B | 32B |
|---|---|---|
| 선언 정확 | 0.45 | 0.75 |
| 바인딩 정확 | 0.45 | 0.75 |
| exact 선언 | 0.025 | 0.475 |
| 생략률 | 0.075 | 0.000 |
| 과다 선언률 | 0.425 | 0.325 |
| 날조 바인딩 n | 6 | 0 |
| INVALID_CONTRACT | 13/40 | 0/40 |
| 운영 실패 (timeout) | 0 | 4/40 = 0.10 |
| cannot_ground (`h40-y-cannot-plan`) | 틀림 | 맞음 |

7B invalid 이유: `binding_not_in_schema` 6, `cannot_ground_without_unbound_role` 4, `cannot_ground_must_not_bind` 2, `role_missing_id` 1.  
파서를 느슨하게 해 정확도를 올리지 않았다. 이는 v1 스키마 엄격성의 운영 비용이다.

주된 7B 실패 모드: **grain + measure를 함께 required_grain에 넣음** (`pond`+`ppb`, `aisle`+`qty` 등). 체커는 측도가 최종 grain이 아니므로 CONTRADICTION → `SEMANTIC_FALSE_BLOCK`.

## 오탐 분리

| | 7B | 32B |
|---|---|---|
| SEMANTIC_FALSE_BLOCK (YES 분모) | 11/26 = **0.423** | 5/26 = **0.192** |
| OBSERVER_FALSE_BLOCK | **0** | **0** |

`h40-y-global`은 전체 합계 YES인데 `units`를 grain으로 묶어 CONTRADICTION이 난 경우로, 관측기 회귀가 아니라 과다 선언이다.

40G 관측기 안전은 이 코퍼스에서 유지된다.

## 증분 탐지

```text
INCREMENTAL_CONTRACT_DETECTION
= Manual NO
AND 선언이 gold grain을 포함
AND checker CONTRADICTION
```

S0 대비 (계약 없음):

| | n / NO | rate |
|---|---|---|
| 7B | 4 / 12 | 0.33 |
| 32B | 8 / 12 | 0.67 |

7B: `h40-n-aisle`, `h40-n-pond`, `h40-n-cohort`, `h40-n-agg-other`  
32B: 위 중 aisle/pond/cohort + `lane`, `lot`, `desk`, `multi`, `global` (agg-other는 32B timeout)

검증기가 이미 fail/uncertain으로 에스컬레이션하는 경우를 빼면 (생산 경로가 노출하는 결함):

| | 검증기 miss ∩ 계약 CONTRADICTION |
|---|---|
| 7B | 3 (`pond`, `cohort`, `agg-other`) |
| 32B | 6 (`lane`, `lot`, `pond`, `cohort`, `multi`, `global`) |

의미 있는 증분이 **있다**. 그러나 아래 오탐·비용 장벽을 넘지 못한다.

## 검증기 보완성 (Manual NO, 생산 V1 `qwen2.5:7b`)

검증기 40건 재실행. median 4.35s. 프롬프트/모델 미변경.

| | IDs |
|---|---|
| 둘 다 잡음 (7B 계약) | `h40-n-aisle` |
| 검증기만 | `h40-n-desk` (7B 계약 INVALID), `h40-n-filter` |
| 7B 계약만 | `pond`, `cohort`, `agg-other` |
| 7B 둘 다 놓침 | `lane`, `lot`, join-union, multi, global, drop-tag |
| 32B 계약만 (검증기 pass) | `lane`, `lot`, `pond`, `cohort`, `multi`, `global` |

계약-only 오류 계급: **잘못된 group_by grain 집계**. Python 라우팅 규칙으로 만들지 않는다.  
검증기-only: **잘못된 filter, grain이 아닌 열 삭제, 잘못된 union**. grain 계약의 범위 밖이다.

**검증기는 제거하지 않는다.** 잘못된 바인딩, 생략된 의무, non-grain 의미 오류, 검사 불가 의미는 계약이 못 본다.

## 다운스트림 정책 (미구현)

라이브/생산형 strong planner replan은 **호출하지 않았다.**

```text
ACTUAL_STRONG_RECOVERY = NOT_MEASURED
```

lookalike Manual YES 계획은 회복 **가능성(proxy)** 만이다.

| 정책 | 시뮬레이션 |
|---|---|
| P0 BLOCK ONLY | 7B 증분 4건 수용 차단 (실제 차단 정책 미구현) |
| P1 강 플래너 | `STRONG_RECOVERY_POTENTIAL` / `PROXY_RECOVERABLE`. 라이브 `qwen3:32b` 플래너 미호출 |
| P2 기존 의미 에스컬레이션 아날로그 | 별도 시뮬 없음. 검증기형 지연이 추가됨 |

| | proxy-recoverable | actual |
|---|---|---|
| 7B contract detections | **4/4** | `NOT_MEASURED` |
| 32B contract detections | **8/8** | `NOT_MEASURED` |

`USEFUL_CONTRACT_DETECTION`은 실제 strong recovery가 아니라 **P1 proxy**다. 탐지된 grain 오답에 올바른 계획이 코퍼스에 존재한다는 뜻이며, 오탐 비용을 상쇄하지 않고 `NO_SAFE_OPERATIONAL_STRATEGY`를 바꾸지 않는다.

## 안정성

7B n=5, 6케이스: desk/cohort/cannot-plan/n-aisle 안정. **`h40-y-aisle` 선언·체커 불안정** (과다 선언 변동).

32B n=3, 2케이스: `h40-n-desk` 안정, **`h40-y-aisle` 불안정**. 32B를 생산 후보로 보지 않으므로 전체 6케이스 n=5는 반복하지 않았다 (timeout 300s 유지).

## 지연 (corpus-specific)

| | mean | median | p90 | p95 | max | timeout |
|---|---|---|---|---|---|---|
| 7B 계약 | 5.527 | **5.556** | **6.932** | 7.541 | 11.524 | 0 |
| 32B 계약 | 111.315 | **85.034** | **268.862** | 300.079 | 300.102 | 4 |
| 결정적 체커 | ~0.001 | | | | | 0 |
| 검증기 (이번 40건) | 4.566 | 4.347 | 5.346 | | | 0 |
| 플래너 (가정, 40C/전형 7B) | | 12.0 | | | | — |

가정은 숨기지 않는다. 플래너 12s는 이 연구 경로의 자리표시이며 생산 mix가 아니다.

## 종단 지연 모델

```text
S0 sequential = T_plan + T_verify ≈ 16.618s
S1 sequential = T_7B + T_plan + T_check + T_verify ≈ 22.175s   (+5.56s)
S1 parallel   = max(T_7B, T_plan) + T_check + T_verify ≈ 16.619s  (~0 추가)
S2 sequential ≈ 101.653s
S2 parallel   ≈ 89.653s
```

7B 계약은 플래너보다 짧으므로, **병렬이면 순차 +5.6s가 거의 0이 된다**. 구현하지 않았다.

## 병렬 조건 (연구만)

계약은 IntegrationPlan을 보지 않으므로, 같은 동결 upstream 증거로 플래너와 개념적으로 병렬 가능하다.

유효 조건: 불변 upstream, 서로의 출력을 보지 않음, identifier 기반 attribution, 완료 순서가 결과를 묶지 않음, P39Q 격리.

**구현·async 생산 변경 없음.** 오탐이 큰 S1을 싸게 만드는 설계일 뿐이라 40I로 진행하지 않는다.

## 기존 호출 재사용

```text
NO_SAFE_EXISTING_CALL_REUSE
```

| 단계 | 이유 |
|---|---|
| schema_infer | 스키마 프로파일. 사용자 grain 계약을 얹으면 책임 혼선 |
| relationship_infer | 쌍 관계 라벨만. 연산/grain 의무 금지 |
| planner | user_prompt + CrossFileUnderstanding을 보지만 **IntegrationPlan을 방출**. 출력 확장은 I1 앵커링 |
| verifier | post-plan. I0 독립 선언이 될 수 없음 |

운영 절감을 위해 책임을 섞지 않는다.

## 선택적 계약 호출

```text
NO_SAFE_CONTRACT_CALL_TRIGGER
```

프롬프트 단어, 연산 가족, 기대 grouping, 열 이름, 엔티티 어휘, 벤치마크 타입으로 건너뛰지 않는다.  
grain이 없는 요청은 모델이 `grounded + required_grain=[]` 또는 기권을 낼 수 있으나, v1 파서는 빈 grounded를 거부하므로 이 또한 운영 마찰이다. Python 프롬프트 파싱으로 호출을 생략하지 않는다.

## 키 정체성 잔차 (fresh analogue, 역사적 campus 이름 미사용)

| 케이스 | 분류 |
|---|---|
| desk NO/YES | `7B_wrong_32B_correct` (7B INVALID / 32B desk 바인딩) |
| cohort NO/YES | `7B_correct_32B_correct` (7B는 score를 과다 grain으로 묶어 YES CONTRADICTION) |

40D campus 한계의 analogue가 desk에서 재현된다. 32B가 선언은 낫지만, 그 이득만으로 85s+timeout 계층을 올리지 않는다 (40C와 같은 모델-비용 패턴).

## 비용 (상대)

```text
Expected contract overhead
= P(contract-applicable) × contract_call_cost

C7_contract  = one qwen2.5:7b I0 call
C32_contract = one qwen3:32b I0 call   (~15–20× wall time vs 7B on this corpus)
Cstrong_planner = existing strong planner path
```

`P(contract-applicable)`를 벤치마크 65/30/5 분포에서 추정하지 않는다. 모든 비율은 **corpus-specific**.

적용 범위: 모든 요청에 grain 계약이 필요한지 Python이 선판정하지 않는다. 중립 결과가 필요하면 모델이 내야 한다.

## 운영 프론티어

| | 증분 NO | 의미 오탐 | 생략 | 운영실패 | 추가 호출 | e2e seq / par | 독립성 |
|---|---|---|---|---|---|---|---|
| S0 | 0 | 0 | — | 0 | 0 | 16.6 / — | — |
| S1 seq | 4 | 0.42 | 0.075 | 0 | 1 | 22.2 | I0 |
| S1 par | 4 | 0.42 | 0.075 | 0 | 1 | 16.6 (미구현) | I0 |
| S2 seq | 8 | 0.19 | 0 | 0.10 | 1 | 101.7 | I0 |

**승자: `S0 — CURRENT PRODUCTION BASELINE`.** S1은 증분이 있으나 의미 오탐 42%로 7B 전략 바를 실패. S2는 증분·선언은 낫지만 오탐 19%, timeout 10%, median 85s로 32B 바를 실패. 병렬은 독립성을 지키지만 오탐을 고치지 못하므로 production implementation 근거가 아니다.

7B 바: 의미 있는 silent-wrong 노출 **부분 충족**, 오탐 **실패**, 안정성 **부분**, 운영실패 OK, 지연은 병렬 시 OK, 다운스트림 가치는 **proxy-recoverable일 뿐 actual recovery는 NOT_MEASURED**, 일반화는 과다선언이 앵커 밖 YES로 번짐 → **실패**.

32B 바: 7B 대비 물질적 이득 일부 (desk, 더 많은 grain NO). 그러나 비용·오탐·timeout이 이득보다 큼. 선언 정확이 높다고 승인하지 않는다.

## 판정

```text
NO_SAFE_OPERATIONAL_STRATEGY
```

의미 계약 **아이디어는 작동**한다 (검증기가 놓친 grain 오답 3–6건).  
**운영 아키텍처는 정당화되지 않는다** (7B 오탐, 32B 비용, 안전한 재사용/트리거 없음).

I1 병합으로 호출을 아끼지 않는다.

생산 구현 설계 Phase(40I)로 진행하지 않는다.

```text
Migration = NOT_APPROVED
Shadow = OFF
NO_PRODUCTION_CHANGE
```

## 다음 (Outcome E)

현재 생산 아키텍처를 유지한다.

잔여 위험: 7B 검증기가 통과시키는 **잘못된 group_by grain** (이 코퍼스에서 lane/lot/pond/cohort/global/multi 등). 계약은 그 구멍을 일부 메우지만, 지금 형태로는 YES를 함께 막는다.

검증기 기본값 `KEEP_7B_DEFAULT`(40C)와 관측기 `OBSERVER_CORRECTED`(40G)는 유지.

## 회귀

`tests/test_phase40h_operational_strategy.py`, `tests/test_phase40g_final_grain_observation.py`, `tests/test_phase40f_lineage_observability.py` 통과.  
`core/integrate`에 PHASE40H / SemanticRequirementContract 문자열 없음.

## 산출물

`benchmark_results/multi/phase40h/` (gitignore). 연구 하니스 `tests/benchmark_multi/phase40h_research.py`.
