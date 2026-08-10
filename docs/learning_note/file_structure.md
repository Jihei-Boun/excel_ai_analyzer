# 프로젝트 파일 구조

Excel AI Analyzer의 디렉터리·모듈 역할을 Phase 5~12 기준으로 정리한 학습 노트입니다.  
UI(`ui/`)와 도메인 로직(`core/`)을 분리하고, 단일 파일 분석은 **AnalysisPlan 파이프라인 우선**입니다.

---

## 1. 전체 트리

```text
excel_ai_analyzer/
├── app.py                 # Streamlit 진입점
├── core/                  # 도메인·분석 로직 (UI 비의존)
├── ui/                    # Streamlit 화면
├── profiles/              # YAML 도메인 프로필·컬럼 힌트
├── tests/                 # 단위·라우팅·Phase 회귀 + benchmark/
├── docs/                  # README 이미지 · learning_note · report 이미지
├── data/uploads/          # 업로드 임시 저장
├── exports/               # charts/ · merges/
├── scripts/               # clean_artifacts.sh 등
├── .streamlit/            # 포트·테마 config
└── .github/workflows/     # CI (pytest)
```

| 경로 | 역할 |
|------|------|
| `app.py` | 세션·스타일·사이드바·워크스페이스·채팅 조립 |
| `core/` | 라우팅, LLM, AnalysisPlan, 집계, 통합, I/O |
| `ui/` | 업로드·미리보기·병합·채팅·session_state |
| `profiles/` | generic/budget/sales/inventory 등 + 컬럼 힌트 |
| `tests/` | pytest · `tests/benchmark/` (42 cases live/deterministic) |
| `docs/learning_note/` | Phase 노트 · 본 문서 |
| `docs/images/report/` | 보고용 다이어그램·성능 그래프 |

---

## 2. `core/` 패키지 한눈에

```text
core/
├── constants.py, profile_loader.py, llm_client.py
├── aggregates.py, code_guardrails.py, suggest_prompts.py
├── analysis/     # AnalysisPlan 파이프라인 (주 경로)
├── routing/      # 의도 판별 · 단일/다중 분기
├── integrate/    # 다중 파일 구조화 통합
├── io/           # 엑셀 로드 · 정규화 · 병합 · export
├── schema/       # 품질 · 스키마 · 행 분류 · 컬럼 매칭
├── filter/       # 값/조건/결측 필터
├── display/      # 표·리스트·차트 후처리
├── summary/      # 파일 요약 (일반·예산)
├── pai/          # PandasAI / Ollama 어댑터 (최후 폴백)
└── common/       # plan retry · locale
```

---

## 3. 루트 모듈 (`core/`)

| 파일 | 역할 |
|------|------|
| `__init__.py` | `core.*` 패키지 진입점 |
| `constants.py` | 경로, Ollama/PandasAI 기본값, 예산 프로필 재노출 |
| `profile_loader.py` | `profiles/*.yaml` 로더 · 활성 프로필 ContextVar |
| `llm_client.py` | Ollama chat — `chat_json` / `chat_text` |
| `aggregates.py` | 집계·리스트 시드·스칼라→표 · multi context 집계 헬퍼 |
| `code_guardrails.py` | LLM 생성 코드 정적 검사 (자동 치환 없이 재생성 지침) |
| `suggest_prompts.py` | 업로드 컬럼 기반 추천 질문 |

---

## 4. `analysis/` — AnalysisPlan 파이프라인

자연어 → **LLM 계획 → 검증 → 결정론 실행 → 결과 검증 → 해석**.  
단일 파일 분석의 **주 경로**입니다.

| 파일 | 역할 |
|------|------|
| `analyzer.py` | `run_analysis` / `run_multi_analysis` 진입점 · fallback 계층 |
| `analysis_pipeline.py` | Planner → Executor → Validator → Interpreter 오케스트레이션 |
| `analysis_plan_types.py` | `AnalysisPlan` / `AnalysisStep` · 허용 op · derive 식 |
| `analysis_plan_contract.py` | Planner system prompt · operation contract · few-shot |
| `analysis_plan_builder.py` | LLM으로 AnalysisPlan JSON 생성 |
| `analysis_plan_compile.py` | 고수준 operation → 원자 steps 컴파일 |
| `analysis_plan_sanitize.py` | step을 허용 op·실제 컬럼으로 sanitize |
| `analysis_column_prefs.py` | 컬럼명 safety 정규화만 (의미 rewrite 없음, Phase 5) |
| `analysis_plan_validate.py` | 실행 전 Plan Validator |
| `analysis_executor.py` | 원자 step 결정론 실행기 |
| `analysis_result_validate.py` | 실행 후 Result Validator (+ semantic soft retry) |
| `analysis_validate.py` | plan/result validate facade |
| `analysis_interpret.py` | 계산 수치만 근거로 자연어 해석 |
| `legacy_fallback.py` | `legacy_simple_groupby_fallback` (단순 X별 Y만) |
| `analysis_ops.py` | ops facade |
| `ops_filters.py` | 행/열 필터·프로젝션 |
| `ops_aggregate.py` | 집계·순위·평균 대비 필터 |
| `ops_stats.py` | 그룹 비교·분포·상관 |

