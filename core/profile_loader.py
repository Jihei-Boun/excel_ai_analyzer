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

    display_labels = _parse_str_map(
        data.get("display_labels"), field="display_labels"
    )
    # 스칼라만 유지 (리스트 값은 display에 쓰지 않음)
    display_labels = {
        key: str(val)
        for key, val in display_labels.items()
        if not isinstance(val, (tuple, list))
    }
    guardrail_hints = _parse_str_map(
        data.get("guardrail_hints"), field="guardrail_hints"
    )
    guardrail_hints = {
        key: str(val)
        for key, val in guardrail_hints.items()
        if not isinstance(val, (tuple, list)) and str(val).strip()
    }

    from core.common.locale_support import normalize_locale

    locale = normalize_locale(_as_str(data.get("locale"), field="locale") or "ko")

    return {
        "name": name,
        "summary": _as_str(data.get("summary"), field="summary") or name,
        "summary_builder": summary_builder,
        "currency": _as_str(data.get("currency"), field="currency") or "none",
        "domain": domain,
        "locale": locale,
        "language_instruction": _as_str(
            data.get("language_instruction"), field="language_instruction"
        ),
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
        "interpret_guidance": _as_str(
            data.get("interpret_guidance"), field="interpret_guidance"
        ),
        "structured_analysis_keywords": _as_tuple(
            data.get("structured_analysis_keywords"),
            field="structured_analysis_keywords",
        ),
        "complex_analysis_keywords": _as_tuple(
            data.get("complex_analysis_keywords"),
            field="complex_analysis_keywords",
        ),
        "display_labels": display_labels,
        "guardrail_hints": guardrail_hints,
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
        "generic_en": "General EN (generic_en)",
        "budget": "예산 표 (budget)",
        "sales": "매출 (sales)",
        "inventory": "재고 (inventory)",
        "custom": "커스텀 템플릿 (custom)",
    }
    try:
        profile = load_profile(name)
        locale = str(profile.get("locale") or "ko")
        base = labels.get(name, name)
        if name not in labels and locale == "en":
            return f"{name} (en)"
        return base
    except ValueError:
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
) -> str:
    """명시 이름 → 컨텍스트 → generic 폴백."""
    if profile_name:
        return str(profile_name).strip().lower()
    ctx = _active_profile_name.get()
    if ctx:
        return str(ctx).strip().lower()
    return "generic"


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
) -> dict[str, Any]:
    """활성 도메인 프로필."""
    return load_profile(
        resolve_profile_name(
            profile_name=profile_name,
        )
    )


def preferred_labels_for(
    *,
    profile_name: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        active_profile(
            profile_name=profile_name,
        ).get("preferred_labels")
        or ()
    )


def footer_labels_for(
    *,
    profile_name: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        active_profile(
            profile_name=profile_name,
        ).get("footer_labels")
        or ()
    )


