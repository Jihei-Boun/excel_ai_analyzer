# Phase 39W — Routing Signal Generalization Expansion

## 한 줄 결론

Phase 39V 동결 규칙은 **유효·다양·YES 과다 코퍼스에서 부분적으로만 일반화**한다. 유효 대조군 오탐은 선언을 정합한 뒤 0에 가깝다. 그러나 **구조 VALID + 의미 오답(W2) 7건을 전부 놓친다.** 기존 failure 경로가 이미 잡는 구조 오류와 겹치고, 실행 전 generic 증거가 없는 의미 오류는 Stage A에서 관측되지 않는다. **전용 구현 Phase는 아직 아니다.**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**  
**Verdict: `KEEP_7B_DEFAULT_AND_CONTINUE_EARLY_ROUTING_RESEARCH`**  
**Implementation: `NOT_YET`**  
**Next: Outcome B — Pre-Execution Signal Ceiling & Verifier Complementarity Research**

이 코퍼스는 **더 타당성 중심의 합성/오프라인 분포**이다. 운영 트래픽이 아니다.

## 진입

- Phase 39V SHA: `fe8b5994e7ce18406c10c599a8c661508a27bd0e` (Gate A, committed)
- working tree 진입 시 clean, Shadow OFF
- planner / verifier / escalation / timeout / DSL / V2.2 / 프로덕션 라우팅 변경 없음
- 동결 규칙: `tests.benchmark_multi.phase39v_research.evaluate_capability_signal`를 **수정 없이 import**
- 버전: `PHASE39V_RULE_V1`

## 동결 규칙 (변경 없음)

```text
ESCALATE if not cannot_plan and (
    final_grain_contradiction
    OR evidence_role_contradiction
    OR (structural_error AND NOT only_unsafe)
)
```

1차 공식 평가 전에 규칙을 튜닝하지 않았다. 구현 세부가 39V와 다르면 중단하도록 소스 가드를 두었다.

## 새 코퍼스

46 attempts (attribution-valid). 39V dev/holdout 복제가 아니다. 도메인·파일·열이 다르다.

| 그룹 | n | 역할 |
|---|---|---|
| W1 일상 유효 | 20 | 불필요 에스컬레이션 |
| W2 VALID+의미 오답 | 8 | 핵심 일반화 |
| W3 구조 무효 | 6 | grain / ref / m2m / fake-dual |
| W4 올바른 cannot_plan | 3 | 거절 ≠ 실패 |
| W5 실패 유사 유효 대조 | 6 | 오탐 공격 |
| W6 모호/운영 | 3 | 강제 라벨 없음 |

분포: **YES 29 (63.0%) / NO 14 (30.4%) / IND 3 (6.5%)**. 목표(60–75 / 20–30 / ≤10) 안. 39V 실패 과표집보다 덜 편향됨.

라벨은 `FAST_ATTEMPT_CORRECT` (시도 단위). 최종 요청 정답으로 대체하지 않음.

## 1차 실행과 픽스처 정합

1차 실행에서 YES 2건이 올랐다.

- `w1-join-then-agg`: 집계인데 `grain=entity` 선언 → `final_grain_contradiction`
- `w1-two-metric-independent`: 독립 두 메트릭인데 비교 역할+entity grain 선언

둘 다 **유효 연산 + 모순 선언**이었다. W1 의도와 라벨이 어긋나 선언만 고쳤다. **규칙은 그대로**다. 아래 수치는 정합 후 공식 평가다.

## 동결 규칙 공식 결과 (정합 후)

라벨 43 (IND 제외).

| 지표 | 값 |
|---|---|
| TP / FP / TN / FN | 6 / 0 / 29 / 8 |
| precision / recall | 1.0 / 0.4286 |
| specificity / FPR | 1.0 / 0.0 |
| escalation rate | 0.1395 |
| unnecessary on valid | 0 (rate 0.0) |
| missed insufficiency | 8 |
| recoverable insufficiency recall | 0.3636 (11 recoverable) |
| VALID-wrong recall | **0.0 (0/7)** |
| useful strong rate (candidate calls) | 0.8333 |
| incremental catch vs failure path | **0** |
| redundant early ∩ failure | 3 |

해석:

- precision은 구조 오류·선언 모순을 잡을 때 높다.
- recall은 39V 홀드아웃 0.83에서 **크게 하락**한다. 이유는 새 NO의 대부분이 W2(신호 없음)이기 때문이다.
- genuine many-to-many는 다시 놓친다. Validator가 실행 전 안전 거절 → `SAFELY_BLOCKED_WITHOUT_STRONG_RECOVERY`. 조기 32B가 필수는 아니다.

## 유효 대조군 (W1+W4+W5)

일상 유효·올바른 cannot_plan·실패 유사 유효는 **대부분 fast에 남는다.**  
W5 6건 lookalike는 모두 `DO_NOT_ESCALATE` (rule-break 통과).

cannot_plan 3건은 올리지 않는다. 올바른 거절을 실패로 취급하지 않음.

## 구조 VALID 오답 (W2) — 필수 결과

