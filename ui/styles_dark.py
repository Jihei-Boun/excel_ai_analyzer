"""다크 테마 CSS — 어두운 배경 + 밝은 글자.

구역:
  CSS 변수 → 앱 배경 → 본문 글자 → 캡션 → 사이드바 → 헤더/패널
  → 채팅/카드 → 입력 → 파일 업로더 → 버튼 → Expander → 표
"""

from __future__ import annotations


def dark_theme_css() -> str:
    """다크 모드 전용 색상."""
    return """
    /* ========== CSS 변수 (다크) ========== */
    :root,
    html[data-app-theme="dark"],
    body[data-app-theme="dark"],
    [data-app-theme="dark"] {
        --bg: #0c1017;
        --sidebar-bg: #0a0e15;
        --surface: #151b27;
        --input-bg: #1a2233;
        --border: #2a3347;
        --text: #e8edf5;
        --muted: #8b97ad;
        --accent: #3b82f6;
        --accent-soft: rgba(59, 130, 246, 0.12);
        --accent-border: rgba(59, 130, 246, 0.3);
        --accent-text: #93c5fd;
        --ok: #22c55e;
        --ok-soft: rgba(34, 197, 94, 0.12);
        --ok-border: rgba(34, 197, 94, 0.3);
        --ok-text: #86efac;
        --chat-user-bg: #1a2d4a;
        --chat-user-border: rgba(59, 130, 246, 0.25);
        --list-bg: rgba(15, 23, 42, 0.55);
        --list-group: #cbd5e1;
        --header-bg: #151b27;
        --table-header-bg: #1a2233;
        --table-row-hover: #1a2233;
        --code-bg: #1e293b;
        --code-text: #e2e8f0;
        --code-border: #334155;
        --background-color: #0c1017;
        --secondary-background-color: #151b27;
        --text-color: #e8edf5;
        --primary-color: #3b82f6;
    }

    /* ========== 앱 전체 배경·기본 글자색 ========== */
    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stToolbar"] {
        color-scheme: dark;
        background: var(--bg) !important;
        color: var(--text) !important;
    }

    /* ========== 본문·위젯 라벨 글자색 ========== */
    .stApp p, .stApp span, .stApp label, .stApp li,
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] span,
    [data-testid="stWidgetLabel"] p,
    .stRadio label,
    .stRadio [data-testid="stMarkdownContainer"] p,
    .stSelectbox label,
    .stMultiSelect label,
    .stCheckbox label {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    /* ========== 캡션·보조 설명 글자색 ========== */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    .stCaption, small {
        color: var(--muted) !important;
        -webkit-text-fill-color: var(--muted) !important;
    }

    /* ========== 사이드바 ========== */
    [data-testid="stSidebar"],
    [data-testid="stSidebar"] > div:first-child {
        background: var(--sidebar-bg) !important;
        border-right: 1px solid var(--border);
        color: var(--text) !important;
    }

    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label {
        color: var(--text) !important;
    }

    /* ========== 앱 헤더·패널 제목/설명 ========== */
    .app-header { border-bottom: 1px solid var(--border); }
    .app-logo { background: var(--accent); }
    .stApp .app-title { color: var(--text) !important; }
    .stApp .app-subtitle,
    .stApp .panel-desc,
    .stApp .sidebar-label,
    .stApp .conn-fail,
    .stApp .result-box .label,
    .stApp .stat-card .label { color: var(--muted) !important; }

    .stApp .status-pill {
        background: var(--accent-soft);
        border: 1px solid var(--accent-border);
        color: var(--accent-text) !important;
    }

    .stApp .panel-title,
    .stApp .meta-line,
    .stApp .chat-user,
    .stApp .chat-assistant,
    .stApp .list-result,
    .stApp .list-result li,
    .stApp .list-group-name,
    .stApp .result-box .value,
    .stApp .stat-card .value { color: var(--text) !important; }

    /* ========== 인라인 코드 ========== */
    .stApp .chat-inline-code {
        background: var(--code-bg) !important;
        color: var(--code-text) !important;
        -webkit-text-fill-color: var(--code-text) !important;
        border: 1px solid var(--code-border);
    }

    /* ========== 연결 성공·필터 성공 배지 ========== */
    .stApp .conn-ok,
    .stApp .filter-ok {
        background: var(--ok-soft);
        border: 1px solid var(--ok-border);
        color: var(--ok-text) !important;
    }

    /* ========== 채팅 말풍선·결과 카드·메트릭 ========== */
    .stApp .chat-user {
        background: var(--chat-user-bg);
        border: 1px solid var(--chat-user-border);
    }

    .stApp .chat-assistant,
    .result-box,
    .stat-card,
    div[data-testid="stMetric"] {
        background: var(--surface);
        border: 1px solid var(--border);
        color: var(--text) !important;
    }

    .stApp .list-result {
        background: var(--list-bg);
        border: 1px solid var(--border);
    }

    .stApp .list-group-name { color: var(--list-group) !important; }
    .app-divider { border-top: 1px solid var(--border); }

    /* ========== 텍스트/숫자/텍스트에어리어 입력 ========== */
    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea,
    [data-baseweb="input"],
    [data-baseweb="input"] > div,
    [data-baseweb="base-input"],
    [data-baseweb="textarea"],
    [data-baseweb="select"] > div {
        background-color: var(--input-bg) !important;
        color: var(--text) !important;
        border-color: var(--border) !important;
        caret-color: var(--text) !important;
    }

    .stTextInput input::placeholder,
    .stTextArea textarea::placeholder {
        color: var(--muted) !important;
        opacity: 1 !important;
    }

    /* ========== 파일 업로더·업로드된 파일 (데이터 패널) ========== */
    [data-testid="stFileUploader"],
    [data-testid="stFileUploader"] section,
    [data-testid="stFileUploaderDropzone"],
    [data-testid="stFileUploaderDropzone"] section,
    [data-testid="stFileUploaderDropzoneInstructions"],
    [data-testid="stFileChips"],
    [data-testid="stFileChip"],
    [data-testid="stFileUploaderFile"],
    [data-testid="stFileUploaderFileName"],
    section[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {
        background: var(--input-bg) !important;
        background-color: var(--input-bg) !important;
        border-color: var(--border) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    /* 업로드된 파일별 카드 — 여러 개일 때 구분 */
    [data-testid="stFileChips"] {
        background: transparent !important;
        background-color: transparent !important;
        border: none !important;
        gap: 0.5rem !important;
        flex-wrap: wrap !important;
    }

    [data-testid="stFileChip"] {
        background: var(--input-bg) !important;
        background-color: var(--input-bg) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        padding: 0.45rem 0.7rem !important;
        margin: 0 !important;
        box-shadow: none !important;
        min-height: 2.4rem !important;
    }

    [data-testid="stFileChipName"],
    [data-testid="stFileChipName"] * {
        background: transparent !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    /* 파일 칩 아이콘·삭제 — 빈 네모/동그라미로 보이므로 숨김. 삭제는 아래 '삭제' 버튼 사용 */
    [data-testid="stFileChip"] > :first-child:not(:has([data-testid="stFileChipName"])),
    [data-testid="stFileChipImagePreview"],
    [data-testid="stFileChipDeleteBtn"],
    [data-testid="stFileChipIconSpinner"],
    [data-testid="stFileChipIconError"] {
        display: none !important;
    }

    [data-testid="stFileUploaderDropzone"] *,
    [data-testid="stFileUploaderDropzoneInstructions"] *,
    [data-testid="stFileChipName"],
    [data-testid="stFileChipName"] * {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    [data-testid="stFileUploaderDropzone"] svg {
        fill: var(--muted) !important;
        stroke: var(--muted) !important;
        color: var(--muted) !important;
    }

    [data-testid="stFileUploader"] button,
    [data-testid="stFileUploaderDropzone"] button {
        background: var(--input-bg) !important;
        color: var(--text) !important;
        border: 1px solid var(--border) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    /* ========== Selectbox / Multiselect ========== */
    [data-testid="stSelectbox"] > div,
    [data-testid="stSelectbox"] [data-baseweb="select"],
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] [data-baseweb="select"],
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"],
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
        background: var(--input-bg) !important;
        background-color: var(--input-bg) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border-color: var(--border) !important;
    }

    [data-testid="stSelectbox"] *,
    [data-testid="stMultiSelect"] label,
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] input {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    [data-testid="stMultiSelect"] [data-baseweb="tag"],
    [data-testid="stMultiSelect"] span[data-baseweb="tag"],
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="tag"],
    [data-testid="stSidebar"] [data-baseweb="tag"],
    div[data-baseweb="select"] [data-baseweb="tag"] {
        background: rgba(59, 130, 246, 0.18) !important;
        background-color: rgba(59, 130, 246, 0.18) !important;
        border: 1px solid rgba(59, 130, 246, 0.45) !important;
        color: #93c5fd !important;
        -webkit-text-fill-color: #93c5fd !important;
    }

    [data-testid="stMultiSelect"] [data-baseweb="tag"] *,
    [data-testid="stSidebar"] [data-baseweb="tag"] *,
    div[data-baseweb="select"] [data-baseweb="tag"] * {
        color: #93c5fd !important;
        -webkit-text-fill-color: #93c5fd !important;
        background: transparent !important;
    }

    [data-testid="stMultiSelect"] [data-baseweb="tag"] svg,
    [data-testid="stMultiSelect"] [data-baseweb="tag"] path,
    [data-testid="stSidebar"] [data-baseweb="tag"] svg,
    [data-testid="stSidebar"] [data-baseweb="tag"] path {
        fill: #93c5fd !important;
        stroke: #93c5fd !important;
        color: #93c5fd !important;
    }

    /* ========== Pills / segmented ========== */
    [data-testid="stPills"] button,
    [data-testid="stBaseButton-pills"],
    [data-testid="stBaseButton-segmentedControl"] {
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border: 1px solid var(--border) !important;
    }

    [data-testid="stBaseButton-pillsActive"],
    [data-testid="stBaseButton-segmentedControlActive"],
    [data-testid="stPills"] button[kind="primary"],
    [data-testid="stPills"] button[aria-pressed="true"] {
        background: rgba(59, 130, 246, 0.18) !important;
        background-color: rgba(59, 130, 246, 0.18) !important;
        color: #93c5fd !important;
        -webkit-text-fill-color: #93c5fd !important;
        border: 1px solid rgba(59, 130, 246, 0.4) !important;
    }

    /* ========== 일반/Primary 버튼 ========== */
    .stButton > button {
        border: 1px solid var(--border) !important;
        background: var(--surface) !important;
        color: var(--text) !important;
    }

    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"] {
        background: var(--accent) !important;
        border-color: var(--accent) !important;
        color: white !important;
    }

    /* ========== Alert ========== */
    [data-testid="stAlert"],
    [data-testid="stAlert"] * {
        color: var(--text) !important;
    }

    /* ========== Expander ========== */
    [data-testid="stExpander"],
    [data-testid="stExpander"] details {
        background: var(--surface) !important;
        color: var(--text) !important;
        border: 1px solid var(--border) !important;
    }

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary:hover,
    [data-testid="stExpander"] summary:focus {
        background: var(--header-bg) !important;
        color: var(--text) !important;
    }

    [data-testid="stExpander"] summary *,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] summary span {
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        opacity: 1 !important;
    }

    [data-testid="stExpander"] summary svg,
    [data-testid="stExpander"] summary path {
        fill: var(--text) !important;
        color: var(--text) !important;
        stroke: var(--text) !important;
    }

    /* ========== 커스텀 HTML 표 (display.py app-df) ========== */
    .app-df-wrap {
        width: 100%;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--surface);
    }

    table.app-df {
        width: 100%;
        border-collapse: collapse;
        background: var(--surface);
        color: var(--text);
        font-size: 0.82rem;
    }

    table.app-df th {
        position: sticky;
        top: 0;
        z-index: 1;
        background: var(--table-header-bg) !important;
        background-color: var(--table-header-bg) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border-bottom: 1px solid var(--border) !important;
        padding: 0.45rem 0.6rem;
        text-align: left;
        white-space: nowrap;
        font-weight: 600;
    }

    table.app-df td {
        background: var(--surface) !important;
        background-color: var(--surface) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border-bottom: 1px solid var(--border) !important;
        padding: 0.4rem 0.6rem;
        white-space: nowrap;
    }

    table.app-df tr:hover td {
        background: var(--table-row-hover) !important;
        background-color: var(--table-row-hover) !important;
    }

    .chart-image {
        border: 1px solid var(--border);
        background: var(--surface);
    }
"""
