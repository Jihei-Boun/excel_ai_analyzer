# Phase 40 Closure — Semantic Reliability Research Architecture Decision Record

Status: **Accepted** (Phase 40 Closure)  
Date: 2026-09-02  
Gate: **A**  
`PHASE_40_RESEARCH = CLOSED`

Related:

- Phase 40H SHA `5fa06b5a6f2ca202f0f5823c2127a7598918e1e7`
- Phase 40G SHA `ac819329a3fec3737285f4c4b83d33cd66023ea6`
- Phase 39Z SHA `9688e504c2784d9441e30d8f29173fa1f9422223`
- Learning note: `docs/learning_note/phase40_closure_semantic_reliability_research.md`

---

## 1. Context

Phase 39X–40H는 다중파일 Candidate 경로에서 **구조적으로 유효하지만 의미적으로 틀린 IntegrationPlan**을 Python이 안전하게 잡을 수 있는지, 잡을 수 없다면 어떤 LLM 층이 운영 가능한지를 연구했다.

아키텍처 경계는 연구 전 기간 동안 유지되었다.

```text
LLM = semantic decision
Python = deterministic observation, structural validation, execution, safety
```

저장소의 실제 Candidate 스택 (코드 기준):

```text
build_integration_plan
→ validate_integration_plan
→ execute_integration_plan
→ validate_integration_result
→ (optional Phase 28 failure-based strong planner)
```

의미 검증은 `run_integration_pipeline_semantic_experimental`에 있다. `route_multi`에 배선되지 않는다. Shadow가 켜진 경우에만 이 경로를 관찰한다. Shadow 기본값은 OFF.

이 실험 경로에서 검증기는 `qwen2.5:7b` variant **V1**이며, Phase 39Z 이후 유계 결과 관측(`observe_result_for_verifier`, 5/24/4000)을 붙인다. 의미 에스컬레이션은 `MAX_SEMANTIC_ESCALATIONS=1`이다.

질문은:

> 39X–40H 증거로, 생산에 정당한 의미 신뢰성 아키텍처는 무엇인가?

이 ADR은 새 실험을 열지 않는다.

---

## 2. Decision

```text
KEEP_CURRENT_PRODUCTION_ARCHITECTURE
PRODUCTION_VERIFIER_MODEL = KEEP_7B_CURRENT
SEMANTIC_CONTRACT_PRODUCTION_STRATEGY = NO_SAFE_OPERATIONAL_STRATEGY
OPERATIONAL_FRONTIER_WINNER = S0
FINAL_GRAIN_OBSERVER = RETAIN_AS_GENERIC_INFRASTRUCTURE
BOUNDED_RESULT_AWARE_VERIFIER = RETAIN
Migration = NOT_APPROVED
Shadow = OFF
```

**SemanticRequirementContract 생성과 생산 ContractPlanChecker를 현재 Candidate 파이프라인에 넣지 않는다.**

이유는 개념이 구조적으로 불가능해서가 아니다. 현재 계약 생성 모델이 **신뢰성·오탐·지연·운영 안전**의 결합 막대를 넘지 못해서다.

이것은 현재 운영 전략의 증거 기반 기각이다. 추상 계약 아키텍처의 영구 기각이 아니다.

Gate A는 연구/증거 폐쇄 품질이다. Candidate → Legacy 이관을 승인하지 않는다.

---

## 3. Evidence

용어는 동의어가 아니다.

| 용어 | 이 결정에서의 값 |
|---|---|
| architecture-safe | 계약 I0 + 결정적 체커는 원칙적으로 가능 |
| operationally justified | 아니오 (40H) |
| production-approved | 아니오 |
| migration-approved | 아니오 |

### 39X

실행 전 결정적 신호는 구조/선언 모순만 잡는다. VALID+의미 오답 7건은 Python이 의미를 읽지 않고는 잡을 수 없다. 동결 verifier는 5/7 거부, lookalike 8/8 통과.

