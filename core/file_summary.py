"""파일 요약 요청용 규칙 기반 분석 (라우터 + 공개 API)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from core.budget_summary import build_budget_summary, looks_like_budget_table
from core.generic_summary import build_generic_summary
from core.pandasai_config import prepare_dataframe_for_ai
from core.summary_utils import excel_shape

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
    use_budget_profile: bool = False,
) -> str:
    """DataFrame을 읽어 사람이 읽을 수 있는 파일 요약 문장을 만든다.

    프로필 ``summary_builder`` 가 budget이고 예산 표로 보이면 전용 요약을 쓴다.
    """
    if df is None or df.empty:
        return "데이터가 비어 있어 요약할 내용이 없습니다."

    from core.profile_loader import active_profile

    prepared = prepare_dataframe_for_ai(df)
    sheets = sheet_names or ([sheet_name] if sheet_name else [])
    shape = excel_shape(file_path) if file_path else None
    profile = active_profile(
        profile_name=profile_name, use_budget_profile=use_budget_profile,
    )
    builder = str(profile.get("summary_builder") or profile.get("summary") or "")

    if builder == "budget" and looks_like_budget_table(prepared):
        return build_budget_summary(
            prepared,
            file_name=file_name,
            sheet_name=sheet_name,
            sheets=sheets,
            excel_shape=shape,
        )
    return build_generic_summary(
        prepared,
        file_name=file_name,
        sheet_name=sheet_name,
        sheets=sheets,
        excel_shape=shape,
    )


def build_multi_file_summary(
    named_dfs: list[tuple[str, pd.DataFrame]],
    *,
    sheet_info: dict[str, dict] | None = None,
    profile_name: str | None = None,
    use_budget_profile: bool = False,
    unit_label: str = "파일",
) -> str:
    """여러 파일(또는 시트)을 짧게 이어서 요약한다."""
    if not named_dfs:
        return f"요약할 {unit_label}이(가) 없습니다."

    parts: list[str] = [f"선택된 {unit_label} {len(named_dfs)}개를 요약합니다.\n"]
    for name, frame in named_dfs:
        info = (sheet_info or {}).get(name) or {}
        block = build_file_summary(
            frame,
            file_name=name,
            sheet_name=info.get("current_sheet"),
            sheet_names=info.get("sheet_names"),
            file_path=info.get("path"),
            profile_name=profile_name, use_budget_profile=use_budget_profile,
        )
        parts.append(f"### {name}\n{block}")
    return "\n\n".join(parts)
