"""xlsx 원본 좌표에 저장된 셀 내용을 그대로 읽는다."""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

CONTRACT_VERSION = "1.0"
OPERATION = "read_range"
MAX_CELLS = 2000
MAX_ROW = 1_048_576
MAX_COLUMN = 16_384

_CELL_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})")

_ERRORS: dict[str, tuple[str, str, str]] = {
    "invalid_address": ("invalid_request", "request", "셀 주소가 올바르지 않다."),
    "reversed_range": ("invalid_request", "request", "범위의 시작이 끝보다 뒤다."),
    "range_too_large": ("invalid_request", "request", "범위가 2000칸을 넘는다."),
    "unsupported_format": ("invalid_request", "request", "xlsx 파일만 읽을 수 있다."),
    "invalid_sheet": ("invalid_request", "file_loading", "시트를 찾을 수 없다."),
    "invalid_file": ("invalid_request", "file_loading", "통합 문서를 열 수 없다."),
    "execution_failed": ("execution_failed", "read", "셀 범위를 읽지 못했다."),
}


class _ReadError(Exception):
    def __init__(self, code: str, exception_type: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.exception_type = exception_type


def read_range(
    path: str | os.PathLike[str],
    *,
    sheet: str | int = 0,
    address: str,
    display_name: str,
    request_id: str,
) -> dict[str, Any]:
    """요청한 직사각형의 셀을 행 우선으로 돌려준다. 파일에 쓰거나 수식을 계산하지 않는다."""
    try:
        normalized, bounds = _parse_address(address)
        _check_format(path)
        return _read(path, sheet, bounds, normalized, display_name, request_id)
    except _ReadError as err:
        return _failure(request_id, err.code, err.exception_type)
    except Exception as exc:
        return _failure(request_id, "execution_failed", type(exc).__name__)


def _parse_address(address: Any) -> tuple[str, tuple[int, int, int, int]]:
    if not isinstance(address, str):
        raise _ReadError("invalid_address")
    parts = address.split(":")
    if len(parts) > 2:
        raise _ReadError("invalid_address")
    coords = [_parse_cell(part) for part in parts]
    top, left = coords[0]
    bottom, right = coords[-1]
    if top > bottom or left > right:
        raise _ReadError("reversed_range")
    if (bottom - top + 1) * (right - left + 1) > MAX_CELLS:
        raise _ReadError("range_too_large")
    normalized = ":".join(_a1(row, column) for row, column in coords)
    return normalized, (top, left, bottom, right)


def _parse_cell(text: str) -> tuple[int, int]:
    match = _CELL_RE.fullmatch(text)
    if match is None:
        raise _ReadError("invalid_address")
    column = 0
    for letter in match.group(1).upper():
        column = column * 26 + (ord(letter) - ord("A") + 1)
    row = int(match.group(2))
    if row > MAX_ROW or column > MAX_COLUMN:
        raise _ReadError("invalid_address")
    return row, column


def _check_format(path: str | os.PathLike[str]) -> None:
    if Path(path).suffix.lower() != ".xlsx":
        raise _ReadError("unsupported_format")


def _read(
    path: str | os.PathLike[str],
    sheet: Any,
    bounds: tuple[int, int, int, int],
    normalized: str,
    display_name: str,
    request_id: str,
) -> dict[str, Any]:
    formulas_book = _open(path, data_only=False)
    try:
        index, worksheet = _resolve_sheet(formulas_book, sheet)
        values_book = _open(path, data_only=True)
        try:
            cells = _read_cells(worksheet, values_book.worksheets[index], bounds)
        finally:
            values_book.close()
    finally:
        formulas_book.close()
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "status": "success",
        "operation": OPERATION,
        "recalculated": False,
        "values_from": "stored_workbook",
        "source": {
            "display_name": display_name,
            "sheet_name": worksheet.title,
            "sheet_index": index,
        },
        "requested": {"address": normalized},
        "returned": {"address": normalized, "cell_count": len(cells)},
        "cells": cells,
    }


def _open(path: str | os.PathLike[str], *, data_only: bool):
    try:
        return load_workbook(path, read_only=False, data_only=data_only)
    except Exception as exc:
        raise _ReadError("invalid_file", type(exc).__name__) from None


