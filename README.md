# Excel AI Analyzer

엑셀 파일을 업로드하고 **PandasAI + Ollama**로 자연어 기반 데이터 선택·연산을 수행하는 Streamlit 앱입니다.

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

실행 후 브라우저에서 `http://localhost:8501` 로 접속하세요.  
(GUI가 있는 PC에서는 브라우저가 자동으로 열리도록 `.streamlit/config.toml`이 설정되어 있습니다.)

Ollama가 `http://localhost:11434`에서 실행 중이어야 합니다. 사이드바에서 Base URL과 모델을 변경할 수 있습니다.

## 프로젝트 구조

```
excel_ai_analyzer/
├── app.py                      # Streamlit 진입점 (레이아웃)
├── requirements.txt
│
├── ui/                         # Streamlit UI
│   ├── styles.py               # 다크 테마 CSS
│   ├── header.py               # 상단 헤더
│   ├── sidebar.py              # 설정 · 활성 파일 · 메뉴
│   ├── workspace.py            # 중앙 워크스페이스 조립
│   ├── upload.py               # 데이터 업로드
│   ├── preview.py              # 시트 선택 · 미리보기 · 페이지네이션
│   ├── data_insights.py        # 컬럼 정보 · 데이터 요약
│   ├── chat_panel.py           # AI 채팅 · 연산 · 다운로드
│   ├── chat.py                 # 채팅 요청 처리
│   └── session_store.py        # session_state
│
├── core/                       # 비즈니스 로직
│   ├── excel_loader.py         # 엑셀 읽기, 전처리
│   ├── pandasai_config.py      # PandasAI SmartDataframe + Ollama LocalLLM
│   ├── llm_client.py           # Ollama JSON API (의도 분류용)
│   ├── prompt_router.py        # 선택 vs 연산 의도 분류
│   ├── selector.py             # 1단계: 데이터 선택 (PandasAI)
│   └── operator.py             # 2단계: 연산 (PandasAI)
│
└── data/uploads/               # 업로드 임시 저장
```

`app.py`가 유일한 Streamlit 진입점이며, `main.py`나 `app/` 패키지는 사용하지 않습니다.

## 사용 흐름

1. 엑셀 파일 업로드 → 원본 미리보기
2. 채팅으로 데이터 선택 요청 → **선택된 데이터** 패널에 `selected_df` 표시
3. 연산 요청 (합계, 평균, 그룹화 등) → **연산 결과** 패널에 표시

의도 분류는 `core/prompt_router.py`가 담당합니다. 선택·연산은 `selector.py` / `operator.py`에서 **PandasAI `SmartDataframe.chat()`** 로 실행합니다 (Ollama `LocalLLM`).

## 환경

| 항목 | 기본값 |
|------|--------|
| Ollama Base URL | `http://localhost:11434` |
| 모델 | `qwen2.5:7b` |

분석 LLM은 PandasAI `LocalLLM`(`http://…/v1`)을 사용합니다. 의도 분류만 `llm_client.py`의 Ollama chat JSON API를 사용합니다.
