# Phase 13 — Multi-file Architecture Audit & IntegrationPlan Design

> **Phase 유형:** 조사·설계 only (대규모 구현 없음)  
> **조사 기준일:** 2026-08-11  
> **근거:** repository 코드 실측 (`core/routing/route_multi.py`, `core/integrate/*`, `core/io/merge_engine.py`, `core/analysis/*`, 관련 tests)  
> **추측 vs 확인:** 본 문서에서 “확인됨”은 코드 경로를 직접 추적한 결과이고, “설계 제안”은 Phase 14+ 구현 방향이다.

Single-file 기준(Phase 12 live): overall 87.30% / AnalysisPlan direct 90.48% / fallback 3.97% / retry_exhausted 0.  
single-file 최적화는 종료 가능 상태로 보고, multi-file로 전환한다.

---

## 1. Executive Summary

현재 multi-file은 **세 갈래**로 나뉘어 있다.

| 경로 | 역할 | 확인됨 |
|---|---|---|
| `route_multi_prompt` 규칙 분기 | summary / missing / quality / schema / legacy aggregate | deterministic·heuristic |
| `try_integrate_pipeline` | LLM schema + LLM `ExecutionPlan` + Python `aggregate_merge` | 사실상 **유사 스키마 표 합산 전용** |
| `run_multi_analysis` → PandasAI | multi 분석 fallback | LLM code-gen |
| UI `merge_panel` → `merge_engine` | 키 기반 relational join | chat integrate와 **분리** |

핵심 문제:

1. `SUPPORTED_OPERATIONS`에 join/concat 등이 선언되어 있으나 **엔진은 `aggregate_merge`만 실행**한다.
2. `_sanitize_plan` / `_sanitize_schema_against_frame`가 빈 필드를 **의미적으로 자동 완성**한다 (identifier 교집합, numeric→additive→sum, unsupported→aggregate_merge).
3. filename 기반 example/source 분리 (`정답|expected|golden|통합결과`)가 있다.
4. integrate 성공 후 **single-file AnalysisPlan으로 자동 handoff 없음**.
5. Plan Validator(실행 전 composition)가 거의 없고, Result Validator만 `aggregate_merge` 중심이다.

목표 아키텍처:

```text
Multiple Excel Files
→ File-level Data Understanding
→ Cross-file Relationship Understanding
→ LLM Integration Planner
→ IntegrationPlan (atomic steps)
→ Integration Plan Validator
→ Deterministic Integration Executor
→ Integration Result Validator
→ (optional) single-file AnalysisPlan Pipeline
→ Interpreter
```

원칙: LLM이 관계를 판단하고, Python은 관측 metadata + deterministic 실행 + validation/feedback만 담당한다.

---

## 2. Current Multi-file Architecture

### 패키지 맵 (확인됨)

```text
ui/chat.py                    _run_multi_prompt (≥2 frames)
core/routing/route_multi.py   route_multi_prompt
core/integrate/
  integrate_pipeline.py       looks_like_*, split_sources_and_examples, run/try_integrate
  schema_infer.py             build_frame_inventory, infer_schemas, schema sanitizer
  plan_builder.py             build_execution_plan, _sanitize_plan
  plan_engine.py              execute_plan → aggregate_merge only
  plan_validate.py            validate_integrate_result (post-exec)
  plan_types.py               FileSchema, ExecutionPlan, ValidationReport, IntegrateResult
core/io/merge_engine.py       UI join merge (chat path 비경유)
core/analysis/analyzer.py     run_multi_analysis → PandasAI
```

### 현재 Integration 추상화

- 타입명: `ExecutionPlan` (향후 `IntegrationPlan`으로 진화 권장; AnalysisPlan과 클래스 병합 금지)
- 사실상 monolith op: `aggregate_merge` = rename → classify rows → concat details → groupby sum → rebuild subtotals
- 선언만 존재하는 ops: `join`, `concat`, `select`, `filter`, `groupby`, `aggregate`, … (`plan_types.SUPPORTED_OPERATIONS`)

