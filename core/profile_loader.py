"""profiles/*.yaml 로더 — 컬럼 힌트·의미·도메인 프로필."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import yaml

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

# 프로필이 아닌 공유 YAML
_SHARED_YAML_NAMES = frozenset({"column_hints.yaml", "column_meanings.yaml"})

# UI/요청 단위로 활성 프로필 이름을 주입
_active_profile_name: ContextVar[str | None] = ContextVar(
    "analysis_profile_name", default=None
)

_HINT_KEYS = (
    "group_column_hints",
    "group_column_suffixes",
    "group_column_exact",
    "item_column_hints",
    "code_column_hints",
    "code_metric_name_hints",
    "amount_column_hints",
)

_ROLE_KEYS = (
    "label_columns",
    "group_columns",
    "metric_denominator",
    "metric_numerator",
    "metric_remaining",
    "metric_remaining_current",
    "key_metrics",
)


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


def _as_bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise TypeError(f"{field} must be a boolean, got {type(value).__name__}")


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


def _parse_str_map(value: Any, *, field: str) -> dict[str, Any]:
    """중첩 dict: 리스트→tuple, 스칼라→str/bool/기타."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a mapping, got {type(value).__name__}")
    out: dict[str, Any] = {}
    for key, raw in value.items():
        name = str(key)
        if isinstance(raw, list):
            out[name] = tuple(str(item) for item in raw)
        elif isinstance(raw, bool):
            out[name] = raw
        elif isinstance(raw, (int, float)):
            out[name] = raw
        elif raw is None:
            out[name] = ()
        else:
            out[name] = str(raw)
    return out


