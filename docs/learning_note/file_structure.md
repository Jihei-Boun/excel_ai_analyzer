# `core/` 폴더·파일 역할

Excel AI Analyzer의 도메인 로직이 모인 패키지입니다. UI(Streamlit)와 분리되어 있으며, 하위 폴더는 책임별로 나뉩니다.

---

## 루트 (`core/`)

공통 진입점·설정 성격의 모듈입니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | `core.*` 도메인 패키지 진입점 |
| `constants.py` | 프로젝트 전역 상수 — 경로, Ollama/PandasAI 기본값, 예산 프로필 재노출 |
| `profile_loader.py` | `profiles/*.yaml` 로더 — 컬럼 힌트·의미·도메인 프로필, 활성 프로필 ContextVar |
| `llm_client.py` | Ollama chat API 클라이언트 — JSON(`chat_json`)·텍스트(`chat_text`) 응답 |
| `aggregates.py` | 집계 표·리스트 시드·스칼라→표 변환, 소스별 프레임 분할 |
| `code_guardrails.py` | LLM 생성 코드 정적 검사·결과 검증 — 자동 치환 없이 재생성 지침만 생성 |
| `suggest_prompts.py` | 업로드 DataFrame 컬럼 기반 추천 질문 생성 |

---

## `analysis/` — 채팅 분석 계획 파이프라인

자연어 질문을 **LLM 계획 → 결정론적 실행 → 검증 → 해석**으로 처리합니다. PandasAI 경로와 병행되는 구조화 분석 경로입니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 채팅 분석 계획 파이프라인 패키지 |
| `analyzer.py` | 자연어 요청을 PandasAI/구조화 파이프라인으로 실행하는 범용 분석 진입점 (`run_analysis`, `run_multi_analysis`) |
| `analysis_pipeline.py` | LLM 계획 → 실행기 → 검증 → 해석을 묶는 오케스트레이터 |
| `analysis_plan_types.py` | `AnalysisPlan`/`AnalysisStep` 타입, 허용 op·derive 식·행 타입 상수 |
| `analysis_plan_builder.py` | LLM으로 채팅 분석 JSON 계획 생성 |
| `analysis_plan_compile.py` | 고수준 operation(예: `top_n_difference`) → 원자 steps 컴파일 |
| `analysis_plan_sanitize.py` | LLM이 낸 step을 허용 op·실제 컬럼으로 sanitize |
| `analysis_column_prefs.py` | 컬럼명 safety 정규화만 (의미 rewrite 제거, Phase 5) |
| `legacy_fallback.py` | Legacy fallback 분류 · `legacy_simple_groupby_fallback` |
| `analysis_executor.py` | 원자 step만 수행하는 결정론적 실행기 (질문 해석 없음) |
| `analysis_plan_validate.py` | 실행 전 Plan Validator |
| `analysis_result_validate.py` | 실행 후 Result Validator |
| `analysis_validate.py` | plan/result validate facade |
| `analysis_interpret.py` | 계산 수치만 근거로 자연어 해석 문장 생성 |
| `analysis_ops.py` | 범용 분석 연산 facade — filters / aggregate / stats 재수출 |
| `ops_filters.py` | 행/열 필터·프로젝션 (`filter_rows`, numeric filter 등) |
| `ops_aggregate.py` | 집계·순위·평균 대비 필터 (`aggregate_groups`, `top_per_group` 등) |
| `ops_stats.py` | 그룹 비교·분포·상관 (`compare_groups`, `correlation_of_columns` 등) |

---

## `routing/` — 프롬프트 의도·라우팅

사용자 프롬프트를 단일/다중 파일 경로로 분기하고, 요약·품질·통합·집계 등 특수 요청을 먼저 처리합니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 프롬프트 의도 판별·단일/다중 라우팅 패키지 |
| `prompt_intent.py` | 의도·출력 타입·집계 연산·리스트/차트 요청 판별 |
| `prompt_router.py` | 라우팅·후처리 공개 API — 하위 모듈 재수출 (Streamlit 비의존) |
| `route_types.py` | 라우팅 공유 타입 (`SingleRouteOutcome` 등) |
| `route_helpers.py` | 차트 컨텍스트·표 후처리·필터 메타·다중 집계 소스 등 공유 헬퍼 |
| `route_single.py` | 단일 파일/시트 프롬프트 라우팅 |
| `route_multi.py` | 다중 파일/시트 프롬프트 라우팅 (통합·스키마·품질 등) |

---

## `integrate/` — 다중 파일 구조화 통합