---

## 3. Actual Execution Flow

```text
User Prompt + N named DataFrames (N≥2)
  │
  ├─[UI] ui/chat._run_multi_prompt
  │     sanitize_dataframe (deterministic)
  │
  └─[Router] route_multi_prompt                          [heuristic gates]
        │
        ├─ A. System/Data (deterministic)
        │     summary / missing_rows / quality / schema_compare
        │
        ├─ B. Legacy multi aggregate (heuristic keyword)
        │     detect_aggregate_op → build_multi_context_aggregate_table
        │
        ├─ C. Structural integrate? looks_like_structural_integrate
        │     │  (keyword: 통합/병합/merge/…)
        │     ▼
        │   try_integrate_pipeline
        │     1. split_sources_and_examples     [filename heuristic]
        │     2. infer_schemas (LLM) + schema sanitize [numeric→additive etc.]
        │     3. build_execution_plan (LLM) + _sanitize_plan [semantic autocomplete]
        │     4. execute_plan → aggregate_merge only   [deterministic]
        │     5. validate_integrate_result             [deterministic result checks]
        │     6. run_plan_retries(max_retries=1)       [thin retry, no repair/family]
        │     └─ success → replace_selection / operation_result
        │        (NO AnalysisPlan handoff)             [확인됨]
        │
        └─ D. Fallback: run_multi_analysis
              heuristics (value_match / list / condition) → chat_multi (PandasAI)
```

별도 UI 경로:

```text
ui/merge_panel → infer_common_keys → merge_named_frames  [deterministic join]
```

### 단계 분류 요약

| 단계 | 모듈·함수 | 종류 |
|---|---|---|
| multi 진입 | `ui/chat._run_multi_prompt` | UI |
| 라우팅 | `route_multi.route_multi_prompt` | heuristic 분기 |
| 요약/품질/스키마 | summary/quality/schema_compare | deterministic (+스키마 LLM 가능) |
| 레거시 집계 | `detect_aggregate_op` + `build_multi_context_aggregate_table` | heuristic + deterministic |
| integrate 의도 | `looks_like_structural_integrate` | keyword heuristic |
| example 분리 | `split_sources_and_examples` | **filename heuristic** |
| schema | `infer_schemas` | LLM + Python sanitize |
| plan | `build_execution_plan` | LLM + Python sanitize |
| execute | `execute_plan` / `_execute_aggregate_merge` | deterministic |
| validate | `validate_integrate_result` | deterministic (result-only) |
| multi analysis | `run_multi_analysis` / `chat_multi` | fallback LLM |
| UI join | `merge_engine` | deterministic primitive |

---

## 4. LLM vs Deterministic Responsibility

| Decision | 현재 담당 (확인됨) | 문제 여부 |
|---|---|---|
| 파일 schema 의미 (id/label/additive) | LLM → Python sanitize 보완 | **문제:** sanitize가 semantic 대체 |
| identifier 후보 | LLM → 비면 `_guess_identifier_columns` (nunique) | **문제:** uniqueness≠identifier |
| additive column | LLM → 비면 **numeric dtype 전부** | **문제:** rate/score/id numeric도 sum 대상 가능 |
| integration operation | LLM → unsupported면 **`aggregate_merge` 강제** | **심각** |
| group key | LLM → 비면 identifier **교집합** | **문제:** join key와 혼동·자동 선택 |
| aggregations | LLM → 비면 additive→**전부 sum** | **문제** |
| join key (UI) | `infer_common_keys` (공통 컬럼/이름 heuristic) | chat integrate와 분리; semantic 자동 |
| 실제 merge/concat/groupby | Python engine | OK (deterministic) |
| plan-time composition validation | 거의 없음 | **갭** |
| result validation | Python (`detail_sum_mismatch`, dup keys, subtotals) | OK but aggregate_merge 전용 |
| cross-file relationship model | 없음 (inventories만) | **갭** |
| integrate → AnalysisPlan | 없음 | **갭** |
| filename → example/source | `_EXAMPLE_NAME_HINTS` | **제거/약화 대상** |
| profile semantic_hints | 프롬프트 힌트 only | KEEP (강제 아님) |