```text
PREEXEC_DETERMINISTIC_SIGNALING = PARTIAL
```

새 Python 의미 라우팅 규칙을 만들지 않는다.

### 39Y / 39Z

한 miss는 결과 증거 부재(E1)였다. 유계 결과 관측이 M1 join-instead-of-union을 PASS→FAIL 5/5로 고쳤다. M2 키 정체성 miss는 남았다. lookalike FALSE_FAIL 0/8.

```text
BOUNDED_RESULT_AWARE_VERIFIER = RETAIN
```

### 40A

7B 프롬프트 변형(P0–P5)은 M2를 안정 교정하지 못했다.

```text
VERIFIER_PROMPT_TUNING = NOT_SUFFICIENT
```

### 40B

이득의 거의 전부는 모델(8B+P0)이다. 7B+P1은 7B+P0과 같다. 키 정체성 혼란은 남는다.

```text
MODEL_CAPABILITY_MATTERS
BUT
MODEL_SWITCH_NOT_YET_JUSTIFIED
```

### 40C

8B 합산 recall 0.81 vs 7B 0.54. valid FF 관측 0/48. 그러나 중앙 지연 ~20.9s vs 4.6s (~5×), 운영 실패 +0.014, 판정 불안정(M2 다수결 PASS), 안전 선택 트리거 없음, 이중 verifier 이점 없음.

```text
PRODUCTION_VERIFIER_MODEL = KEEP_7B_CURRENT
NO_SAFE_SELECTIVE_8B_TRIGGER
```

의미 모델 라우팅을 구현하지 않는다.

### 40D–40E

최소 LLM 선언 grain+binding은 일부 의미 오답을 Python이 의미를 읽지 않고 노출할 수 있다. I0 > I1 (계획 앵커링). 생산 승인 아님.

```text
SEMANTIC_CONTRACT_IDEA = ARCHITECTURALLY_PARTIALLY_PROMISING
v1 = required_grain + LLM-authored grounded binding
semantic label = non-authoritative to Python
binding decision = LLM
binding existence/lineage = Python
```

function / outputs / distinctions / relations / ontology는 v1에서 제거.

### 40F–40G

이미 선언된 바인딩에 대해 결정적 검사가 가능하다. 구조 연구 FALSE_CONTRADICTION=0, FALSE_PRESERVED=0. 네이티브 final-grain 관측이 필요했고 40G가 `observe_final_grain_identities`를 넣었다. FALSE_KNOWN_GRAIN=0, KNOWN_IDENTITY_MISMATCH=0. 파이프라인 반환 lineage에는 붙이지 않았다.

```text
CONTRACT_CHECKER_ARCHITECTURE = DETERMINISTICALLY_FEASIBLE
FINAL_GRAIN_OBSERVER = RETAIN_AS_GENERIC_INFRASTRUCTURE
```

계약 채택과 무관하게 유지한다. 의미 없음, 저지연, 향후 provenance/진단용.

### 40H (결정적 운영 결과, fresh holdout 40)

| | 7B I0 v1 | 32B I0 v1 |
|---|---|---|
| 선언/바인딩 | 0.45 | 0.75 |
| INVALID | 13/40 | 0/40 |
| 날조 바인딩 | 6 | 0 |
| 의미 오탐 (YES) | 11/26 = 0.423 | 5/26 = 0.192 |
| 증분 NO 탐지 | 4/12 | 8/12 |
| 관측기 오탐 | 0 | 0 |
| median 지연 | 5.556s | 85.034s |
| p90 | 6.932s | 268.862s |
| timeout | 0 | 4/40 = 0.10 |

병렬 7B는 순차 +5.6s를 ~0으로 줄일 수 있으나 오탐을 고치지 못한다.  
`NO_SAFE_EXISTING_CALL_REUSE`. `NO_SAFE_CONTRACT_CALL_TRIGGER`. I1 기각.  
`ACTUAL_STRONG_RECOVERY = NOT_MEASURED`. proxy 4/4·8/8은 lookalike 가능성일 뿐.  
40D 43건은 `OBSERVER_REANALYSIS_ONLY`.

