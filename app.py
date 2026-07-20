"""Streamlit 진입점 — 실행: streamlit run app.py"""

from __future__ import annotations

import streamlit as st

from ui.chat_panel import render_chat_panel
from ui.header import render_header
from ui.session_store import init_session_state
from ui.sidebar import render_sidebar
from ui.styles import inject_styles
from ui.upload import apply_pending_widget_sync
from ui.workspace import render_workspace

st.set_page_config(
    page_title="Excel AI Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_styles()
init_session_state()
apply_pending_widget_sync()
render_sidebar()
render_header()

col_workspace, col_chat = st.columns([1.7, 1], gap="large")

with col_workspace:
    render_workspace()

with col_chat:
    render_chat_panel()
