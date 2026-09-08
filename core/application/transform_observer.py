"""Deterministic bounded workbook observation for transform planning (I2-D3B).

Collects visible structural facts only. Does not infer labels, groups, headers,
or synonyms. Never writes the source workbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from core.application.artifacts import json_safe, sha256_file
from core.application.contracts import ContractError, WarningItem

MAX_OBSERVED_SHEETS = 16
MAX_OBSERVED_CELLS = 1_500
MAX_OBSERVED_MERGES = 400
MAX_CELL_CHARS = 200
MAX_OBSERVATION_CHARS = 32_000
_EXECUTION = "execution"


@dataclass(frozen=True)
class ObservedMerge:
    start_row: int
    start_column: int
    end_row: int
    end_column: int
    top_left_value: Any
    top_left_is_formula: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_row": self.start_row,
            "start_column": self.start_column,
            "end_row": self.end_row,
            "end_column": self.end_column,
            "top_left_value": self.top_left_value,
            "top_left_is_formula": self.top_left_is_formula,
        }


@dataclass(frozen=True)
class ObservedCell:
    row: int
    column: int
    coordinate: str
    value: Any
    is_formula: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "column": self.column,
            "coordinate": self.coordinate,
            "value": self.value,
            "is_formula": self.is_formula,
        }


@dataclass(frozen=True)
class ObservedSheet:
    index: int
    title: str
    visible: bool
    max_row: int
    max_column: int
    merged_ranges: tuple[ObservedMerge, ...]
    cells: tuple[ObservedCell, ...]
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "visible": self.visible,
            "max_row": self.max_row,
            "max_column": self.max_column,
            "merged_ranges": [item.to_dict() for item in self.merged_ranges],
            "cells": [item.to_dict() for item in self.cells],
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class WorkbookObservation:
    sheets: tuple[ObservedSheet, ...]
    truncated: bool
    warnings: tuple[WarningItem, ...]
    source_sha256: str
    sheet_count_total: int
    cell_count: int
    merge_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheets": [item.to_dict() for item in self.sheets],
            "truncated": self.truncated,
            "warnings": [item.to_dict() for item in self.warnings],
            "sheet_count_total": self.sheet_count_total,
            "cell_count": self.cell_count,
            "merge_count": self.merge_count,
        }


def observe_workbook(path: str | Path) -> WorkbookObservation:
    source = Path(path)
    before = sha256_file(source)
    try:
        observation = _observe_open(source)
    except ContractError:
        _assert_unchanged(source, before)
        raise
    except Exception:
        _assert_unchanged(source, before)
        raise ContractError(
            "invalid_file",
            "Workbook could not be observed.",
            stage=_EXECUTION,
            retryable=False,
        ) from None
    _assert_unchanged(source, before)
    return observation


def _observe_open(source: Path) -> WorkbookObservation:
    try:
        workbook = load_workbook(filename=source, read_only=False, data_only=False, keep_vba=False)
    except (InvalidFileException, BadZipFile, OSError, KeyError, ValueError):
        raise ContractError(
            "invalid_file",
            "Workbook could not be opened.",
            stage=_EXECUTION,
            retryable=False,
        ) from None
    try:
        return _collect(workbook, source_sha256=sha256_file(source))
    finally:
        try:
            workbook.close()
        except Exception:
            pass


def _collect(workbook: Workbook, *, source_sha256: str) -> WorkbookObservation:
    warnings: list[WarningItem] = []
    sheets: list[ObservedSheet] = []
    cells_budget = MAX_OBSERVED_CELLS
    merges_budget = MAX_OBSERVED_MERGES
    truncated = False
    value_truncated = False
    cell_or_merge_truncated = False
    all_sheets = list(workbook.worksheets)
    if len(all_sheets) > MAX_OBSERVED_SHEETS:
        truncated = True
        warnings.append(
            WarningItem(
                code="observation_truncated_sheets",
                message="Sheet observation cap was reached.",
                stage="observation",
            )
        )
        all_sheets = all_sheets[:MAX_OBSERVED_SHEETS]
    for index, worksheet in enumerate(all_sheets, start=1):
        sheet, cells_budget, merges_budget, sheet_truncated, sheet_value_truncated = _collect_sheet(
            worksheet,
            index=index,
            cells_budget=cells_budget,
            merges_budget=merges_budget,
        )
        sheets.append(sheet)
        if sheet_truncated:
            truncated = True
            cell_or_merge_truncated = True
        if sheet_value_truncated:
            value_truncated = True
    if cell_or_merge_truncated:
        warnings.append(
            WarningItem(
                code="observation_truncated_cells",
                message="Cell or merge observation cap was reached.",
                stage="observation",
            )
        )
    if value_truncated:
        warnings.append(
            WarningItem(
                code="observation_truncated_values",
                message="Cell value character cap was reached.",
                stage="observation",
            )
        )
    observation = WorkbookObservation(
        sheets=tuple(sheets),
        truncated=truncated,
        warnings=tuple(warnings),
        source_sha256=source_sha256,
        sheet_count_total=len(workbook.worksheets),
        cell_count=sum(len(item.cells) for item in sheets),
        merge_count=sum(len(item.merged_ranges) for item in sheets),
    )
    return _enforce_serialized_cap(observation)


def _collect_sheet(
    worksheet: Worksheet,
    *,
    index: int,
    cells_budget: int,
    merges_budget: int,
) -> tuple[ObservedSheet, int, int, bool, bool]:
    max_row = max(int(worksheet.max_row or 1), 1)
    max_col = max(int(worksheet.max_column or 1), 1)
    visible = str(getattr(worksheet, "sheet_state", "visible") or "visible") == "visible"
    merges, merges_budget, merge_truncated, merge_value_truncated = _collect_merges(
        worksheet, budget=merges_budget
    )
    cells: list[ObservedCell] = []
    cell_truncated = cells_budget <= 0
    value_truncated = merge_value_truncated
    if not cell_truncated:
        for row in range(1, max_row + 1):
            row_done = False
            for column in range(1, max_col + 1):
                if cells_budget <= 0:
                    cell_truncated = True
                    row_done = True
                    break
                cell = worksheet.cell(row=row, column=column)
                if isinstance(cell, MergedCell):
                    continue
                raw = cell.value
                if raw is None or raw == "":
                    continue
                value, truncated_value = _bounded_value(raw)
                value_truncated = value_truncated or truncated_value
                cells.append(
                    ObservedCell(
                        row=row,
                        column=column,
                        coordinate=f"{get_column_letter(column)}{row}",
                        value=value,
                        is_formula=_is_formula(raw),
                    )
                )
                cells_budget -= 1
            if row_done or (cells_budget <= 0 and row < max_row):
                if cells_budget <= 0:
                    cell_truncated = True
                break
    truncated = merge_truncated or cell_truncated
    sheet = ObservedSheet(
        index=index,
        title=str(worksheet.title),
        visible=visible,
        max_row=max_row,
        max_column=max_col,
        merged_ranges=tuple(merges),
        cells=tuple(cells),
        truncated=truncated,
    )
    return sheet, cells_budget, merges_budget, truncated, value_truncated


def _collect_merges(
    worksheet: Worksheet, *, budget: int
) -> tuple[list[ObservedMerge], int, bool, bool]:
    keys: list[tuple[int, int, int, int]] = []
    for item in worksheet.merged_cells.ranges:
        keys.append((int(item.min_row), int(item.min_col), int(item.max_row), int(item.max_col)))
    keys.sort()
    truncated = False
    if len(keys) > budget:
        truncated = True
        keys = keys[:budget]
        budget = 0
    else:
        budget -= len(keys)
    merges: list[ObservedMerge] = []
    value_truncated = False
    for start_row, start_col, end_row, end_col in keys:
        anchor = worksheet.cell(row=start_row, column=start_col)
        raw = None if isinstance(anchor, MergedCell) else anchor.value
        top_left: Any = None
        if raw not in (None, ""):
            top_left, truncated_value = _bounded_value(raw)
            value_truncated = value_truncated or truncated_value
        merges.append(
            ObservedMerge(
                start_row=start_row,
                start_column=start_col,
                end_row=end_row,
                end_column=end_col,
                top_left_value=top_left,
                top_left_is_formula=_is_formula(raw),
            )
        )
    return merges, budget, truncated, value_truncated


def _enforce_serialized_cap(observation: WorkbookObservation) -> WorkbookObservation:
    payload = json_safe(observation.to_dict())
    encoded = _encode(payload)
    if len(encoded) <= MAX_OBSERVATION_CHARS:
        return WorkbookObservation(
            sheets=observation.sheets,
            truncated=observation.truncated,
            warnings=observation.warnings,
            source_sha256=observation.source_sha256,
            sheet_count_total=observation.sheet_count_total,
            cell_count=observation.cell_count,
            merge_count=observation.merge_count,
        )
    sheets = [_copy_sheet(item) for item in observation.sheets]
    extra = WarningItem(
        code="observation_truncated_serialized",
        message="Serialized observation cap was reached.",
        stage="observation",
    )
    candidate = _rebuild(sheets, observation, extra)
    while sheets and len(_encode(json_safe(candidate.to_dict()))) > MAX_OBSERVATION_CHARS:
        last = sheets[-1]
        if last.cells:
            last = ObservedSheet(
                index=last.index,
                title=last.title,
                visible=last.visible,
                max_row=last.max_row,
                max_column=last.max_column,
                merged_ranges=last.merged_ranges,
                cells=last.cells[:-1],
                truncated=True,
            )
            sheets[-1] = last
        elif last.merged_ranges:
            last = ObservedSheet(
                index=last.index,
                title=last.title,
                visible=last.visible,
                max_row=last.max_row,
                max_column=last.max_column,
                merged_ranges=last.merged_ranges[:-1],
                cells=last.cells,
                truncated=True,
            )
            sheets[-1] = last
        else:
            sheets.pop()
        candidate = _rebuild(sheets, observation, extra)
    return candidate


def _rebuild(
    sheets: list[ObservedSheet],
    observation: WorkbookObservation,
    extra: WarningItem,
) -> WorkbookObservation:
    rebuilt = tuple(sheets)
    return WorkbookObservation(
        sheets=rebuilt,
        truncated=True,
        warnings=observation.warnings + (extra,),
        source_sha256=observation.source_sha256,
        sheet_count_total=observation.sheet_count_total,
        cell_count=sum(len(item.cells) for item in rebuilt),
        merge_count=sum(len(item.merged_ranges) for item in rebuilt),
    )


def _copy_sheet(item: ObservedSheet) -> ObservedSheet:
    return ObservedSheet(
        index=item.index,
        title=item.title,
        visible=item.visible,
        max_row=item.max_row,
        max_column=item.max_column,
        merged_ranges=item.merged_ranges,
        cells=item.cells,
        truncated=item.truncated,
    )


def _encode(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _bounded_value(raw: Any) -> tuple[Any, bool]:
    if raw is None:
        return None, False
    if isinstance(raw, (bytes, bytearray)):
        return None, False
    if isinstance(raw, bool):
        return raw, False
    if isinstance(raw, (int, float)):
        return json_safe(raw), False
    if isinstance(raw, (datetime, date)):
        try:
            return raw.isoformat(), False
        except Exception:
            text = str(raw)
            if len(text) > MAX_CELL_CHARS:
                return text[:MAX_CELL_CHARS], True
            return text, False
    text = str(raw)
    if len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS], True
    return text, False


def _is_formula(raw: Any) -> bool:
    return isinstance(raw, str) and raw.startswith("=")


def _assert_unchanged(path: Path, expected: str) -> None:
    try:
        actual = sha256_file(path)
    except OSError as exc:
        raise ContractError(
            "source_mutated",
            "Source workbook could not be rehashed.",
            stage=_EXECUTION,
            retryable=False,
        ) from exc
    if actual != expected:
        raise ContractError(
            "source_mutated",
            "Source workbook changed during observation.",
            stage=_EXECUTION,
            retryable=False,
        )