| | n |
|---|---|
| VALID + exec success + FAST NO | 7 |
| 동결 규칙이 잡음 | **0** |
| 동결 규칙이 놓침 | **7** |
| 기존 failure escalation이 잡음 | 0 |
| 이 하네스의 semantic 근사가 잡음 | 0 |

8번째 W2(`w2-roles-collapse`)는 사이드 선언 후 한 메트릭만 물질화되어 **구조 오류/역할 모순**으로 잡힌다. 39V G2와 같은 관측 가능 구역이다.

역할 없는 W2(붕괴, 한쪽 분기, join vs union, 잘못된 group, 열 누락, union-when-compare, 잘못된 필터)는 실행 전 generic 신호가 없다. 요청 의미를 파이썬이 추론해야 한다. **채택하지 않음.**

이 부정 결과는 중요하다.

> 실행 전 구조 증거로는 planner 부족의 **일부만** 보인다. semantic verifier escalation은 대체될 수 없고 보완으로 남는다.

## 현재 파이프라인 대비 증분

```text
EARLY_ROUTING_INCREMENTAL_CATCH = 0
EARLY_ROUTING_REDUNDANT_ESCALATION = (구조 무효 NO 중 동결이 올린 것)
```

동결 규칙이 잡는 FAST NO는 **이미 failure escalation이 올리는 것과 겹친다.**  
VALID 오답을 실행 전에 새로 막는 증분은 이 코퍼스에서 없다.

더 이른 에스컬레이션이 자동으로 더 낫지 않다. 현재 경로(구조 실패 → 실행 → verifier → semantic 32B)가 같은 실패를 더 낮은 유효 오탐으로 다룰 수 있다. 다만 이 Phase는 라이브 verifier를 돌리지 않았으므로 W2를 verifier가 **실제로** 잡는지는 다음 연구에서 확인해야 한다.

## 32B 가치

라이브 n=5를 다시 돌리지 않았다. 39T/분석가 오라클.

- W2 다수: `STRONG_RECOVERS`로 표기 (형태 교정 가정)
- m2m / 스키마 불일치: `BOTH_INSUFFICIENT`
- fake-dual: `CORRECT_CANNOT_PLAN`

높은 recall만으로는 부족하다. 이 코퍼스에서 조기 라우팅이 올리는 유용 호출은 주로 **이미 실패 경로가 올리는 구조 오류**다.

## 현실적 믹스 (합성 시나리오)

M1/M2/M3는 **관측된 운영 분포가 아니다.**  
YES 과다 믹스에서 S2(동결 조기)는 S1(현재)보다 strong 호출이 조금 늘 수 있으나, 늘어난 호출의 상당수는 유효 오탐 또는 중복이다. W2를 못 잡으면 조기 라우팅의 이득이 작다.

지연은 39T 역사적 대략치(fast ~25s, 32B ~200s, D01 RC-J ~277s). 정확한 초를 만들지 않음. RC-J는 능력과 분리.

## 연산/grain 분해

분석용이다. 라우터에 넣지 않는다.  
filter/aggregate/join/union/분기/rename/다단계 모두에서 W2 미스는 **연산 종류가 아니라 선언된 모순의 유무**에 따라 갈린다.

## 신호 없는 오답 구역

`no_signal_wrong_attempts.json`: FAST NO이면서 동결=`DO_NOT_ESCALATE`.

대부분 **semantic-only**. 실행 전 구조로 안 보인다. 잡으려면 요청 의도 추론이 필요하다.  
m2m은 구조적으로 보이지만 정책상 안전 거절이 바람직.

이 코퍼스에서 실행 전 결정적 증거로 관측 가능한 FAST NO 비율(올린 것 + unsafe-only 안전 차단)은 천장 추정치로만 기록한다. 작은 N의 보편 비율이 아니다.

## 탐색 신호 (공식 결과와 분리)

공식 평가 **후**에만 보았다. `exploratory_signal_candidates.json`.

- undeclared comparison collapse
- single partition when multiple values exist

둘 다 generic·결정적 장벽을 넘지 못한다. **채택하지 않음. 프로덕션 미적용.**

## 구현 권고 바

1. 일반화 recall이 높다 — **아니오** (W2 0)
2. 불필요 에스컬레이션이 낮다 — 정합 후 대체로 예
3. YES 과다 믹스에서 유지 — 오탐은 낮으나 이득이 작다
4. 현재 에스컬레이션 너머의 가치 — **증분 0**
5. 올린 오류가 32B로 회복 — 구조 오류 쪽은 부분적, W2는 안 올림
6. 누수 없음 — 예
7. VALID 오답 구역을 이해함 — 예 (천장)
8. 비용이 구현 시험을 정당화 — **아니오**

→ **`NOT_YET`**. Phase 39X 구현은 하지 않는다.

## 하지 않은 것

프로덕션 라우팅, 프롬프트, 모델, temperature, retry, timeout, DSL, V2.2, Shadow ON, 규칙 사후 튜닝, 벤치마크/도메인/파일/열 피처.

## 다음

**Outcome B.** 조기 라우팅을 넓게 넣지 말고, 실행 전 신호 천장과 verifier 보완을 연구한다.
