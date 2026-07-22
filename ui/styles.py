"""앱 전역 스타일 — 다크/라이트 테마를 각각 독립 지정."""

from __future__ import annotations

import streamlit as st

THEMES = ("dark", "light")


def _shared_layout_css() -> str:
    """색과 무관한 레이아웃·구조 규칙."""
    return """
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');

    html, body, .stApp {
        font-family: 'DM Sans', sans-serif;
    }

    #MainMenu, footer {
        visibility: hidden;
    }

    header[data-testid="stHeader"] {
        background: transparent !important;
    }

    [data-testid="stSidebarCollapsed"],
    [data-testid="stSidebarCollapseButton"] {
        visibility: visible !important;
        display: flex !important;
        z-index: 999 !important;
    }

    [data-testid="stSidebar"] .block-container {
        padding-top: 1.25rem;
    }

    .block-container {
        padding-top: 1rem;
        padding-bottom: 1.5rem;
        max-width: 100%;
    }

    .app-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding-bottom: 0.85rem;
        margin-bottom: 1rem;
    }

    .app-header-left {
        display: flex;
        align-items: center;
        gap: 0.7rem;
    }

    .app-logo {
        width: 36px;
        height: 36px;
        border-radius: 9px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        color: white !important;
        font-weight: 700;
    }

    .stApp .app-title {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.02em;
    }

    .stApp .app-subtitle {
        font-size: 0.75rem;
        margin: 0.1rem 0 0 0;
    }

    .app-divider {
        border: none;
        margin: 0 0 1rem 0;
    }

    .stApp .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.3rem 0.7rem;
        border-radius: 8px;
        font-size: 0.78rem;
        font-weight: 500;
    }

    .stApp .panel-title {
        font-size: 0.95rem;
        font-weight: 600;
        margin: 0 0 0.15rem 0;
    }

    .stApp .panel-desc {
        font-size: 0.78rem;
        margin: 0 0 0.85rem 0;
    }

    .stApp .sidebar-label,
    [data-testid="stSidebar"] .sidebar-label {
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        margin: 0.25rem 0 0.55rem 0;
    }

    .stApp .conn-ok {
        border-radius: 8px;
        padding: 0.45rem 0.65rem;
        font-size: 0.78rem;
        margin: 0.4rem 0 0.75rem 0;
    }

    .stApp .conn-fail {
        font-size: 0.78rem;
        margin: 0.35rem 0 0.75rem 0;
    }

    .stApp .meta-line {
        font-size: 0.78rem;
        margin: 0.35rem 0 0.75rem 0;
    }

    .stApp .chat-user,
    .stApp .chat-assistant {
        border-radius: 10px;
        padding: 0.7rem 0.85rem;
        margin-bottom: 0.55rem;
        font-size: 0.85rem;
    }

    .stApp .filter-ok {
        border-radius: 8px;
        padding: 0.45rem 0.65rem;
        font-size: 0.78rem;
        margin: 0.35rem 0 0.65rem 0;
    }

    .stApp .list-result {
        margin: 0.35rem 0 0.65rem 0;
        padding: 0.55rem 0.85rem 0.55rem 1.35rem;
        border-radius: 8px;
        font-size: 0.84rem;
        line-height: 1.55;
    }

    .stApp .list-result li {
        margin: 0.12rem 0;
    }

    .stApp .list-group-name {
        margin: 0.55rem 0 0.2rem 0;
        font-size: 0.82rem;
        font-weight: 600;
    }

    .list-group-name:first-child {
        margin-top: 0.15rem;
    }

    .list-result-nested {
        margin: 0.1rem 0 0.45rem 0.35rem;
        padding: 0.35rem 0.75rem 0.35rem 1.2rem;
    }

    .result-box {
        border-radius: 10px;
        padding: 0.75rem 0.9rem;
        margin: 0.5rem 0;
    }

    .stApp .result-box .label {
        font-size: 0.72rem;
    }

    .stApp .result-box .value {
        font-size: 1.15rem;
        font-weight: 700;
        margin-top: 0.15rem;
    }

    .stat-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0.55rem;
        margin-top: 0.85rem;
    }

    .stat-card {
        border-radius: 10px;
        padding: 0.7rem 0.8rem;
    }

    .stApp .stat-card .label {
        font-size: 0.72rem;
        margin-bottom: 0.25rem;
    }

    .stApp .stat-card .value {
        font-size: 1.1rem;
        font-weight: 700;
    }

    @media (max-width: 1100px) {
        .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    .stButton > button {
        border-radius: 8px;
    }

    [data-testid="stExpander"] {
        border-radius: 8px !important;
    }

    [data-testid="stExpander"] summary {
        list-style: none !important;
    }

    /* 차트/이미지 옆 Streamlit 툴바(검은 네모) 숨김 */
    [data-testid="stElementToolbar"],
    [data-testid="stElementToolbarButton"],
    [data-testid="stImage"] button,
    [data-testid="stImageContainer"] button {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
    }

    .chart-image {
        width: 100%;
        border-radius: 8px;
        display: block;
    }
"""


