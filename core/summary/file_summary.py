"""파일 요약 요청용 규칙 기반 분석 (라우터 + 공개 API)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from core.pai.pandasai_config import prepare_dataframe_for_ai
from core.summary.summary_builders import run_summary_builder
from core.summary.summary_utils import excel_shape

_SUMMARY_KEYWORDS = (
    "요약",
    "개요",
    "파일소개",
    "파일설명",
    "어떤파일",
    "무슨파일",
    "파일내용",
    "파일알려",
    "summarize",
    "summary",
    "overview",
)


def is_summary_request(prompt: str) -> bool:
    """파일 요약·개요 요청인지 판별한다. 차트 요청은 제외."""
    if not prompt or not prompt.strip():
        return False
    lowered = prompt.lower()
    if any(k in lowered for k in ("차트", "그래프", "chart", "plot", "graph")):
        return False
    normalized = re.sub(r"\s+", "", lowered)
    return any(keyword in normalized for keyword in _SUMMARY_KEYWORDS)


def build_file_summary(
    df: pd.DataFrame,
    *,
    file_name: str | None = None,
    sheet_name: str | None = None,
    sheet_names: list[str] | None = None,
    file_path: str | Path | None = None,
    profile_name: str | None = None,
) -> str:
    """DataFrame을 읽어 사람이 읽을 수 있는 파일 요약 문장을 만든다.

    프로필 ``summary_builder`` 이름에 등록된 빌더를 사용한다.
    """
    from core.profile_loader import active_profile, locale_for
    from core.summary.generic_summary import summary_copy

    copy = summary_copy(locale=locale_for(profile_name=profile_name))
    if df is None or df.empty:
        return copy["empty"]

    prepared = prepare_dataframe_for_ai(df)
    sheets = sheet_names or ([sheet_name] if sheet_name else [])
    shape = excel_shape(file_path) if file_path else None
    profile = active_profile(
        profile_name=profile_name,
    )
    builder = str(
        profile.get("summary_builder") or profile.get("summary") or "generic"
    ).strip() or "generic"

    return run_summary_builder(
        builder,
        prepared,
        file_name=file_name,
        sheet_name=sheet_name,
        sheets=sheets,
        excel_shape=shape,
        profile_name=profile_name,
    )


def build_multi_file_summary(
    named_dfs: list[tuple[str, pd.DataFrame]],
    *,
    sheet_info: dict[str, dict] | None = None,
    profile_name: str | None = None,
    unit_label: str = "파일",
) -> str:
    """여러 파일(또는 시트)을 짧게 이어서 요약한다."""
    from core.profile_loader import locale_for
    from core.summary.generic_summary import summary_copy

    copy = summary_copy(locale=locale_for(profile_name=profile_name))
    # EN unit labels for common Korean defaults
    unit = unit_label
    if locale_for(profile_name=profile_name) == "en":
        if unit_label in {"파일", "file"}:
            unit = "file"
        elif unit_label in {"시트", "sheet"}:
            unit = "sheet"

    if not named_dfs:
        return copy["multi_empty"].format(unit=unit)

    parts: list[str] = [copy["multi_intro"].format(unit=unit, count=len(named_dfs))]
    for name, frame in named_dfs:
        info = (sheet_info or {}).get(name) or {}
        block = build_file_summary(
            frame,
            file_name=name,
            sheet_name=info.get("current_sheet"),
            sheet_names=info.get("sheet_names"),
            file_path=info.get("path"),
            profile_name=profile_name,
        )
        parts.append(f"### {name}\n{block}")
    return "\n\n".join(parts)