```text
SEMANTIC_CONTRACT_PRODUCTION_STRATEGY = NO_SAFE_OPERATIONAL_STRATEGY
OPERATIONAL_FRONTIER_WINNER = S0
```

---

## 4. Alternatives considered

| Candidate | Correctness benefit | False-block risk | Latency/cost | Stability | Architecture-safe | Decision |
|---|---:|---:|---:|---:|---|---|
| **S0 current** (7B V1 verifier, no contract) | baseline; 40C recall 0.54 on that corpus; 40H verifier catches some NO | lookalike FF 0 on studied valid sets | verifier median ~4.6s (40C/40H) | 7B silent-wrong도 안정 PASS — 즉 miss가 안정 | yes | **KEEP** |
| 8B verifier default | 40C recall 0.81; FF 0/48 observed | not observed on that valid N; not “never” | median 20.9s, ~5× | M2/mean 불안정 | yes (same layer) | reject |
| selective 8B verifier | same recall as default 8B in V2 sim | same | almost all 7B PASS still call 8B | same 8B instability | **no safe trigger** | reject |
| 7B independent contract | 40H incremental 4/12 NO (3 vs verifier miss) | **0.423 YES** | median 5.6s; parallel ~0 extra | aisle YES 불안정; INVALID 13/40 | I0 yes; Python checker yes | reject operate |
| 32B independent contract | incremental 8/12; decl 0.75 | 0.192 YES | median 85s, p90 269s, timeout 0.10 | aisle YES 불안정 | I0 yes | reject operate |
| plan-aware contract (I1) | 40D 7B NO recall collapsed 0.53→0.18 | anchoring | saves a call | worse 7B | **unsafe** (plan leak) | reject |
| parallel independent 7B contract | same as 7B contract | same 0.423 | seq +5.6s → ~0 | same | I0 preserved in principle | reject as correctness fix |

수치가 없는 칸은 발명하지 않았다. 코퍼스는 연구 분포이며 생산 mix가 아니다.

---

## 5. Rejected alternatives

- 7B verifier 프롬프트 강화로 잔여 키 정체성을 고친다
- 생산 기본 검증기를 8B로 올린다
- 아키텍처-불안전 의미 라우팅 / 벤치마크·열·프롬프트 트리거
- 새 generic 신호 없이 선택 8B
- plan-aware 계약 생성 (지연 절감)
- 7B 또는 32B 계약 생산 기본
- 병렬 계약을 정확성 해법으로 쓴다
- timeout을 올려 32B를 구한다
- 벤치마크 특수 수리

새 프롬프트 변형만으로는 재오픈하지 않는다.

---

## 6. Consequences

### 생산에 남는 것

- Integration Planner, IntegrationPlan DSL, Structural Validator, Executor, Result Validator
- Phase 28 실패 기반 강 플래너 에스컬레이션 (의미 라우팅 아님)
- Phase 39Z 유계 결과 인식 verifier (실험 Candidate/Shadow 경로)
- `qwen2.5:7b` V1, observe 5/24/4000, `MAX_SEMANTIC_ESCALATIONS=1`
- V2.2 lineage/provenance
- Phase 40G `observe_final_grain_identities` (정의됨, 계약/Validator에 미배선, 유지)
- Legacy 사용자 경로 미변경
- Shadow OFF

### 연구 전용 / NOT_WIRED

```text
SemanticRequirementContract generation
contract-plan checker
independent contract LLM call
32B contract generation
plan-aware contract generation
contract-based routing/retry
parallel contract/planner execution
```

`core/` 생산 경로에 계약 generator import, checker 호출, 계약 config flag, 계약 모델 라우팅, 계약 기반 Validator 분기는 **없음**.

### 주장하지 않는 것