def _dark_theme_css() -> str:
    """다크 모드 전용 색상."""
    return """
    :root {
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
        --background-color: #0c1017;
        --secondary-background-color: #151b27;
        --text-color: #e8edf5;
        --primary-color: #3b82f6;
    }

    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stToolbar"] {
        color-scheme: dark;
        background: var(--bg) !important;
        color: var(--text) !important;
    }

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

    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    .stCaption, small {
        color: var(--muted) !important;
        -webkit-text-fill-color: var(--muted) !important;
    }

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

    .stApp .conn-ok,
    .stApp .filter-ok {
        background: var(--ok-soft);
        border: 1px solid var(--ok-border);
        color: var(--ok-text) !important;
    }

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

    [data-testid="stFileUploaderDropzone"],
    [data-testid="stFileUploaderDropzone"] section,
    [data-testid="stFileUploaderFile"],
    [data-testid="stFileUploaderFileName"],
    section[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {
        background-color: var(--input-bg) !important;
        border-color: var(--border) !important;
        color: var(--text) !important;
    }

    [data-testid="stFileUploaderDropzone"] *,
    [data-testid="stFileUploaderFile"] * {
        color: var(--muted) !important;
    }

    [data-testid="stFileUploaderDropzone"] svg {
        fill: var(--muted) !important;
        stroke: var(--muted) !important;
        color: var(--muted) !important;
    }

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

    [data-testid="stAlert"],
    [data-testid="stAlert"] * {
        color: var(--text) !important;
    }

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

    div[data-testid="stDataFrame"],
    div[data-testid="stDataFrame"] [role="grid"],
    [data-testid="stTable"] {
        background: var(--surface) !important;
        color: var(--text) !important;
    }
"""