### Python이 semantic decision을 대신하는 목록 (실측)

1. `plan.operation ∉ SUPPORTED` → `aggregate_merge` (`plan_builder._sanitize_plan`)
2. empty `group_keys` → identifier intersection
3. empty `aggregations` → additive columns → `sum`
4. empty additive in schema → all numeric dtypes (`schema_infer._sanitize_schema_against_frame`)
5. empty identifiers → nunique-based guess
6. empty summary labels → cell token guess (소계/합계/total)
7. empty sources → all filenames
8. `infer_common_keys`: common columns as join keys (UI)
9. `looks_like_structural_integrate`: keyword gate
10. filename `정답|expected|golden|통합결과` → example frame

> 설계 방향: 1–6은 Validator error + Planner retry로 옮기고, sanitizer는 **존재하는 컬럼만 keep / 타입 coerce** 수준으로 축소.

---

## 5. Problems / Architectural Debt

1. **Monolith op:** `aggregate_merge`가 union+aggregate+layout rebuild를 한 번에 수행 → join/union/부분 source 표현 불가.
2. **Fake operation surface:** SUPPORTED에 join/concat이 있으나 실행 시 ValueError 또는 sanitize rewrite.
3. **Sanitizer-as-planner:** incomplete plan을 “합리적으로” 완성 → single-file Phase 원칙(4: Python이 semantic 대체 금지)과 충돌.
4. **No plan-time validator:** 잘못된 relation/key를 실행 전에 체계적으로 막지 못함.
5. **Thin retry:** integrate `max_retries=1`, string errors only — repair/regenerate/family diversity 없음.
6. **Dual merge worlds:** chat integrate vs UI merge_engine — 개념·검증·observability 불일치.
7. **Filename semantics:** benchmark/demo 편의이지만 범용성·안전성 저해.
8. **No AnalysisPlan handoff:** 통합 결과를 “합친 뒤 분석”하려면 사용자 재질문/세션에 의존.
9. **Budget-shaped bias risk:** aggregate_merge + additive sum + example golden 파일이 “유사 예산 표 합산”에 최적화되어 보임 (코드에 budget if문은 없으나 abstraction이 편향).

---

## 6. Reusable Single-file Patterns

### 공유 가능 (control plane)

| 패턴 | 위치 | IntegrationPlan 적용 |
|---|---|---|
| `RetryAttempt` / `run_plan_retries` | `core/common/plan_retry.py` | 그대로 |
| `ValidationIssue` / `ValidationReport` | `core/integrate/plan_types.py` | 공유; 장기적으로 `core/common/validation.py` 이전 검토 |
| plan/result 2-phase validation | analysis_pipeline | Integration에도 동일 분리 |
| `normalize_plan_signature` | analysis_plan_contract | Integration step signature 변형 |
| `operation_family_signature` + diversity feedback | Phase 12 | join vs union vs aggregate_merge family |
| `choose_retry_mode` repair/regenerate | Phase 11 | incomplete fields=repair; wrong relation=regenerate |
| structured feedback (category/invariant/previous plan) | format_plan_validation_feedback | 복제 패턴 |
| Interpreter = explain only | analysis_interpret | 통합 후 분석 결과에 재사용 |

### 재사용하면 안 되는 것 (analysis-specific)

- `filter_vs_mean` / `compare_groups` / ranking composition rules
- grain_hint entity ranking detectors
- AnalysisPlan atomic ops compile redirects
- AnalysisPlan과 IntegrationPlan 단일 클래스 병합

### 권장 관계

```text
IntegrationPlan (cross-file structure)
  → produces Integrated DataFrame (+ provenance)
  → try_analysis_pipeline / AnalysisPlan (single-file analytics)
```

