# Excel AI Analyzer

형식에 관계없이 엑셀 파일을 pandas DataFrame으로 읽고, **PandasAI + Ollama**로 자연어 기반 분석을 수행하는 Streamlit 앱입니다.

단일·다중 파일 업로드, 필터·집계·리스트·차트 요청을 지원합니다. 기본은 **범용** 경로이고, 예실대비표 전용 요약은 사이드바 **예산 표 모드**를 켠 뒤에만 적용됩니다.

## 실행

```bash
cd excel_ai_analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Python 3.12에서는 pandasai가 pandas==1.5.3을 요구해 충돌할 수 있습니다.
# 그 경우 아래처럼 설치하세요:
#   pip install pandasai==2.3.2 --no-deps
#   pip install -r requirements.txt

# 방법 1) 권장
bash run.sh

# 방법 2)
streamlit run app.py
```

실행 후 브라우저에서 `http://localhost:8502` 로 접속하세요.
(GUI가 있는 PC에서는 브라우저가 자동으로 열리도록 `.streamlit/config.toml`이 설정되어 있습니다.)

Ollama가 `http://localhost:11434`에서 실행 중이어야 합니다. 사이드바에서 Base URL과 모델을 변경할 수 있습니다.

### 예산 표 모드

사이드바 **분석 상세 → 예산 표 모드** 체크박스입니다. 기본은 **꺼짐**입니다.

| 모드 | 동작 |
|------|------|
| **OFF (기본)** | `파일을 요약해줘` → 범용 요약(행·열·수치 통계). 그룹 집계는 합계/소계 행만 제외 |
| **ON** | 컬럼이 예산 표로 보이면 예실대비표 전용 요약(예산·집행·잔액·집행률). 그룹 집계 시 `내부흡수액`/`외부유출액`도 제외 |

예실대비표·연구과제 예산 엑셀을 다룰 때만 켜면 됩니다. 일반 표는 끄고 쓰면 도메인 문구가 섞이지 않습니다.

### 프로필 YAML (컬럼 힌트)

하드코딩 대신 `profiles/` 아래 YAML로 힌트를 관리합니다. 수정 후 **앱을 재시작**하면 반영됩니다.

| 파일 | 용도 |
|------|------|
| [`profiles/column_hints.yaml`](profiles/column_hints.yaml) | 항상 쓰는 그룹/항목/코드/금액 컬럼 힌트 |
| [`profiles/budget.yaml`](profiles/budget.yaml) | 예산 표 모드 ON일 때만 — 판별 키워드, 컬럼 후보, footer 라벨, 소개 문장 |

로더는 [`core/profile_loader.py`](core/profile_loader.py)이고, [`core/constants.py`](core/constants.py)가 이를 재노출합니다.

### 테스트

```bash
pytest -q
```

GitHub Actions CI(`.github/workflows/ci.yml`)에서 push/PR 시 동일 테스트가 실행됩니다.

## 프로젝트 구조

```
excel_ai_analyzer/
├── app.py                      # Streamlit 진입점 (레이아웃)
├── run.sh                      # 실행 스크립트
├── requirements.txt
├── pytest.ini
│
├── profiles/                   # 도메인·컬럼 힌트 YAML
│   ├── column_hints.yaml       # 범용 컬럼 역할 힌트
│   └── budget.yaml             # 예산 표 모드 전용 프로필
│
├── ui/                         # Streamlit UI
│   ├── styles.py               # 공통·다크·라이트 테마 CSS
│   ├── display.py              # 표·차트 HTML 렌더
│   ├── header.py               # 상단 헤더
│   ├── sidebar.py              # 설정 · 활성 파일 · 테마 · 예산 표 모드
│   ├── workspace.py            # 중앙 워크스페이스 조립
│   ├── upload.py               # 데이터 업로드 · 다중 파일
│   ├── preview.py              # 시트 선택 · 미리보기 · 데이터 요약
│   ├── chat_panel.py           # AI 채팅 · 연산 · 다운로드
│   ├── chat.py                 # 채팅 요청 처리 · 집계/차트 라우팅
│   └── session_store.py        # session_state
│
├── core/                       # 비즈니스 로직
│   ├── constants.py            # 경로·기본값 · 프로필 재노출
│   ├── profile_loader.py       # profiles/*.yaml 로더
│   ├── excel_loader.py         # 엑셀 → 범용 DataFrame (2단 헤더 포함)
│   ├── analyzer.py             # 자연어 분석 · 필터 · 집계 진입점
│   ├── chart_utils.py          # 막대그래프 자체 렌더 (한글·콤마 포맷)
│   ├── file_summary.py         # 「파일을 요약해줘」라우터·공개 API
│   ├── budget_summary.py       # 예실대비표·예산 표 전용 요약
│   ├── generic_summary.py      # 일반 표 요약 (카테고리·수치 통계)
│   ├── summary_utils.py        # 요약 공통 헬퍼
│   ├── result_format.py        # 리스트·집계 행 제거 · 표시 포맷
│   └── pandasai_config.py      # PandasAI SmartDataframe + Ollama LocalLLM
│
├── tests/                      # 단위 테스트 (LLM 호출 없음)
├── exports/charts/             # 생성된 차트 PNG
├── data/uploads/               # 업로드 임시 저장
└── .github/workflows/ci.yml    # CI
```

