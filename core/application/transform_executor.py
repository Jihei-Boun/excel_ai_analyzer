"""Deterministic openpyxl executor for Transform v1 (Phase I2-D2).

Executes an already-parsed TransformRequest. Does not infer rows, columns,
labels, or operations. Source workbooks are never saved in place.
"""

from __future__ import annotations

import os
import tempfile
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook import Workbook
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from core.application.artifacts import (
    media_type_for,
    resolve_output_root,
    sanitize_filename,
    sanitize_request_dirname,
    sha256_file,
)
from core.application.contracts import Artifact, ContractError, WarningItem
from core.application.transform_contracts import (
    EXTRACT_TO_SHEET_OP,
    FILL_POLICY_FILL_ALL,
    FILL_POLICY_KEEP_TOP_LEFT,
    FILL_POLICY_LEAVE_EMPTY,
    INVALID_SHEET_CHARS,
    MAX_SHEET_NAME_LEN,
    UNMERGE_CELLS_OP,
    UNMERGE_SCOPE_ALL,
    AppliedOperation,
    AxisSelection,
    ExtractToSheetOp,
    MergeRange,
    TransformOp,
    TransformRequest,
    UnmergeCellsOp,
)

_OUTPUT_SUFFIX = "_transformed.xlsx"
_VALIDATION = "workbook_validation"
_EXECUTION = "execution"


@dataclass
class _SheetRef:
    worksheet: Worksheet
    title: str
    index_1based: int
    hidden: bool


@dataclass
class _StyleSnapshot:
    font: Any
    fill: Any
    border: Any
    alignment: Any
    number_format: str
    protection: Any


@dataclass
class TransformExecution:
    artifact: Artifact
    applied_operations: tuple[AppliedOperation, ...]
    warnings: tuple[WarningItem, ...]
    source_sha256: str
    output_contained: bool = True


def execute_transform(request: TransformRequest) -> TransformExecution:
    """Apply transformations to an in-memory copy and publish one .xlsx artifact.

    ``output_directory`` is already the request-scoped root. This function
    writes ``{request_id}_transformed.xlsx`` directly inside it and does not
    create another request-id subdirectory.
    """
    source = Path(request.source.path)
    source_sha = sha256_file(source)
    dest_dir = resolve_output_root(request.output_directory)
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(
        f"{sanitize_request_dirname(request.request_id)}{_OUTPUT_SUFFIX}",
        default="transformed.xlsx",
    )
    final_path = (dest_dir / filename).resolve()
    _assert_output_path(source, dest_dir, final_path)
    if final_path.exists():
        raise ContractError(
            "output_exists",
            "A transformed workbook already exists for this request_id.",
            stage=_EXECUTION,
            retryable=False,
        )
    tmp_path: Path | None = None
    published = False
    workbook: Workbook | None = None
    warnings: list[WarningItem] = []
    applied: list[AppliedOperation] = []
    try:
        workbook = _load_workbook(source)
        for operation in request.transformations:
            applied.append(_apply_operation(workbook, operation, warnings))
        tmp_path = _write_temp_workbook(workbook, dest_dir=dest_dir, filename=filename)
        _publish_exclusive(tmp_path, final_path)
        published = True
        _unlink_owned(tmp_path)
        tmp_path = None
        _reopen_smoke(final_path)
        if not final_path.is_file() or final_path.suffix.lower() != ".xlsx":
            raise ContractError(
                "invalid_output",
                "Output artifact is not a regular .xlsx file.",
                stage=_EXECUTION,
                retryable=False,
            )
        size = final_path.stat().st_size
        if size <= 0:
            raise ContractError(
                "invalid_output",
                "Output artifact is empty.",
                stage=_EXECUTION,
                retryable=False,
            )
        digest = sha256_file(final_path)
    except ContractError:
        _cleanup_owned(tmp_path=tmp_path, final_path=final_path, published=published)
        raise
    except Exception:
        _cleanup_owned(tmp_path=tmp_path, final_path=final_path, published=published)
        raise ContractError(
            "execution_failed",
            "Workbook transformation failed.",
            stage=_EXECUTION,
            retryable=False,
        ) from None
    finally:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:
                pass
        after_sha = sha256_file(source)
        if after_sha != source_sha:
            _cleanup_owned(tmp_path=tmp_path, final_path=final_path, published=published)
            raise ContractError(
                "source_mutated",
                "Source workbook changed during transformation.",
                stage=_EXECUTION,
                retryable=False,
            )
    artifact = Artifact(
        artifact_id="transformed-workbook",
        kind="workbook",
        path=str(final_path),
        media_type=media_type_for(final_path),
        filename=final_path.name,
        size_bytes=size,
        sha256=digest,
    )
    return TransformExecution(
        artifact=artifact,
        applied_operations=tuple(applied),
        warnings=tuple(warnings),
        source_sha256=source_sha,
        output_contained=True,
    )


