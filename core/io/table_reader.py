"""사용자가 지정한 xlsx 범위와 헤더 행으로 원본 좌표를 지킨 표를 읽는다."""

from __future__ import annotations

import os
from typing import Any

from openpyxl.utils import get_column_letter

from core.io import cell_reader
from core.io.cell_reader import (
    CONTRACT_VERSION,
    _a1,
    _cell_fields,
    _check_format,
    _empty_fields,
    _merge_map,
    _open,
    _parse_address,
    _parse_cell,
    _plain,
    _ReadError,
    _resolve_sheet,
)

OPERATION = "read_table"

_ERRORS: dict[str, tuple[str, str, str]] = {
    **cell_reader._ERRORS,
    "missing_header": ("invalid_request", "request", "헤더 지정이 없다."),
    "invalid_header": ("invalid_request", "request", "헤더 행 목록이 올바르지 않다."),
}

_HEADER_OUTSIDE = "헤더 행이 요청 범위 안에 있지 않다."
_HEADER_MALFORMED = "헤더 행 목록이 올바르지 않다."
_HEADER_ORDER = "헤더 행은 겹치지 않고 연속된 오름차순이어야 한다."
_HEADER_NOT_FIRST = "헤더는 범위의 첫 행에서 시작해야 한다."
_HEADER_TOO_MANY = "헤더는 두 줄까지다."

_MAX_HEADER_ROWS = 2


class _HeaderError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def read_table(
    path: str | os.PathLike[str],
    *,
    sheet: str | int = 0,
    address: str,
    header: Any,
    display_name: str,
    request_id: str,
) -> dict[str, Any]:
    """요청 범위를 헤더 열 설명과 원본 좌표 데이터 칸으로 돌려준다. 파일에 쓰거나 수식을 계산하지 않는다."""
    try:
        normalized, bounds = _parse_address(address)
        header_rows = _parse_header(header, bounds)
        _check_format(path)
        return _read(path, sheet, bounds, normalized, header_rows, display_name, request_id)
    except _HeaderError as err:
        return _failure(request_id, err.code, message=err.message)
    except _ReadError as err:
        return _failure(request_id, err.code, err.exception_type)
    except Exception as exc:
        return _failure(request_id, "execution_failed", type(exc).__name__)


def _parse_header(header: Any, bounds: tuple[int, int, int, int]) -> list[int]:
    if header is None:
        raise _HeaderError("missing_header")
    rows = header.get("rows") if isinstance(header, dict) else None
    if not isinstance(rows, list) or any(isinstance(row, bool) or not isinstance(row, int) for row in rows):
        raise _HeaderError("invalid_header", _HEADER_MALFORMED)
    top, _, bottom, _ = bounds
    if any(row < top or row > bottom for row in rows):
        raise _HeaderError("invalid_header", _HEADER_OUTSIDE)
    if any(after != before + 1 for before, after in zip(rows, rows[1:])):
        raise _HeaderError("invalid_header", _HEADER_ORDER)
    if rows and rows[0] != top:
        raise _HeaderError("invalid_header", _HEADER_NOT_FIRST)
    if len(rows) > _MAX_HEADER_ROWS:
        raise _HeaderError("invalid_header", _HEADER_TOO_MANY)
    return list(rows)


def _read(
    path: str | os.PathLike[str],
    sheet: Any,
    bounds: tuple[int, int, int, int],
    normalized: str,
    header_rows: list[int],
    display_name: str,
    request_id: str,
) -> dict[str, Any]:
    formulas_book = _open(path, data_only=False)
    try:
        values_book = _open(path, data_only=True)
        try:
            index, worksheet = _resolve_sheet(formulas_book, sheet)
            columns, rows = _build_table(worksheet, values_book.worksheets[index], bounds, header_rows)
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
        "freshness": "unconfirmed",
        "source": {
            "display_name": display_name,
            "sheet_name": worksheet.title,
            "sheet_index": index,
        },
        "requested": {"address": normalized, "header": {"rows": list(header_rows)}},
        "table": {
            "address": normalized,
            "blank_rows": "kept",
            "blank_columns": "kept",
            "columns": columns,
            "rows": rows,
        },
    }