def _parse_roles(data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """roles 블록 + 레거시 *_column_candidates 별칭을 역할 스키마로 정규화."""
    raw_roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    roles: dict[str, tuple[str, ...]] = {}
    for key in _ROLE_KEYS:
        roles[key] = _as_tuple(raw_roles.get(key), field=f"roles.{key}")

    # 레거시 필드 → roles (roles가 비어 있을 때만)
    legacy_map = {
        "label_columns": "item_column_candidates",
        "group_columns": "category_column_candidates",
        "metric_denominator": "budget_column_candidates",
        "metric_numerator": "executed_column_candidates",
        "metric_remaining": "remaining_column_candidates",
        "metric_remaining_current": "current_remaining_column_candidates",
        "key_metrics": "key_column_hints",
    }
    for role_key, legacy_key in legacy_map.items():
        if roles[role_key]:
            continue
        roles[role_key] = _as_tuple(data.get(legacy_key), field=legacy_key)
    return roles


def _normalize_profile(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """도메인 YAML을 공통 프로필 dict로 정규화한다."""
    domain = _as_str(data.get("domain"), field="domain") or name
    meanings = _parse_meanings(data.get("meanings"), field="meanings")
    if name == "generic" and not meanings:
        meanings = load_column_meanings()

    roles = _parse_roles(data)
    column_prefs = _parse_str_map(data.get("column_prefs"), field="column_prefs")
    column_hint_extras = _parse_str_map(
        data.get("column_hint_extras"), field="column_hint_extras"
    )
    summary_builder = (
        _as_str(data.get("summary_builder"), field="summary_builder")
        or _as_str(data.get("summary"), field="summary")
        or name
    )

    preferred = _as_tuple(data.get("preferred_labels"), field="preferred_labels")
    if not preferred and roles["label_columns"]:
        preferred = roles["label_columns"]

    return {
        "name": name,
        "summary": _as_str(data.get("summary"), field="summary") or name,
        "summary_builder": summary_builder,
        "currency": _as_str(data.get("currency"), field="currency") or "none",
        "domain": domain,
        "detect_min_hits": _as_int(
            data.get("detect_min_hits"), field="detect_min_hits", default=2
        ),
        "enable_column_prefs": _as_bool(
            data.get("enable_column_prefs"),
            field="enable_column_prefs",
            default=(name == "budget"),
        ),
        "preferred_labels": preferred,
        "plan_guidance": _as_str(data.get("plan_guidance"), field="plan_guidance"),
        "column_hints": _as_tuple(data.get("column_hints"), field="column_hints"),
        # 역할 스키마
        "roles": roles,
        "label_columns": roles["label_columns"],
        "group_columns": roles["group_columns"],
        "metric_denominator": roles["metric_denominator"],
        "metric_numerator": roles["metric_numerator"],
        "metric_remaining": roles["metric_remaining"],
        "metric_remaining_current": roles["metric_remaining_current"],
        "key_metrics": roles["key_metrics"],
        # 레거시 별칭 (budget_summary / constants 호환)
        "item_column_candidates": roles["label_columns"],
        "category_column_candidates": roles["group_columns"],
        "budget_column_candidates": roles["metric_denominator"],
        "executed_column_candidates": roles["metric_numerator"],
        "remaining_column_candidates": roles["metric_remaining"],
        "current_remaining_column_candidates": roles["metric_remaining_current"],
        "key_column_hints": roles["key_metrics"],
        "column_prefs": column_prefs,
        "column_hint_extras": column_hint_extras,
        "footer_labels": _as_tuple(data.get("footer_labels"), field="footer_labels"),
        "intro": _as_str(data.get("intro"), field="intro"),
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
        "suggested_prompts_multi_sheet": _as_tuple(
            data.get("suggested_prompts_multi_sheet"),
            field="suggested_prompts_multi_sheet",
        ),
        "meanings": meanings,
    }


@lru_cache(maxsize=1)
def load_column_hints() -> dict[str, tuple[str, ...]]:
    path = PROFILES_DIR / "column_hints.yaml"
    data = _load_yaml(path)
    return {
        key: _as_tuple(data.get(key), field=key)
        for key in _HINT_KEYS
    }


@lru_cache(maxsize=1)
def load_column_meanings() -> tuple[tuple[tuple[str, ...], str], ...]:
    """일반 모드 컬럼 의미 규칙 (profiles/column_meanings.yaml)."""
    path = PROFILES_DIR / "column_meanings.yaml"
    data = _load_yaml(path)
    return _parse_meanings(data.get("meanings"), field="meanings")


def list_profile_names() -> tuple[str, ...]:
    """profiles/*.yaml 중 공유 파일을 제외한 프로필 이름."""
    if not PROFILES_DIR.is_dir():
        return ()
    names = sorted(
        path.stem
        for path in PROFILES_DIR.glob("*.yaml")
        if path.name not in _SHARED_YAML_NAMES
    )
    return tuple(names)


def profile_display_label(name: str) -> str:
    """사이드바용 짧은 표시명."""
    labels = {
        "generic": "일반 (generic)",
        "budget": "예산 표 (budget)",
        "sales": "매출 (sales)",
    }
    return labels.get(name, name)


@lru_cache(maxsize=16)
def _load_named_profile(name: str) -> dict[str, Any]:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.is_file():
        known = ", ".join(list_profile_names()) or "(none)"
        raise ValueError(f"Unknown profile: {name!r} (available: {known})")
    data = _load_yaml(path)
    return _normalize_profile(name, data)


def load_budget_profile() -> dict[str, Any]:
    return _load_named_profile("budget")


def load_generic_profile() -> dict[str, Any]:
    return _load_named_profile("generic")


def load_profile(name: str) -> dict[str, Any]:
    """프로필 이름으로 로드. ``profiles/<name>.yaml`` 이 있으면 허용."""
    key = str(name or "generic").strip().lower()
    if not key or "/" in key or "\\" in key or ".." in key:
        raise ValueError(f"Invalid profile name: {name!r}")
    return _load_named_profile(key)


def resolve_profile_name(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> str:
    """명시 이름 → 컨텍스트 → (deprecated) budget/generic 폴백.

    ``use_budget_profile`` 은 하위 호환용이다. 신규 코드는 ``profile_name`` 또는
    ``use_profile()`` 컨텍스트만 사용한다.
    """
    if profile_name:
        return str(profile_name).strip().lower()
    ctx = _active_profile_name.get()
    if ctx:
        return str(ctx).strip().lower()
    return "budget" if use_budget_profile else "generic"


def set_active_profile_name(name: str | None) -> None:
    """요청 스코프에서 활성 프로필을 설정한다."""
    _active_profile_name.set(str(name).strip().lower() if name else None)


@contextmanager
def use_profile(name: str | None) -> Iterator[None]:
    token = _active_profile_name.set(str(name).strip().lower() if name else None)
    try:
        yield
    finally:
        _active_profile_name.reset(token)


def active_profile(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> dict[str, Any]:
    """활성 도메인 프로필."""
    return load_profile(
        resolve_profile_name(
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        )
    )


def preferred_labels_for(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> tuple[str, ...]:
    return tuple(
        active_profile(
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        ).get("preferred_labels")
        or ()
    )


def footer_labels_for(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> tuple[str, ...]:
    return tuple(
        active_profile(
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        ).get("footer_labels")
        or ()
    )


def column_prefs_for(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> dict[str, Any]:
    """활성 프로필의 column_prefs dict."""
    return dict(
        active_profile(
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        ).get("column_prefs")
        or {}
    )


def roles_for(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> dict[str, tuple[str, ...]]:
    profile = active_profile(
        profile_name=profile_name, use_budget_profile=use_budget_profile,
    )
    roles = profile.get("roles") or {}
    return {key: tuple(roles.get(key) or ()) for key in _ROLE_KEYS}


def column_hints_for(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> dict[str, tuple[str, ...]]:
    """공유 힌트 + 활성 프로필 column_hint_extras 병합."""
    base = load_column_hints()
    extras = active_profile(
        profile_name=profile_name, use_budget_profile=use_budget_profile,
    ).get("column_hint_extras") or {}
    merged: dict[str, tuple[str, ...]] = {}
    for key in _HINT_KEYS:
        seen: list[str] = []
        for item in tuple(base.get(key) or ()) + tuple(extras.get(key) or ()):
            text = str(item)
            if text not in seen:
                seen.append(text)
        merged[key] = tuple(seen)
    return merged


def load_meaning_rules(
    *,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """의미 규칙. 도메인 meanings를 앞에 두고 일반 규칙을 이어 붙인다."""
    generic = load_column_meanings()
    name = resolve_profile_name(
        profile_name=profile_name, use_budget_profile=use_budget_profile,
    )
    if name in {"generic", ""}:
        return generic
    domain = load_profile(name).get("meanings") or ()
    return tuple(domain) + tuple(generic)


def clear_profile_cache() -> None:
    """테스트·핫리로드용."""
    load_column_hints.cache_clear()
    load_column_meanings.cache_clear()
    _load_named_profile.cache_clear()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path.name} root must be a mapping")
    return data
