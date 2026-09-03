"""Shared helpers for headless application tests. Not collected by pytest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.application.contracts import CONTRACT_VERSION
from core.constants import DEFAULT_OLLAMA_MODEL


def write_table(path: Path, rows: dict[str, list[Any]] | None = None, **sheets: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        pd.DataFrame(rows or {"항목": ["A"], "값": [1]}).to_csv(path, index=False)
        return path
    if sheets:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name, frame in sheets.items():
                frame.to_excel(writer, index=False, sheet_name=str(name)[:31] or "Sheet1")
        return path
    pd.DataFrame(rows or {"항목": ["A", "B"], "값": [1, 2]}).to_excel(path, index=False)
    return path


def base_request(
    tmp_path: Path,
    *,
    paths: list[Path],
    prompt: str,
    analysis_mode: str | None = None,
    request_id: str = "req-1",
    profile_name: str = "generic",
    sheet: str | int = 0,
    display_names: list[str] | None = None,
    timeout_seconds: float = 180,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if analysis_mode is None:
        analysis_mode = "single" if len(paths) == 1 else "multi"
    names = display_names or [path.name for path in paths]
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "operation": "analyze",
        "inputs": [
            {
                "source_id": f"file-{index + 1}",
                "path": str(path.resolve()),
                "sheet": sheet,
                "display_name": names[index],
            }
            for index, path in enumerate(paths)
        ],
        "user_prompt": prompt,
        "analysis_mode": analysis_mode,
        "profile_name": profile_name,
        "model": {
            "base_url": "http://127.0.0.1:11434",
            "name": DEFAULT_OLLAMA_MODEL,
        },
        "timeout_seconds": timeout_seconds,
        "output_directory": str((tmp_path / "out").resolve()),
    }
    if extra:
        payload.update(extra)
    return payload
