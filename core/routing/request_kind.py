"""질문 종류 판정 — 어떤 처리기로 보낼지 LLM이 고른다.

예전에는 단어 목록(요약·결측·품질·행/열 …)으로 미리 갈랐다. 그 방식은
"부서별 매출을 요약한 표"를 파일 요약으로, "어떤 열들이 있어?"를 분석으로
보냈다. 이제 LLM이 질문과 열 이름을 보고 처리기를 고르고, 코드는 답이 정해진
종류 중 하나인지만 확인한다. 확인에 실패하면 분석 경로로 보낸다. 분석 경로는
자체 검증과 실패 경고가 있다.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import pandas as pd

KIND_SUMMARY = "summary"
KIND_MISSING_ROWS = "missing_rows"
KIND_QUALITY = "quality"
KIND_SCHEMA = "schema"
KIND_COLUMN_MEANING = "column_meaning"
KIND_ANALYSIS = "analysis"

REQUEST_KINDS = (
    KIND_SUMMARY,
    KIND_MISSING_ROWS,
    KIND_QUALITY,
    KIND_SCHEMA,
    KIND_COLUMN_MEANING,
    KIND_ANALYSIS,
)

# 처리기마다 "무엇을 돌려주는지"만 적는다. 어떤 단어가 나오면 어디로 가라는 규칙은 적지 않는다.
REQUEST_KIND_SYSTEM_PROMPT = """You route a user's question about a spreadsheet to one handler.
Each handler returns a fixed kind of output. Pick the handler whose output answers the question.

- summary: a short overview of the whole file (what the table is about, its size, its main columns).
  It computes nothing the user asked for.
- missing_rows: the rows of the table that have at least one empty cell, shown as they are.
- quality: a data-quality report: empty cells per column, duplicate rows, columns with mixed types,
  fully empty columns, and what to clean before analysis.
- schema: facts about the table's structure: number of rows and columns, the column list,
  column data types, empty-cell counts per column, which columns are numeric/text/date,
  and columns shared between sheets or files.
- column_meaning: an explanation of what each column probably means.
- analysis: anything computed from the data values: filtering, totals, averages, counts per group,
  rankings, comparisons, new columns, reshaping or transposing the table, summary tables built
  from the values, charts.

Answer with JSON only: {"kind": "<one of: summary, missing_rows, quality, schema, column_meaning, analysis>"}"""

ChatJson = Callable[..., dict[str, Any]]

MAX_COLUMNS_SHOWN = 60


def _request_kind_prompt(prompt: str, column_names: list[str]) -> str:
    shown = [str(c) for c in column_names[:MAX_COLUMNS_SHOWN]]
    more = len(column_names) - len(shown)
    columns = ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")
    return f"Columns: {columns}\nQuestion: {prompt}"


def parse_request_kind(raw: Any) -> str | None:
    """LLM 응답에서 종류를 꺼낸다. 정해진 종류가 아니면 None."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if isinstance(kind, str) and kind.strip().lower() in REQUEST_KINDS:
        return kind.strip().lower()
    return None


def classify_request(
    prompt: str,
    frames: list[pd.DataFrame] | pd.DataFrame | None,
    *,
    base_url: str,
    model: str,
    chat: ChatJson | None = None,
) -> str:
    """질문을 처리할 종류를 돌려준다. 판정할 수 없으면 analysis."""
    if not prompt or not str(prompt).strip():
        return KIND_ANALYSIS
    if chat is None:
        # 운영·테스트 스위치: "off"면 판정 없이 analysis, 종류 이름이면 그 종류로 고정.
        forced = os.environ.get("EXCEL_ANALYZER_REQUEST_KIND", "").strip().lower()
        if forced == "off":
            return KIND_ANALYSIS
        if forced in REQUEST_KINDS:
            return forced
        from core.llm_client import chat_json as chat

    if isinstance(frames, pd.DataFrame):
        frames = [frames]
    column_names: list[str] = []
    for frame in frames or []:
        for col in getattr(frame, "columns", []):
            if str(col) not in column_names:
                column_names.append(str(col))

    try:
        raw = chat(
            _request_kind_prompt(prompt, column_names),
            system=REQUEST_KIND_SYSTEM_PROMPT,
            base_url=base_url,
            model=model,
            timeout=120,
        )
    except Exception:  # noqa: BLE001 — 판정 실패는 분석 경로로
        return KIND_ANALYSIS
    return parse_request_kind(raw) or KIND_ANALYSIS
