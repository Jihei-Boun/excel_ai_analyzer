"""앱 전역 스타일 — 다크/라이트 테마."""

from __future__ import annotations

import streamlit as st

THEMES = {
    "dark": {
        "bg": "#0c1017",
        "sidebar_bg": "#0a0e15",
        "surface": "#151b27",
        "input_bg": "#1a2233",
        "border": "#2a3347",
        "text": "#e8edf5",
        "muted": "#8b97ad",
        "accent": "#3b82f6",
        "accent_soft": "rgba(59, 130, 246, 0.12)",
        "accent_border": "rgba(59, 130, 246, 0.3)",
        "accent_text": "#93c5fd",
        "ok": "#22c55e",
        "ok_soft": "rgba(34, 197, 94, 0.12)",
        "ok_border": "rgba(34, 197, 94, 0.3)",
        "ok_text": "#86efac",
        "chat_user_bg": "#1a2d4a",
        "chat_user_border": "rgba(59, 130, 246, 0.25)",
        "list_bg": "rgba(15, 23, 42, 0.55)",
        "list_group": "#cbd5e1",
        "color_scheme": "dark",
    },
    "light": {
        "bg": "#f4f6fa",
        "sidebar_bg": "#ffffff",
        "surface": "#ffffff",
        "input_bg": "#ffffff",
        "border": "#d8dee9",
        "text": "#1a2332",
        "muted": "#5c6b82",
        "accent": "#2563eb",
        "accent_soft": "rgba(37, 99, 235, 0.1)",
        "accent_border": "rgba(37, 99, 235, 0.28)",
        "accent_text": "#1d4ed8",
        "ok": "#16a34a",
        "ok_soft": "rgba(22, 163, 74, 0.1)",
        "ok_border": "rgba(22, 163, 74, 0.28)",
        "ok_text": "#15803d",
        "chat_user_bg": "#eff6ff",
        "chat_user_border": "rgba(37, 99, 235, 0.22)",
        "list_bg": "rgba(241, 245, 249, 0.9)",
        "list_group": "#334155",
        "color_scheme": "light",
    },
}


