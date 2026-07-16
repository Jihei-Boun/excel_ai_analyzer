# Excel AI Analyzer

형식에 관계없이 엑셀 파일을 pandas DataFrame으로 읽고, **PandasAI + Ollama**로 자연어 기반 분석을 수행하는 Streamlit 앱입니다.

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
│   ├── preview.py              # 시트 선택 · 미리보기 · 데이터 요약
│   ├── chat_panel.py           # AI 채팅 · 연산 · 다운로드
│   ├── chat.py                 # 채팅 요청 처리
│   └── session_store.py        # session_state
│
├── core/                       # 비즈니스 로직
│   ├── excel_loader.py         # 엑셀 → 범용 DataFrame 변환
│   ├── analyzer.py             # 단일 자연어 분석 진입점
│   ├── pandasai_config.py      # PandasAI SmartDataframe + Ollama LocalLLM
│
└── data/uploads/               # 업로드 임시 저장
```

`app.py`가 유일한 Streamlit 진입점이며, `main.py`나 `app/` 패키지는 사용하지 않습니다.

## 사용 흐름

1. 엑셀 파일 업로드 → `pandas.read_excel()` → 범용 DataFrame
2. 사용자 자연어 요청 → PandasAI `SmartDataframe.chat()`
3. Ollama LLM이 요청과 DataFrame 스키마를 해석해 pandas 코드 생성
4. PandasAI가 pandas 코드를 실행
5. DataFrame·숫자·문자 결과를 화면에 표시하고 Excel로 다운로드

특정 도메인의 컬럼명이나 문서 형식을 가정하지 않습니다. 필터링·정렬·집계·그룹화 등 모든 채팅 요청은 `core/analyzer.py`의 단일 경로에서 **PandasAI `SmartDataframe.chat()`** 로 실행합니다.

## 환경

| 항목 | 기본값 |
|------|--------|
| Ollama Base URL | `http://localhost:11434` |
| 모델 | `qwen2.5:7b` |

분석 LLM은 PandasAI `LocalLLM`(`http://…/v1`)을 사용합니다.
