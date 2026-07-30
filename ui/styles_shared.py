"""레이아웃·구조 CSS (테마 무관)."""

from __future__ import annotations


def shared_layout_css() -> str:
    """색과 무관한 레이아웃·구조 규칙."""
    return """
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');

    html, body, .stApp {
        font-family: 'DM Sans', sans-serif;
    }

    #MainMenu, footer {
        visibility: hidden;
    }

    /* 테마 CSS/스크립트 주입용 st.html 빈 영역 숨김 */
    [data-testid="stHtml"]:has(style),
    [data-testid="stHtml"]:has(script) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        border: none !important;
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

    .stApp .chat-inline-code {
        display: inline;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 0.9em;
        padding: 0.1em 0.35em;
        border-radius: 4px;
        white-space: pre-wrap;
        word-break: break-word;
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

    /* 업로더 안 파일 칩 — 깨진 아이콘/칩삭제만 숨김 (파일명은 유지) */
    [data-testid="stFileChip"] > :first-child:not(:has([data-testid="stFileChipName"])),
    [data-testid="stFileChipImagePreview"],
    [data-testid="stFileChipDeleteBtn"],
    [data-testid="stFileChipIconSpinner"],
    [data-testid="stFileChipIconError"] {
        display: none !important;
    }

    [data-testid="stFileChips"] {
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 0.5rem !important;
    }

    [data-testid="stFileChip"] {
        border-radius: 8px !important;
        padding: 0.45rem 0.7rem !important;
    }
"""