def _build_css(theme: str) -> str:
    t = THEMES.get(theme, THEMES["dark"])
    return f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');

    :root {{
        --bg: {t["bg"]};
        --sidebar-bg: {t["sidebar_bg"]};
        --surface: {t["surface"]};
        --input-bg: {t["input_bg"]};
        --border: {t["border"]};
        --text: {t["text"]};
        --muted: {t["muted"]};
        --accent: {t["accent"]};
        --accent-soft: {t["accent_soft"]};
        --accent-border: {t["accent_border"]};
        --accent-text: {t["accent_text"]};
        --ok: {t["ok"]};
        --ok-soft: {t["ok_soft"]};
        --ok-border: {t["ok_border"]};
        --ok-text: {t["ok_text"]};
        --chat-user-bg: {t["chat_user_bg"]};
        --chat-user-border: {t["chat_user_border"]};
        --list-bg: {t["list_bg"]};
        --list-group: {t["list_group"]};

        /* Streamlit 내장 테마 변수 덮어쓰기 */
        --background-color: {t["bg"]};
        --secondary-background-color: {t["surface"]};
        --text-color: {t["text"]};
        --primary-color: {t["accent"]};
    }}

    html, body, .stApp {{
        color-scheme: {t["color_scheme"]};
        font-family: 'DM Sans', sans-serif;
        background: var(--bg) !important;
        color: var(--text) !important;
    }}

    .stApp {{
        background: var(--bg) !important;
        color: var(--text) !important;
    }}

    /* Streamlit 기본 텍스트/캡션 — OS 다크 테마와 충돌 방지 */
    .stApp p, .stApp span, .stApp label, .stApp li,
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] span,
    [data-testid="stWidgetLabel"] p,
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    .stCaption, small {{
        color: var(--text) !important;
    }}

    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    .stCaption {{
        color: var(--muted) !important;
    }}

    [data-testid="stSidebar"] {{
        background: var(--sidebar-bg) !important;
        border-right: 1px solid var(--border);
        color: var(--text) !important;
    }}

    [data-testid="stSidebar"] .block-container {{
        padding-top: 1.25rem;
    }}

    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{
        color: var(--text) !important;
    }}

    /* 메뉴/푸터만 숨기고, 사이드바 토글은 남긴다 */
    #MainMenu, footer {{
        visibility: hidden;
    }}

    header[data-testid="stHeader"] {{
        background: transparent !important;
    }}

    /* 사이드바가 접혀도 다시 열 수 있게 */
    [data-testid="stSidebarCollapsed"],
    [data-testid="stSidebarCollapseButton"] {{
        visibility: visible !important;
        display: flex !important;
        z-index: 999 !important;
    }}

    .block-container {{
        padding-top: 1rem;
        padding-bottom: 1.5rem;
        max-width: 100%;
    }}

    .app-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding-bottom: 0.85rem;
        margin-bottom: 1rem;
        border-bottom: 1px solid var(--border);
    }}

    .app-header-left {{
        display: flex;
        align-items: center;
        gap: 0.7rem;
    }}

    .app-logo {{
        width: 36px;
        height: 36px;
        border-radius: 9px;
        background: var(--accent);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        color: white !important;
        font-weight: 700;
    }}

    .stApp .app-title {{
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0;
        color: var(--text) !important;
        letter-spacing: -0.02em;
    }}

    .stApp .app-subtitle {{
        font-size: 0.75rem;
        color: var(--muted) !important;
        margin: 0.1rem 0 0 0;
    }}

    .app-divider {{
        border: none;
        border-top: 1px solid var(--border);
        margin: 0 0 1rem 0;
    }}

    .stApp .status-pill {{
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.3rem 0.7rem;
        border-radius: 8px;
        background: var(--accent-soft);
        border: 1px solid var(--accent-border);
        color: var(--accent-text) !important;
        font-size: 0.78rem;
        font-weight: 500;
    }}

    .stApp .panel-title {{
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--text) !important;
        margin: 0 0 0.15rem 0;
    }}

    .stApp .panel-desc {{
        font-size: 0.78rem;
        color: var(--muted) !important;
        margin: 0 0 0.85rem 0;
    }}

    .stApp .sidebar-label,
    [data-testid="stSidebar"] .sidebar-label {{
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: var(--muted) !important;
        margin: 0.25rem 0 0.55rem 0;
    }}

    .stApp .conn-ok {{
        background: var(--ok-soft);
        border: 1px solid var(--ok-border);
        color: var(--ok-text) !important;
        border-radius: 8px;
        padding: 0.45rem 0.65rem;
        font-size: 0.78rem;
        margin: 0.4rem 0 0.75rem 0;
    }}

    .stApp .conn-fail {{
        color: var(--muted) !important;
        font-size: 0.78rem;
        margin: 0.35rem 0 0.75rem 0;
    }}

    .stApp .meta-line {{
        color: var(--muted) !important;
        font-size: 0.78rem;
        margin: 0.35rem 0 0.75rem 0;
    }}

    .stApp .chat-user {{
        background: var(--chat-user-bg);
        border: 1px solid var(--chat-user-border);
        border-radius: 10px;
        padding: 0.7rem 0.85rem;
        margin-bottom: 0.55rem;
        font-size: 0.85rem;
        color: var(--text) !important;
    }}

    .stApp .chat-assistant {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.7rem 0.85rem;
        margin-bottom: 0.55rem;
        font-size: 0.85rem;
        color: var(--text) !important;
    }}

    .stApp .filter-ok {{
        background: var(--ok-soft);
        border: 1px solid var(--ok-border);
        color: var(--ok-text) !important;
        border-radius: 8px;
        padding: 0.45rem 0.65rem;
        font-size: 0.78rem;
        margin: 0.35rem 0 0.65rem 0;
    }}

    .stApp .list-result {{
        margin: 0.35rem 0 0.65rem 0;
        padding: 0.55rem 0.85rem 0.55rem 1.35rem;
        background: var(--list-bg);
        border: 1px solid var(--border);
        border-radius: 8px;
        font-size: 0.84rem;
        line-height: 1.55;
        color: var(--text) !important;
    }}

    .stApp .list-result li {{
        margin: 0.12rem 0;
        color: var(--text) !important;
    }}

    .stApp .list-group-name {{
        margin: 0.55rem 0 0.2rem 0;
        font-size: 0.82rem;
        font-weight: 600;
        color: var(--list-group) !important;
    }}

    .list-group-name:first-child {{
        margin-top: 0.15rem;
    }}

    .list-result-nested {{
        margin: 0.1rem 0 0.45rem 0.35rem;
        padding: 0.35rem 0.75rem 0.35rem 1.2rem;
    }}

    .result-box {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.75rem 0.9rem;
        margin: 0.5rem 0;
    }}

    .stApp .result-box .label {{
        font-size: 0.72rem;
        color: var(--muted) !important;
    }}

    .stApp .result-box .value {{
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--text) !important;
        margin-top: 0.15rem;
    }}

    /* 입력/선택/텍스트영역 */
    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea,
    [data-baseweb="select"] > div {{
        background-color: var(--input-bg) !important;
        color: var(--text) !important;
        border-color: var(--border) !important;
        caret-color: var(--text) !important;
    }}

    .stTextInput input::placeholder,
    .stTextArea textarea::placeholder,
    [data-baseweb="input"] input::placeholder,
    [data-baseweb="textarea"] textarea::placeholder {{
        color: var(--muted) !important;
        opacity: 1 !important;
    }}

    /* 파일 업로더 */
    [data-testid="stFileUploaderDropzone"],
    [data-testid="stFileUploaderDropzone"] section {{
        background-color: var(--input-bg) !important;
        border-color: var(--border) !important;
        color: var(--text) !important;
    }}

    [data-testid="stFileUploaderDropzone"] *,
    [data-testid="stFileUploaderDropzone"] span,
    [data-testid="stFileUploaderDropzone"] small,
    [data-testid="stFileUploaderDropzone"] p {{
        color: var(--muted) !important;
    }}

    [data-testid="stFileUploaderDropzone"] svg {{
        fill: var(--muted) !important;
        stroke: var(--muted) !important;
        color: var(--muted) !important;
    }}

    /* 라디오 / 셀렉트 / 멀티셀렉트 라벨 */
    .stRadio label,
    .stRadio [data-testid="stMarkdownContainer"] p,
    .stSelectbox label,
    .stMultiSelect label {{
        color: var(--text) !important;
    }}

    /* 버튼 */
    .stButton > button {{
        border-radius: 8px;
        border: 1px solid var(--border) !important;
        background: var(--surface) !important;
        color: var(--text) !important;
    }}

    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"] {{
        background: var(--accent) !important;
        border-color: var(--accent) !important;
        color: white !important;
    }}

    /* 알림 박스 */
    [data-testid="stAlert"] {{
        color: var(--text) !important;
    }}

    div[data-testid="stMetric"] {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 0.4rem 0.65rem;
        color: var(--text) !important;
    }}

    .stat-grid {{
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0.55rem;
        margin-top: 0.85rem;
    }}

    .stat-card {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.7rem 0.8rem;
    }}

    .stApp .stat-card .label {{
        font-size: 0.72rem;
        color: var(--muted) !important;
        margin-bottom: 0.25rem;
    }}

    .stApp .stat-card .value {{
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text) !important;
    }}

    @media (max-width: 1100px) {{
        .stat-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
</style>
"""


def inject_styles() -> None:
    theme = st.session_state.get("theme", "dark")
    if theme not in THEMES:
        theme = "dark"
    st.markdown(_build_css(theme), unsafe_allow_html=True)