여러 엑셀을 **스키마 추론 → LLM 계획 → 엔진 실행 → 검증**으로 병합합니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 다중 파일 구조화 통합 파이프라인 패키지 |
| `integrate_pipeline.py` | 스키마→계획→엔진→검증 오케스트레이터, export까지 포함 |
| `schema_infer.py` | LLM 기반 범용 스키마 추론 (열 이름 하드코딩 없음), frame inventory |
| `plan_types.py` | `ExecutionPlan`/`FileSchema`/`ValidationReport` 등 타입·허용 연산 |
| `plan_builder.py` | LLM 실행 계획 생성 (도메인 전용 함수 호출 없음) |
| `plan_engine.py` | 허용된 연산만 수행하는 결정론적 실행 엔진 (`aggregate_merge` 등) |
| `plan_validate.py` | 통합 결과 검증 — 잘못된 파일을 조용히 저장하지 않도록 차단 |

---

## `io/` — 입출력·정규화·병합

엑셀 로딩, 컬럼/텍스트 정규화, 단순 병합, export를 담당합니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 엑셀 로딩·정규화·병합·export 패키지 |
| `excel_loader.py` | 엑셀 → DataFrame 로드, 다중 헤더·병합 셀 헤더 정리 |
| `normalize.py` | 업로드 DataFrame 공통 정규화 — 컬럼명·타입·키 후보 |
| `text_normalize.py` | 텍스트 정규화·키워드 매칭 (`normalize_text`, `keyword_in_text`) |
| `merge_engine.py` | 다중 DataFrame 비교·병합 엔진 + `MergeReport` |
| `export_utils.py` | 분석/병합 결과 xlsx 바이트·파일 export |

---

## `schema/` — 스키마·품질·행 분류·컬럼 매칭

표 구조 이해와 컬럼/행 역할 판별에 쓰입니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 스키마 비교·품질·행 분류·컬럼 매칭 패키지 |
| `column_match.py` | 프롬프트 ↔ DataFrame 컬럼 매칭 (groupby·metric·언급 컬럼 등) |
| `row_classify.py` | 행 역할 분류 — detail / subtotal / total / footer / blank |
| `schema_compare.py` | 스키마·메타 요청 규칙 경로 (행 수, 컬럼 목록, 공통 컬럼, 컬럼 의미 설명) |
| `schema_hints.py` | 분석용 스키마 힌트 — 컬럼 의미를 LLM에 힌트로만 전달 (강제 rewrite 금지) |
| `quality.py` | 업로드 파일/시트 품질 진단 — 의도별 응답 렌더링 |

---

## `filter/` — 값·조건 필터

프롬프트에 언급된 값/조건으로 행을 좁힙니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 값 매칭·조건 필터·필터 맥락 패키지 |
| `value_filter.py` | 값 매칭·필터·맥락 라벨 공개 facade — 하위 모듈 재수출 |
| `value_match.py` | 프롬프트 값 매칭 코어 (셀/라벨 매칭) |
| `filter_context.py` | 필터 소스 결정·맥락 라벨·필터 요약 |
| `condition_filter.py` | 조건형 행 필터 (예: A가 0인데 B가 있는 행) |
| `missing_rows.py` | 결측 행 필터 요청 판별·결과 |

---

## `display/` — 결과 표시·정렬·차트

분석 결과를 UI에 맞게 다듬고 차트를 만듭니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 결과 표시·정렬·차트 패키지 |
| `result_format.py` | 결과 후처리·리스트 표시 판별 facade — 하위 모듈 재수출 |
| `list_display.py` | 리스트 표시 변환·집계 행 제외 |
| `result_order.py` | 명시 정렬이 없을 때 원본 행 순서 복원 |
| `chart_utils.py` | 차트 파일 저장·폴백 차트 생성·경로 materialize |

---

## `summary/` — 파일 요약

규칙 기반 파일/시트 요약 (일반 표·예산 표).

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | 파일 요약(일반·예산) 및 builder 레지스트리 패키지 |
| `file_summary.py` | 파일 요약 요청용 규칙 기반 분석 — 라우터 + 공개 API |
| `summary_builders.py` | 요약 builder 레지스트리 — 프로필 `summary_builder`로 선택 |
| `generic_summary.py` | 일반 표 형태 데이터용 규칙 기반 요약 |
| `budget_summary.py` | 예실대비표·예산 표 전용 규칙 기반 요약 |
| `summary_utils.py` | 요약 공통 헬퍼 (셀 텍스트·라벨·엑셀 shape) |

---

## `pai/` — PandasAI / Ollama 어댑터

PandasAI SmartDataframe 경로의 설정·전처리·결과 언랩입니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | PandasAI / Ollama 런타임 어댑터 패키지 |
| `pandasai_config.py` | Ollama LLM 설정·`chat`/`chat_multi` 등 공개 진입점 |
| `pandasai_setup.py` | LocalLLM / SmartDataFrame·Datalake 생성, 안전 코드 규칙 |
| `pandasai_frame.py` | PandasAI용 DataFrame prep (계층 라벨 fill, 합계행 제외 등) |
| `pandasai_result.py` | 결과 unwrap·에러 메시지·요약 헬퍼 |

