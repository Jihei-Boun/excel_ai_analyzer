# Phase 40 Closure — Semantic Reliability Research (39X–40H)

## 한 줄 이야기

실행 전 Python은 의미 오답의 일부를 볼 수 없다. 유계 결과 증거는 한 종류의 verifier miss를 고쳤다. 남은 키 정체성 miss는 프롬프트로 안 고쳐졌고, 더 큰 모델은 재현율을 올리지만 운영 막대를 넘지 못했다. 의미 계약은 **설계·체커는 가능**했으나 **지금 모델로는 운영 불가**였다. 그래서 S0가 이겼다.

```text
PHASE_40_RESEARCH = CLOSED
KEEP_CURRENT_PRODUCTION_ARCHITECTURE
```

이 노트는 ADR을 복사하지 않는다. 가설이 어떻게 바뀌었는지를 시간순으로 적는다.  
ADR: `docs/architecture/adr_phase40_semantic_reliability_strategy.md`

**Gate A. Migration = NOT_APPROVED. Shadow = OFF. 생산 코드 변경 없음 (이 폐쇄 Phase).**

---

## 진입

```text
Phase 40H SHA = 5fa06b5a6f2ca202f0f5823c2127a7598918e1e7
Strategy verdict = NO_SAFE_OPERATIONAL_STRATEGY
Operational frontier winner = S0
Gate A, committed, working tree clean
Shadow OFF
Migration = NOT_APPROVED
새 live 실험 없음. 커밋된 학습 노트만 합성.
```

---

## 가설의 이동

처음 가설은 “실행 전 결정적 신호를 넓히면 의미 오답을 더 잡을 수 있다”였다. 39X가 그 천장을 보여 주었다.

다음 가설은 “verifier에 결과만 주면 된다”였다. 39Y/39Z는 한 miss만 그 종류였고, 키 정체성은 증거가 있는데도 7B가 대조하지 못했다.

그다음 가설은 “프롬프트를 바꾸면 7B가 그 대조를 한다”였다. 40A가 이를 부정했다.

그다음 가설은 “8B로 올리면 된다 / 선택적으로만 8B를 쓴다”였다. 40B는 모델이 이득의 원천임을 보였고, 40C는 그 이득이 지연·불안정·트리거 부재로 운영되지 않음을 보였다.

마지막 가설은 “LLM이 grain을 선언하고 Python이 계획과만 대조하면, 추가 호출 비용 대비 침묵 오답을 줄일 수 있다”였다. 40D–40G는 설계와 관측기를 열었고, 40H가 운영 가치를 측정해 닫았다.

S0가 이긴 이유: 대안마다 **한 축의 재현율**은 올랐지만, 오탐·지연·안정·아키텍처 안전 중 하나가 프론티어를 밀어냈다.

---

## Phase 39X — 실행 전 신호 천장

가설: 더 많은 결정적 pre-exec 관측이면 VALID 의미 오답도 잡을 수 있다.

증거: 39W VALID-오답 7건은 `python_without_meaning = NO`. 같은 구역에서 동결 verifier는 5/7 거부, lookalike 8/8 통과.

방향 전환: 조기 라우팅을 넓히지 않는다. 층 분담을 유지한다.

```text
PREEXEC_DETERMINISTIC_SIGNALING = PARTIAL
```

---

## Phase 39Y / 39Z — 결과 증거 vs 추론 잔여

가설: verifier miss는 결과 객체가 없어서다.

증거: M1 join-instead-of-union은 E1. V1에 유계 결과를 붙이면 FAIL 5/5. lookalike FF 0/8.  
M2 wrong-group-grain은 결과(3행 tid)와 plan `group_by`가 보여도 7B PASS. E5 추론 실패.

방향 전환: 결과 인식은 생산 개선으로 남긴다. 추론 잔여는 40A로 분리한다. V2/V3로 variant를 바꾸지 않는다.

```text
BOUNDED_RESULT_AWARE_VERIFIER = RETAIN
```

의도적 생산 변경: `core/integrate/result_observation.py` + semantic_escalation/verifier가 성공 결과에 관측을 붙임. SHA `9688e504c2784d9441e30d8f29173fa1f9422223`.

---

## Phase 40A — 프롬프트만으로 되는가

가설: 일반 프롬프트 변형이 7B의 키 정체성 대조를 만든다.

증거: P0–P5 모두 M2 PASS. 8B+P0도 M2 PASS. 이 코퍼스에서만 8B+P1/P2가 wrong 11/11.

방향 전환: 프롬프트 튜닝을 기본 해법으로 두지 않는다. 일반화가 필요한 상호작용은 40B로.