의미 신뢰성이 해결되었다고 말하지 않는다. verifier가 완전히 옳다고 말하지 않는다.

> 현재 아키텍처는 연구된 정확성/비용 제약 아래 **가장 잘 지지되는 프론티어**이며, 잔여 의미 위험은 문서화되어 있다.

---

## 7. Residual risks

한국어 레지스터는 학습 노트 §잔여 위험에 둔다. 요약:

| 위험 | 현재 완화 | 한계 |
|---|---|---|
| 키 정체성 혼동 | 7B verifier; 계약은 연구만 | 7B/8B 모두 campus류 miss; 7B 계약도 desk INVALID |
| 잘못된 의미 바인딩 | verifier | 계약 과다선언이 YES를 막음 |
| 구조 VALID 잘못된 grouping | verifier 일부; 계약은 연구에서만 추가 탐지 | 40H 검증기 miss 다수 |
| verifier miss | 유계 결과 + 의미 에스컬레이션 1회 | M2형 추론 잔여 |
| 강 모델 불안정 | 8B/32B를 기본으로 올리지 않음 | 비용·timeout |
| 구조 모순으로 표현 불가한 오류 | Python이 의미를 읽지 않음 | 실행 전 신호 천장 |

Python이 프롬프트/열 의미를 해석해 고치면 아키텍처 경계를 깨므로 안전하지 않다.

---

## 8. Reopen conditions

다음이 **동시에** 바뀌지 않으면 재오픈하지 않는다.

1. **모델 능력 변화** — 소/중형 모델이 선언·바인딩 정확을 올리고 의미 오탐을 낮추며, 지연·운영실패·fresh holdout 안정이 수용 가능.
2. **기존 모델 업그레이드** — 새 7B/8B 릴리스가 verifier 또는 계약 능력을 물질적으로 바꿈.
3. **새 의미 증거 아키텍처** — 추가 고비용 호출 없이 독립 pre-plan 의미 아티팩트가 생김.
4. **생산 텔레메트리** — 신뢰할 수 있는 Shadow가 현재 연구에 없는 고빈도 잔여 계급을 보임. 지금 Shadow를 켜지 않는다.
5. **비용 변화** — 추론 비용/지연이 운영 프론티어를 바꿀 만큼 변함.

재오픈 시 40H 가정을 blind inherit하지 않는다. 최소 반복:

선언 정확, 바인딩 정확, 의미 오탐, 관측기 오탐, 생략, 날조 바인딩, 운영 실패, 지연, 안정성, 검증기 보완성, **실제** 회복 증거.

새 모델은 동결 40H fresh holdout 베이스라인과 직접 비교한다. 반복 프롬프트 튜닝에 그 holdout을 쓰지 않는다.

개발 코퍼스와 동결 재평가 holdout을 분리한다. 동결 집합은 `tests/benchmark_multi/phase40h_research.py`의 `h40-*` 40건이다.

---

## 9. Migration status

```text
NOT_APPROVED
```

Gate A ≠ migration.

---

## 10. Shadow status

```text
OFF
```

이 폐쇄에서 새 live Shadow 캠페인을 시작하지 않는다.

---

## Frozen frontier statement

Phase 40H까지 수집한 증거에서, 현재 생산 Candidate 아키텍처(7B V1 유계 결과 verifier, 계약 층 없음)는 더 강한 verifier나 의미-계약 층을 더하는 것보다 낫다. 대안은 선택된 의미 재현율을 올리지만, 결합된 신뢰성·지연·안정성 또는 아키텍처 안전 막대를 실패한다.

근거: 8B verifier는 recall 0.54→0.81이나 ~5× 지연과 불안정; 7B 계약은 NO 증분 4/12이나 YES 오탐 0.423; 32B 계약은 오탐 0.192에 median 85s·timeout 10%. 병렬은 지연만 줄인다.

```text
architecture-safe in principle = partially yes
deterministic checker feasibility = yes
operational justification with current models = no
```

따라서 구현하지 않는다.