def _light_theme_css() -> str:
    """라이트 모드 전용 — 흰 배경 + 검은 글자."""
    return """
    :root {
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
        --background-color: #f7f8fa;
        --secondary-background-color: #ffffff;
        --text-color: #111827;
        --primary-color: #2563eb;
    }

    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stMain"],
    section.main {
        color-scheme: light !important;
        background: #f7f8fa !important;
        color: #111827 !important;
    }

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

    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] *,
    .stCaption, small {
        color: #4b5563 !important;
        -webkit-text-fill-color: #4b5563 !important;
    }

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

    .stApp .conn-ok,
    .stApp .filter-ok {
        background: rgba(22, 163, 74, 0.12);
        border: 1px solid rgba(22, 163, 74, 0.35);
        color: #166534 !important;
    }

    .stApp .chat-user {
        background: #eff6ff;
        border: 1px solid rgba(37, 99, 235, 0.25);
    }

    .stApp .chat-assistant,
    .result-box,
    .stat-card,
    div[data-testid="stMetric"] {
        background: #ffffff !important;
        border: 1px solid #d0d7e2 !important;
        color: #111827 !important;
    }

    .stApp .list-result {
        background: #f3f4f6 !important;
        border: 1px solid #d0d7e2 !important;
    }

    .app-divider { border-top: 1px solid #d0d7e2; }

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

    /* 파일 업로더·업로드된 파일 칩 */
    [data-testid="stFileUploader"],
    [data-testid="stFileUploader"] section,
    [data-testid="stFileUploaderDropzone"],
    [data-testid="stFileUploaderDropzone"] section,
    [data-testid="stFileUploaderDropzoneInstructions"],
    [data-testid="stFileChips"],
    [data-testid="stFileChip"],
    [data-testid="stFileChipName"],
    [data-testid="stFileChipImagePreview"] {
        background: #ffffff !important;
        background-color: #ffffff !important;
        border-color: #d0d7e2 !important;
        color: #111827 !important;
    }

    [data-testid="stFileUploader"] *,
    [data-testid="stFileUploaderDropzone"] *,
    [data-testid="stFileUploaderDropzoneInstructions"] *,
    [data-testid="stFileChips"] *,
    [data-testid="stFileChip"] *,
    [data-testid="stFileChipName"],
    [data-testid="stFileChipName"] * {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    [data-testid="stFileUploaderDropzone"] svg,
    [data-testid="stFileChip"] svg,
    [data-testid="stFileChipDeleteBtn"] svg {
        fill: #4b5563 !important;
        stroke: #4b5563 !important;
        color: #4b5563 !important;
    }

    /* Browse files 버튼 */
    [data-testid="stFileUploader"] button,
    [data-testid="stFileUploaderDropzone"] button {
        background: #ffffff !important;
        color: #111827 !important;
        border: 1px solid #d0d7e2 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* Selectbox / Multiselect */
    [data-testid="stSelectbox"] > div,
    [data-testid="stSelectbox"] [data-baseweb="select"],
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] [data-baseweb="select"],
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
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
    [data-testid="stMultiSelect"] * {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

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

    [data-testid="stAlert"],
    [data-testid="stAlert"] * {
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
    }

    /* Expander */
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

    /* DataFrame / Table — 흰 배경 + 검은 글자 */
    div[data-testid="stDataFrame"],
    div[data-testid="stDataFrame"] > div,
    div[data-testid="stDataFrame"] [role="grid"],
    div[data-testid="stDataFrame"] [class*="glide"],
    div[data-testid="stDataFrame"] canvas,
    [data-testid="stTable"],
    [data-testid="stTable"] table,
    [data-testid="stTable"] th,
    [data-testid="stTable"] td {
        background: #ffffff !important;
        background-color: #ffffff !important;
        color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        border-color: #d0d7e2 !important;
    }

    div[data-testid="stDataFrame"] * {
        color: #111827 !important;
    }

    /* 라이트 모드 커스텀 표 */
    .light-df-wrap {
        width: 100%;
    }

    table.light-df {
        width: 100%;
        border-collapse: collapse;
        background: #ffffff;
        color: #111827;
        font-size: 0.82rem;
    }

    table.light-df th {
        position: sticky;
        top: 0;
        z-index: 1;
        background: #f3f4f6;
        color: #111827;
        border-bottom: 1px solid #d0d7e2;
        padding: 0.45rem 0.6rem;
        text-align: left;
        white-space: nowrap;
        font-weight: 600;
    }

    table.light-df td {
        background: #ffffff;
        color: #111827;
        border-bottom: 1px solid #e5e7eb;
        padding: 0.4rem 0.6rem;
        white-space: nowrap;
    }

    table.light-df tr:hover td {
        background: #f9fafb;
    }

    /* 이미지/차트 옆 Streamlit 툴바(전체화면 검은 네모) 숨김 */
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
        border: 1px solid #d0d7e2;
        background: #ffffff;
        display: block;
    }
"""


