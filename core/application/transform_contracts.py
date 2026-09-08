"""Workbook transformation contracts (Phase I2-D1 / I2-D1A).

This module is a structural parse/validation boundary only. It does not
execute XLSX edits, call an LLM planner, or route analysis prompts.

Coordinate system
-----------------
All row, column, and merge-range numbers in this contract are **1-based
Excel coordinates**, inclusive on both ends. Sheet indices, when given as
integers, are also **1-based** (Excel tab order). Python 0-based indexes
are not accepted.

File format (v1)
----------------
Transform v1 accepts only ``.xlsx``. Suffix matching is case-insensitive
(``.XLSX`` is compared as ``.xlsx``), matching the analyze boundary.
``.xls``, ``.xlsm``, and ``.csv`` are rejected here. A later contract
version may add those formats once a dedicated fidelity policy and
executor exist.

Sheet selector
--------------
``source`` is a workbook path only. Each transformation operation must set
``sheet``. The parser does not copy, default, or reconcile sheet values
across operations or from source. Whether a named sheet exists in the
workbook is an executor concern (Phase I2-D2).

Output directory
----------------
``output_directory`` is the caller-provided request-scoped output root.
The executor writes ``{sanitized_request_id}_transformed.xlsx`` directly
inside that directory. It does not create another request-id subdirectory.

Python's role
-------------
Validate JSON shape, enums, ranges, paths, and overwrite invariants.
Do not infer rows/columns from labels, expand groups, rewrite operations,
or inject workbook-specific defaults.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from core.application.contracts import (
    CONTRACT_VERSION,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    Artifact,
    ContractError,
    ErrorInfo,
    SafetyInfo,
    TimingInfo,
    WarningItem,
)

TRANSFORM_CONTRACT_VERSION = CONTRACT_VERSION
TRANSFORM_OPERATION = "transform"

EXTRACT_TO_SHEET_OP = "extract_to_sheet"
UNMERGE_CELLS_OP = "unmerge_cells"
TRANSFORM_OPS = frozenset({EXTRACT_TO_SHEET_OP, UNMERGE_CELLS_OP})

FILL_POLICY_FILL_ALL = "fill_all"
FILL_POLICY_KEEP_TOP_LEFT = "keep_top_left"
FILL_POLICY_LEAVE_EMPTY = "leave_empty"
FILL_POLICIES = frozenset(
    {
        FILL_POLICY_FILL_ALL,
        FILL_POLICY_KEEP_TOP_LEFT,
        FILL_POLICY_LEAVE_EMPTY,
    }
)
UNMERGE_SCOPE_ALL = "all_merged_ranges"
UNMERGE_SCOPE_EXPLICIT = "explicit_ranges"
UNMERGE_SCOPES = frozenset({UNMERGE_SCOPE_ALL, UNMERGE_SCOPE_EXPLICIT})

# fill_all: copy the existing top-left cell value into every cell of that
# merged range after unmerge. keep_top_left: leave only the top-left cell
# populated. leave_empty: clear previously hidden cells. The validator does
# not choose a policy; the request must set fill_policy explicitly.

TRANSFORM_STATUSES = frozenset(
    {
        "success",
        "invalid_request",
        "validation_failed",
        "execution_failed",
        "timeout",
        "cancelled",
        "cannot_plan",
        "model_unavailable",
    }
)

TRANSFORM_ALLOWED_EXTENSIONS = frozenset({".xlsx"})
MAX_REQUEST_ID_LEN = 64
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_SHEET_NAME_LEN = 31
INVALID_SHEET_CHARS = set(r":\/?*[]")
MAX_SHEET_INDEX = 256
MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COL = 16_384

_TOP_KEYS = frozenset(
    {
        "contract_version",
        "operation",
        "request_id",
        "source",
        "output_directory",
        "timeout_seconds",
        "transformations",
        "output_policy",
    }
)
_SOURCE_KEYS = frozenset({"path"})
_OUTPUT_POLICY_KEYS = frozenset({"source_immutable", "create_copy"})
_EXTRACT_KEYS = frozenset(
    {
        "op",
        "sheet",
        "rows",
        "row_ranges",
        "columns",
        "column_ranges",
        "header_rows",
        "include_header_rows",
        "destination_sheet_base_name",
        "preserve_selection_order",
    }
)
_UNMERGE_KEYS = frozenset(
    {
        "op",
        "sheet",
        "scope",
        "merge_ranges",
        "fill_policy",
    }
)


@dataclass(frozen=True)
class InclusiveRange:
    """1-based inclusive Excel range on one axis (row or column)."""

    start: int
    end: int

    def to_dict(self) -> dict[str, int]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class AxisSelection:
    """Exactly one of ``indices`` or ``ranges`` (1-based, request order preserved)."""

    indices: tuple[int, ...] | None
    ranges: tuple[InclusiveRange, ...] | None

    def to_dict(self, *, index_key: str, range_key: str, start_key: str, end_key: str) -> dict[str, Any]:
        if self.indices is not None:
            return {index_key: list(self.indices)}
        assert self.ranges is not None
        return {
            range_key: [{start_key: item.start, end_key: item.end} for item in self.ranges]
        }


@dataclass(frozen=True)
class MergeRange:
    """1-based inclusive rectangular merge range."""

    start_row: int
    start_column: int
    end_row: int
    end_column: int

    def to_dict(self) -> dict[str, int]:
        return {
            "start_row": self.start_row,
            "start_column": self.start_column,
            "end_row": self.end_row,
            "end_column": self.end_column,
        }


@dataclass(frozen=True)
class TransformSource:
    """Workbook path only. Target sheet lives on each operation."""

    path: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path}


@dataclass(frozen=True)
class OutputPolicy:
    """Copy-only output. Source overwrite is never allowed."""

    source_immutable: bool
    create_copy: bool

    def to_dict(self) -> dict[str, bool]:
        return {
            "source_immutable": bool(self.source_immutable),
            "create_copy": bool(self.create_copy),
        }


@dataclass(frozen=True)
class ExtractToSheetOp:
    op: str
    sheet: str | int
    rows: AxisSelection
    columns: AxisSelection
    header_rows: tuple[int, ...]
    include_header_rows: bool
    destination_sheet_base_name: str
    preserve_selection_order: bool

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op": self.op,
            "sheet": self.sheet,
            "header_rows": list(self.header_rows),
            "include_header_rows": self.include_header_rows,
            "destination_sheet_base_name": self.destination_sheet_base_name,
            "preserve_selection_order": self.preserve_selection_order,
        }
        payload.update(
            self.rows.to_dict(
                index_key="rows",
                range_key="row_ranges",
                start_key="start_row",
                end_key="end_row",
            )
        )
        payload.update(
            self.columns.to_dict(
                index_key="columns",
                range_key="column_ranges",
                start_key="start_column",
                end_key="end_column",
            )
        )
        return payload


@dataclass(frozen=True)
class UnmergeCellsOp:
    op: str
    sheet: str | int
    scope: str
    merge_ranges: tuple[MergeRange, ...]
    fill_policy: str

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "op": self.op,
            "sheet": self.sheet,
            "scope": self.scope,
            "fill_policy": self.fill_policy,
        }
        if self.scope == UNMERGE_SCOPE_EXPLICIT:
            payload["merge_ranges"] = [item.to_dict() for item in self.merge_ranges]
        return payload


TransformOp = ExtractToSheetOp | UnmergeCellsOp


@dataclass(frozen=True)
class TransformRequest:
    contract_version: str
    request_id: str
    operation: str
    source: TransformSource
    output_directory: str
    timeout_seconds: float
    transformations: tuple[TransformOp, ...]
    output_policy: OutputPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "operation": self.operation,
            "source": self.source.to_dict(),
            "output_directory": self.output_directory,
            "timeout_seconds": self.timeout_seconds,
            "transformations": [item.to_dict() for item in self.transformations],
            "output_policy": self.output_policy.to_dict(),
        }


@dataclass(frozen=True)
class AppliedOperation:
    op: str
    sheet: str | int
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "sheet": self.sheet,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TransformResponse:
    """JSON envelope produced by ``transform_excel()`` (Phase I2-D2)."""

    contract_version: str = TRANSFORM_CONTRACT_VERSION
    request_id: str = ""
    status: str = "execution_failed"
    text: str = ""
    applied_operations: tuple[AppliedOperation, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    warnings: tuple[WarningItem, ...] = ()
    safety: SafetyInfo = field(
        default_factory=lambda: SafetyInfo(validation_status="not_applicable")
    )
    timing: TimingInfo = field(default_factory=TimingInfo)
    error: ErrorInfo | None = None
    source_immutable_verified: bool = False
    output_contained: bool = False

    def to_dict(self) -> dict[str, Any]:
        safety = self.safety.to_dict()
        safety["source_immutable_verified"] = bool(self.source_immutable_verified)
        safety["output_contained"] = bool(self.output_contained)
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "status": self.status,
            "text": self.text,
            "applied_operations": [item.to_dict() for item in self.applied_operations],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "warnings": [item.to_dict() for item in self.warnings],
            "safety": safety,
            "timing": self.timing.to_dict(),
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        return payload


def parse_transform_request(payload: TransformRequest | dict[str, Any] | None) -> TransformRequest:
    """Parse and structurally validate a transform request.

    Raises ContractError on any structural/contract failure. Does not execute.
    """
    if isinstance(payload, TransformRequest):
        _validate_parsed(payload)
        return payload
    if not isinstance(payload, dict):
        raise ContractError("invalid_request", "Request must be a JSON object.")
    _reject_unknown_keys(payload, _TOP_KEYS, context="request")

    version = str(payload.get("contract_version") or "").strip()
    if version != TRANSFORM_CONTRACT_VERSION:
        raise ContractError(
            "unsupported_contract_version",
            f"Unsupported contract_version: {version!r}. Expected {TRANSFORM_CONTRACT_VERSION}.",
        )

    operation = str(payload.get("operation") or "").strip()
    if operation != TRANSFORM_OPERATION:
        raise ContractError(
            "invalid_operation",
            f"Unsupported operation: {operation!r}. Expected {TRANSFORM_OPERATION!r}.",
        )

    request_id = _parse_request_id(payload.get("request_id"))
    timeout_seconds = _parse_timeout(payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    output_directory = _parse_output_directory(payload.get("output_directory"))
    source = _parse_source(payload.get("source"))
    _reject_source_output_overwrite(source.path, output_directory)
    output_policy = _parse_output_policy(payload.get("output_policy"))
    transformations = _parse_transformations(payload.get("transformations"))

    parsed = TransformRequest(
        contract_version=TRANSFORM_CONTRACT_VERSION,
        request_id=request_id,
        operation=TRANSFORM_OPERATION,
        source=source,
        output_directory=output_directory,
        timeout_seconds=timeout_seconds,
        transformations=transformations,
        output_policy=output_policy,
    )
    _validate_parsed(parsed)
    return parsed


def _validate_parsed(request: TransformRequest) -> None:
    if request.operation != TRANSFORM_OPERATION:
        raise ContractError(
            "invalid_operation",
            f"Unsupported operation: {request.operation!r}.",
        )
    if not request.transformations:
        raise ContractError(
            "empty_transformations",
            "transformations must contain at least one operation.",
        )
    if not request.output_policy.source_immutable or not request.output_policy.create_copy:
        raise ContractError(
            "invalid_output_policy",
            "output_policy must set source_immutable=true and create_copy=true.",
        )
    _reject_source_output_overwrite(request.source.path, request.output_directory)


def _parse_request_id(raw: Any) -> str:
    if raw is None or not str(raw).strip():
        raise ContractError("missing_request_id", "request_id is required.")
    request_id = str(raw).strip()
    if len(request_id) > MAX_REQUEST_ID_LEN or not REQUEST_ID_RE.fullmatch(request_id):
        raise ContractError(
            "invalid_request_id",
            "request_id must be 1–64 characters in [A-Za-z0-9._-].",
        )
    return request_id


def _parse_timeout(raw: Any) -> float:
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_timeout", "timeout_seconds must be a positive number.") from exc
    if not math.isfinite(value) or value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise ContractError(
            "invalid_timeout",
            "timeout_seconds must be a finite number greater than 0.",
        )
    return value


def _parse_output_directory(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(
            "invalid_output_directory",
            "output_directory is required and must be an absolute path.",
        )
    path = Path(raw.strip())
    if not path.is_absolute():
        raise ContractError(
            "invalid_output_directory",
            "output_directory must be an absolute path.",
        )
    if ".." in path.parts:
        raise ContractError(
            "invalid_output_directory",
            "output_directory must not contain '..' segments.",
        )
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ContractError(
            "invalid_output_directory",
            "output_directory could not be resolved.",
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise ContractError(
            "invalid_output_directory",
            "output_directory exists and is not a directory.",
        )
    return str(resolved)


def _parse_source(raw: Any) -> TransformSource:
    if not isinstance(raw, dict):
        raise ContractError("invalid_source", "source must be an object with path.")
    _reject_unknown_keys(raw, _SOURCE_KEYS, context="source")
    path_raw = raw.get("path")
    if not isinstance(path_raw, str) or not path_raw.strip():
        raise ContractError("missing_input_path", "source.path is required.")
    path = Path(path_raw.strip())
    if not path.is_absolute():
        raise ContractError("invalid_input_path", "source.path must be an absolute path.")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ContractError("invalid_input_path", "source.path could not be resolved.") from exc
    if ".." in Path(path_raw.strip()).parts:
        raise ContractError("invalid_input_path", "source.path must not contain '..' segments.")
    suffix = resolved.suffix.lower()
    if suffix not in TRANSFORM_ALLOWED_EXTENSIONS:
        raise ContractError(
            "unsupported_extension",
            f"Unsupported file extension: {suffix or resolved.name!r}. Transform v1 accepts .xlsx only.",
        )
    if not resolved.is_file():
        raise ContractError("missing_file", "source.path does not exist or is not a file.")
    return TransformSource(path=str(resolved))


def _parse_output_policy(raw: Any) -> OutputPolicy:
    if not isinstance(raw, dict):
        raise ContractError(
            "invalid_output_policy",
            "output_policy is required and must set source_immutable and create_copy to true.",
        )
    _reject_unknown_keys(raw, _OUTPUT_POLICY_KEYS, context="output_policy")
    source_immutable = raw.get("source_immutable")
    create_copy = raw.get("create_copy")
    if source_immutable is not True or create_copy is not True:
        raise ContractError(
            "invalid_output_policy",
            "output_policy must set source_immutable=true and create_copy=true.",
        )
    return OutputPolicy(source_immutable=True, create_copy=True)


def _reject_source_output_overwrite(source_path: str, output_directory: str) -> None:
    try:
        source = Path(source_path).resolve()
        output = Path(output_directory).resolve()
    except OSError as exc:
        raise ContractError("invalid_output_directory", "paths could not be resolved.") from exc
    if source == output:
        raise ContractError(
            "overwrite_forbidden",
            "source path and output_directory must be different.",
        )
    try:
        source.relative_to(output)
    except ValueError:
        return
    raise ContractError(
        "overwrite_forbidden",
        "source workbook must not live inside output_directory.",
    )


def _parse_transformations(raw: Any) -> tuple[TransformOp, ...]:
    if not isinstance(raw, list):
        raise ContractError("empty_transformations", "transformations must be a non-empty list.")
    if not raw:
        raise ContractError(
            "empty_transformations",
            "transformations must contain at least one operation.",
        )
    parsed: list[TransformOp] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError(
                "invalid_transformation",
                f"transformations[{index}] must be an object.",
            )
        op = str(item.get("op") or "").strip()
        if op == EXTRACT_TO_SHEET_OP:
            parsed.append(_parse_extract(item, index=index))
        elif op == UNMERGE_CELLS_OP:
            parsed.append(_parse_unmerge(item, index=index))
        else:
            raise ContractError(
                "invalid_operation",
                f"transformations[{index}].op is unsupported: {op!r}.",
            )
    return tuple(parsed)


def _parse_extract(raw: dict[str, Any], *, index: int) -> ExtractToSheetOp:
    if "output_path" in raw or "output_directory" in raw:
        raise ContractError(
            "invalid_output_path",
            f"transformations[{index}] must not set an output path.",
        )
    _reject_unknown_keys(raw, _EXTRACT_KEYS, context=f"transformations[{index}]")
    sheet = _parse_sheet(raw.get("sheet"), context=f"transformations[{index}].sheet")
    rows = _parse_axis(
        raw,
        index_key="rows",
        range_key="row_ranges",
        start_key="start_row",
        end_key="end_row",
        bound=MAX_EXCEL_ROW,
        context=f"transformations[{index}]",
        axis="row",
    )
    columns = _parse_axis(
        raw,
        index_key="columns",
        range_key="column_ranges",
        start_key="start_column",
        end_key="end_column",
        bound=MAX_EXCEL_COL,
        context=f"transformations[{index}]",
        axis="column",
    )
    header_rows = _parse_header_rows(raw.get("header_rows"), context=f"transformations[{index}]")
    include_header_rows = raw.get("include_header_rows")
    if include_header_rows is None:
        include_flag = bool(header_rows)
    elif include_header_rows is True:
        if not header_rows:
            raise ContractError(
                "invalid_header_rows",
                f"transformations[{index}].include_header_rows is true but header_rows is empty.",
            )
        include_flag = True
    elif include_header_rows is False:
        if header_rows:
            raise ContractError(
                "invalid_header_rows",
                f"transformations[{index}].include_header_rows is false but header_rows is not empty.",
            )
        include_flag = False
    else:
        raise ContractError(
            "invalid_header_rows",
            f"transformations[{index}].include_header_rows must be a boolean.",
        )
    dest = raw.get("destination_sheet_base_name")
    if not isinstance(dest, str) or not dest.strip():
        raise ContractError(
            "invalid_sheet_name",
            f"transformations[{index}].destination_sheet_base_name is required.",
        )
    dest_name = dest.strip()
    _validate_sheet_base_name(dest_name, context=f"transformations[{index}].destination_sheet_base_name")
    preserve = raw.get("preserve_selection_order", True)
    if preserve is not True:
        raise ContractError(
            "invalid_selection_order",
            f"transformations[{index}].preserve_selection_order must be true.",
        )
    return ExtractToSheetOp(
        op=EXTRACT_TO_SHEET_OP,
        sheet=sheet,
        rows=rows,
        columns=columns,
        header_rows=header_rows,
        include_header_rows=include_flag,
        destination_sheet_base_name=dest_name,
        preserve_selection_order=True,
    )


def _parse_unmerge(raw: dict[str, Any], *, index: int) -> UnmergeCellsOp:
    if "output_path" in raw or "output_directory" in raw:
        raise ContractError(
            "invalid_output_path",
            f"transformations[{index}] must not set an output path.",
        )
    _reject_unknown_keys(raw, _UNMERGE_KEYS, context=f"transformations[{index}]")
    sheet = _parse_sheet(raw.get("sheet"), context=f"transformations[{index}].sheet")
    if "fill_policy" not in raw:
        raise ContractError(
            "missing_fill_policy",
            f"transformations[{index}].fill_policy is required.",
        )
    fill_policy = str(raw.get("fill_policy") or "").strip()
    if fill_policy not in FILL_POLICIES:
        raise ContractError(
            "invalid_fill_policy",
            f"transformations[{index}].fill_policy must be one of {sorted(FILL_POLICIES)}.",
        )
    merge_raw = raw.get("merge_ranges")
    scope_raw = raw.get("scope")
    if scope_raw is None and merge_raw is None:
        raise ContractError(
            "invalid_unmerge_scope",
            f"transformations[{index}] requires scope or merge_ranges.",
        )
    if scope_raw is None:
        scope = UNMERGE_SCOPE_EXPLICIT
    else:
        scope = str(scope_raw).strip()
        if scope not in UNMERGE_SCOPES:
            raise ContractError(
                "invalid_unmerge_scope",
                f"transformations[{index}].scope must be one of {sorted(UNMERGE_SCOPES)}.",
            )
    if scope == UNMERGE_SCOPE_ALL:
        if merge_raw not in (None, []):
            raise ContractError(
                "invalid_unmerge_scope",
                f"transformations[{index}]: all_merged_ranges must not include merge_ranges.",
            )
        ranges: tuple[MergeRange, ...] = ()
    else:
        ranges = _parse_merge_ranges(merge_raw, context=f"transformations[{index}]")
        if not ranges:
            raise ContractError(
                "invalid_unmerge_scope",
                f"transformations[{index}].merge_ranges must be a non-empty list.",
            )
    return UnmergeCellsOp(
        op=UNMERGE_CELLS_OP,
        sheet=sheet,
        scope=scope,
        merge_ranges=ranges,
        fill_policy=fill_policy,
    )


def _parse_sheet(raw: Any, *, context: str) -> str | int:
    if isinstance(raw, bool) or raw is None:
        raise ContractError("invalid_sheet", f"{context} is required (1-based index or name).")
    if isinstance(raw, int):
        if raw < 1 or raw > MAX_SHEET_INDEX:
            raise ContractError("invalid_sheet", f"{context} sheet index must be 1–{MAX_SHEET_INDEX}.")
        return raw
    if isinstance(raw, str) and raw.strip():
        name = raw.strip()
        _validate_sheet_base_name(name, context=context)
        return name
    raise ContractError("invalid_sheet", f"{context} is invalid.")


def _validate_sheet_base_name(name: str, *, context: str) -> None:
    if len(name) > MAX_SHEET_NAME_LEN:
        raise ContractError(
            "invalid_sheet_name",
            f"{context} exceeds Excel's {MAX_SHEET_NAME_LEN}-character limit.",
        )
    if any(ch in INVALID_SHEET_CHARS for ch in name) or name.startswith("'") or name.endswith("'"):
        raise ContractError(
            "invalid_sheet_name",
            f"{context} contains characters that Excel does not allow.",
        )


def _parse_axis(
    raw: dict[str, Any],
    *,
    index_key: str,
    range_key: str,
    start_key: str,
    end_key: str,
    bound: int,
    context: str,
    axis: str,
) -> AxisSelection:
    has_indices = index_key in raw and raw[index_key] is not None
    has_ranges = range_key in raw and raw[range_key] is not None
    if has_indices == has_ranges:
        raise ContractError(
            "invalid_range",
            f"{context} must set exactly one of {index_key} or {range_key}.",
        )
    if has_indices:
        values = _parse_index_list(raw[index_key], bound=bound, context=f"{context}.{index_key}", axis=axis)
        return AxisSelection(indices=values, ranges=None)
    ranges = _parse_range_list(
        raw[range_key],
        start_key=start_key,
        end_key=end_key,
        bound=bound,
        context=f"{context}.{range_key}",
        axis=axis,
    )
    return AxisSelection(indices=None, ranges=ranges)


def _parse_index_list(raw: Any, *, bound: int, context: str, axis: str) -> tuple[int, ...]:
    if not isinstance(raw, list) or not raw:
        raise ContractError("invalid_range", f"{context} must be a non-empty list of 1-based {axis}s.")
    values: list[int] = []
    seen: set[int] = set()
    for item in raw:
        number = _parse_positive_int(item, bound=bound, context=context, axis=axis)
        if number in seen:
            raise ContractError("duplicate_range", f"{context} contains duplicate {axis} {number}.")
        seen.add(number)
        values.append(number)
    return tuple(values)


def _parse_range_list(
    raw: Any,
    *,
    start_key: str,
    end_key: str,
    bound: int,
    context: str,
    axis: str,
) -> tuple[InclusiveRange, ...]:
    if not isinstance(raw, list) or not raw:
        raise ContractError("invalid_range", f"{context} must be a non-empty list of ranges.")
    ranges: list[InclusiveRange] = []
    covered: set[int] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError("invalid_range", f"{context}[{index}] must be an object.")
        extra = set(item) - {start_key, end_key}
        if extra:
            raise ContractError(
                "unknown_field",
                f"{context}[{index}] has unknown field(s): {sorted(extra)}.",
            )
        start = _parse_positive_int(item.get(start_key), bound=bound, context=f"{context}[{index}].{start_key}", axis=axis)
        end = _parse_positive_int(item.get(end_key), bound=bound, context=f"{context}[{index}].{end_key}", axis=axis)
        if start > end:
            raise ContractError(
                "reversed_range",
                f"{context}[{index}] is reversed: {start_key}={start} > {end_key}={end}.",
            )
        span = set(range(start, end + 1))
        if covered & span:
            raise ContractError(
                "overlapping_range",
                f"{context}[{index}] overlaps another {axis} selection.",
            )
        covered |= span
        ranges.append(InclusiveRange(start=start, end=end))
    return tuple(ranges)


def _parse_header_rows(raw: Any, *, context: str) -> tuple[int, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ContractError("invalid_header_rows", f"{context}.header_rows must be a list of 1-based rows.")
    if not raw:
        return ()
    return _parse_index_list(raw, bound=MAX_EXCEL_ROW, context=f"{context}.header_rows", axis="row")


def _parse_merge_ranges(raw: Any, *, context: str) -> tuple[MergeRange, ...]:
    if not isinstance(raw, list):
        raise ContractError("invalid_range", f"{context}.merge_ranges must be a list.")
    ranges: list[MergeRange] = []
    seen: set[tuple[int, int, int, int]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError("invalid_range", f"{context}.merge_ranges[{index}] must be an object.")
        extra = set(item) - {"start_row", "start_column", "end_row", "end_column"}
        if extra:
            raise ContractError(
                "unknown_field",
                f"{context}.merge_ranges[{index}] has unknown field(s): {sorted(extra)}.",
            )
        start_row = _parse_positive_int(
            item.get("start_row"), bound=MAX_EXCEL_ROW, context=f"{context}.merge_ranges[{index}].start_row", axis="row"
        )
        end_row = _parse_positive_int(
            item.get("end_row"), bound=MAX_EXCEL_ROW, context=f"{context}.merge_ranges[{index}].end_row", axis="row"
        )
        start_col = _parse_positive_int(
            item.get("start_column"),
            bound=MAX_EXCEL_COL,
            context=f"{context}.merge_ranges[{index}].start_column",
            axis="column",
        )
        end_col = _parse_positive_int(
            item.get("end_column"),
            bound=MAX_EXCEL_COL,
            context=f"{context}.merge_ranges[{index}].end_column",
            axis="column",
        )
        if start_row > end_row or start_col > end_col:
            raise ContractError(
                "reversed_range",
                f"{context}.merge_ranges[{index}] is reversed.",
            )
        key = (start_row, start_col, end_row, end_col)
        if key in seen:
            raise ContractError("duplicate_range", f"{context}.merge_ranges[{index}] is duplicated.")
        seen.add(key)
        ranges.append(
            MergeRange(
                start_row=start_row,
                start_column=start_col,
                end_row=end_row,
                end_column=end_col,
            )
        )
    return tuple(ranges)


def _parse_positive_int(raw: Any, *, bound: int, context: str, axis: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ContractError("invalid_range", f"{context} must be a 1-based integer {axis}.")
    if raw < 1:
        raise ContractError("invalid_range", f"{context} must be >= 1.")
    if raw > bound:
        raise ContractError("invalid_range", f"{context} exceeds Excel {axis} limit {bound}.")
    return raw


def _reject_unknown_keys(payload: Mapping[str, Any], allowed: frozenset[str], *, context: str) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        raise ContractError("unknown_field", f"{context} has unknown field(s): {extra}.")
