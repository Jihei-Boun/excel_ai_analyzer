"""profiles/*.yaml 로더 — 컬럼 힌트·의미·일반/예산 프로필."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

_PROFILE_NAMES = frozenset({"generic", "budget"})


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


def _parse_meanings(value: Any, *, field: str) -> tuple[tuple[tuple[str, ...], str], ...]:
    """YAML meanings 리스트 → ((hints...), meaning) 튜플."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list, got {type(value).__name__}")
    rules: list[tuple[tuple[str, ...], str]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError(f"{field}[{idx}] must be a mapping")
        hints = _as_tuple(item.get("hints"), field=f"{field}[{idx}].hints")
        meaning = _as_str(item.get("meaning"), field=f"{field}[{idx}].meaning")
        if not hints or not meaning:
            continue
        rules.append((hints, meaning))
    return tuple(rules)


def _parse_semantic_hints(value: Any, *, field: str) -> dict[str, Any]:
    """LLM 힌트용 semantic_hints. 실행 경로를 강제하지 않는다."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a mapping, got {type(value).__name__}")
    result: dict[str, Any] = {}
    for key, raw in value.items():
        name = str(key)
        if isinstance(raw, list):
            result[name] = [str(item) for item in raw]
        elif isinstance(raw, str):
            result[name] = raw
        else:
            result[name] = raw
    return result


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
def load_column_meanings() -> tuple[tuple[tuple[str, ...], str], ...]:
    """일반 모드 컬럼 의미 규칙 (profiles/column_meanings.yaml)."""
    path = PROFILES_DIR / "column_meanings.yaml"
    data = _load_yaml(path)
    return _parse_meanings(data.get("meanings"), field="meanings")


@lru_cache(maxsize=1)
def load_budget_profile() -> dict[str, Any]:
    path = PROFILES_DIR / "budget.yaml"
    data = _load_yaml(path)
    return {
        "name": "budget",
        "summary": _as_str(data.get("summary"), field="summary") or "budget",
        "currency": _as_str(data.get("currency"), field="currency") or "krw",
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
        "domain": _as_str(data.get("domain"), field="domain"),
        "semantic_hints": _parse_semantic_hints(
            data.get("semantic_hints"), field="semantic_hints"
        ),
        "suggested_prompts": _as_tuple(
            data.get("suggested_prompts"), field="suggested_prompts"
        ),
        "suggested_prompts_multi_file": _as_tuple(
            data.get("suggested_prompts_multi_file"),
            field="suggested_prompts_multi_file",
        ),
        "meanings": _parse_meanings(data.get("meanings"), field="meanings"),
    }


@lru_cache(maxsize=1)
def load_generic_profile() -> dict[str, Any]:
    path = PROFILES_DIR / "generic.yaml"
    data = _load_yaml(path)
    return {
        "name": "generic",
        "summary": _as_str(data.get("summary"), field="summary") or "generic",
        "currency": _as_str(data.get("currency"), field="currency") or "none",
        "suggested_prompts": _as_tuple(
            data.get("suggested_prompts"), field="suggested_prompts"
        ),
        "suggested_prompts_multi_file": _as_tuple(
            data.get("suggested_prompts_multi_file"),
            field="suggested_prompts_multi_file",
        ),
        "suggested_prompts_multi_sheet": _as_tuple(
            data.get("suggested_prompts_multi_sheet"),
            field="suggested_prompts_multi_sheet",
        ),
        "domain": _as_str(data.get("domain"), field="domain") or "generic",
        "semantic_hints": _parse_semantic_hints(
            data.get("semantic_hints"), field="semantic_hints"
        ),
        "meanings": load_column_meanings(),
        "footer_labels": (),
    }


def load_profile(name: str) -> dict[str, Any]:
    """프로필 이름으로 로드. ``generic`` | ``budget``."""
    key = str(name or "generic").strip().lower()
    if key not in _PROFILE_NAMES:
        raise ValueError(f"Unknown profile: {name!r} (expected generic|budget)")
    if key == "budget":
        return load_budget_profile()
    return load_generic_profile()


def active_profile(*, use_budget_profile: bool = False) -> dict[str, Any]:
    """예산 표 모드 여부에 따른 활성 프로필."""
    return load_profile("budget" if use_budget_profile else "generic")


def load_meaning_rules(*, use_budget_profile: bool = False) -> tuple[
    tuple[tuple[str, ...], str], ...
]:
    """의미 규칙. 예산 모드면 budget meanings를 앞에 두고 일반 규칙을 이어 붙인다."""
    generic = load_column_meanings()
    if not use_budget_profile:
        return generic
    budget = load_budget_profile().get("meanings") or ()
    return tuple(budget) + tuple(generic)


def clear_profile_cache() -> None:
    """테스트·핫리로드용."""
    load_column_hints.cache_clear()
    load_column_meanings.cache_clear()
    load_budget_profile.cache_clear()
    load_generic_profile.cache_clear()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path.name} root must be a mapping")
    return data