def column_prefs_for(
    *,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """활성 프로필의 column_prefs dict."""
    return dict(
        active_profile(
            profile_name=profile_name,
        ).get("column_prefs")
        or {}
    )


_DISPLAY_LABEL_DEFAULTS: dict[str, str] = {
    "rate": "비율",
    "item": "항목",
    "denominator": "분모",
    "item_count": "항목수",
    "zero_rate_count": "0%비율수",
    "zero_denominator_sum": "0%분모합",
    "max_rate": "최대비율",
    "min_rate": "최소비율",
    "diff": "차이",
    "split_label": "구분",
}


def locale_for(*, profile_name: str | None = None) -> str:
    from core.common.locale_support import normalize_locale

    return normalize_locale(
        active_profile(profile_name=profile_name).get("locale") or "ko"
    )


def locale_preset_for(*, profile_name: str | None = None) -> dict[str, Any]:
    from core.common.locale_support import locale_preset

    return locale_preset(locale_for(profile_name=profile_name))


def schema_ui_for(*, profile_name: str | None = None) -> dict[str, str]:
    """스키마·결측 UI 라벨/문구 (locale 프리셋)."""
    raw = locale_preset_for(profile_name=profile_name).get("schema_ui") or {}
    return {str(k): str(v) for k, v in dict(raw).items()}


def language_instruction_for(*, profile_name: str | None = None) -> str:
    """LLM 응답 언어 지시. 프로필 오버라이드 → locale 프리셋."""
    profile = active_profile(profile_name=profile_name)
    override = str(profile.get("language_instruction") or "").strip()
    if override:
        return override
    return str(locale_preset_for(profile_name=profile_name).get("language_instruction") or "")


def interpret_system_prompt_for(*, profile_name: str | None = None) -> str:
    """해석 LLM system 프롬프트 (역할·언어·스타일)."""
    preset = locale_preset_for(profile_name=profile_name)
    lang = language_instruction_for(profile_name=profile_name)
    parts = [
        str(preset.get("interpret_role") or ""),
        lang,
        "제공된 JSON에 없는 수치·항목·비율을 만들지 마세요. "
        "Do not invent numbers, items, or rates absent from the JSON. "
        "상관관계와 원인을 구분하세요. Distinguish correlation from causation. ",
        str(preset.get("interpret_style") or ""),
        "correlation이면 1) 전체 상관 2) 분포·양수 표본 3) 결론 순으로, "
        "그 외에는 가능하면 1) 전체 비교 2) 그룹별 특징 3) 결론 순으로 쓰세요. "
        "For correlation: (1) overall correlation (2) distribution / positive-pair "
        "sample (3) conclusion. Otherwise prefer (1) overall comparison "
        "(2) group traits (3) conclusion.",
    ]
    return " ".join(p for p in parts if p).strip()


def interpret_user_prefix_for(*, profile_name: str | None = None) -> str:
    return str(
        locale_preset_for(profile_name=profile_name).get("interpret_user_prefix") or ""
    )


def meaning_prompts_for(
    *,
    profile_name: str | None = None,
) -> tuple[str, str]:
    """컬럼 의미 설명 LLM (system, user_suffix)."""
    preset = locale_preset_for(profile_name=profile_name)
    system = str(preset.get("meaning_system") or "")
    # language_instruction을 system 앞에 보강
    lang = language_instruction_for(profile_name=profile_name)
    if lang and lang not in system:
        system = f"{lang} {system}"
    suffix = str(preset.get("meaning_user_suffix") or "")
    return system, suffix


def dataframe_request_hint_for(*, profile_name: str | None = None) -> str:
    return str(
        locale_preset_for(profile_name=profile_name).get("dataframe_request_hint") or ""
    )


def plan_language_note_for(*, profile_name: str | None = None) -> str:
    return str(
        locale_preset_for(profile_name=profile_name).get("plan_language_note") or ""
    )


def locale_intent_keywords_for(*, profile_name: str | None = None) -> tuple[str, ...]:
    extras = locale_preset_for(profile_name=profile_name).get("intent_keywords_extra") or ()
    return tuple(str(x) for x in extras)


def display_labels_for(
    *,
    profile_name: str | None = None,
) -> dict[str, str]:
    """결과 표·분포 요약용 표시 라벨. locale 기본값 + column_prefs + 프로필."""
    profile = active_profile(profile_name=profile_name)
    labels = {
        str(k): str(v)
        for k, v in dict(profile.get("display_labels") or {}).items()
        if v is not None and str(v).strip()
    }
    prefs = column_prefs_for(profile_name=profile_name)
    for key, pref_key in (
        ("rate", "rate_name"),
        ("diff", "diff_name"),
        ("split_label", "split_label_name"),
        ("share", "share_name"),
    ):
        if key not in labels and prefs.get(pref_key):
            labels[key] = str(prefs[pref_key])
    preset_labels = locale_preset_for(profile_name=profile_name).get("display_labels")
    if isinstance(preset_labels, dict) and preset_labels:
        out = {str(k): str(v) for k, v in preset_labels.items()}
    else:
        out = dict(_DISPLAY_LABEL_DEFAULTS)
    out.update(labels)
    return out


_GUARDRAIL_HINT_DEFAULTS: dict[str, str] = {
    "code_col": "코드",
    "name_col": "명칭",
    "group_col": "분류",
    "fill_example": "상위 분류값",
    "footer_examples": "하단 요약 행",
}


def guardrail_hints_for(
    *,
    profile_name: str | None = None,
) -> dict[str, str]:
    """PandasAI 가드레일·스키마 힌트용 도메인 예시 문자열."""
    profile = active_profile(profile_name=profile_name)
    hints = {
        str(k): str(v)
        for k, v in dict(profile.get("guardrail_hints") or {}).items()
        if v is not None and str(v).strip()
    }
    roles = roles_for(profile_name=profile_name)
    if "group_col" not in hints and roles.get("group_columns"):
        hints["group_col"] = str(roles["group_columns"][0])
    if "name_col" not in hints and roles.get("label_columns"):
        hints["name_col"] = str(roles["label_columns"][0])
    footers = footer_labels_for(profile_name=profile_name)
    if "footer_examples" not in hints and footers:
        hints["footer_examples"] = "·".join(footers)
    out = dict(_GUARDRAIL_HINT_DEFAULTS)
    out.update(hints)
    return out


def interpret_guidance_for(
    *,
    profile_name: str | None = None,
) -> str:
    return str(
        active_profile(profile_name=profile_name).get("interpret_guidance") or ""
    ).strip()


def structured_analysis_keywords_for(
    *,
    profile_name: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        active_profile(profile_name=profile_name).get("structured_analysis_keywords")
        or ()
    )


def complex_analysis_keywords_for(
    *,
    profile_name: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        active_profile(profile_name=profile_name).get("complex_analysis_keywords")
        or ()
    )


def preferred_columns_present(
    columns: set[str] | list[str] | tuple[str, ...],
    *,
    profile_name: str | None = None,
) -> list[str]:
    """스키마에 존재하는 preferred/label/group 열 (출력 후보)."""
    colset = {str(c) for c in columns}
    roles = roles_for(profile_name=profile_name)
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in (
        *preferred_labels_for(profile_name=profile_name),
        *roles.get("label_columns", ()),
        *roles.get("group_columns", ()),
    ):
        name = str(candidate)
        if name in colset and name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


def detail_label_columns_present(
    columns: set[str] | list[str] | tuple[str, ...],
    *,
    profile_name: str | None = None,
) -> list[str]:
    """세부 항목 라벨 열 (그룹/분류보다 label_columns 우선)."""
    colset = {str(c) for c in columns}
    roles = roles_for(profile_name=profile_name)
    labels = list(roles.get("label_columns") or ())
    if not labels:
        groups = set(roles.get("group_columns") or ())
        labels = [
            c
            for c in preferred_labels_for(profile_name=profile_name)
            if c not in groups
        ]
    return [c for c in labels if c in colset]


def related_metric_columns_present(
    columns: set[str] | list[str] | tuple[str, ...],
    *,
    profile_name: str | None = None,
    key: str = "find_related_metrics",
) -> list[str]:
    """column_prefs의 관련 메트릭 힌트 중 스키마에 있는 열."""
    colset = {str(c) for c in columns}
    prefs = column_prefs_for(profile_name=profile_name)
    raw = prefs.get(key) or ()
    if isinstance(raw, str):
        raw = (raw,) if raw else ()
    return [str(c) for c in raw if str(c) in colset]


def default_rate_name(*, profile_name: str | None = None) -> str:
    return display_labels_for(profile_name=profile_name).get("rate", "비율")


def default_diff_name(*, profile_name: str | None = None) -> str:
    return display_labels_for(profile_name=profile_name).get("diff", "차이")


def default_split_label_name(*, profile_name: str | None = None) -> str:
    return display_labels_for(profile_name=profile_name).get("split_label", "구분")


def roles_for(
    *,
    profile_name: str | None = None,
) -> dict[str, tuple[str, ...]]:
    profile = active_profile(
        profile_name=profile_name,
    )
    roles = profile.get("roles") or {}
    return {key: tuple(roles.get(key) or ()) for key in _ROLE_KEYS}


def column_hints_for(
    *,
    profile_name: str | None = None,
) -> dict[str, tuple[str, ...]]:
    """공유 힌트 + 활성 프로필 column_hint_extras 병합."""
    base = load_column_hints()
    extras = active_profile(
        profile_name=profile_name,
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
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """의미 규칙. 도메인 meanings를 앞에 두고 일반 규칙을 이어 붙인다."""
    generic = load_column_meanings()
    name = resolve_profile_name(
        profile_name=profile_name,
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


def score_profile_for_frame(
    df: Any,
    profile_name: str,
) -> int:
    """컬럼명 기준으로 프로필 적합 점수를 계산한다."""
    try:
        columns = [str(c) for c in getattr(df, "columns", [])]
    except Exception:  # noqa: BLE001
        return 0
    if not columns:
        return 0
    joined = " ".join(columns)
    joined_norm = joined.lower()
    profile = load_profile(profile_name)
    hits = 0
    seen: set[str] = set()

    def _count(candidates: Any) -> None:
        nonlocal hits
        for raw in candidates or ():
            hint = str(raw).strip()
            if not hint or hint in seen:
                continue
            seen.add(hint)
            if hint in joined or hint.lower() in joined_norm:
                hits += 1

    _count(profile.get("column_hints"))
    extras = profile.get("column_hint_extras") or {}
    for key in (
        "group_column_hints",
        "group_column_exact",
        "item_column_hints",
        "amount_column_hints",
        "code_column_hints",
    ):
        _count(extras.get(key))
    roles = profile.get("roles") or {}
    for key in _ROLE_KEYS:
        _count(roles.get(key))
    _count(profile.get("preferred_labels"))
    return hits


def suggest_profile_name(
    df: Any,
    *,
    candidates: tuple[str, ...] | None = None,
) -> tuple[str, int]:
    """업로드 표에 맞는 프로필을 추정한다. (이름, 점수).

    generic은 후보에서 제외하고, detect_min_hits 미만이면 generic을 반환한다.
    """
    names = candidates or tuple(n for n in list_profile_names() if n != "generic")
    best_name = "generic"
    best_score = 0
    for name in names:
        try:
            profile = load_profile(name)
        except ValueError:
            continue
        score = score_profile_for_frame(df, name)
        min_hits = int(profile.get("detect_min_hits") or 2)
        if score >= min_hits and score > best_score:
            best_name = name
            best_score = score
    return best_name, best_score


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path.name} root must be a mapping")
    return data
