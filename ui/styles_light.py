"""라이트 테마 CSS — 흰 배경 + 어두운 글자.

구역:
  CSS 변수 → 앱 배경 → 본문 글자 → 캡션 → 사이드바 → 헤더/패널
  → 채팅/카드 → 입력 → 파일 업로더 → 셀렉트 → 버튼 → Expander → 표
"""

from __future__ import annotations


def light_theme_css() -> str:
    """라이트 모드 전용 — 흰 배경 + 검은 글자."""
    return """
    /* ========== CSS 변수 (라이트) ========== */
    :root,
    html[data-app-theme="light"],
    body[data-app-theme="light"],
    [data-app-theme="light"] {
        --bg: #f7f8fa;
        --sidebar-bg: #ffffff;
        --surface: #ffffff;
        --input-bg: #ffffff;
        --border: #d0d7e2;
        --text: #111827;
        --muted: #4b5563;
        --accent: #2563eb;
        --accent-soft: rgba(37, 99, 235, 0.1);
        --accent-border: rgba(37, 99, 235, 0.28);
        --accent-text: #1d4ed8;
        --ok: #15803d;
        --ok-soft: rgba(22, 163, 74, 0.12);
        --ok-border: rgba(22, 163, 74, 0.35);
        --ok-text: #166534;
        --chat-user-bg: #eff6ff;
        --chat-user-border: rgba(37, 99, 235, 0.25);
        --list-bg: #f3f4f6;
        --list-group: #111827;
        --header-bg: #f3f4f6;
        --table-header-bg: #f3f4f6;
        --table-row-hover: #f9fafb;
        --code-bg: #eff6ff;
        --code-text: #1e3a8a;
        --code-border: #bfdbfe;
        --background-color: #f7f8fa;
        --secondary-background-color: #ffffff;
        --text-color: #111827;
        --primary-color: #2563eb;
    }

    /* ========== 앱 전체 배경·기본 글자색 ========== */
    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stMain"],
    section.main {
        color-scheme: light !important;
        background: var(--bg) !important;
        color: var(--text) !important;
    }

    /* ========== 본문·위젯 라벨 글자색 ========== */
    .stApp p, .stApp span, .stApp label, .stApp li,
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdownContainer"] *,
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] *,
    .stRadio label,
    .stRadio [data-testid="stMarkdownContainer"] p,
    .stRadio *,
    .stSelectbox label,
    .stMultiSelect label,
    .stCheckbox label,
    .stCheckbox * {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* ========== 캡션·보조 설명 글자색 ========== */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] *,
    .stCaption, small {
        color: #4b5563 !important;
        -webkit-text-fill-color: #4b5563 !important;
    }

    /* ========== 사이드바 ========== */
    [data-testid="stSidebar"],
    [data-testid="stSidebar"] > div:first-child {
        background: #ffffff !important;
        border-right: 1px solid #d0d7e2;
        color: #111827 !important;
    }

    [data-testid="stSidebar"] *,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* ========== 앱 헤더·패널 제목/설명 ========== */
    .app-header { border-bottom: 1px solid #d0d7e2; }
    .app-logo { background: #2563eb; }
    .stApp .app-title { color: #111827 !important; }
    .stApp .app-subtitle,
    .stApp .panel-desc,
    .stApp .sidebar-label,
    .stApp .conn-fail,
    .stApp .result-box .label,
    .stApp .stat-card .label {
        color: #4b5563 !important;
        -webkit-text-fill-color: #4b5563 !important;
    }

    .stApp .status-pill {
        background: rgba(37, 99, 235, 0.1);
        border: 1px solid rgba(37, 99, 235, 0.28);
        color: #1d4ed8 !important;
    }

    .stApp .panel-title,
    .stApp .meta-line,
    .stApp .chat-user,
    .stApp .chat-assistant,
    .stApp .list-result,
    .stApp .list-result li,
    .stApp .list-group-name,
    .stApp .result-box .value,
    .stApp .stat-card .value {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* ========== 인라인 코드·프리 ========== */
    .stApp .chat-inline-code,
    .stApp .chat-assistant code,
    .stApp .chat-user code,
    .stApp code {
        background: var(--code-bg) !important;
        background-color: var(--code-bg) !important;
        color: var(--code-text) !important;
        -webkit-text-fill-color: var(--code-text) !important;
        border: 1px solid var(--code-border) !important;
        padding: 0.1em 0.35em;
        border-radius: 4px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 0.9em;
    }

    .stApp pre, .stApp pre code {
        background: var(--list-bg) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }

    /* ========== 연결 성공·필터 성공 배지 ========== */
    .stApp .conn-ok,
    .stApp .filter-ok {
        background: rgba(22, 163, 74, 0.12);
        border: 1px solid rgba(22, 163, 74, 0.35);
        color: #166534 !important;
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
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        color: var(--text) !important;
    }

    .stApp .list-result {
        background: var(--list-bg) !important;
        border: 1px solid var(--border) !important;
    }

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
        background-color: #ffffff !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        border-color: #d0d7e2 !important;
        caret-color: #111827 !important;
    }

    .stTextInput input::placeholder,
    .stTextArea textarea::placeholder {
        color: #6b7280 !important;
        -webkit-text-fill-color: #6b7280 !important;
        opacity: 1 !important;
    }

    /* ========== 파일 업로더·업로드된 파일 칩 (데이터 패널) ========== */
    [data-testid="stFileUploader"],
    [data-testid="stFileUploader"] section,
    [data-testid="stFileUploaderDropzone"],
    [data-testid="stFileUploaderDropzone"] section,
    [data-testid="stFileUploaderDropzoneInstructions"],
    [data-testid="stFileChips"],
    [data-testid="stFileChip"] {
        background: #ffffff !important;
        background-color: #ffffff !important;
        border-color: #d0d7e2 !important;
        color: #111827 !important;
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
        background: #ffffff !important;
        background-color: #ffffff !important;
        border: 1px solid #d0d7e2 !important;
        border-radius: 8px !important;
        padding: 0.45rem 0.7rem !important;
        margin: 0 !important;
        box-shadow: none !important;
        min-height: 2.4rem !important;
    }

    [data-testid="stFileChipName"],
    [data-testid="stFileChipName"] * {
        background: transparent !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
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
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    [data-testid="stFileUploaderDropzone"] svg {
        fill: #4b5563 !important;
        stroke: #4b5563 !important;
        color: #4b5563 !important;
    }

    /* Browse files / + 버튼 */
    [data-testid="stFileUploader"] button,
    [data-testid="stFileUploaderDropzone"] button {
        background: #ffffff !important;
        color: #111827 !important;
        border: 1px solid #d0d7e2 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* ========== Selectbox / Multiselect ========== */
    [data-testid="stSelectbox"] > div,
    [data-testid="stSelectbox"] [data-baseweb="select"],
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] [data-baseweb="select"],
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"],
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    [data-baseweb="popover"] ul,
    [data-baseweb="menu"],
    [data-baseweb="menu"] li,
    [role="listbox"],
    [role="option"] {
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        border-color: #d0d7e2 !important;
    }

    [data-testid="stSelectbox"] *,
    [data-testid="stMultiSelect"] label,
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] input {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* 멀티셀렉트 태그(칩): BaseWeb 다크 잔여 덮어쓰기 */
    [data-testid="stMultiSelect"] [data-baseweb="tag"],
    [data-testid="stMultiSelect"] span[data-baseweb="tag"],
    [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="tag"],
    [data-testid="stSidebar"] [data-baseweb="tag"],
    div[data-baseweb="select"] [data-baseweb="tag"] {
        background: #dbeafe !important;
        background-color: #dbeafe !important;
        border: 1px solid #93c5fd !important;
        border-color: #93c5fd !important;
        color: #1e3a8a !important;
        -webkit-text-fill-color: #1e3a8a !important;
    }

    [data-testid="stMultiSelect"] [data-baseweb="tag"] *,
    [data-testid="stMultiSelect"] [data-baseweb="tag"] span,
    [data-testid="stMultiSelect"] [data-baseweb="tag"] div,
    [data-testid="stSidebar"] [data-baseweb="tag"] *,
    div[data-baseweb="select"] [data-baseweb="tag"] * {
        color: #1e3a8a !important;
        -webkit-text-fill-color: #1e3a8a !important;
        background: transparent !important;
        background-color: transparent !important;
    }

    [data-testid="stMultiSelect"] [data-baseweb="tag"] svg,
    [data-testid="stMultiSelect"] [data-baseweb="tag"] path,
    [data-testid="stSidebar"] [data-baseweb="tag"] svg,
    [data-testid="stSidebar"] [data-baseweb="tag"] path {
        fill: #1e40af !important;
        stroke: #1e40af !important;
        color: #1e40af !important;
    }

    /* ========== Pills / segmented (시트·모드 선택 등) ========== */
    [data-testid="stPills"],
    [data-testid="stPills"] > div,
    [data-testid="stBaseButton-pills"],
    [data-testid="stBaseButton-pillsActive"],
    [data-testid="stBaseButton-segmentedControl"],
    [data-testid="stBaseButton-segmentedControlActive"] {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    [data-testid="stPills"] button,
    [data-testid="stBaseButton-pills"] {
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        border: 1px solid #d0d7e2 !important;
    }

    [data-testid="stBaseButton-pillsActive"],
    [data-testid="stPills"] button[kind="primary"],
    [data-testid="stPills"] button[aria-pressed="true"] {
        background: #dbeafe !important;
        background-color: #dbeafe !important;
        color: #1e3a8a !important;
        -webkit-text-fill-color: #1e3a8a !important;
        border: 1px solid #93c5fd !important;
    }

    /* ========== 일반/Primary 버튼 ========== */
    .stButton > button {
        border: 1px solid #d0d7e2 !important;
        background: #ffffff !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"] {
        background: #2563eb !important;
        border-color: #2563eb !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }

    /* ========== Alert ========== */
    [data-testid="stAlert"],
    [data-testid="stAlert"] * {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* ========== Expander ========== */
    [data-testid="stExpander"],
    [data-testid="stExpander"] details {
        background: #ffffff !important;
        color: #111827 !important;
        border: 1px solid #d0d7e2 !important;
    }

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary:hover,
    [data-testid="stExpander"] summary:focus,
    [data-testid="stExpander"] summary:active,
    [data-testid="stExpander"] summary > div,
    [data-testid="stExpander"] summary > div > div {
        background: #f3f4f6 !important;
        background-color: #f3f4f6 !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    [data-testid="stExpander"] summary *,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] summary span,
    [data-testid="stExpander"] summary label {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        background: transparent !important;
        opacity: 1 !important;
        visibility: visible !important;
    }

    [data-testid="stExpander"] summary svg,
    [data-testid="stExpander"] summary path {
        fill: #111827 !important;
        color: #111827 !important;
        stroke: #111827 !important;
        opacity: 1 !important;
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
        border-bottom: 1px solid #e5e7eb !important;
        padding: 0.4rem 0.6rem;
        white-space: nowrap;
    }

    table.app-df tr:hover td {
        background: var(--table-row-hover) !important;
        background-color: var(--table-row-hover) !important;
    }

    /* ========== 차트/이미지 툴바 숨김 ========== */
    [data-testid="stElementToolbar"],
    [data-testid="stElementToolbarButton"],
    [data-testid="stImage"] [data-testid="stElementToolbar"],
    [data-testid="stImageContainer"] [data-testid="stElementToolbar"],
    [data-testid="stImage"] button,
    [data-testid="stImageContainer"] button {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }

    .chart-image {
        width: 100%;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--surface);
        display: block;
    }
"""