코드 복사보다 **control-plane 추상화 공유 + data-plane 분리**.

---

## 7. Proposed Cross-file Understanding Model

### 원칙

- Python: **관측 가능한 deterministic metadata**만 계산
- LLM: metadata + column samples/roles로 **relationship 판단**
- Python이 relationship을 자동 확정하지 않음 (후보 제시는 OK, 선택 강제는 NG)

### File-level inventory (기존 `build_frame_inventory` 확장)

```json
{
  "source": "orders.xlsx",
  "row_count": 1200,
  "columns": [
    {
      "name": "customer_id",
      "dtype": "categorical",
      "null_ratio": 0.0,
      "unique_ratio": 0.4,
      "sample_values": ["C1", "C2"],
      "role_hints": ["identifier_candidate"]
    }
  ]
}
```

role_hints는 single-file과 같이 **optional hint** (강제 금지).

### Cross-file relation candidates (신규, Phase 14)

최소 contract 제안:

```json
{
  "left": "customers.xlsx",
  "right": "orders.xlsx",
  "pair_stats": [
    {
      "left_column": "customer_id",
      "right_column": "customer_id",
      "name_similarity": 1.0,
      "dtype_compatible": true,
      "left_unique_ratio": 1.0,
      "right_unique_ratio": 0.35,
      "value_overlap_ratio": 0.91,
      "estimated_cardinality": "one_to_many",
      "null_ratio_left": 0.0,
      "null_ratio_right": 0.02
    }
  ],
  "schema_similarity": 0.22,
  "shared_column_names": ["customer_id"],
  "row_counts": {"left": 100, "right": 1200}
}
```

LLM output (별도, planner 입력/중간):

```json
{
  "left": "customers.xlsx",
  "right": "orders.xlsx",
  "relationship": "join_candidate",
  "key_pairs": [["customer_id", "customer_id"]],
  "cardinality": "one_to_many",
  "confidence": 0.92,
  "rationale": "..."
}
```

**평가:** 예시는 적절. 다만 Phase 14에서는 Python이 `relationship`/`confidence`를 쓰지 않고 **pair_stats만** 제공하고, LLM이 relationship을 쓰도록 하는 편이 single-file 원칙과 일치한다.  
`confidence`를 Python이 만들면 다시 heuristic decision이 되므로, confidence는 LLM 또는 생략.

### Schema similarity

- column name overlap, dtype profile distance, role_hint overlap — **metrics only**
- “same schema → must union” 자동 결정 금지

---

## 8. IntegrationPlan v1 Contract

### 권장 형태 (steps[] composition)

```json
{
  "steps": [
    {
      "op": "select_source",
      "sources": ["file_a.xlsx", "file_b.xlsx"],
      "as": "S"
    },
    {
      "op": "rename_columns",
      "input": "file_a.xlsx",
      "mapping": {"CustID": "customer_id"},
      "as": "A"
    },
    {
      "op": "union_rows",
      "inputs": ["A", "B"],
      "column_alignment": "by_name",
      "as": "U"
    },
    {
      "op": "aggregate",
      "input": "U",
      "group_by": ["customer_id"],
      "metrics": [{"column": "amount", "fn": "sum"}],
      "as": "out"
    }
  ],
  "output": "out",
  "criteria_note": "",
  "interpret": false
}
```

### 기존 `ExecutionPlan`과의 관계

- v1에서 `ExecutionPlan.aggregate_merge`는 **compile sugar** 또는 fallback-only로 강등
- 새 이름: `IntegrationPlan` (AnalysisPlan과 병존)
- `FileSchema`는 file-understanding 산출물로 유지하되 sanitize autocomplete 축소

### Safe failure

관계가 불확실하거나 key_pairs가 비고 ambiguity가 크면:

```text
Plan Validator → semantic_ambiguity / unsafe_relation
→ safe failure (PandasAI로 억지 merge 금지)
```