def _load_workbook(path: Path) -> Workbook:
    try:
        return load_workbook(filename=path, read_only=False, data_only=False, keep_vba=False)
    except (InvalidFileException, BadZipFile, OSError, KeyError, ValueError):
        raise ContractError(
            "invalid_file",
            "Workbook could not be opened.",
            stage=_EXECUTION,
            retryable=False,
        ) from None


def _apply_operation(
    workbook: Workbook,
    operation: TransformOp,
    warnings: list[WarningItem],
) -> AppliedOperation:
    if isinstance(operation, ExtractToSheetOp):
        return _extract_to_sheet(workbook, operation, warnings)
    if isinstance(operation, UnmergeCellsOp):
        return _unmerge_cells(workbook, operation, warnings)
    raise ContractError(
        "invalid_operation",
        "Unsupported transformation operation.",
        stage=_VALIDATION,
        retryable=False,
    )


def _resolve_sheet(workbook: Workbook, selector: str | int) -> _SheetRef:
    sheets = list(workbook.worksheets)
    if isinstance(selector, int):
        if selector < 1 or selector > len(sheets):
            raise ContractError(
                "sheet_index_out_of_range",
                "Sheet index is outside this workbook.",
                stage=_VALIDATION,
                retryable=False,
            )
        worksheet = sheets[selector - 1]
    else:
        if selector not in workbook.sheetnames:
            raise ContractError(
                "sheet_not_found",
                "Sheet was not found.",
                stage=_VALIDATION,
                retryable=False,
            )
        worksheet = workbook[selector]
    hidden = str(getattr(worksheet, "sheet_state", "visible") or "visible") != "visible"
    return _SheetRef(
        worksheet=worksheet,
        title=worksheet.title,
        index_1based=sheets.index(worksheet) + 1,
        hidden=hidden,
    )


def _expand_axis(selection: AxisSelection) -> list[int]:
    if selection.indices is not None:
        return list(selection.indices)
    assert selection.ranges is not None
    values: list[int] = []
    for item in selection.ranges:
        values.extend(range(item.start, item.end + 1))
    return values