def _theme_script(theme: str) -> str:
    header_bg = "#f3f4f6" if theme == "light" else "#151b27"
    header_fg = "#111827" if theme == "light" else "#e8edf5"
    chip_bg = "#ffffff" if theme == "light" else "#1a2233"
    return f"""
<script>
(() => {{
  const theme = "{theme}";
  const headerBg = "{header_bg}";
  const headerFg = "{header_fg}";
  const chipBg = "{chip_bg}";
  let observer = null;

  const paint = (el, bg, fg) => {{
    if (!el) return;
    el.style.setProperty("background", bg, "important");
    el.style.setProperty("background-color", bg, "important");
    el.style.setProperty("color", fg, "important");
    el.style.setProperty("-webkit-text-fill-color", fg, "important");
  }};

  const paintWidgets = () => {{
    document.querySelectorAll('[data-testid="stExpander"] summary').forEach((el) => {{
      paint(el, headerBg, headerFg);
      el.querySelectorAll("div, span, p, label").forEach((child) => {{
        child.style.setProperty("background", "transparent", "important");
        child.style.setProperty("color", headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
      }});
      el.querySelectorAll("svg, path").forEach((child) => {{
        child.style.setProperty("fill", headerFg, "important");
        child.style.setProperty("stroke", headerFg, "important");
      }});
    }});

    document.querySelectorAll('[data-testid="stFileChip"]').forEach((el) => {{
      paint(el, chipBg, headerFg);
      el.style.setProperty(
        "border",
        theme === "light" ? "1px solid #d0d7e2" : "1px solid #2a3347",
        "important"
      );
      el.querySelectorAll("span, div, p, small").forEach((child) => {{
        child.style.setProperty("color", headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
        child.style.setProperty("background", "transparent", "important");
      }});
      el.querySelectorAll("svg, path").forEach((child) => {{
        child.style.setProperty("fill", headerFg, "important");
        child.style.setProperty("stroke", headerFg, "important");
      }});
    }});

    document.querySelectorAll(
      '[data-testid="stFileUploaderDropzone"], [data-testid="stFileUploaderDropzone"] section, [data-testid="stFileUploaderDropzoneInstructions"]'
    ).forEach((el) => {{
      paint(el, chipBg, headerFg);
      el.querySelectorAll("span, div, p, small, button").forEach((child) => {{
        if (child.tagName === "BUTTON") {{
          paint(child, chipBg, headerFg);
          child.style.setProperty("border", "1px solid #d0d7e2", "important");
        }} else {{
          child.style.setProperty("color", headerFg, "important");
          child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
        }}
      }});
    }});

    document.querySelectorAll('[data-testid="stSelectbox"] [data-baseweb="select"] > div').forEach((el) => {{
      paint(el, chipBg, headerFg);
      el.style.setProperty(
        "border-color",
        theme === "light" ? "#d0d7e2" : "#2a3347",
        "important"
      );
      el.querySelectorAll("*").forEach((child) => {{
        child.style.setProperty("color", headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
      }});
    }});
  }};

  const apply = () => {{
    if (observer) observer.disconnect();
    document.documentElement.setAttribute("data-app-theme", theme);
    document.documentElement.style.colorScheme = theme;
    if (document.body) {{
      document.body.setAttribute("data-app-theme", theme);
      document.body.style.colorScheme = theme;
    }}
    // Streamlit 네이티브 테마 키만 맞춰 둔다 (새로고침 없이 CSS/JS로 위젯 보정)
    try {{
      const desired = theme === "light" ? "Light" : "Dark";
      const baseKey = `stActiveTheme-${{window.location.pathname}}`;
      const activeKey = `${{baseKey}}-v2`;
      window.localStorage.setItem(activeKey, JSON.stringify(desired));
    }} catch (_) {{}}
    paintWidgets();
    if (observer) {{
      observer.observe(document.documentElement, {{ childList: true, subtree: true }});
    }}
  }};

  let timer = null;
  observer = new MutationObserver(() => {{
    if (timer) return;
    timer = setTimeout(() => {{
      timer = null;
      apply();
    }}, 50);
  }});
  apply();
}})();
</script>
"""


def _build_css(theme: str) -> str:
    color_css = _light_theme_css() if theme == "light" else _dark_theme_css()
    return f"""
<style>
{_shared_layout_css()}
{color_css}
</style>
{_theme_script(theme)}
"""


def inject_styles() -> None:
    theme = st.session_state.get("theme", "dark")
    if theme not in THEMES:
        theme = "dark"
    st.markdown(_build_css(theme), unsafe_allow_html=True)
