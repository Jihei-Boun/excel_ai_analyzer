"""다크 테마 CSS."""

from __future__ import annotations


def dark_theme_css() -> str:
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

    .stApp .chat-inline-code {
        background: #1e293b !important;
        color: #e2e8f0 !important;
        -webkit-text-fill-color: #e2e8f0 !important;
        border: 1px solid #334155;
    }

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


