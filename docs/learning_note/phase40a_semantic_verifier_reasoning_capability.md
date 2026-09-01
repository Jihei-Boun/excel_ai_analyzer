# Phase 40A — Semantic Verifier Reasoning Capability

## 한 줄 결론

Phase 39Z 이후에도 남는 오답은 **증거가 부족해서가 아니다.** 7B는 유계 결과를 보고도 요청의 키 정체성(누구에 대해 집계하는가)을 계획/결과의 키와 대조하지 못한다.

- **7B + 어떤 일반 프롬프트 변형도 M2를 안정적으로 교정하지 못함** (P0–P5 모두 PASS, P0/P1/P2는 5/5 PASS).
- **8B + 현재 생산 프롬프트(P0)도 M2 PASS.** tid를 agent ID로 가정.
- **8B + 의도-우선(P1) 또는 삼자 비교(P2)** 는 이 코퍼스에서 wrong 11/11을 거부하고 valid FALSE_FAIL=0. M2 8B는 n=1이며 수동 판정은 CORRECT_REJECTION.

잔여 원인은 **MIXED**. 생산 프롬프트/모델은 바꾸지 않았다.

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**  
다음: **Outcome C — Phase 40B Verifier Prompt-vs-Model Strategy Generalization** (시작하지 않음).

## 진입

```text
Phase 39Z SHA = 9688e504c2784d9441e30d8f29173fa1f9422223
Gate A, committed, working tree clean, Shadow OFF
variant V1, qwen2.5:7b, observe_result_for_verifier 유지
planner / Validator / Executor / DSL / V2.2 / Legacy / threshold / policy 미변경
```

## 코퍼스

24 attempts. Wrong 11 / Valid 13. DEV 14 / HOLD 10.  
M2는 DEV. 홀드아웃에 다른 그룹 키 오답 `a40-wrong-receipt-grain`과 비그룹 오답(필터/브랜치/형태) 포함.  
새 케이스는 M2 스키마를 복제하지 않음.

P0 재현: M2 **PASS**. 추가 silent-wrong: `a40-wrong-metric-mean`, `a40-wrong-receipt-grain`. 목표 ≥3 residual 충족.

## P0 (39Z 생산 동등)

| | |
|---|---|
| CORRECT_REJECTION | 8/11 |
| CORRECT_PASS | 13/13 |
| SILENT_WRONG | 3 |
| FALSE_FAIL | 0 |
| UNCERTAIN | 0 |
| 평균 지연 | 5.0s |

이미 39Z가 잡은 형태/필터/브랜치/사이드 붕괴는 유지. 남는 것은 **그룹 키 정체성**과 **집계 함수 의미(total vs mean)**.

## 7B 프롬프트 변형

모두 일반 문장만 추가. group_by/agent/tid/join 지시 없음.

| 변형 | 차원 | recall | FF | M2 |
|---|---|---|---|---|
| P0 생산 | — | 0.73 | 0 | PASS 5/5 |
| P1 의도 우선 | H1 | 0.73 | 0 | PASS 5/5 |
| P2 삼자 비교 | H3 | 0.73 | 0 | PASS 5/5 |
| P3 claims last | H2 | 0.64 | 0 | PASS (M1도 놓침) |
| P4 claims 제거 | H2 | 0.73 | 0 | PASS |
| P5 모순 탐색 | H3 | 0.73 | 0 | PASS |

DEV에서 P1/P2를 winner로 동결한 뒤 HOLD를 재튜닝하지 않음. HOLD도 7B에서 receipt-grain silent-wrong 1건으로 동일.

**H2 claims: CLAIMS_NEUTRAL 재확인.** P4 silent-wrong 집합 = P0.  
**H1/H3: 7B에서는 조직/절차만으로 M2 미교정.**  
P2 흡연 증거: `required_outcome=Sum ticket hours per agent`, `observed_computation=by tid`, `semantic_mismatches=[]`, verdict=**pass**. 절차는 수행했고 모순 선언에 실패.

Valid lookalike 3건 P1/P2 n=5 전부 PASS. FALSE_FAIL 증가 없음.

## 모델

같은 페이로드·같은 관측.

| | recall | SW | FF | 지연 |
|---|---|---|---|---|
| 7B P0 | 0.73 | 3 | 0 | 5.0s |
| 7B P1 | 0.73 | 3 | 0 | 4.8s |
| 8B P0 | 0.82 | 2 (M2, receipt-grain) | 0 | 25s |
| 8B P1 | **1.00** | **0** | 0 | 29s |

8B P0도 M2에서 tid=agent로 가정하고 PASS. 더 큰 모델이 자동 정답이 아님.  
8B P1 M2 FAIL 근거는 결과 샘플 T1/T2/T3를 티켓으로 읽고 요청의 agent와 불일치를 지적. 수동 라벨과 일치.

32B는 필요하지 않아 생략 (8B가 상호작용을 이미 보임).

## 질문에 대한 답

- 7B 프롬프트만으로 M2 안정 교정? **NO**
- 모델만 바꾸면? **NO** (8B P0 실패)
- 둘 다? 이 코퍼스에서 **PARTIAL / n=1 S3 성공**, 일반화·안정성·비용은 다음 Phase.

7B 기본 verifier: **YES_WITH_LIMITATIONS**. 39Z 이후 형태/필터/구분 붕괴는 잘 잡고, 키 정체성 대조는 약함.

## 구조 VALID 과신

7B는 `final schema contains tid and hrs`를 요청 충족의 증거로 쓴다. Validator를 바꾸지 않음.

결과 읽기: truncation 통제 `a40-valid-wide`는 P0 PASS (column_count 유지). M2는 행/열을 보지만 의미를 잘못 붙인다. 증거 부족이 아님.

UNCERTAIN은 이 코퍼스에서 거의 안 나옴 (unsafe confidence 쪽).

## 생산

프롬프트·모델·threshold·escalation 미변경. Python 의미 비교 없음. 조기 라우팅 재개방 없음.

```text
Migration = NOT_APPROVED
```
