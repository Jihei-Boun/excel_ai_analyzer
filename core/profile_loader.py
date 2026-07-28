"""profiles/*.yaml 로더 — 컬럼 힌트·예산 프로필."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def _as_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list of strings, got {type(value).__name__}")
    return tuple(str(item) for item in value)


def _as_str(value: Any, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string, got {type(value).__name__}")
    return value.strip()


def _as_int(value: Any, *, field: str, default: int) -> int:
    if value is None:
        return default
    return int(value)


@lru_cache(maxsize=1)
def load_column_hints() -> dict[str, tuple[str, ...]]:
    path = PROFILES_DIR / "column_hints.yaml"
    data = _load_yaml(path)
    return {
        "group_column_hints": _as_tuple(data.get("group_column_hints"), field="group_column_hints"),
        "group_column_suffixes": _as_tuple(
            data.get("group_column_suffixes"), field="group_column_suffixes"
        ),
        "group_column_exact": _as_tuple(data.get("group_column_exact"), field="group_column_exact"),
        "item_column_hints": _as_tuple(data.get("item_column_hints"), field="item_column_hints"),
        "code_column_hints": _as_tuple(data.get("code_column_hints"), field="code_column_hints"),
        "code_metric_name_hints": _as_tuple(
            data.get("code_metric_name_hints"), field="code_metric_name_hints"
        ),
        "amount_column_hints": _as_tuple(
            data.get("amount_column_hints"), field="amount_column_hints"
        ),
    }


@lru_cache(maxsize=1)
def load_budget_profile() -> dict[str, Any]:
    path = PROFILES_DIR / "budget.yaml"
    data = _load_yaml(path)
    return {
        "detect_min_hits": _as_int(
            data.get("detect_min_hits"), field="detect_min_hits", default=2
        ),
        "column_hints": _as_tuple(data.get("column_hints"), field="column_hints"),
        "item_column_candidates": _as_tuple(
            data.get("item_column_candidates"), field="item_column_candidates"
        ),
        "category_column_candidates": _as_tuple(
            data.get("category_column_candidates"), field="category_column_candidates"
        ),
        "budget_column_candidates": _as_tuple(
            data.get("budget_column_candidates"), field="budget_column_candidates"
        ),
        "executed_column_candidates": _as_tuple(
            data.get("executed_column_candidates"), field="executed_column_candidates"
        ),
        "remaining_column_candidates": _as_tuple(
            data.get("remaining_column_candidates"), field="remaining_column_candidates"
        ),
        "current_remaining_column_candidates": _as_tuple(
            data.get("current_remaining_column_candidates"),
            field="current_remaining_column_candidates",
        ),
        "key_column_hints": _as_tuple(data.get("key_column_hints"), field="key_column_hints"),
        "footer_labels": _as_tuple(data.get("footer_labels"), field="footer_labels"),
        "intro": _as_str(data.get("intro"), field="intro"),
    }


def clear_profile_cache() -> None:
    """테스트·핫리로드용."""
    load_column_hints.cache_clear()
    load_budget_profile.cache_clear()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path.name} root must be a mapping")
    return data