---

## 5. `routing/` — 의도·라우팅

Router는 **분석 방법(비교/비율/순위)을 고르지 않습니다.**  
System/Data 명령과 chart-only만 가로채고, 나머지는 `run_analysis`로 보냅니다.

| 파일 | 역할 |
|------|------|
| `prompt_intent.py` | 의도·출력 타입·집계/리스트/차트 판별 |
| `prompt_router.py` | 라우팅 공개 API (재수출, Streamlit 비의존) |
| `route_types.py` | `SingleRouteOutcome` 등 |
| `route_helpers.py` | 차트 컨텍스트·표 후처리·필터 메타 |
| `route_single.py` | 단일 파일/시트 라우팅 |
| `route_multi.py` | 다중 파일/시트 라우팅 |

---

## 6. 그 밖의 `core/` 하위 패키지

### `integrate/` — 다중 파일 구조화 통합

| 파일 | 역할 |
|------|------|
| `integrate_pipeline.py` | 스키마 → 계획 → 엔진 → 검증 · export |
| `schema_infer.py` | LLM 스키마 추론 (열 이름 하드코딩 없음) |
| `plan_types.py` | `ExecutionPlan` / `FileSchema` / `ValidationReport` |
| `plan_builder.py` | LLM 실행 계획 |
| `plan_engine.py` | 허용 연산만 실행 (`aggregate_merge` 등) |
| `plan_validate.py` | 통합 결과 검증 |

### `io/` — 입출력

| 파일 | 역할 |
|------|------|
| `excel_loader.py` | 엑셀 → DataFrame · 다중 헤더 정리 |
| `normalize.py` | 컬럼명·타입·키 후보 정규화 |
| `text_normalize.py` | 텍스트 정규화·키워드 매칭 |
| `merge_engine.py` | 다중 DF 비교·병합 + `MergeReport` |
| `export_utils.py` | 결과 xlsx bytes/파일 |

### `schema/` — 표 구조 이해

| 파일 | 역할 |
|------|------|
| `column_match.py` | 프롬프트 ↔ 컬럼 매칭 |
| `row_classify.py` | detail / subtotal / total / footer / blank |
| `schema_compare.py` | 행·열·공통 컬럼·컬럼 의미 (LLM±규칙) |
| `schema_hints.py` | 분석용 스키마 힌트 (강제 rewrite 금지) |
| `quality.py` | 품질 진단 |

### `filter/` — 값·조건 필터

| 파일 | 역할 |
|------|------|
| `value_filter.py` | facade 재수출 |
| `value_match.py` | exact retrieval (single-file: Planner 이후) |
| `filter_context.py` | 필터 소스·맥락 라벨 |
| `condition_filter.py` | 조건형 행 필터 (single-file 주경로 제거, multi·테스트용) |
| `missing_rows.py` | 결측 행 조회 |

### `display/` · `summary/` · `pai/` · `common/`

| 패키지 | 핵심 파일 | 역할 |
|--------|-----------|------|
| `display/` | `result_format`, `list_display`, `result_order`, `chart_utils` | 표·리스트·차트 |
| `summary/` | `file_summary`, `generic_summary`, `budget_summary`, builders | 규칙 기반 파일 요약 |
| `pai/` | `pandasai_config/setup/frame/result` | PandasAI 최후 폴백 |
| `common/` | `plan_retry`, `locale_support` | 재시도 루프 · locale |

---

## 7. `ui/` · `profiles/` · `tests/`

### `ui/`

| 파일 | 역할 |
|------|------|
| `session_store.py` | session_state 초기화 |
| `sidebar.py` | Ollama · 프로필 · 분석 대상 |
| `upload.py` / `preview.py` / `workspace.py` | 업로드 · 미리보기 · 중앙 레이아웃 |
| `merge_panel.py` | 다중 파일 비교·병합 UI |
| `chat.py` / `chat_panel.py` | 채팅 요청 처리 · 패널 |
| `file_state.py` | 업로드·분석 대상 state API |
| `display.py` / `header.py` / `styles.py` | 표시 헬퍼 · 헤더 · 스타일 |

### `profiles/`

| 파일 | 용도 |
|------|------|
| `generic.yaml` / `generic_en.yaml` | 기본 · 영어 locale |
| `budget.yaml` | 예실대비표 (예산 모드) |
| `sales.yaml` / `inventory.yaml` / `custom.yaml` | 도메인 샘플 · 템플릿 |
| `column_hints.yaml` / `column_meanings.yaml` | 범용 컬럼 역할·의미 힌트 |

### `tests/`

| 경로 | 역할 |
|------|------|
| `test_*.py` | 단위·라우팅·Phase 5~12 회귀 |
| `benchmark/` | 10 도메인 · **42** 자연어 질문 live/deterministic harness |
| `benchmark/cases/*.yaml` | budget, sales, inventory, orders, hr, sensor, survey, dirty, ambiguous, negative |
| `benchmark/runner.py` · `evaluate.py` · `metrics.py` | 실행 · 채점 · KPI |
| `fixtures/` | 통합 골드 샘플 등 |

