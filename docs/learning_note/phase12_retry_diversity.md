# Phase 12 — Retry Diversity (operation-family)

목표: validation feedback 이후 Planner가 **같은 invalid operation family를 반복하지 않고**
의미적으로 다른 composition을 탐색하게 한다.
(validator 완화 / stockout formula / inventory keyword rule / first-plan 대규모 rewrite 금지)

Baseline: Phase 11 3-run mean  
Current: Phase 12 3-run (`benchmark_results/phase12/`, `qwen2.5:7b`, 동일 42 cases)

---

## KPI

| KPI | Phase 11 | Phase 12 | Δ |
|---|---:|---:|---:|
| overall_ok_rate | 85.71 | **87.30** | +1.59 |
| analysis_plan_direct_rate | 85.71 | **90.48** | +4.77 |
| fallback_rate | 9.52 | **3.97** | −5.55 |
| pandasai_fallback_rate | 9.52 | **3.17** | −6.35 |
| wrong_operation | 3.0 | **2.67** | −0.33 |
| retry_success | 2.0 | **4.0** | +2.0 |
| retry_exhausted | 2.0 | **0.0** | −2.0 |
| safe_ambiguity_rate (보조) | — | **1.59** | new |

Focus:

| Focus | Phase 11 | Phase 12 |
|---|---|---|
| column_vs_column_failure events / run | inventory exhausted | **0** |
| inventory_below_safety | 0/3 (regression) | **3/3 direct ok** |
| inventory_stockout | exhausted (mean_based×3) | direct wrong_result (retry 1회 후 다른 family) |
| same_operation_family_repeat | (미계측, 실질 3회 동일) | 감지·피드백 동작 (events 2–4/run) |

---

## 1. Inventory residual trace (Phase 11)

3-run 모두 동일:

```
attempt 0–2: filter_vs_mean(재고수량, below)
family: mean_based_filter (당시 composition=column_vs_column_failure)
→ duplicate_plan → exhausted → PandasAI
```

semantic family 자체가 반복됨. JSON signature duplicate만 감지하고 family-level diversity feedback이 약했음.

---

## 2. Repeated operation-family 원인

1. Planner가 threshold 질문을 mean-based filtering으로 해석
2. Phase 11 first-plan hint가 `not filter_vs_mean`을 **명시** → mention bias 가능
3. duplicate 시 ranking 예시 feedback이 col-vs-col 탐색을 방해
4. validator가 정답 shape를 말해도 동일 family 재생성

---

## 3. operation_family_signature

`analysis_plan_contract.operation_family_signature`:

| family | 예 |
|---|---|
| mean_based_filter | filter_vs_mean only |
| column_comparison_filter | filter_rows left/right |
| entity_or_global_ranking | aggregate→sort→limit |
| row_ranking | sort→limit |
| group_comparison / ratio_derivation / … | … |

JSON signature와 별도로 reasoning pattern 반복을 감지.

---

## 4. Retry diversity 변경

- rejected family 추적 → 반복 시:
  - Previous rejected family: mean-based filtering
  - materially different analytical approach (정답 op 미지정)
- regenerate + family 반복 후에만 forbidden-family 강화
- Phase 11 ranking few-shot 예시 제거 (col-vs-col 방해 요인)
- col-vs-col feedback에서 `filter_rows{left,right}` 처방 문구 제거

---

## 5. Semantic role / threshold hint

- role_hints: actual / target / threshold / minimum / maximum / baseline / current / planned
- complementary roles 있을 때 **possible numeric relationships** 한 줄 (op 미지정)
- Phase 11의 `prefer filter_rows … (not filter_vs_mean)` first-plan 처방 **제거** (regression 원인 후보)

---

## 6. below_safety regression 원인

| | Phase 10 | Phase 11 | Phase 12 |
|---|---|---|---|
| first plan | filter_rows left/right | filter_vs_mean | filter_rows |
| outcome | ok | exhausted | **ok** |

일반 원인: Phase 11 recovery가 first-plan에 op를 부정/긍정으로 언급하고,
retry 예시가 ranking으로 편향 → mean family lock-in.
Phase 12는 first-plan 처방을 빼고 retry diversity에 집중 → 회복.

---

## 7. Ambiguous compare 정책

- overall_ok 정의 변경 없음
- 보조 metric `safe_ambiguity_rate` 추가
  (direct + wrong_operation + semantic_ambiguity warning)
- ambiguous_sales_compare는 억지 정답 맞추기 안 함

---

## 8. expected-negative fallback

- missing-column 계열 exhaust 시 `safe_plan_failure`로 PandasAI 스킵 (보수적)
- composition residual은 기존 fallback 유지
- Phase 12 fallback 대부분 negative/safe 경로; inventory exhausted 소멸

---

## 9–13. Tests & live

- pytest: **322 passed, 1 skipped**
- 42×3 유지, expected 변경 없음
- direct 90.48 / fallback ~4 / retry_exhausted **0**
- col-vs-col failure event **0**; below_safety **3/3**

---

## 14–15. Single-file 종료 / multi-file

종료 기준 대비:

- direct ≥ 80% ✅ (90.48)
- fallback ≤ ~10% ✅ (~4)
- validator FP ≈ 0 ✅
- 반복 structural failure 소수 ✅ (exhausted=0)
- 잔여: semantic ambiguity + stockout semantic(wrong_result) + 소형 모델 한계

**single-file 최적화는 일단 종료해도 되는 수준.**  
다음 Phase는 multi-file Planner 전환을 권장.
stockout은 multi-file 이전이라도 semantic intent(비교 vs ranking) residual로 남음.

---

## 해석

이번 Phase 핵심인 **같은 실패 3회 반복 방지**는 달성 (retry_exhausted=0, mean_based×3 소멸).
below_safety regression 회복 + direct↑ + fallback↓.
stockout은 family 탈출에는 성공했으나 최종 composition이 ranking 쪽으로 가 wrong_result — 다음 단계 과제.