def _stable_unique(values: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _extract_to_sheet(
    workbook: Workbook,
    operation: ExtractToSheetOp,
    warnings: list[WarningItem],
) -> AppliedOperation:
    source = _resolve_sheet(workbook, operation.sheet)
    rows = _expand_axis(operation.rows)
    columns = _expand_axis(operation.columns)
    if operation.include_header_rows:
        rows = _stable_unique([*operation.header_rows, *rows])
    else:
        rows = _stable_unique(rows)
    columns = _stable_unique(columns)
    _assert_within_bounds(source.worksheet, rows=rows, columns=columns)
    dest_title = _allocate_sheet_title(workbook, operation.destination_sheet_base_name)
    destination = workbook.create_sheet(title=dest_title)
    skipped_hyperlinks = 0
    skipped_comments = 0
    for dest_row, source_row in enumerate(rows, start=1):
        for dest_col, source_col in enumerate(columns, start=1):
            src = source.worksheet.cell(row=source_row, column=source_col)
            dst = destination.cell(row=dest_row, column=dest_col)
            skipped_hyperlinks += int(_has_hyperlink(src))
            skipped_comments += int(_has_comment(src))
            _copy_cell_content(src, dst)
    if skipped_hyperlinks:
        warnings.append(
            WarningItem(
                code="hyperlink_not_copied",
                message="Hyperlinks are not copied onto extraction sheets.",
                stage="extract_to_sheet",
            )
        )
    if skipped_comments:
        warnings.append(
            WarningItem(
                code="comment_not_copied",
                message="Comments are not copied onto extraction sheets.",
                stage="extract_to_sheet",
            )
        )
    return AppliedOperation(
        op=EXTRACT_TO_SHEET_OP,
        sheet=operation.sheet,
        metadata={
            "source_sheet": source.title,
            "source_sheet_hidden": source.hidden,
            "destination_sheet": dest_title,
            "row_count": len(rows),
            "column_count": len(columns),
            "cell_count": len(rows) * len(columns),
            "header_rows_applied": list(operation.header_rows) if operation.include_header_rows else [],
        },
    )


def _unmerge_cells(
    workbook: Workbook,
    operation: UnmergeCellsOp,
    warnings: list[WarningItem],
) -> AppliedOperation:
    source = _resolve_sheet(workbook, operation.sheet)
    current = _current_merge_keys(source.worksheet)
    if operation.scope == UNMERGE_SCOPE_ALL:
        ranges = _snapshot_merge_ranges(source.worksheet)
        if not ranges:
            warnings.append(
                WarningItem(
                    code="no_merged_ranges",
                    message="No merged ranges were present on the target sheet.",
                    stage="unmerge_cells",
                )
            )
    else:
        requested = [_merge_key(item) for item in operation.merge_ranges]
        missing = [key for key in requested if key not in current]
        if missing:
            raise ContractError(
                "merged_range_not_found",
                "Requested merged range does not exist on the target sheet.",
                stage=_VALIDATION,
                retryable=False,
            )
        ranges = requested
    cells_written = 0
    for key in ranges:
        cells_written += _unmerge_one(source.worksheet, key, operation.fill_policy)
    return AppliedOperation(
        op=UNMERGE_CELLS_OP,
        sheet=operation.sheet,
        metadata={
            "source_sheet": source.title,
            "source_sheet_hidden": source.hidden,
            "fill_policy": operation.fill_policy,
            "scope": operation.scope,
            "merged_range_count": len(ranges),
            "cell_count": cells_written,
        },
    )


def _unmerge_one(worksheet: Worksheet, key: tuple[int, int, int, int], fill_policy: str) -> int:
    start_row, start_col, end_row, end_col = key
    anchor = worksheet.cell(row=start_row, column=start_col)
    value = _cell_value(anchor)
    style = _snapshot_style(anchor)
    worksheet.unmerge_cells(
        start_row=start_row,
        start_column=start_col,
        end_row=end_row,
        end_column=end_col,
    )
    written = 0
    if fill_policy == FILL_POLICY_FILL_ALL:
        for row in range(start_row, end_row + 1):
            for column in range(start_col, end_col + 1):
                cell = worksheet.cell(row=row, column=column)
                cell.value = value
                _apply_style(cell, style)
                written += 1
    elif fill_policy == FILL_POLICY_LEAVE_EMPTY:
        for row in range(start_row, end_row + 1):
            for column in range(start_col, end_col + 1):
                if (row, column) == (start_row, start_col):
                    continue
                worksheet.cell(row=row, column=column).value = None
                written += 1
    elif fill_policy == FILL_POLICY_KEEP_TOP_LEFT:
        written = 0
    return written


def _assert_within_bounds(worksheet: Worksheet, *, rows: list[int], columns: list[int]) -> None:
    max_row = max(int(worksheet.max_row or 1), 1)
    max_col = max(int(worksheet.max_column or 1), 1)
    if any(row > max_row for row in rows) or any(column > max_col for column in columns):
        raise ContractError(
            "coordinate_out_of_bounds",
            "A selected row or column is outside the source sheet bounds.",
            stage=_VALIDATION,
            retryable=False,
        )


def _allocate_sheet_title(workbook: Workbook, base_name: str) -> str:
    name = str(base_name or "").strip()
    if not name or any(ch in INVALID_SHEET_CHARS for ch in name) or name.startswith("'") or name.endswith("'"):
        raise ContractError(
            "invalid_destination_sheet",
            "Destination sheet base name is invalid.",
            stage=_VALIDATION,
            retryable=False,
        )
    existing = {item.lower() for item in workbook.sheetnames}
    if name.lower() not in existing and len(name) <= MAX_SHEET_NAME_LEN:
        return name
    suffix_index = 2
    while suffix_index <= 10_000:
        suffix = f"_{suffix_index}"
        room = MAX_SHEET_NAME_LEN - len(suffix)
        if room < 1:
            break
        candidate = f"{name[:room]}{suffix}"
        if candidate.lower() not in existing:
            return candidate
        suffix_index += 1
    raise ContractError(
        "invalid_destination_sheet",
        "Could not allocate a unique destination sheet name.",
        stage=_VALIDATION,
        retryable=False,
    )


def _current_merge_keys(worksheet: Worksheet) -> set[tuple[int, int, int, int]]:
    return {_range_key(item) for item in worksheet.merged_cells.ranges}


def _snapshot_merge_ranges(worksheet: Worksheet) -> list[tuple[int, int, int, int]]:
    keys = [_range_key(item) for item in worksheet.merged_cells.ranges]
    keys.sort()
    return keys


def _range_key(item: CellRange) -> tuple[int, int, int, int]:
    return (int(item.min_row), int(item.min_col), int(item.max_row), int(item.max_col))


def _merge_key(item: MergeRange) -> tuple[int, int, int, int]:
    return (item.start_row, item.start_column, item.end_row, item.end_column)


def _cell_value(cell: Any) -> Any:
    if isinstance(cell, MergedCell):
        return None
    return cell.value


def _has_hyperlink(cell: Any) -> bool:
    try:
        return getattr(cell, "hyperlink", None) is not None
    except Exception:
        return False


def _has_comment(cell: Any) -> bool:
    try:
        return getattr(cell, "comment", None) is not None
    except Exception:
        return False


def _copy_cell_content(src: Any, dst: Any) -> None:
    if isinstance(src, MergedCell):
        dst.value = None
        return
    dst.value = src.value
    _apply_style(dst, _snapshot_style(src))


def _snapshot_style(cell: Any) -> _StyleSnapshot | None:
    try:
        if isinstance(cell, MergedCell) or not getattr(cell, "has_style", False):
            return None
        return _StyleSnapshot(
            font=copy(cell.font),
            fill=copy(cell.fill),
            border=copy(cell.border),
            alignment=copy(cell.alignment),
            number_format=str(cell.number_format or "General"),
            protection=copy(cell.protection),
        )
    except Exception:
        return None


def _apply_style(cell: Any, style: _StyleSnapshot | None) -> None:
    if style is None:
        return
    cell.font = style.font
    cell.fill = style.fill
    cell.border = style.border
    cell.alignment = style.alignment
    cell.number_format = style.number_format
    cell.protection = style.protection


def _assert_output_path(source: Path, dest_dir: Path, final_path: Path) -> None:
    source_resolved = source.resolve()
    dest_resolved = dest_dir.resolve()
    final_resolved = final_path.resolve()
    try:
        final_resolved.relative_to(dest_resolved)
    except ValueError as exc:
        raise ContractError(
            "invalid_output_directory",
            "Output path escaped the request output directory.",
            stage=_EXECUTION,
            retryable=False,
        ) from exc
    if final_resolved == source_resolved:
        raise ContractError(
            "overwrite_forbidden",
            "Output path must not overwrite the source workbook.",
            stage=_EXECUTION,
            retryable=False,
        )


def _write_temp_workbook(workbook: Workbook, *, dest_dir: Path, filename: str) -> Path:
    handle, raw_name = tempfile.mkstemp(
        prefix=f".{filename}.",
        suffix=".tmp",
        dir=str(dest_dir),
    )
    os.close(handle)
    tmp_path = Path(raw_name)
    try:
        tmp_path.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        _unlink_owned(tmp_path)
        raise ContractError(
            "invalid_output_directory",
            "Temporary output path escaped the request output directory.",
            stage=_EXECUTION,
            retryable=False,
        )
    try:
        workbook.save(tmp_path)
    except OSError:
        _unlink_owned(tmp_path)
        raise ContractError(
            "save_failed",
            "Workbook could not be saved.",
            stage=_EXECUTION,
            retryable=True,
        ) from None
    return tmp_path


def _publish_exclusive(tmp_path: Path, final_path: Path) -> None:
    """Create final only if it does not already exist. Never replace a pre-existing file."""
    try:
        os.link(tmp_path, final_path)
    except FileExistsError:
        raise ContractError(
            "output_exists",
            "A transformed workbook already exists for this request_id.",
            stage=_EXECUTION,
            retryable=False,
        ) from None
    except OSError:
        raise ContractError(
            "save_failed",
            "Workbook could not be saved.",
            stage=_EXECUTION,
            retryable=True,
        ) from None


def _reopen_smoke(path: Path) -> None:
    try:
        reopened = load_workbook(filename=path, read_only=True, data_only=False)
        try:
            if not reopened.sheetnames:
                raise ContractError(
                    "invalid_output",
                    "Output workbook has no sheets.",
                    stage=_EXECUTION,
                    retryable=False,
                )
        finally:
            reopened.close()
    except ContractError:
        raise
    except Exception:
        raise ContractError(
            "invalid_output",
            "Output workbook could not be reopened.",
            stage=_EXECUTION,
            retryable=False,
        ) from None


def _cleanup_owned(*, tmp_path: Path | None, final_path: Path, published: bool) -> None:
    _unlink_owned(tmp_path)
    if published:
        _unlink_owned(final_path)


def _unlink_owned(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    except OSError:
        pass