---

## 9. Atomic Operations (IntegrationPlan v1 최소 집합)

### v1에 포함할 것 (최소·충분)

| op | 용도 | 최소 contract |
|---|---|---|
| `select_source` | 일부 파일만 사용 | `sources[]`, `as` |
| `rename_columns` | 정렬 전 이름 정규화 | `input`, `mapping`, `as` |
| `union_rows` | 동형/유사 스키마 세로 결합 | `inputs[]`, `column_alignment`, `as` |
| `join` | 이형 스키마 가로 결합 | `left`, `right`, `on`/`left_on`/`right_on`, `how`, `as` |
| `aggregate` | union 후 키 합산 등 | `input`, `group_by`, `metrics[{column,fn}]`, `as` |
| `select_columns` | 출력 축소 | `input`, `columns`, `as` |
| `filter_rows` | 통합 전/후 행 필터 (optional v1.1) | single-file과 유사 numeric/column filters |
| `sort` | 출력 정렬 (optional) | `input`, `by`, `as` |

### v1에서 제외/연기

| op | 이유 |
|---|---|
| `derive_column` | 유용하나 범위 확대; Phase 15 후반 |
| `aggregate_merge` monolith | sugar로만; 원자 분해 후 deprecated |
| `insert_subtotal` / `grand_total` | layout sugar; AnalysisPlan/후처리로 |
| `export_workbook` | IO side-effect; pipeline 밖으로 |

### 시나리오 매핑

| 시나리오 | steps |
|---|---|
| 같은 schema 파일 | `union_rows` |
| monthly inventory 합산 | `union_rows` → `aggregate` |
| customers + orders | `join` |
| 일부 source만 | `select_source` → `join`/`union_rows` |
| 관계 불확실 | safe failure |
| 유사 budget 표 (사례일 뿐) | `rename` → `union_rows` → `aggregate` (+ optional layout 후처리) |

### `aggregate_merge` 분해 (답 B 선반영)

```text
aggregate_merge
  ≈  per-source: rename_columns → filter/classify detail rows
  →  union_rows
  →  aggregate(group_by=keys, metrics=sums)
  →  (optional) layout/derived summary rows  — v1 밖 또는 별도 sugar
```

---

## 10. Integration Plan Validator Invariants

**원칙:** invalid → issues → feedback → Planner retry. **plan을 고치지 않음.**

### Source / schema

- source 이름 존재
- 참조 column 존재 (rename 전/후 명확히)
- intermediate `as` dependency 순서
- rename collision / overwrite

### Join

- key columns 존재
- dtype compatibility (관측)
- null ratio warning threshold (error vs warning 정책 고정 필요)
- uniqueness / estimated cardinality vs declared how
- many-to-many → error 또는 high-severity warning (기본: unsafe unless criteria_note)
- duplicate amplification estimate (optional metadata)

### Union

- schema compatibility (required columns, dtype family)
- missing columns policy (`error` | `align_with_nulls`는 plan에 명시)
- semantic mismatch warning (role_hints 충돌) — 자동 수정 금지

### Aggregate

- group_by / metric columns 존재
- fn ∈ allowlist
- non-numeric에 sum 금지
- additive는 **plan이 명시한 것만** (numeric 자동 추정으로 통과시키지 않음)

### Composition

- empty plan / unknown op
- output missing
- meaningless chains (union of one, join without on)
- mixing incompatible families without intermediate
- ambiguity: multiple plausible key_pairs and plan picks none

Plan-time validator는 Phase 16에서 AnalysisPlan의 `validate_analysis_plan` 패턴을 따른다.

---

## 11. Integration Result Validator Invariants

기존 `validate_integrate_result`에서 **KEEP 가능한 것:**

| 기존 체크 | 재사용 |
|---|---|
| `detail_sum_mismatch` | aggregate 결과에 일반화 |
| `duplicate_keys` (post-aggregate) | KEEP |
| subtotal consistency | layout sugar 시에만 |
| label conflict warnings | KEEP as warning |
| missing_group_keys / missing columns | plan-time으로 이동 권장 |