`app.py`가 유일한 Streamlit 진입점이며, `main.py`나 `app/` 패키지는 사용하지 않습니다.

## 사용 흐름

1. 엑셀 파일 업로드(단일 또는 여러 파일) → `core/excel_loader.py`에서 범용 DataFrame 변환
2. 사용자 자연어 요청 → `core/analyzer.py` / `ui/chat.py`에서 요청 유형 판별
3. **단순 요청**(값 필터, 리스트, 파일별 집계, 차트)은 규칙 기반으로 먼저 처리
4. **복잡한 요청**은 PandasAI `SmartDataframe.chat()` → Ollama LLM이 pandas 코드 생성·실행
5. DataFrame·차트·숫자 결과를 화면에 표시하고 Excel로 다운로드

### 요청 예시

| 요청 | 동작 |
|------|------|
| `비용명이 121인 것만 보여줘` | 값 필터 → 해당 행만 표시 |
| `내부인건비 리스트로 뽑아줘` | 분류 필터 → 리스트 표 |
| `실행예산_합계의 총 합을 파일별로 보여줘` | 소계/합계 행 제외 후 파일별 집계 표 |
| `파일별로 실행예산_합계 차트로 보여줘` | 집계 표와 동일한 데이터로 막대그래프 |
| `파일을 요약해줘` | 기본: 범용 요약 / 예산 표 모드 ON이면 예실대비표 요약 (차트 없음) |

이전 필터(예: `내부인건비`)가 있으면, 후속 집계·차트 요청에 **같은 범위**가 적용됩니다.

## 범용성

**기본은 범용**, 예실대비표 특화는 **예산 표 모드 플러그인**으로 분리된 상태입니다.

### 범용으로 쓸 수 있는 부분

- **엑셀 → DataFrame** — 컬럼명을 코드에 고정하지 않고, 업로드한 시트 구조를 읽습니다. 2단 병합 헤더도 처리합니다.
- **자연어 → pandas** — 필터, 정렬, 집계, 그룹화 등은 PandasAI + Ollama로 **어떤 표 형태 데이터**든 시도할 수 있습니다.
- **규칙 기반 단축 경로** — 프롬프트에 나온 **컬럼명·값**을 기준으로 동작합니다. 힌트 목록은 `profiles/column_hints.yaml`에서 조정합니다.
- **다중 파일** — 파일 여러 개 업로드 후 비교·파일별 집계·차트가 가능합니다.
- **파일 요약(기본)** — 행·열·수치 합/최소/최대·문자형 상위 값 등 범용 개요.

### 아직 “완전 범용 SaaS” 수준은 아닌 이유

| 항목 | 현실 |
|------|------|
| **선택적 도메인 프로필** | 예산 표 모드 ON일 때만 `profiles/budget.yaml` 규칙이 적용됩니다. |
| **단축 경로 범위** | 값 필터, 리스트, 파일별 합계, 막대 차트는 안정적입니다. **피벗, 상관, 복잡 조인**은 LLM에 의존해 **성공률이 들쭉날쭉**합니다. |
| **차트** | 자체 렌더는 **막대그래프** 위주입니다. |
| **언어** | 한국어 프롬프트·한글 컬럼에 최적화되어 있습니다. |
| **인프라** | Ollama 로컬 실행이 필요합니다. |
| **검증** | 파일 요약·프로필 로더는 fixture + 계약 테스트로 CI에서 회귀 검증합니다. |

### 파일 요약 검증 루프

전수 UI 테스트 대신 아래를 기본으로 합니다.

1. `core/file_summary.py` 또는 관련 로직·`profiles/*.yaml` 수정
2. `pytest tests/test_file_summary.py tests/test_profile_loader.py -q` (전체는 `pytest -q`)
3. 실패 → 코드/YAML 수정, 또는 의도적 동작 변경이면 assert/fixture만 갱신
4. UI 수동 확인은 **fixture에 없는 새 유형 엑셀 1개**를 만났을 때만
5. 그 표를 DataFrame으로 축소해 `tests/test_file_summary.py` fixture + 계약 assert에 추가 → 이후 자동 회귀