```text
VERIFIER_PROMPT_TUNING = NOT_SUFFICIENT
```

생산 프롬프트/모델 미변경.

---

## Phase 40B — 프롬프트인가 모델인가

가설: 40A의 8B+P1 상호작용이 새 홀드아웃에도 일반화된다.

증거: 새 홀드아웃에서 그 상호작용은 일반화되지 않는다. S1(7B+P1)=S0. 이득의 거의 전부는 S2(8B+P0). 키 정체성 혼란 잔존.

방향 전환: 모델 전환 여부를 비용·안정으로 묻는다 (40C). 구현 설계 없음.

```text
MODEL_CAPABILITY_MATTERS
BUT
MODEL_SWITCH_NOT_YET_JUSTIFIED
```

---

## Phase 40C — 8B를 운영할 수 있는가

가설: 8B 재현율 이득이 지연·실패·라우팅 비용을 이긴다. 또는 7B PASS에만 8B를 붙인다.

증거: 합산 recall 0.54→0.81, FF 0/48. 중앙 지연 4.6s→20.9s. M2 n=5 다수결이 오답 PASS. 안전 선택 트리거 없음. V2는 7B PASS의 81%에 8B를 붙인다.

방향 전환: 검증기 기본은 7B. 의미 모델 라우팅 없음. 계약 연구(40D)는 검증기 교체가 아니라 **선언된 grain의 결정적 대조**라는 다른 축이다.

```text
PRODUCTION_VERIFIER_MODEL = KEEP_7B_CURRENT
NO_SAFE_SELECTIVE_8B_TRIGGER
```

---

## Phase 40D — 계약 아이디어

가설: LLM이 도메인 중립 의미 의무를 쓰면, Python은 사용자 의미를 읽지 않고 플래너 실수를 구조적으로 볼 수 있다.

증거: 부분적으로 예. I0이 I1보다 낫다 (7B I1 NO 재현 0.53→0.18 붕괴). 최소 유용 집합은 grain(+당시 function). 32B 선언은 낫지만 ~115s. 관측 공백 오탐과 의미 오탐을 섞으면 안 된다.

방향 전환: 스키마를 줄인다. 생산 배선 없음.

```text
SEMANTIC_CONTRACT_IDEA = ARCHITECTURALLY_PARTIALLY_PROMISING
```

---

## Phase 40E — 최소 설계

가설: grain+binding만 남기면 검사 가능하고 오탐 표면이 작다.

동결: `required_grain` + grounded binding. function/outputs/distinctions/relations/`partially_grounded` 제거. Python은 `semantic_label`을 읽지 않는다. `cannot_ground ≠ cannot_plan`.

설계만. 구현 시작 금지.

---

## Phase 40F — Python이 선언만 검사할 수 있는가

가설: 이미 선언된 바인딩 E가 최종 grain에 남는지, 의미를 해석하지 않고 증명할 수 있다.

증거: 78쌍 FALSE_CONTRADICTION=0, FALSE_PRESERVED=0. 그러나 네이티브 final-grain 필드가 없고 파생 grain은 join/union 후 불완전. 관측 공백이 오탐처럼 보였다.

방향 전환: 계약 LLM과 동시에 구현하지 말고, 관측기를 먼저 고친다.

```text
CONTRACT_CHECKER_ARCHITECTURE = DETERMINISTICALLY_FEASIBLE
FIX_OBSERVER_FIRST
```

---

## Phase 40G — 관측기

가설: 의미 없는 first-class grain 관측이면 공백 오탐이 사라진다.

증거: `observe_final_grain_identities`. FALSE_KNOWN_GRAIN=0. 40F `FINAL_GRAIN_UNKNOWN` 5건 중 1건만 안전하게 KNOWN. 나머지는 소스 undeclared grain 또는 conservative join → IND. mean observer ~0.11ms.

의도적 생산 변경: `core/integrate/schema_lineage.py`에 함수 추가. `build_schema_lineage` 반환/verifier payload에는 붙이지 않음. SHA `ac819329a3fec3737285f4c4b83d33cd66023ea6`.

계약이 기각되어도 이 함수는 남긴다. 파이프라인이 호출하지 않아도 의미-무관 인프라다.

```text
FINAL_GRAIN_OBSERVER = RETAIN_AS_GENERIC_INFRASTRUCTURE
```

---

## Phase 40H — 운영할 가치가 있는가

가설: 독립 I0 v1 호출 + 40G 체커가 지연·실패·오탐을 감수할 만큼 silent-wrong을 줄인다.

증거 (fresh 40, 40D 43은 `OBSERVER_REANALYSIS_ONLY`):