def _build_table(
    worksheet,
    cached_worksheet,
    bounds: tuple[int, int, int, int],
    header_rows: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    top, left, bottom, right = bounds
    merges = _merge_map(worksheet, bounds)

    header_cells: dict[tuple[int, int], dict[str, Any]] = {}
    for row in header_rows:
        for column in range(left, right + 1):
            header_cells[(row, column)] = _header_entry(
                worksheet, cached_worksheet, row, column, merges.get((row, column))
            )

    columns: list[dict[str, Any]] = []
    used: set[str] = set()
    for column in range(left, right + 1):
        letter = get_column_letter(column)
        headers = [header_cells[(row, column)] for row in header_rows]
        display_name, derived = _display_name(headers, header_cells, bounds)
        key = _unique_key(display_name, letter, used)
        entry: dict[str, Any] = {"key": key, "display_name": display_name}
        if derived:
            entry["display_derived"] = True
        entry.update({"source_column": letter, "column_index": column, "headers": headers})
        columns.append(entry)

    keys = [entry["key"] for entry in columns]
    first_data_row = header_rows[-1] + 1 if header_rows else top
    rows = [
        {
            "source_row": row,
            "cells": [
                _data_cell(worksheet, cached_worksheet, row, column, keys[column - left], merges.get((row, column)))
                for column in range(left, right + 1)
            ],
        }
        for row in range(first_data_row, bottom + 1)
    ]
    return columns, rows


def _table_fields(worksheet, cached_worksheet, row: int, column: int, merge: dict[str, str] | None):
    if merge is not None and merge["role"] == "covered":
        return _empty_fields(), None
    cell = worksheet.cell(row=row, column=column)
    fields = _cell_fields(cell, cached_worksheet)
    if fields["cached_value_state"] == "stored":
        cached = cached_worksheet.cell(row=row, column=column)
        fields["value"], fields["value_type"] = _plain(cached.data_type, cached.value)
    return fields, cell


def _header_entry(worksheet, cached_worksheet, row: int, column: int, merge: dict[str, str] | None) -> dict[str, Any]:
    fields, _ = _table_fields(worksheet, cached_worksheet, row, column, merge)
    entry: dict[str, Any] = {
        "row": row,
        "a1": _a1(row, column),
        "value": fields["value"],
        "value_type": fields["value_type"],
        "merge": merge,
    }
    if fields["formula"] is not None:
        entry["formula"] = fields["formula"]
        entry["cached_value"] = fields["cached_value"]
        entry["cached_value_state"] = fields["cached_value_state"]
    return entry


def _data_cell(
    worksheet,
    cached_worksheet,
    row: int,
    column: int,
    key: str,
    merge: dict[str, str] | None,
) -> dict[str, Any]:
    fields, cell = _table_fields(worksheet, cached_worksheet, row, column, merge)
    entry: dict[str, Any] = {
        "a1": _a1(row, column),
        "key": key,
        "value": fields["value"],
        "value_type": fields["value_type"],
        "number_format": None if cell is None else cell.number_format,
        "formula": fields["formula"],
        "cached_value": fields["cached_value"],
        "cached_value_state": fields["cached_value_state"],
    }
    if merge is not None:
        entry["merge"] = merge
    return entry


def _display_name(
    headers: list[dict[str, Any]],
    header_cells: dict[tuple[int, int], dict[str, Any]],
    bounds: tuple[int, int, int, int],
) -> tuple[str | None, bool]:
    top, left, bottom, right = bounds
    pieces: list[str] = []
    derived = False
    for header in headers:
        merge = header["merge"]
        if merge is not None and merge["role"] == "covered":
            anchor_row, anchor_column = _parse_cell(merge["anchor"])
            if not (top <= anchor_row <= bottom and left <= anchor_column <= right):
                continue
            piece = _piece(header_cells[(anchor_row, anchor_column)]["value"])
            derived = True
        else:
            piece = _piece(header["value"])
        if piece and (not pieces or pieces[-1] != piece):
            pieces.append(piece)
    if not pieces:
        return None, derived
    return "_".join(pieces), derived


def _piece(value: Any) -> str:
    return "" if value is None else str(value)


def _unique_key(display_name: str | None, letter: str, used: set[str]) -> str:
    if display_name is not None and display_name not in used:
        key = display_name
    else:
        base = f"column_{letter}"
        key = base
        suffix = 2
        while key in used:
            key = f"{base}_{suffix}"
            suffix += 1
    used.add(key)
    return key


def _failure(
    request_id: str,
    code: str,
    exception_type: str | None = None,
    *,
    message: str | None = None,
) -> dict[str, Any]:
    status, stage, default_message = _ERRORS[code]
    error: dict[str, Any] = {
        "code": code,
        "message": message or default_message,
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