### 신규/일반화 Result checks

```text
unexpected row amplification (join)
row loss vs declared how
join match coverage / unmatched left-right ratio
duplicate key explosion
aggregate total conservation (per metric)
null increase beyond input
schema/column loss vs select_columns
column collision after join
provenance loss (optional _source column)
```

**분리:**

- Plan Validator = 실행 가능·안전한 계획인가
- Result Validator = 실행 결과가 계획/통계적으로 이상한가

`MergeReport` (`merge_engine`)의 match_rate / missing samples는 Result Validator 입력으로 **재사용 가능**.

---

## 12. KEEP / REFACTOR / FALLBACK Classification

| 기능 | 분류 | 이유 |
|---|---|---|
| `build_frame_inventory` | **KEEP** | deterministic metadata |
| `semantic_hints_text` (profile) | **KEEP** | hint only |
| `ValidationReport` / `ValidationIssue` | **KEEP** | 공유 control plane |
| `run_plan_retries` | **KEEP** | 공유 |
| `classify_rows` / row types | **KEEP** (file understanding) | structural metadata |
| `merge_engine.merge_named_frames` | **KEEP as primitive** | deterministic join 구현체 |
| `infer_common_keys` 자동 확정 | **REFACTOR** | 후보 제시는 OK, 자동 선택→Validator/Planner로 |
| `aggregate_merge` engine | **REFACTOR → FALLBACK sugar** | atomic으로 분해 |
| `_sanitize_plan` semantic fill | **REMOVE / 대폭 약화** | 최우선 제거 대상 |
| numeric→additive fallback | **REMOVE / 약화** | semantic 대체 |
| identifier nunique guess 자동 채움 | **REMOVE / 약화** | 후보 metadata만 |
| filename example/source split | **REMOVE or FALLBACK-only (tests)** | filename semantic decision |
| `looks_like_structural_integrate` keywords | **REFACTOR** | router는 넓히고 Planner가 판단; 또는 analytical multi도 Integration/Analysis로 |
| legacy `build_multi_context_aggregate_table` | **FALLBACK ONLY** | single-file과 동일 철학 |
| multi PandasAI (`chat_multi`) | **FALLBACK ONLY** | last resort |
| UI merge_panel | **KEEP UI**; 엔진은 Integration join과 정렬 | |

판단 기준: 임의 Excel 다중 파일의 안전 이해/통합에 도움이 되는가 — 예산 샘플 성공이 아님.

---

## 13. Expected File Changes (Phase 14+)

| 파일 | 현재 역할 | 문제 | 향후 역할 | 규모 |
|---|---|---|---|---|
| `core/integrate/plan_types.py` | ExecutionPlan monolith | ops 선언≠실행 | IntegrationPlan + step types | 중 |
| `core/integrate/schema_infer.py` | LLM schema + heavy sanitize | semantic autocomplete | inventory + light sanitize; relation stats | 중 |
| `core/integrate/plan_builder.py` | LLM aggregate_merge plan | sanitize rewrite | IntegrationPlanner (steps) | 대 |
| `core/integrate/plan_engine.py` | aggregate_merge only | 비범용 | atomic executor | 대 |
| `core/integrate/plan_validate.py` | result-only | plan-time 부재 | plan + result validators | 중~대 |
| `core/integrate/integrate_pipeline.py` | thin orchestrator | retry 빈약 | analysis_pipeline 패턴 이식 | 중 |
| `core/routing/route_multi.py` | keyword gates | Planner 우회 | Integration → optional AnalysisPlan handoff | 중 |
| `core/io/merge_engine.py` | UI join | 자동 key | join primitive + reports | 소~중 |
| `core/analysis/analyzer.py` | multi→PandasAI | Planner 없음 | handoff 소비측 유지 | 소 |
| `core/analysis/*` | single-file engine | — | **안정 유지**; handoff 입력만 | 최소 |
| `core/common/plan_retry.py` | shared retry | — | KEEP | 소 |
| `tests/test_integrate_*` | aggregate_merge gold | budget-shaped | generic multi fixtures | 중 |
| 신규 `tests/benchmark/multi_*` | 없음 | — | Phase 19 | 대 |
| 신규 `core/integrate/relation_stats.py` | 없음 | — | pair_stats | 중 |