7B는 선언 0.45, INVALID 13/40, 의미 오탐 0.423. 증분은 있다(4/12, 검증기 miss 대비 3). 지연은 작다. 병렬은 그 지연을 거의 없앤다. **오탐을 없애지 않는다.**

32B는 선언 0.75, 증분 8/12, 오탐 0.192, median 85s, timeout 10%.

실제 강 플래너 회복은 측정하지 않았다 (`ACTUAL_STRONG_RECOVERY = NOT_MEASURED`). lookalike 4/4·8/8은 proxy다.

방향 전환: 구현 설계(40I)로 가지 않는다. 현재 생산을 유지한다.

```text
SEMANTIC_CONTRACT_PRODUCTION_STRATEGY = NO_SAFE_OPERATIONAL_STRATEGY
OPERATIONAL_FRONTIER_WINNER = S0
```

왜 S0인가: 계약은 검증기가 놓친 grain 집계를 일부 보지만, 같은 생성기가 YES에 측도를 grain으로 넣어 막는다. 그 오탐율은 증분보다 운영적으로 크다. 더 큰 모델은 오탐을 줄여도 지연이 프론티어를 밀어낸다. 검증기를 계약으로 대체할 수도 없다 (filter/union/비-grain).

---

## 닫힌 연구 방향

새 물질적 조건 없이 다시 열지 않는다.

- 단순 7B verifier 프롬프트 강화
- 생산 8B 기본 verifier
- 아키텍처-불안전 의미 라우팅
- 새 generic 신호 없는 선택 8B
- plan-aware 계약 생성
- 7B/32B 계약 생산 기본
- 병렬 계약을 정확성 해법으로 사용
- 벤치마크 특수 수리
- timeout 인상으로 강 모델 구제

---

## 재오픈

ADR §8. 최소 측정 막대는 40H와 같다. 동결 재평가 집합은 `h40-*` 40건. 개발 코퍼스와 섞어 프롬프트를 튜닝하지 않는다.

---

## 잔여 의미 위험

의미 신뢰성은 해결되지 않았다. verifier는 완전히 옳지 않다.

| 위험 | 현재 완화 | 남은 한계 | Python이 고치면 안 되는 이유 | verifier/에스컬레이션 | 재오픈 |
|---|---|---|---|---|---|
| 키 정체성 혼동 | 7B V1; 결과 샘플 | campus/desk류 miss. 8B도 일부 miss | 열 이름 의미를 파싱하면 라우팅 | 종종 PASS (silent) | 새 소형 모델이 안정 대조 |
| 잘못된 의미 바인딩 | verifier | 계약 과다선언은 YES 오탐 | 바인딩을 Python이 고르면 LLM 경계를 침범 | 부분 | 계약 생성 품질이 오탐막대를 통과 |
| 구조 VALID 잘못된 grouping | verifier 일부 | 40H에서 lane/lot/pond 등 miss | group_by를 프롬프트 단어로 강제 | 불완전 | 동일 |
| verifier miss | 유계 결과, 에스컬레이션 1 | 추론 잔여, parse_failed는 에스컬레이션 안 함 | Python이 요청을 해석하면 안 됨 | 해당 층 자체 | 모델/증거 아키텍처 변화 |
| 강 모델 불안정 | 기본으로 안 올림 | 8B n=5 붕괴, 32B timeout | 해당 없음 | 해당 없음 | 안정+지연 막대 통과 |
| 구조 모순으로 못 쓰는 오류 | 실행 전 신호는 PARTIAL | 의미 비교가 필요 | 39X 천장 | verifier 몫 | 새 독립 의미 아티팩트 |

---

## 생산 vs 연구

**의도적 생산 변경 (39X–40H):**

1. 39Z 유계 결과 인식 verifier
2. 40G `observe_final_grain_identities` (미배선 인프라)

40A–40F, 40H는 `core/` 의미 경로에 계약을 넣지 않았다. 생산 경로 audit: 계약 generator/checker/config/라우팅 **NONE**.

Candidate 실험 경로: plan → structural validate → execute → result validate → (failure escalation) → bounded observe → 7B V1 → semantic escalation ≤1.  
`route_multi` / Legacy 사용자 결과에는 이 검증기가 기본으로 붙지 않는다. Shadow OFF.

동결 재평가 holdout: `tests/benchmark_multi/phase40h_research.py` `build_fresh_holdout()` 40건. gitignore 캐시를 커밋하지 않는다.

---

## 다음

다음 개발 목표는 이 폐쇄가 자동으로 고르지 않는다. Phase 41, 이관, 새 Shadow, 새 verifier 연구, 계약 구현을 여기서 시작하지 않는다.