def _resolve_sheet(book, sheet: Any):
    worksheets = book.worksheets
    if isinstance(sheet, bool):
        raise _ReadError("invalid_sheet")
    if isinstance(sheet, int):
        if 0 <= sheet < len(worksheets):
            return sheet, worksheets[sheet]
        raise _ReadError("invalid_sheet")
    if isinstance(sheet, str):
        for index, worksheet in enumerate(worksheets):
            if worksheet.title == sheet:
                return index, worksheet
    raise _ReadError("invalid_sheet")


def _read_cells(worksheet, cached_worksheet, bounds: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    top, left, bottom, right = bounds
    merges = _merge_map(worksheet, bounds)
    cells: list[dict[str, Any]] = []
    for row in range(top, bottom + 1):
        for column in range(left, right + 1):
            merge = merges.get((row, column))
            if merge is not None and merge["role"] == "covered":
                fields = _empty_fields()
            else:
                fields = _cell_fields(worksheet.cell(row=row, column=column), cached_worksheet)
            cells.append(
                {
                    "a1": _a1(row, column),
                    "row": row,
                    "column_index": column,
                    "column_letter": get_column_letter(column),
                    **fields,
                    "merge": merge,
                }
            )
    return cells


def _merge_map(worksheet, bounds: tuple[int, int, int, int]) -> dict[tuple[int, int], dict[str, str]]:
    top, left, bottom, right = bounds
    result: dict[tuple[int, int], dict[str, str]] = {}
    for merged in worksheet.merged_cells.ranges:
        if merged.max_row < top or merged.min_row > bottom:
            continue
        if merged.max_col < left or merged.min_col > right:
            continue
        anchor = _a1(merged.min_row, merged.min_col)
        span = f"{anchor}:{_a1(merged.max_row, merged.max_col)}"
        for row in range(max(merged.min_row, top), min(merged.max_row, bottom) + 1):
            for column in range(max(merged.min_col, left), min(merged.max_col, right) + 1):
                role = "anchor" if (row, column) == (merged.min_row, merged.min_col) else "covered"
                result[(row, column)] = {"role": role, "anchor": anchor, "range": span}
    return result


def _empty_fields() -> dict[str, Any]:
    return {
        "value": None,
        "value_type": "empty",
        "formula": None,
        "cached_value": None,
        "cached_value_state": "not_applicable",
    }


def _cell_fields(cell, cached_worksheet) -> dict[str, Any]:
    if cell.data_type == "f":
        cached = cached_worksheet.cell(row=cell.row, column=cell.column)
        if cached.value is None:
            cached_value, state = None, "absent"
        else:
            cached_value, state = _plain(cached.data_type, cached.value)[0], "stored"
        return {
            "value": None,
            "value_type": "formula",
            "formula": _formula_text(cell.value),
            "cached_value": cached_value,
            "cached_value_state": state,
        }
    value, value_type = _plain(cell.data_type, cell.value)
    return {
        "value": value,
        "value_type": value_type,
        "formula": None,
        "cached_value": None,
        "cached_value_state": "not_applicable",
    }


def _formula_text(value: Any) -> str:
    text = value if isinstance(value, str) else getattr(value, "text", None)
    if isinstance(text, str) and text.startswith("="):
        return text
    raise TypeError("formula is not readable as text")


def _plain(data_type: str, value: Any) -> tuple[Any, str]:
    if value is None:
        return None, "empty"
    if data_type == "e":
        return str(value), "error"
    if isinstance(value, bool):
        return value, "boolean"
    if isinstance(value, (int, float)):
        return value, "number"
    if isinstance(value, str):
        return value, "string"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat(), "datetime"
    if isinstance(value, dt.timedelta):
        return _duration(value), "datetime"
    raise TypeError("unsupported cell value")


def _duration(delta: dt.timedelta) -> str:
    total = delta.total_seconds()
    sign = "-" if total < 0 else ""
    days, rest = divmod(abs(total), 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, seconds = divmod(rest, 60)
    return f"{sign}P{int(days)}DT{int(hours)}H{int(minutes)}M{seconds:g}S"


def _a1(row: int, column: int) -> str:
    return f"{get_column_letter(column)}{row}"


def _failure(request_id: str, code: str, exception_type: str | None = None) -> dict[str, Any]:
    status, stage, message = _ERRORS[code]
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "stage": stage,
        "retryable": False,
    }
    if code in ("invalid_file", "execution_failed"):
        error["exception_type"] = exception_type or "Exception"
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "status": status,
        "operation": OPERATION,
        "recalculated": False,
        "error": error,
    }