### 용도별 가능 여부

| 용도 | 가능 여부 |
|------|-----------|
| **예실대비표·예산 엑셀** (예산 표 모드 ON) | ✅ 잘 맞음 — 필터 → 집계 → 차트·전용 요약 |
| **일반 표 형태 엑셀** (헤더 + 행 + 숫자) | ⚠️ 대체로 가능 — LLM 품질·질문 난이도에 따라 편차 있음 |
| **아무 엑셀이나 100%** | ❌ 아직 아님 |

**요약:** 기본 경로는 범용 표 분석이고, 예산 도메인은 사이드바 모드 + YAML 프로필로 켜는 구조입니다.

## 환경

| 항목 | 기본값 |
|------|--------|
| Ollama Base URL | `http://localhost:11434` |
| 모델 | `qwen2.5:7b` |
| Streamlit 포트 | `8502` |
| 예산 표 모드 | OFF |

분석 LLM은 PandasAI `LocalLLM`(`http://…/v1`)을 사용합니다.

---

## 진행 상황

> 마지막 GitHub 커밋: `add light mode` (`ce2c1b5`)  
> 아래는 그 이후 로컬에서 완료된 작업입니다. (아직 push 전)

### 완료

#### 데이터 로딩
- [x] 2단 병합 헤더 자동 감지 (`openpyxl`)
- [x] 복합 컬럼명 flatten — `실행예산_이월예산`, `실행예산_당해예산`, `실행예산_합계` 등
- [x] `Unnamed` / MultiIndex 헤더 처리 개선

#### 분석·집계
- [x] 값 필터 우선 적용 (리스트보다 먼저, 예: `비용명 121`)
- [x] 숫자형 코드(121 등) 정확 매칭
- [x] 컬럼명 `실행예산_합계`의 "합계"를 행 필터로 오인하지 않도록 수정
- [x] 소계·합계·총계 행 제외 후 합산 (`sum_metric_excluding_totals`)
- [x] 다중 파일 파일별 집계 요약 표 (`출처파일 | 수치컬럼`)
- [x] 이전 필터 맥락 유지 — 필터 후 집계·차트에 동일 범위 적용
- [x] 리스트 표시: 필터 컬럼 제외, 다중 파일 시 `출처파일` 그룹
- [x] `파일을 요약해줘` 규칙 기반 요약 — 범용 + (모드 ON 시) 예산/집행/잔액 요약
- [x] **예산 표 모드** 플러그인 — 기본 OFF, 사이드바에서 ON
- [x] 컬럼·예산 힌트를 `profiles/*.yaml`로 분리

#### 차트
- [x] LLM matplotlib 대신 자체 막대그래프 렌더러 (`core/chart_utils.py`)
- [x] 한글 폰트 지원 (NanumGothic 등)
- [x] 표와 차트 수치 일치 (동일 집계 표 기반)
- [x] 막대 순서 — 업로드 순서 유지 (값 크기 정렬 제거)
- [x] 숫자 표기 — `187,090,387` 형식 (콤마, 축약 없음)
- [x] 막대 위 값 라벨 표시
- [x] 차트 X축 라벨 단순화 — 표는 `맥락 · 파일명`, X축은 파일명만 (맥락은 제목)

#### UI·테마
- [x] 라이트 / 다크 테마 전환
- [x] 라이트 모드 대비·위젯 색상 수정 (dataframe, selectbox, 업로더 등)
- [x] 테마 CSS 구조 분리 (공통 + 다크 + 라이트)
- [x] 차트 base64 HTML 렌더 (Streamlit 툴바 검은 네모 제거)
- [x] 분석 모델 선택 radio 위젯 (라이트 모드 가독성)

#### 품질
- [x] 단위 테스트 (`tests/`) — 라우팅, 엑셀 로더, 차트, 결과 포맷, 파일 요약, 프로필 로더
- [x] GitHub Actions CI (`.github/workflows/ci.yml`)

### 미완료 / 향후

- [ ] 라이트 모드에서 분석 영역 일부 위젯 잔여 스타일 이슈 점검
- [ ] LLM 복잡 질의(피벗·상관 등) 정확도 개선
- [ ] 범용 요약 품질을 예산 요약 수준까지 끌어올리기

---

## GitHub

저장소: [Jihei-Boun/excel_ai_analyzer](https://github.com/Jihei-Boun/excel_ai_analyzer)
