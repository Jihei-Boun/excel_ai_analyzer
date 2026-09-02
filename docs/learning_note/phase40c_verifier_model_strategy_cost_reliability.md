# Phase 40C — Verifier Model Strategy, Cost & Reliability

## 한 줄 결론

8B+P0는 의미 오답 재현율을 올리지만, **지연·판정 불안정·키 정체성 잔여** 때문에 생산 기본값으로 바꿀 근거가 없다.

- **전략 판정: `KEEP_7B_DEFAULT`**
- **선택 라우팅: `NO_SAFE_SELECTIVE_TRIGGER_FOUND`**
- **7B 기본 유지: `YES`**
- **생산 변경: 없음**
- **다음: Outcome D** — 7B 기본 유지. 40D 구현 설계 없음.

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**

## 진입

```text
Phase 40B SHA = faa9d2606636170db2eb6643325d8489371d63c7
Gate A, committed, working tree clean, Shadow OFF
variant V1, qwen2.5:7b, P0
observe 5/24/4000, timeout 300s 유지
P1 최적화 없음. 8B+P0가 유일한 강한 후보
planner / Validator / Executor / DSL / V2.2 / Legacy 미변경
```

## 전략 (구현하지 않음)

| | 내용 |
|---|---|
| V0 | 7B+P0 현재 기본 |
| V1 | 8B+P0 기본 |
| V2 | 7B 후, 7B PASS에만 8B 재검토 (시뮬레이션) |
| V3 | 아키텍처-안전 선택 8B — 신호 없음 |

재시도 정책: 7B/8B 동일 **0회**. 타임아웃 300s 동결 (8B를 좋게 보이려고 올리지 않음).

## 코퍼스

- Phase 40B EXACT_REPLAY 42 (YES 26 / NO 16)
- 새 holdout CANONICAL 32 (YES 22 / NO 10 = 69% / 31%)
- 합산 74. 연구 분포이며 생산 트래픽이 아님.

## 정확도

| | 40B recall | holdout recall | 합산 recall | FF (valid N) |
|---|---|---|---|---|
| V0 7B | 8/16 = 0.50 | 6/10 = 0.60 | 14/26 = 0.54 | **0 / 48** |
| V1 8B | 13/16 = 0.81 | 8/10 = 0.80 | 21/26 = 0.81 | **0 / 48** |

8B FALSE_FAIL은 **관측 0/48**이지 “never”가 아니다.

V1 추가 정거부: +7. 추가 false-fail: 0. 추가 운영실패율: +0.0135 (40B orchard 300s).

## 7B PASS 영역 (V2의 핵심)

n=60 (YES 48 / NO 12).

- 8B가 고친 Manual NO: **7** (`SECOND_OPINION_CORRECTIONS`)
- 8B가 잘못 거부한 YES: **0**
- 8B 운영 실패: **1** (orchard parse_failed)

V2 8B 호출 비율: **81%**. valid-heavy면 7B PASS가 대부분이므로 V2는 선택적이 아니다.  
V2 재현율 ≈ V1. 추정 e2e도 V1과 거의 같음 (~95s vs V0 ~50s).

## 안정성 (8B+P0 n=5)

| 케이스 | 분포 | 다수결 |
|---|---|---|
| 원본 M2 | FAIL 1, PASS 4 | **오답 PASS** |
| route (교정) | FAIL 5 | 안정 FAIL |
| mean-not-total (first-shot 교정) | FAIL 2, PASS 3 | **오답 PASS** |
| drop-name | FAIL 5 | 안정 FAIL |
| 유효 4건 | PASS 5/5 | 안정 PASS |

40B에서 8B+P0가 M2를 한 번 FAIL한 것은 재현되지 않는다.  
metric 오답도 first-shot FAIL이 반복에서 무너진다.

7B n=5 선택 반복: campus silent-wrong과 valid 모두 PASS 5/5. **불안정 신호가 silent-wrong을 예측하지 않음.**

## 다운스트림 회복 (5건, 39O lineage)

fast NO → 7B PASS → 8B NON-PASS → 32B 의미 재계획.

| | 분류 |
|---|---|
| route, union-not-compare, mean-not-total, drop-name | `STRONG_RECOVERS` |
| agg-events | `STRONG_STILL_WRONG` (cannot_plan) |

`USEFUL_8B_DETECTION_RATE` = **4/5 = 0.80**.  
검출은 맞았으나 행 유지 케이스는 강한 플래너가 회복하지 못함.  
32B wall-clock 중앙 218s, 평균 240s. RC-J와 섞지 않음.

## 키 정체성 잔여 (라우팅 규칙 아님)

7B 그룹 재현율: 40B 0/4, holdout 0/2.  
8B: 40B 1/4 (route), holdout 1/2 (depot).  
남는 8B silent-wrong: campus, vessel, ward.  
**8B 기본으로도 핵심 키 정체성 갭이 남는다.**

## 안전 트리거

- 7B UNCERTAIN: silent-wrong에서 **0**. 자신 있는 PASS.
- truncation: 8B 교정과 무관.
- 7B parse/timeout: silent-wrong과 무관.
- 7B 반복 불안정: 예측력 없음.
- grouping/join/필터/키워드/파일 수/도메인: **금지**, 검사만 하고 기각.

```text
NO_SAFE_SELECTIVE_TRIGGER_FOUND
```

## 비용 프론티어 (이 코퍼스)

C7=4.8s, C8=27.7s, CS=240s (32B 소표본).

| | recall | FF | 운영실패 | 중앙 verifier | 추정 e2e | strong 비율 |
|---|---|---|---|---|---|---|
| V0 | 0.54 | 0/48 | 0 | 4.6s | 50s | 0.19 |
| V1 | 0.81 | 0/48 | 0.014 | 20.9s | 96s | 0.28 |
| V2 | 0.81 | 0/48 | 0.014 | ~22s | 95s | 0.28 |

V2는 V1보다 정확하지 않고, 거의 모든 PASS에 8B를 붙인다.

## 결정 막대

8B 기본: 재현율·FF 관측은 좋으나 **안정성 실패, 지연 5×, 키 갭 잔존**. 구현 설계 단계 아님.  
이중 verifier: 교정 7건·FF 0이지만 V1을 frontier에서 이기지 못하고 아키텍처가 무거움.  
선택 라우팅: 안전 신호 없음.

## 생산

프롬프트·모델·threshold·escalation·timeout 미변경. Python 의미 라우팅 없음.

```text
Migration = NOT_APPROVED
Shadow = OFF
NO_PRODUCTION_CHANGE
KEEP_7B_DEFAULT
```

## 다음

**Outcome D.** 7B 생산 기본 유지. 8B는 능력은 있으나 배포 가치가 아직 없다.  
40D 구현 설계는 시작하지 않는다.
