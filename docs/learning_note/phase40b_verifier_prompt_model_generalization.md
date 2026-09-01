# Phase 40B — Verifier Prompt-vs-Model Strategy Generalization

## 한 줄 결론

Phase 40A의 `8B + 의도-우선 P1` 상호작용은 **원본 M2에서 안정 재현되지만, 새 홀드아웃에서는 일반화되지 않는다.**

의미 이득의 거의 전부는 **모델만(S2 = 8B+P0)** 으로 설명된다. 7B에 P1만 붙이는 것(S1)은 S0과 동일하다.

- **전략 판정: `MODEL_ONLY_SUFFICIENT`**
- **7B 기본: `KEEP_7B_CURRENT`**
- **생산 변경: 없음**
- **다음: Outcome C — Verifier Model Strategy / Cost-Reliability Research**
- **40C 구현 설계는 시작하지 않음**

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**

## 진입

```text
Phase 40A SHA = 9fd1b1009c69fdd8a33383d46f5b434a0ff7af59
Gate A, committed
working tree: 연구 파일만 추가, core/ 미변경
Shadow OFF
variant V1, qwen2.5:7b
observe bounds 5/24/4000 유지
P0/P1 문구·해시 40A 동결
8B = qwen3:8b (40A와 동일, 대체 없음)
```

## 동결 전략

| | 모델 | 프롬프트 |
|---|---|---|
| S0 | qwen2.5:7b | P0 생산 |
| S1 | qwen2.5:7b | P1 의도-우선 (40A 동결) |
| S2 | qwen3:8b | P0 생산 |
| S3 | qwen3:8b | P1 의도-우선 (40A 동결) |

P0 sha `7d9238548ae40e59a68d15852bf8f97becb00cbbe38b7be92782d5d811e8f2cd`  
P1 sha `af3d48d01a24be17e96164cee5387bc57cd58be0d899f34c1b010743d0358e90`

평가 중 P1 문구를 바꾸지 않음. 새 P6/연산 힌트/도메인 용어 없음.

## 새 코퍼스

42 attempts. Manual YES 26 (62%) / NO 16 (38%) / IND 0.  
40A attempt_id와 겹치지 않음. 연구 분포이며 생산 트래픽이 아님.

- 그룹 키 오답 4 + 유효 lookalike 4 (campus/crm, vessel/hid, orchard/tid, route/sid). M2 스키마 미복제.
- 비그룹 오답 12 (전체 NO의 75%): 필터, 적재 vs 조인, 비교 vs union, 출력 누락, mean vs total, 사이드 붕괴, 행 유지 vs 집계.
- truncation 통제 3 (wide/tall/wide2), 전부 Manual YES.

## 1차 행렬 (동일 증거, 동일 라벨)

| | recall | FF rate | SW | CR | CP | 중앙 지연 |
|---|---|---|---|---|---|---|
| S0 7B+P0 | 0.50 (8/16) | 0 | 8 | 8 | 26/26 | 4.3s |
| S1 7B+P1 | 0.50 (8/16) | 0 | 8 | 8 | 26/26 | 4.5s |
| S2 8B+P0 | 0.81 (13/16) | 0 | 2 | 13 | 26/26 | 20.2s |
| S3 8B+P1 | 0.88 (14/16) | 0 | 2 | 14 | 26/26 | 22.0s |

S1 집합 = S0 집합. **PROMPT_ONLY_GAIN = 0.**

S0/S1 silent-wrong: 그룹 4 + union-not-compare + mean-not-total + agg-events + drop-name.

S2/S3 silent-wrong: **campus, vessel만.** 비그룹 오답 12/12는 8B가 잡음.

S2 orchard는 **300s parse_failed** (의미 PASS가 아님). S3 orchard FAIL. 파싱된 모든 케이스에서 S2 라벨 = S3 라벨.

`COMBINATION_ONLY_CORRECTIONS` = **0** (S2 타임아웃은 상호작용 승리로 세지 않음).

## 이득 분해

```text
PROMPT_ONLY_GAIN  CR +0
MODEL_ONLY_GAIN   CR +5, recall +0.31
COMBINED_GAIN     CR +6, recall +0.38
INTERACTION_GAIN  combination-only 0
```

비그룹 CR: S0=8, S2=12, S3=12.  
그룹 CR: S0=0, S2=1 (route), S3=2 (route+orchard; orchard는 S2 타임아웃).

8B는 필터·형태·메트릭·출력 누락에서 7B를 이긴다. 의도-우선 문장은 그 위에 안정적 추가 이득을 주지 않는다.

## M2 앵커 (홀드아웃 분모 제외)

| | first | S3 n=5 |
|---|---|---|
| S0 | PASS | — |
| S1 | PASS | — |
| S2 | FAIL | — |
| S3 | FAIL | **FAIL 5/5** |

