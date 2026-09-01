# Phase 39Z — Bounded Result-Aware Semantic Verifier

## 한 줄 결론

프로덕션 semantic verifier의 **E1 `RESULT_EVIDENCE_MISSING` 결함은 교정되었다.** 의미 판단은 여전히 verifier가 하고, Python은 결정적·유계 결과 사실만 노출한다.

- **M1 join-instead-of-union:** OLD `result=None` = PASS 1/1, NEW 유계 결과 증거 = FAIL **5/5**.
- **M2 wrong-group-grain:** OLD/NEW 모두 PASS **5/5**. 이번 Phase에서 고치지 않음.
- 유효 lookalike **FALSE_FAIL = 0/8**. 정당 join·union 통제는 PASS.

**Gate A. Migration = NOT_APPROVED. Shadow = OFF.**  
**주장: `production verifier result-evidence deficiency E1 corrected`**  
**하지 않은 주장: `semantic verifier fixed`**

다음 Phase는 **40A — Semantic Verifier Reasoning Capability Research** (시작하지 않음).

## 진입

```text
Phase 39Y SHA = 7c471b6aa7f40392aefc44a23a69beebad9804af
Gate A, committed
working tree 진입 시 clean
Shadow OFF
planner / Validator / Executor / verifier prompt body / model / threshold /
escalation policy / timeout / DSL / V2.2 미변경
```

## 변형 선택 (Option B)

V2/V3로 바꾸지 않았다. 프로덕션 variant는 **V1 유지**.

| | V1 (결과 없음) | V1+관측 (39Z) | V2 | V3 |
|---|---|---|---|---|
| system prompt | 동일 | 동일 | 동일 | 동일 |
| planner claims | 유지 | 유지 | 유지 | 유지 |
| V2.2 provenance | 유지 | 유지 | 유지 | 유지 |
| observed_result | 없음 | 있음 | 있음 | 있음 |
| prefix "and observed result" | 없음 | 결과 있을 때만 | 있음 | 있음 |
| CrossFileUnderstanding | 없음 | 없음 | 없음 | 있음 |

V3는 CrossFileUnderstanding이라는 **두 번째 증거 변수**를 넣으므로 금지. V2로 이름만 바꾸는 것은 불필요. prefix 한 구는 기존 V2 서식과 동일한 최소 포맷이다. 조인/union/grouping 지시문은 추가하지 않았다.

## 프로덕션 경로

```text
successful fast result
→ observe_result_for_verifier(final_output)   # 같은 attempt, 재실행 없음
→ run_semantic_verification(result=observed, variant=V1, V2.2)
→ verifier 의미 판단
→ (FAIL/UNCERTAIN이면) Strong Planner
```

관측 실패 시: **이전 페이로드로 fail-open** (`result=None`). Candidate가 죽지 않는다. Legacy 무관.

## 유계 계약

필수 DataFrame 필드: `row_count`, `column_count`, `columns`, `sample_rows`, `truncated`.

```text
MAX_RESULT_SAMPLE_ROWS = 5
MAX_RESULT_SAMPLE_COLUMNS = 24
MAX_RESULT_SERIALIZED_CHARS = 4000
row selection = head(N)  # 비무작위
```

근거: Phase 39Y에서 row_count+columns가 M1을 고쳤고 sample-only는 부족했다. 5/24는 그 연구 상한과 같고, 4000자는 프롬프트 무한 성장을 막는다.

Python이 만들지 **않는** 것: join이 틀렸다, union이어야 한다, 한 쪽이 사라졌다, grouping이 틀렸다.

## M1 / M2

**M1 (E1, in scope).** 같은 계획·같은 결과·같은 7B·같은 시스템 프롬프트. 증거만 다름.

- OLD: row_count를 못 봄 → inner join 1행을 적재로 오해 → PASS
- NEW: `row_count=1`, columns `sku, units_left, units_right` → FAIL 5/5

**M2 (E5, out of scope).** 결과 3행 `tid/hrs`가 보여도 7B는 `tid=agent`로 읽음 → PASS 5/5. 의도된 잔여.

## 지표 (39Y 동결 코퍼스, 7B V1)

| | OLD `result=None` | NEW 유계 관측 |
|---|---|---|
| CORRECT_PASS (lookalike 8) | 8 | 8 |
| CORRECT_REJECTION (wrong 7) | 5 | 6 |
| SILENT_WRONG | 2 (M1, M2) | 1 (M2) |
| FALSE_FAIL | 0 | 0 |
| 블라인드 recall | 5/7 | 6/7 |

페이로드: 평균 +306자 (약 80 토큰 규모), 최대 +339자, 새 페이로드 최대 3357자 < 4000. 관측 구축 ~0.03ms. 이 코퍼스에서 결과 truncation 0.

## 격리 / Legacy / Shadow

- 기존 `compact_result_fingerprint` / `dataframe_fingerprint`에 바인딩
- request/attempt-local, 전역 캐시 없음
- 동시 attempt는 각자 관측을 받음
- DataFrame 비변이
- Legacy 경로·응답·폴백 미변경
- Shadow OFF, live Shadow 0건

## 코드 리뷰 (프로덕션)

| 파일 | 이유 | 의미 동작 변경 | 증거만 | Legacy |
|---|---|---|---|---|
| `core/integrate/result_observation.py` (신규) | 유계 결정적 결과 사실 | 없음 (사실만) | 예 | 아니오 |
| `core/integrate/semantic_escalation.py` | 성공 결과에 관측을 붙여 verifier 호출 | 증거 공급만. 정책/threshold 동일 | 예 | 아니오 |
| `core/integrate/semantic_verifier.py` | V1이 결과가 있으면 `observed_result` 부착, prefix 최소 서식 | 지시문/모델/파서 동일 | 예 | 아니오 |

미변경: planner, Validator, Executor 연산, escalation policy, routing, DSL, V2.2, verifier system prompt, model `qwen2.5:7b`.

## 남은 것

M2형: **증거는 충분한 현재 verifier 추론이 부족**. Phase 40A에서만 다룰 것 (프롬프트 구조 ablation → claims 이전 독립 추론 → 요청 vs 계획/결과 비교 → 모델 비교). 39Z와 섞지 않음.

```text
Migration = NOT_APPROVED
```
