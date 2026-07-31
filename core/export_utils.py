"""분석/병합 결과 엑셀 export 유틸."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd

from core.constants import MERGES_DIR


def dataframe_to_xlsx_bytes(df: pd.DataFrame, *, sheet_name: str = "Sheet1") -> bytes:
    """DataFrame을 xlsx 바이트로 직렬화한다."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Sheet1")
    return buffer.getvalue()


def export_dataframe_xlsx(
    df: pd.DataFrame,
    *,
    filename: str | None = None,
    directory: Path | None = None,
    sheet_name: str = "Sheet1",
) -> Path:
    """DataFrame을 exports/merges 등에 저장하고 경로를 반환한다."""
    target_dir = Path(directory) if directory is not None else MERGES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"merged_{stamp}.xlsx"
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        filename = f"{filename}.xlsx"

    path = target_dir / filename
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Sheet1")
    return path


def safe_download_name(base: str, *, default: str = "export.xlsx") -> str:
    """다운로드 파일명에 쓸 수 있게 정리한다."""
    text = str(base or "").strip() or default
    for ch in '\\/:*?"<>|':
        text = text.replace(ch, "_")
    if not text.lower().endswith(".xlsx"):
        text = f"{text}.xlsx"
    return text