---

## 8. 현재 데이터 흐름 (Phase 5+)

### 단일 파일

```text
사용자 프롬프트
  └─ routing/route_single
       ├─ [규칙] 요약 / 결측 / 품질 / 스키마·메타
       ├─ [규칙] Chart-only (표 없이 차트만)
       └─ analysis/analyzer.run_analysis
            ├─ [규칙±LLM] 컬럼 의미 · 요약 · chart-only
            ├─ [하이브리드] AnalysisPlan Pipeline
            │     Planner → Plan Validator → Executor
            │     → Result Validator → Interpreter
            ├─ [규칙] value_match / list_seed   ← lightweight retrieval
            ├─ [규칙] legacy_simple_groupby     ← 단순 X별 Y만
            └─ [LLM] PandasAI                   ← 최후 폴백
                 └─ display/  표·차트 후처리
```

### 다중 파일

```text
route_multi
  ├─ [규칙] 요약 / 결측 / 품질 / 스키마
  ├─ [하이브리드] integrate/  (통합·병합 힌트 시)
  └─ run_multi_analysis
       ├─ 기존 fallback (context aggregate · condition · value_match · list)
       └─ PandasAI chat_multi
     ※ multi-file AnalysisPlan Planner는 아직 미구현
```

입출력은 `io/`, 도메인 힌트는 `profile_loader` + `profiles/*.yaml`,  
LLM 호출은 `llm_client` / `pai/`가 담당합니다.

---

## 9. 라우터 · `run_analysis` 우선순위

### `route_single` (Router)

| 순서 | 기능 | 경로 | 모듈 |
|------|------|------|------|
| 1 | 파일 요약 | 규칙 | `summary/file_summary.py` |
| 2 | 결측 행 | 규칙 | `filter/missing_rows.py` |
| 3 | 품질 진단 | 규칙 | `schema/quality.py` |
| 4 | 스키마·메타 | 규칙 | `schema/schema_compare.py` |
| 5 | Chart-only | 규칙 | `display/chart_utils.py` |
| 6 | 그 외 분석 | → `run_analysis` | `analysis/analyzer.py` |

> Router → groupby/context 선점은 제거됨 (Phase 2/5).  
> 비교·비율·순위 등 **분석 방법 선택은 Planner**가 합니다.

### `run_analysis` (단일 파일)

| 순서 | 기능 | 경로 |
|------|------|------|
| 1 | 컬럼 의미 설명 | LLM → 실패 시 규칙 폴백 |
| 2 | 파일 요약 / chart-only | 규칙 |
| 3 | **AnalysisPlan Pipeline** | 하이브리드 (주 경로) |
| 4 | value_match / list_seed | 규칙 retrieval |
| 5 | legacy_simple_groupby | 규칙 fallback (단순 X별 Y) |
| 6 | PandasAI | LLM 최후 폴백 |

`try_condition_row_filter` / context aggregate는 **single-file 주경로에서 제거**되어 multi·테스트·차트 헬퍼에만 남습니다.

---

## 10. LLM vs 규칙

### 규칙만

요약, 결측, 품질, 스키마·메타, chart-only, value_match, list_seed, legacy groupby, 행 분류, I/O·정규화, 결과 후처리, code_guardrails(검사만).

### 하이브리드 (LLM 계획 + 규칙 실행·검증)

| 파이프라인 | LLM | 규칙 |
|------------|-----|------|
| `analysis/` | plan JSON · interpret | sanitize/compile · executor · validate |
| `integrate/` | schema infer · plan JSON | plan_engine · plan_validate · export |

재시도: `common/plan_retry.py` (+ Phase 12 operation-family diversity).

### LLM으로만 넘어가는 경우

| 기능 | API | 모듈 |
|------|-----|------|
| 컬럼 의미 | `chat_text` | `schema_compare.explain_column_meanings` |
| 분석 계획 | `chat_json` | `analysis_plan_builder` (+ `analysis_plan_contract`) |
| 결과 해석 | `chat_text` | `analysis_interpret` |
| 통합 스키마·계획 | `chat_json` | `integrate/schema_infer`, `plan_builder` |
| 최후 분석 | PandasAI → Ollama | `pai/pandasai_config` |

---

## 11. Before → After (구조 관점)

```text
기존                         현재 (단일 파일)
────                         ────────────────
질문                         질문
 ↓                            ↓
키워드/Rule 판단              Data Understanding (라우터 system/data)
 ↓                            ↓
전용 Python 분석              LLM Planner → AnalysisPlan
 ↓                            ↓
PandasAI                      Plan Validator → Python Executor
                              ↓
                              Result Validator → LLM Interpreter
                              ↓
                              (실패 시) retrieval → legacy groupby → PandasAI
```

상세 KPI·벤치마크는 `phase6_benchmark.md` ~ `phase12_retry_diversity.md`,  
보고용 이미지는 `docs/images/report/`를 참고하세요.
