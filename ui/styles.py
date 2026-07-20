"""앱 전역 스타일."""

from __future__ import annotations

import streamlit as st

APP_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');

    :root {
        --bg: #0c1017;
        --surface: #151b27;
        --border: #2a3347;
        --text: #e8edf5;
        --muted: #8b97ad;
        --accent: #3b82f6;
        --accent-soft: rgba(59, 130, 246, 0.12);
        --ok: #22c55e;
        --ok-soft: rgba(34, 197, 94, 0.12);
    }

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
    }

    .stApp {
        background: var(--bg);
        color: var(--text);
    }

    [data-testid="stSidebar"] {
        background: #0a0e15;
        border-right: 1px solid var(--border);
    }

    [data-testid="stSidebar"] .block-container {
        padding-top: 1.25rem;
    }

    /* 메뉴/푸터만 숨기고, 사이드바 토글은 남긴다 */
    #MainMenu, footer {
        visibility: hidden;
    }

    header[data-testid="stHeader"] {
        background: transparent !important;
    }

    /* 사이드바가 접혀도 다시 열 수 있게 */
    [data-testid="stSidebarCollapsed"],
    [data-testid="stSidebarCollapseButton"] {
        visibility: visible !important;
        display: flex !important;
        z-index: 999 !important;
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
        border-bottom: 1px solid var(--border);
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
        background: var(--accent);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        color: white;
        font-weight: 700;
    }

    .app-title {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0;
        color: var(--text);
        letter-spacing: -0.02em;
    }

    .app-subtitle {
        font-size: 0.75rem;
        color: var(--muted);
        margin: 0.1rem 0 0 0;
    }

    .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.3rem 0.7rem;
        border-radius: 8px;
        background: var(--accent-soft);
        border: 1px solid rgba(59, 130, 246, 0.3);
        color: #93c5fd;
        font-size: 0.78rem;
        font-weight: 500;
    }

    .panel-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--text);
        margin: 0 0 0.15rem 0;
    }

    .panel-desc {
        font-size: 0.78rem;
        color: var(--muted);
        margin: 0 0 0.85rem 0;
    }

    .sidebar-label {
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: var(--muted);
        margin: 0.25rem 0 0.55rem 0;
    }

    .conn-ok {
        background: var(--ok-soft);
        border: 1px solid rgba(34, 197, 94, 0.3);
        color: #86efac;
        border-radius: 8px;
        padding: 0.45rem 0.65rem;
        font-size: 0.78rem;
        margin: 0.4rem 0 0.75rem 0;
    }

    .conn-fail {
        color: var(--muted);
        font-size: 0.78rem;
        margin: 0.35rem 0 0.75rem 0;
    }

    .meta-line {
        color: var(--muted);
        font-size: 0.78rem;
        margin: 0.35rem 0 0.75rem 0;
    }

    .chat-user {
        background: #1a2d4a;
        border: 1px solid rgba(59, 130, 246, 0.25);
        border-radius: 10px;
        padding: 0.7rem 0.85rem;
        margin-bottom: 0.55rem;
        font-size: 0.85rem;
    }

    .chat-assistant {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.7rem 0.85rem;
        margin-bottom: 0.55rem;
        font-size: 0.85rem;
    }

    .filter-ok {
        background: var(--ok-soft);
        border: 1px solid rgba(34, 197, 94, 0.3);
        color: #86efac;
        border-radius: 8px;
        padding: 0.45rem 0.65rem;
        font-size: 0.78rem;
        margin: 0.35rem 0 0.65rem 0;
    }

    .result-box {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.75rem 0.9rem;
        margin: 0.5rem 0;
    }

    .result-box .label {
        font-size: 0.72rem;
        color: var(--muted);
    }

    .result-box .value {
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--text);
        margin-top: 0.15rem;
    }

    .stButton > button {
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--surface);
        color: var(--text);
    }

    .stButton > button[kind="primary"] {
        background: var(--accent);
        border-color: var(--accent);
        color: white;
    }

    div[data-testid="stMetric"] {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 0.4rem 0.65rem;
    }

    .stat-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0.55rem;
        margin-top: 0.85rem;
    }

    .stat-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 0.7rem 0.8rem;
    }

    .stat-card .label {
        font-size: 0.72rem;
        color: var(--muted);
        margin-bottom: 0.25rem;
    }

    .stat-card .value {
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text);
    }

    @media (max-width: 1100px) {
        .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
</style>
"""


def inject_styles() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)
