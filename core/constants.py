"""프로젝트 전역 상수 — 경로, 기본 설정, 프로필 재노출."""

from __future__ import annotations

from pathlib import Path

from core.profile_loader import load_budget_profile, load_column_hints

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
CHARTS_DIR = PROJECT_ROOT / "exports" / "charts"
PROFILES_DIR = PROJECT_ROOT / "profiles"

# ---------------------------------------------------------------------------
# Ollama / PandasAI 기본값
# ---------------------------------------------------------------------------
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_TIMEOUT_SEC = 5
PANDASAI_MAX_RETRIES = 1

# ---------------------------------------------------------------------------
# 예실대비표·예산 표 도메인 (예산 표 모드 ON일 때만 사용)
# 원본: profiles/budget.yaml
# ---------------------------------------------------------------------------
_budget = load_budget_profile()

BUDGET_COLUMN_HINTS = _budget["column_hints"]
BUDGET_DETECT_MIN_HITS = _budget["detect_min_hits"]
BUDGET_ITEM_COLUMN_CANDIDATES = _budget["item_column_candidates"]
BUDGET_CATEGORY_COLUMN_CANDIDATES = _budget["category_column_candidates"]
BUDGET_BUDGET_COLUMN_CANDIDATES = _budget["budget_column_candidates"]
BUDGET_EXECUTED_COLUMN_CANDIDATES = _budget["executed_column_candidates"]
BUDGET_REMAINING_COLUMN_CANDIDATES = _budget["remaining_column_candidates"]
BUDGET_CURRENT_REMAINING_COLUMN_CANDIDATES = _budget[
    "current_remaining_column_candidates"
]
BUDGET_KEY_COLUMN_HINTS = _budget["key_column_hints"]
BUDGET_FOOTER_LABELS = _budget["footer_labels"]
BUDGET_INTRO = _budget["intro"]

# ---------------------------------------------------------------------------
# 컬럼 역할 힌트 (분석·리스트 표시 공통)
# 원본: profiles/column_hints.yaml
# ---------------------------------------------------------------------------
_hints = load_column_hints()

GROUP_COLUMN_HINTS = _hints["group_column_hints"]
GROUP_COLUMN_SUFFIXES = _hints["group_column_suffixes"]
GROUP_COLUMN_EXACT = _hints["group_column_exact"]
ITEM_COLUMN_HINTS = _hints["item_column_hints"]
CODE_COLUMN_HINTS = _hints["code_column_hints"]
CODE_METRIC_NAME_HINTS = _hints["code_metric_name_hints"]
AMOUNT_COLUMN_HINTS = _hints["amount_column_hints"]

# ---------------------------------------------------------------------------
# 표시·샘플링 한도
# ---------------------------------------------------------------------------
SUMMARY_TOP_N = 3
SUMMARY_PREVIEW_COLS = 8
SUMMARY_NUMERIC_COLS = 5
CHART_MAX_CATEGORIES = 20
MULTI_FILE_INVENTORY_COLS = 12
CODE_METRIC_SAMPLE_SIZE = 20
CODE_METRIC_ABS_MAX = 10_000
CODE_METRIC_INT_RATIO = 0.8
CHAT_EXAMPLE_LIMIT = 4
CHAT_PREVIEW_ROWS = 10
CODE_PAIR_SAMPLE_SIZE = 8
SUMMARY_RANKING_BITS = 8
CODE_SUMMARY_LINE_LEN = 80