원본 M2의 8B+P1 교정은 안정적이다.  
다만 40A에서 S2(8B+P0)는 M2 PASS였고, 40B first-shot은 FAIL이다. **8B+P0의 M2는 불안정**하다. 40A 동결 기록을 다시 쓰지 않는다.

## 새 M2-유사 S3 안정성 n=5

| 케이스 | S3 |
|---|---|
| b40-n-campus (crm vs campus) | **PASS 5/5** silent-wrong |
| b40-n-vessel (hid vs vessel) | **PASS 5/5** silent-wrong |
| b40-n-orchard (tid vs orchard) | FAIL 5/5 |
| b40-y-campus / b40-y-vessel lookalike | PASS 5/5, FALSE_FAIL 없음 |

핵심 잔여: `crm`을 campus로, `hid`를 vessel로 **가정**. 40A M2의 tid=agent 오류와 같은 종류다. P1은 이를 막지 못한다.

## 비그룹 안정성 S3 n=5

filter-grade 오답 FAIL 5/5, join-not-stack FAIL 5/5.  
유효 lookalike filter-grade / stack-weeks PASS 5/5.

비그룹 이득은 S2에서 이미 발생. S3가 추가로 넓히지 않음.

## False-fail / Uncertain / Truncation

- S3 VALID_FALSE_FAIL_RATE = **0**. 수동 재라벨 없음.
- UNCERTAIN = 0 (appropriate/inappropriate 모두 0). 모호함을 인정하기보다 자신 있는 PASS/FAIL.
- truncation 3건 전부 PASS. S3가 잘린 관측만으로 거부하지 않음.

## 의도-우선 품질

P1은 `required_outcome` JSON을 강제하지 않아 구조적 `SELF_INCONSISTENT_VERDICT` = 0.

산문 진단:

- S1 campus: 요청을 인용한 뒤 `group by crm`을 일치로 선언. 40A 7B와 동일.
- S3 campus: 요청은 campus인데 `crm (assumed campus identifier)` 후 PASS.
- S3 orchard/M2: 키를 실제로 대조하고 FAIL.

**8B가 마지막 대조 단계를 안정적으로 닫지 않는다.**

구조 VALID 과신: S3도 `final_schema contains crm and lux`를 충족 증거로 쓴다. S3가 이 습관을 제거하지 않음.

## 지연 / 운영

| | mean | median | p90 | timeout |
|---|---|---|---|---|
| S0 | 4.5s | 4.3s | 5.7s | 0 |
| S1 | 4.7s | 4.5s | 5.5s | 0 |
| S2 | 29.8s | 20.2s | 28.5s | **1** (orchard 300s) |
| S3 | 25.3s | 22.0s | 38.9s | 0 (first-shot) |

8B는 약 5× 느리다. 화폐 비용은 측정하지 않음 (상대 크기 7B vs 8B만).  
S2 타임아웃은 8B를 기본값으로 올리기 전에 신뢰성 연구가 필요함을 뜻한다.

## 다운스트림

이 코퍼스에서 거절률: S0 19% → S3 33%. 유용 에스컬레이션 정밀도 전부 1.0 (FALSE_FAIL 0).  
생산 트래픽 비율이 아님. 새로 잡은 오답의 32B 회복은 호출하지 않음 (UNKNOWN). 역사적 M2만 39W `STRONG_RECOVERS`.

## 결정 막대 (§51)

1. S0보다 오답 재현율 향상 — 예  
2. S1·S2보다 **실질적으로** 나음 — **아니오** (S2≈S3)  
3. combination-only 복수 — **0**  
4. 유효 false-fail 낮음 — 예  
5. M2 및 새 M2-유사 안정 교정 — M2 예, **새 케이스 아니오**  
6. 그룹 외 개선 — 모델 이득은 예, S3 고유 이득은 아니오  
7. 지연/신뢰성 수용 — 8B 지연·S2 타임아웃으로 **미흡**  
8. 벤치 누수 없음 — 예  
9. 유용 에스컬레이션 정밀도 — 예  
10. 주장 품질이 S3 일반화를 뒷받침 — **아니오** (campus/vessel 가정)

구현 권고 없음.

## 생산

프롬프트·모델·threshold·escalation·planner·Validator·Executor·DSL·V2.2 미변경.  
Python 의미 비교 없음. 그룹 라우팅 없음. Shadow 라이브 0.

```text
Migration = NOT_APPROVED
Shadow = OFF
NO_PRODUCTION_CHANGE
```

## 다음

**Outcome C** — 모델 능력(8B)이 이득의 대부분이다. 기본값을 8B로 바꾸기 전에 timeout·키 정체성 잔여·비용을 별도 연구해야 한다.  
Phase 40C combined 구현 설계는 하지 않는다.
