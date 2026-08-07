"""파일 요약 builder 레지스트리 — 프로필 summary_builder 로 선택."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

SummaryBuildFn = Callable[..., str]
SummaryDetectFn = Callable[[pd.DataFrame], bool]

_REGISTRY: dict[str, dict[str, Any]] = {}


def register_summary_builder(
    name: str,
    build: SummaryBuildFn,
    *,
    detect: SummaryDetectFn | None = None,
) -> None:
    """``summary_builder: <name>`` 에 연결할 요약 함수를 등록한다."""
    key = str(name).strip().lower()
    if not key:
        raise ValueError("summary builder name must be non-empty")
    _REGISTRY[key] = {"build": build, "detect": detect}


def list_summary_builders() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get_summary_builder(name: str) -> dict[str, Any] | None:
    return _REGISTRY.get(str(name).strip().lower())


def ensure_builtin_summary_builders() -> None:
    """내장 generic / budget 빌더를 한 번 등록한다."""
    if "generic" in _REGISTRY and "budget" in _REGISTRY:
        return
    from core.summary.budget_summary import build_budget_summary, looks_like_budget_table
    from core.summary.generic_summary import build_generic_summary

    register_summary_builder("generic", build_generic_summary)
    register_summary_builder(
        "budget",
        build_budget_summary,
        detect=looks_like_budget_table,
    )


def run_summary_builder(
    builder_name: str,
    df: pd.DataFrame,
    *,
    file_name: str | None = None,
    sheet_name: str | None = None,
    sheets: list[str] | None = None,
    excel_shape: tuple[int, int] | None = None,
    profile_name: str | None = None,
) -> str:
    """등록된 builder로 요약. 없거나 detect 실패 시 generic으로 폴백."""
    ensure_builtin_summary_builders()
    entry = get_summary_builder(builder_name) or get_summary_builder("generic")
    assert entry is not None
    detect = entry.get("detect")
    build = entry["build"]
    if detect is not None and not detect(df):
        generic = get_summary_builder("generic")
        assert generic is not None
        build = generic["build"]
    return build(
        df,
        file_name=file_name,
        sheet_name=sheet_name,
        sheets=sheets or [],
        excel_shape=excel_shape,
        profile_name=profile_name,
    )