**single-file `core/analysis/*`는 가능한 한 변경 최소화.**

---

## 14. Phase 14~19 Roadmap

### Phase 14 — Cross-file Data Understanding

- **목표:** file inventory + pair_stats (relationship 확정 없이)
- **변경:** `schema_infer` 정리, `relation_stats` 추가, sanitize autocomplete 축소 시작
- **완료 조건:** 임의의 2+ frames에 대해 deterministic pair_stats JSON 생성; LLM schema는 hint
- **테스트:** inventory/pair_stats unit tests (synthetic customers/orders, monthly clones)

### Phase 15 — IntegrationPlan v1

- **목표:** steps[] contract + Planner LLM + compile sugar(`aggregate_merge`→atoms)
- **변경:** `plan_types`, `plan_builder`; sanitizer는 column existence만
- **완료 조건:** union / join / aggregate plans가 JSON으로 표현·파싱
- **테스트:** contract/parse tests; mock planner fixtures

### Phase 16 — Integration Plan Validator

- **목표:** 실행 전 invariants; feedback; repair/regenerate
- **변경:** `plan_validate` 분리(plan vs result), contract signatures/families
- **완료 조건:** incomplete/wrong-key plans blocked; no silent rewrite
- **테스트:** validator unit matrix (join/union/aggregate)

### Phase 17 — Deterministic Integration Executor

- **목표:** atomic ops 실행; `merge_engine`을 join primitive로 연결
- **변경:** `plan_engine` rewrite; aggregate_merge = compile path
- **완료 조건:** customers⋈orders, monthly union+agg fixtures pass without domain rules
- **테스트:** executor golden (generic schemas)

### Phase 18 — Result Validator + Retry Recovery

- **목표:** amplification/coverage/sum conservation; Phase 12-style diversity
- **변경:** result validator 확장; pipeline retry 강화; optional AnalysisPlan handoff
- **완료 조건:** unsafe join safe-fails; recoverable plans retry_success
- **테스트:** amplification / mismatch / retry mock

### Phase 19 — Multi-file Benchmark

- **목표:** domain-agnostic suite + KPIs
- **변경:** datasets/cases/runner; no expected hardcoding shortcuts
- **완료 조건:** suite docs + baseline live run; budget는 한 family일 뿐
- **테스트:** benchmark harness + CI smoke (deterministic mocks)

순서 조정 여지: Plan Validator(16)를 Executor(17)보다 약간 앞서 두는 현재 순서가 single-file 교훈과 맞음.

---

## 15. Multi-file Benchmark Proposal

### Dataset families (budget 편향 방지)

```text
sales_monthly_files          # same schema union
inventory_snapshots          # union → aggregate by sku
customers_orders             # one-to-many join
products_sales               # join + aggregate
survey_batches               # union
sensor_batches               # union / time align (later)
hr_master_department         # join
different_schema             # should safe-fail or explicit map
ambiguous_keys               # safe_failure_accuracy
many_to_many                 # amplification detection
dirty_files                  # headers/summary rows
similar_budget_files         # ONE family among many — not the center
```

### Case categories

- correct union / join / aggregate composition
- select_source subset
- ambiguous relation → safe failure
- wrong key temptation
- many-to-many danger
- schema mismatch
- dirty summary rows
- integrate then analyze (handoff)

### KPIs (overall 외에)

```text
integration_direct
safe_failure_accuracy
wrong_relation
wrong_join_key
wrong_operation
duplicate_amplification
retry_success
retry_exhausted
fallback_rate
pandasai_fallback_rate
handoff_analysis_direct   # optional
```