---

## `common/` — analysis / integrate 공통

두 파이프라인이 공유하는 작은 헬퍼입니다.

| 파일 | 역할 |
|-----|-----|
| `__init__.py` | analysis / integrate 공통 헬퍼 패키지 |
| `plan_retry.py` | LLM 계획→실행→검증 재시도 루프 (`run_plan_retries`) |
| `locale_support.py` | 응답 언어(locale) 프리셋 — 프로필 `locale` / `language_instruction` 주입 |

---

## 데이터 흐름 요약

```text
프롬프트
  └─ routing/          의도 판별 · 단일/다중 분기
       ├─ summary/     파일 요약          ← 규칙
       ├─ schema/      스키마·품질        ← 규칙
       ├─ filter/      값·조건 필터       ← 규칙
       ├─ aggregates   그룹/맥락 집계     ← 규칙
       ├─ integrate/   다중 파일 통합     ← 하이브리드 (LLM 계획 + 규칙 실행)
       └─ analysis/    구조화 분석        ← 하이브리드 (LLM 계획 + 규칙 실행)
            또는 pai/  PandasAI 코드 생성 ← LLM
                 └─ display/  표·리스트·차트 후처리 ← 규칙
```

입출력은 `io/`, 도메인 힌트는 `profile_loader.py` + `profiles/*.yaml`, LLM 호출은 `llm_client.py` / `pai/`가 담당합니다.

---

## LLM 경로 vs 규칙 경로

요청은 `routing/route_single.py` · `route_multi.py`에서 **규칙 경로를 먼저** 시도하고, 맞지 않으면 `analyzer.run_analysis` / `run_multi_analysis`로 내려갑니다. LLM은 계획·해석·코드 생성에만 쓰고, 표 계산·검증·차트 렌더는 가능한 한 결정론 규칙으로 둡니다.

### 라우터 우선순위 (단일 파일)

| 순서 | 기능 | 경로 | 주요 모듈 |
|-----|------|------|----------|
| 1 | 파일 요약 | 규칙 | `summary/file_summary.py` |
| 2 | 결측 행 조회 | 규칙 | `filter/missing_rows.py` |
| 3 | 품질 진단 | 규칙 | `schema/quality.py` |
| 4 | 스키마·메타 (행/열 수, 공통 컬럼, dtype·결측, 타입 분류) | 규칙 | `schema/schema_compare.py` |
| 5 | 그룹별 집계 (`비용명별 … 합계` 등) | 규칙 | `aggregates.py` |
| 6 | 맥락(필터) 집계 | 규칙 | `aggregates.py` |
| 7 | 차트만 요청 (표 없이) | 규칙 | `display/chart_utils.py` |
| 8 | 그 외 → `run_analysis` | 혼합 | `analysis/analyzer.py` |

다중 파일(`route_multi`)도 1~4는 같고, 집계 다음에 **구조화 통합**(`integrate/`, 하이브리드)을 시도한 뒤 `run_multi_analysis`로 갑니다.

### `run_analysis` 내부 우선순위

| 순서 | 기능 | 경로 | 비고 |
|-----|------|------|------|
| 1 | 컬럼 의미 설명 | **LLM** (`chat_text`) → 실패 시 **규칙 폴백** | `explain_column_meanings` / `build_rule_based_column_meanings` |
| 2 | 차트 | 규칙 | 자체 렌더러 우선 (한글·축 포맷) |
| 3 | 파일 요약 | 규칙 | 라우터에서 이미 처리됐을 수 있음 |
| 4 | 그룹 집계 단축 | 규칙 | `skip_aggregate_shortcuts`면 생략 |
| 5 | 조건 필터 (A가 0인데 B가 있는 등) | 규칙 | `filter/condition_filter.py` |
| 6 | 값 일치 필터 (예: 비용명 121) | 규칙 | `filter/value_match.py` |
| 7 | 리스트/목록 시드 | 규칙 | `aggregates._build_list_seed_frame` |
| 8 | 구조화 분석 파이프라인 | **하이브리드** | 아래 표 참고 |
| 9 | PandasAI 코드 생성 | **LLM** | `pai/pandasai_config.chat` — 최후 폴백 |

다중 분석(`run_multi_analysis`)도 유사하며, 집계·조건·값 필터 후 `chat_multi`(PandasAI)로 갑니다. (구조화 분석 파이프라인은 단일 `run_analysis` 쪽 중심.)

---

### 규칙만으로 처리되는 기능

키워드·컬럼 힌트·프로필 YAML로 판별하고, pandas를 직접 돌립니다. **Ollama/PandasAI를 호출하지 않습니다.**

