# Phase 39V — Evidence-Based Planner Capability Routing Research

## 한 줄 결론

7B 부족은 **일부 generic Stage-A 신호로 관측 가능**하다. 가장 강한 단일 신호는 `final_grain_contradiction`과 `evidence_role_contradiction`(선언된 비교 사이드 vs V2.2 물질화/동일 증거)이다. 동결 합성 규칙의 홀드아웃은 recall 0.83, unnecessary escalation 0, precision 1.0이다. 놓친 것은 의도적으로 기존 정책과 같이 건너뛴 genuine many-to-many뿐이다. 코퍼스가 작고 합성·실패 과표집이므로 **프로덕션 라우팅은 권고하지 않는다.**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**  
**Verdict: `KEEP_7B_DEFAULT_AND_RESEARCH_EARLY_ROUTING`**  
**Production: `NO_PRODUCTION_ROUTING_RULE_RECOMMENDED`**  
**Next: Outcome B — Routing Signal Generalization Expansion**

## 현재 아키텍처 (39U 이후)

```text
7B fast
 → validate_integration_plan(..., frames=sources)   # 39U
 → should_escalate_after_fast_path                  # failure 32B
      skip: cannot_plan / success / unsafe-only / expected-negative
      trigger: final_grain_contradiction 등
 → execute
 → semantic_verifier
 → _should_semantic_escalate (verdict=fail)         # semantic 32B
```

Stage A 후보와 기존 failure escalation은 grain에서 겹친다. G2(구조 VALID, 의미 오답)는 실행 후 verifier가 잡는 것이 현재 경로다.

## 코퍼스

시도 31 (dev 17 / holdout 14). 형태 분할. FAST YES 16 / NO 12 / IND 3.  
G1 일반 조인·union·집계·올바른 분기·올바른 cannot_plan.  
G2 union 붕괴·fake-dual. G3 grain/잘못된 분기/m2m. G4 올바른 cannot_plan. G5 모호/타임아웃.

라벨은 시도 단위. 32B가 나중에 고쳐도 부모 fast는 NO로 유지.

**이 코퍼스는 실패 유형을 과표집한다. 운영 트래픽 오답률이 아니다.**

## 최강 단일 신호

| 신호 | recall | unnecessary | 비고 |
|---|---|---|---|
| grain only | 0.75 | 0 | 기존 failure trigger |
| evidence_role | 0.58 | 0 | G2에 필요 |
| any structural | 1.00 | 0 | 이 코퍼스에선 m2m도 오류라 완벽해 보임. 정책상 m2m 에스컬레이션은 비권고 |
| union 단독 | 0.25 | 2 | 유효 union을 침범. 금지 |
| file count 단독 | 0.67 | 8 | 금지 |

## 동결 합성 규칙 (구현하지 않음)

```text
ESCALATE if not cannot_plan and (
  final_grain_contradiction
  OR evidence_role_contradiction
  OR (structural_error AND NOT only_unsafe_codes)
)
```

홀드아웃: recall 0.833, precision 1.0, FP 0, missed 1 (m2m).  
전체: missed 2, 둘 다 genuine m2m.  
Ablation: evidence를 빼면 G2 일부가 P2와 같아진다. grain을 빼도 다른 항이 대부분 커버. union/filecount는 쓰지 않음.

일반화 근거: validator 코드와 선언 role vs V2.2 evidence signature. 도메인/파일명/패밀리/정답 연산 없음.

## 현재 에스컬레이션 vs 후보

R은 P2보다 labeled recall 0.75 → 0.83. 추가분은 구조 오류가 아닌 역할/물질화 모순.  
P3 근사는 이 코퍼스에서 P2와 거의 같다(많은 G2가 required_field 때문에 이미 invalid).  
실제 라이브 G2(D02)는 VALID 후 verifier FAIL → semantic 32B. Stage A는 verifier 한 번을 줄일 수 있으나 32B planner 비용은 남는다.

## 32B 회복

39T 앵커: C03 32B cannot_plan 5/5, D01/D02 32B 올바른 계획 5/5.  
코퍼스 라벨: STRONG_RECOVERS 다수, BOTH_INSUFFICIENT = m2m.  
타임아웃은 능력과 분리 (RC-J).

## 7B 기본값

KEEP. always-32B는 unnecessary 16/28. 운영 대부분은 G1에 가깝다. RC-J 때문에 기본 32B는 별도 운영 연구 대상.

## 하지 않은 것

프로덕션 `planner_model_strategy` / pipeline / timeout / verifier 변경 없음.  
라이브 Shadow 없음. n=5 안정성은 39T 재사용.