overall_ok 정의를 몰래 바꾸지 말고 보조 KPI로 병행 (Phase 12 `safe_ambiguity_rate`와 동일 철학).

---

## 16. Final Recommendation

1. Phase 14부터 **metadata-first understanding**으로 시작하고, sanitizer semantic autocomplete를 즉시 약화한다.
2. `IntegrationPlan`은 atomic steps; `aggregate_merge`는 sugar/fallback.
3. `merge_engine`은 제거하지 말고 **join primitive + MergeReport**로 흡수한다.
4. AnalysisPlan control plane을 이식하고, data plane은 분리한다.
5. integrate 성공 후 **명시적 AnalysisPlan handoff**를 Phase 18에 넣는다.
6. multi PandasAI / legacy multi aggregate는 fallback only.
7. single-file `core/analysis/*`는 안정 유지.

### A. 가장 먼저 제거/약화할 semantic heuristic

**`_sanitize_plan` + `_sanitize_schema_against_frame`의 의미적 자동 완성**  
특히:

1. unsupported/missing operation → `aggregate_merge`
2. empty aggregations → 모든 additive/numeric `sum`
3. empty group_keys → identifier intersection 자동 선택
4. empty additive → 모든 numeric dtype

부차: filename 기반 `split_sources_and_examples`.

### B. `aggregate_merge` 분해

```text
rename_columns → (detail filter/classify) → union_rows → aggregate → (optional layout sugar)
```

monolith 실행 경로를 유지하더라도 **내부적으로 위 composition으로 compile**하고, Planner 표면에서는 atomic steps를 기본으로 한다.

### C. `merge_engine`

**제거하지 말 것.**  
`join` atomic op의 deterministic 구현 + `MergeReport`(coverage/amplification)로 재사용.  
다만 `infer_common_keys`의 **자동 키 확정**은 Planner/Validator 앞으로 옮기고, 엔진은 명시된 keys만 조인.

### D. IntegrationPlan vs AnalysisPlan

| 공유 | 분리 |
|---|---|
| ValidationReport, retry loop, signature/family, repair/regenerate, feedback shape, interpreter-after-validate | op vocabulary, composition invariants, schema (FileSchema/relations vs column inventory), executor |
| handoff: Integration output DF → AnalysisPlan | 단일 God-Plan 클래스 금지 |

### E. Phase 14 전에 contract로 고정할 것

1. **IntegrationPlan v1 atomic op 목록과 JSON shape** (본 문서 §8–9)
2. **Python metadata vs LLM decision 경계** (pair_stats는 Python; relationship 선택은 LLM; sanitizer는 existence/coerce만)
3. **Plan Validator vs Result Validator 책임 분리**
4. **safe failure 정책** (ambiguous/many-to-many/default)
5. **aggregate_merge = sugar/fallback** 선언 (신규 plan의 기본 op 아님)
6. **filename으로 integration semantics 결정 금지**
7. **성공 경로 handoff:** integrated DF → optional AnalysisPlan (시점에 명시)

---

## Appendix — 확인됨 vs 설계 제안

| 항목 | 상태 |
|---|---|
| multi AnalysisPlan Planner 미구현 | 확인됨 (`route_multi` 주석, `run_multi_analysis` docstring) |
| execute는 aggregate_merge만 | 확인됨 (`plan_engine.execute_plan`) |
| sanitizer rewrite 목록 | 확인됨 (`plan_builder._sanitize_plan`) |
| integrate → AnalysisPlan 자동 연결 없음 | 확인됨 |
| merge_engine은 UI 경로 | 확인됨 |
| IntegrationPlan steps DSL | **설계 제안** (미구현) |
| pair_stats contract | **설계 제안** |
| Phase 14–19 일정 | **설계 제안** |

---

*End of Phase 13 design note. No production IntegrationPlan executor was implemented in this phase.*