| 기능 | 예시 질문 | 모듈 |
|-----|----------|------|
| 파일/시트 요약 | “파일 요약해줘” | `summary/*` |
| 결측 행 | “결측값이 있는 행 보여줘” | `filter/missing_rows.py` |
| 품질 진단 | “데이터 품질 어때?” | `schema/quality.py` |
| 스키마·메타 | “행 수·컬럼 목록”, “공통 컬럼”, “dtype·결측” | `schema/schema_compare.py` (`is_schema_request`) |
| 그룹/맥락 집계 | “분류별 합계”, 필터 범위 합계 | `aggregates.py` |
| 조건 행 필터 | “집행계가 0인데 실행예산이 있는 행” | `filter/condition_filter.py` |
| 값 매칭 필터 | “비용명 121만” | `filter/value_match.py` |
| 단순 리스트 | “비용명 목록” | 리스트 시드 (`aggregates`) |
| 차트 렌더 | “그래프로 보여줘” (축·한글) | `display/chart_utils.py` |
| 추천 질문 | UI 예시 프롬프트 | `suggest_prompts.py` |
| 행 역할 분류 | detail/subtotal/total 등 | `schema/row_classify.py` |
| 결과 후처리 | 리스트 표시, 원본 행순 복원 | `display/*` |
| 엑셀 I/O·정규화·단순 병합 | 업로드·export·키 병합 | `io/*` |
| 생성 코드 정적 검사 | import/open/pivot 이슈 | `code_guardrails.py` (재생성 **지침만**, LLM은 호출측) |

의도 판별 자체(`prompt_intent.py`, `is_*_request`)도 전부 규칙(키워드·정규식)입니다.

---

### 하이브리드 — LLM이 계획하고, 규칙은 실행·검증

| 파이프라인 | LLM이 하는 일 | 규칙이 하는 일 | 진입 |
|-----------|--------------|---------------|------|
| **구조화 분석** `analysis/` | `analysis_plan_builder` — JSON 계획 (`chat_json`); `interpret=true`면 `analysis_interpret` (`chat_text`) | sanitize·compile → `analysis_executor` + `ops_*` → `analysis_validate` | `try_analysis_pipeline` (표/구조화 의도일 때) |
| **구조화 통합** `integrate/` | `schema_infer` — 파일 스키마 (`chat_json`); `plan_builder` — 실행 계획 (`chat_json`) | `plan_engine` (`aggregate_merge` 등) → `plan_validate` → export | `try_integrate_pipeline` (다중 + “통합/병합” 힌트) |

공통 재시도: `common/plan_retry.py`. 검증 실패 시 이전 오류를 LLM에 넘겨 계획을 다시 받습니다.

---

### LLM으로 넘어가는 기능

| 기능 | LLM API | 모듈 | 비고 |
|-----|---------|------|------|
| 컬럼 의미 설명 | `llm_client.chat_text` | `schema/schema_compare.explain_column_meanings` | 실패·짧은 응답 → 규칙 폴백 |
| 분석 계획 생성 | `llm_client.chat_json` | `analysis/analysis_plan_builder.py` | pandas 코드가 아니라 JSON step |
| 분석 결과 해석 | `llm_client.chat_text` | `analysis/analysis_interpret.py` | 계산된 수치만 근거로 문장 생성 |
| 통합 스키마 추론 | `llm_client.chat_json` | `integrate/schema_infer.py` | 열 이름 하드코딩 없음 |
| 통합 실행 계획 | `llm_client.chat_json` | `integrate/plan_builder.py` | |
| 일반 자연어 분석 (최후) | PandasAI → Ollama | `pai/pandasai_config.chat` / `chat_multi` | LLM이 pandas 코드 생성·실행 |

PandasAI 경로에서는 `code_guardrails`가 생성 코드를 검사하고, 이슈가 있으면 **재생성 프롬프트**를 만들어 다시 LLM에 넣습니다(자동 코드 치환 없음).

---

### 한눈에 보는 분기

```text
사용자 프롬프트
│
├─ [규칙] 요약 / 결측 / 품질 / 스키마·메타
├─ [규칙] 그룹·맥락 집계 / (다중) 구조화 통합 힌트?
│         └─ 통합이면 → [LLM 스키마·계획] → [규칙 엔진·검증]
├─ [규칙] 차트만
│
└─ run_analysis / run_multi_analysis
     ├─ [LLM±규칙폴백] 컬럼 의미
     ├─ [규칙] 차트 · 요약 · 집계 · 조건/값 필터 · 리스트
     ├─ [하이브리드] 구조화 분석 계획 파이프라인
     └─ [LLM] PandasAI 코드 생성 (최후 폴백)
              └─ [규칙] 결과 후처리 · 차트 폴백 · 가드레일
```
