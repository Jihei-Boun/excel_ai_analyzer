"""Structural tests for the workbook transform contract (Phase I2-D1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.application import parse_analyze_request, parse_transform_request
from core.application.contracts import CONTRACT_VERSION, ContractError
from core.application.transform_contracts import (
    EXTRACT_TO_SHEET_OP,
    FILL_POLICY_FILL_ALL,
    TRANSFORM_CONTRACT_VERSION,
    TRANSFORM_OPERATION,
    UNMERGE_CELLS_OP,
    ExtractToSheetOp,
    TransformRequest,
    UnmergeCellsOp,
)
from tests.application_support import base_request, write_table

ROOT = Path(__file__).resolve().parents[1]
TRANSFORM_MODULE = ROOT / "core" / "application" / "transform_contracts.py"


def _source_book(tmp_path: Path, name: str = "book.xlsx") -> Path:
    return write_table(tmp_path / name, {"code": ["A", "B"], "amount": [1, 2]})


def _base_transform(
    tmp_path: Path,
    *,
    source: Path | None = None,
    transformations: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = source or _source_book(tmp_path)
    payload: dict[str, Any] = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": "req-transform-1",
        "source": {"path": str(path.resolve())},
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "output_policy": {"source_immutable": True, "create_copy": True},
        "transformations": transformations
        if transformations is not None
        else [
            {
                "op": EXTRACT_TO_SHEET_OP,
                "sheet": "Sheet1",
                "row_ranges": [{"start_row": 3, "end_row": 5}],
                "columns": [1, 6],
                "header_rows": [1, 2],
                "include_header_rows": True,
                "destination_sheet_base_name": "Extracted",
                "preserve_selection_order": True,
            }
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def _raises(payload: dict[str, Any], *, code: str) -> None:
    with pytest.raises(ContractError) as exc:
        parse_transform_request(payload)
    assert exc.value.code == code


def test_parse_extract_to_sheet_single_operation(tmp_path: Path) -> None:
    parsed = parse_transform_request(_base_transform(tmp_path))
    assert parsed.operation == TRANSFORM_OPERATION
    assert parsed.contract_version == TRANSFORM_CONTRACT_VERSION
    assert len(parsed.transformations) == 1
    op = parsed.transformations[0]
    assert isinstance(op, ExtractToSheetOp)
    assert op.op == EXTRACT_TO_SHEET_OP
    assert op.rows.ranges is not None
    assert op.rows.ranges[0].start == 3
    assert op.rows.ranges[0].end == 5
    assert op.columns.indices == (1, 6)
    assert op.header_rows == (1, 2)
    assert op.include_header_rows is True
    assert op.destination_sheet_base_name == "Extracted"
    assert op.preserve_selection_order is True
    assert op.sheet == "Sheet1"
    assert "sheet" not in parsed.source.to_dict()
    assert list(parsed.source.to_dict()) == ["path"]


def test_parse_unmerge_fill_all(tmp_path: Path) -> None:
    payload = _base_transform(
        tmp_path,
        transformations=[
            {
                "op": UNMERGE_CELLS_OP,
                "sheet": "Sheet1",
                "scope": "all_merged_ranges",
                "fill_policy": FILL_POLICY_FILL_ALL,
            }
        ],
    )
    parsed = parse_transform_request(payload)
    op = parsed.transformations[0]
    assert isinstance(op, UnmergeCellsOp)
    assert op.fill_policy == FILL_POLICY_FILL_ALL
    assert op.scope == "all_merged_ranges"
    assert op.merge_ranges == ()


def test_transformation_order_preserved(tmp_path: Path) -> None:
    payload = _base_transform(
        tmp_path,
        transformations=[
            {
                "op": UNMERGE_CELLS_OP,
                "sheet": 1,
                "scope": "explicit_ranges",
                "merge_ranges": [
                    {"start_row": 2, "start_column": 2, "end_row": 2, "end_column": 4}
                ],
                "fill_policy": FILL_POLICY_FILL_ALL,
            },
            {
                "op": EXTRACT_TO_SHEET_OP,
                "sheet": 1,
                "rows": [4, 9],
                "column_ranges": [{"start_column": 2, "end_column": 4}],
                "header_rows": [1],
                "destination_sheet_base_name": "추출결과",
            },
        ],
    )
    parsed = parse_transform_request(payload)
    assert [item.op for item in parsed.transformations] == [
        UNMERGE_CELLS_OP,
        EXTRACT_TO_SHEET_OP,
    ]
    extract = parsed.transformations[1]
    assert isinstance(extract, ExtractToSheetOp)
    assert extract.rows.indices == (4, 9)
    assert extract.columns.ranges is not None
    assert extract.columns.ranges[0].start == 2
    assert extract.columns.ranges[0].end == 4
    assert extract.destination_sheet_base_name == "추출결과"


def test_request_id_timeout_and_copy_policy_preserved(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["request_id"] = "job.42_ok"
    payload["timeout_seconds"] = 42
    parsed = parse_transform_request(payload)
    assert parsed.request_id == "job.42_ok"
    assert parsed.timeout_seconds == 42
    assert parsed.output_policy.source_immutable is True
    assert parsed.output_policy.create_copy is True
    dumped = parsed.to_dict()
    assert dumped["output_policy"]["create_copy"] is True
    assert dumped["timeout_seconds"] == 42


def test_unicode_destination_sheet_base_name(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["destination_sheet_base_name"] = "추출결과"
    parsed = parse_transform_request(payload)
    op = parsed.transformations[0]
    assert isinstance(op, ExtractToSheetOp)
    assert op.destination_sheet_base_name == "추출결과"


def test_round_trip_to_dict(tmp_path: Path) -> None:
    parsed = parse_transform_request(_base_transform(tmp_path))
    again = parse_transform_request(parsed.to_dict())
    assert again == parsed


def test_reject_analyze_operation(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["operation"] = "analyze"
    _raises(payload, code="invalid_operation")


def test_reject_unsupported_operation(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["operation"] = "reshape"
    _raises(payload, code="invalid_operation")
    payload["operation"] = TRANSFORM_OPERATION
    payload["transformations"][0]["op"] = "pivot"
    _raises(payload, code="invalid_operation")


def test_reject_invalid_timeout(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["timeout_seconds"] = 0
    _raises(payload, code="invalid_timeout")
    payload["timeout_seconds"] = -5
    _raises(payload, code="invalid_timeout")


def test_adjacent_non_overlapping_ranges_allowed(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["row_ranges"] = [
        {"start_row": 2, "end_row": 3},
        {"start_row": 4, "end_row": 6},
    ]
    parsed = parse_transform_request(payload)
    op = parsed.transformations[0]
    assert isinstance(op, ExtractToSheetOp)
    assert op.rows.ranges is not None
    assert [(item.start, item.end) for item in op.rows.ranges] == [(2, 3), (4, 6)]


def test_reject_missing_source_and_two_inputs(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    del payload["source"]
    _raises(payload, code="invalid_source")
    left = _source_book(tmp_path, "a.xlsx")
    right = _source_book(tmp_path, "b.xlsx")
    payload = _base_transform(tmp_path, source=left)
    payload["source"] = [
        {"path": str(left)},
        {"path": str(right)},
    ]
    _raises(payload, code="invalid_source")


def test_reject_empty_transformations(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path, transformations=[])
    _raises(payload, code="empty_transformations")


def test_reject_zero_and_negative_coordinates(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["columns"] = [0]
    _raises(payload, code="invalid_range")
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["row_ranges"] = [{"start_row": -1, "end_row": 2}]
    _raises(payload, code="invalid_range")


def test_reject_reversed_and_overlapping_ranges(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["row_ranges"] = [{"start_row": 5, "end_row": 2}]
    _raises(payload, code="reversed_range")
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["row_ranges"] = [
        {"start_row": 1, "end_row": 4},
        {"start_row": 4, "end_row": 6},
    ]
    _raises(payload, code="overlapping_range")
    payload = _base_transform(tmp_path)
    payload["transformations"][0].pop("row_ranges")
    payload["transformations"][0]["rows"] = [3, 3]
    _raises(payload, code="duplicate_range")


def test_reject_both_row_selectors(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["rows"] = [3]
    _raises(payload, code="invalid_range")


def test_reject_fill_policy_missing_and_invalid(tmp_path: Path) -> None:
    payload = _base_transform(
        tmp_path,
        transformations=[
            {
                "op": UNMERGE_CELLS_OP,
                "sheet": "Sheet1",
                "scope": "all_merged_ranges",
            }
        ],
    )
    _raises(payload, code="missing_fill_policy")
    payload["transformations"][0]["fill_policy"] = "spread"
    _raises(payload, code="invalid_fill_policy")


def test_reject_overwrite_source_inside_output(tmp_path: Path) -> None:
    nested = tmp_path / "out"
    nested.mkdir()
    source = write_table(nested / "book.xlsx", {"code": ["A"], "amount": [1]})
    payload = _base_transform(tmp_path, source=source)
    payload["output_directory"] = str(nested.resolve())
    _raises(payload, code="overwrite_forbidden")
    payload["output_directory"] = str(source.resolve())
    _raises(payload, code="invalid_output_directory")
    payload["output_directory"] = str(tmp_path.resolve())
    _raises(payload, code="overwrite_forbidden")


def test_reject_invalid_and_too_long_sheet_names(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["destination_sheet_base_name"] = "bad:name"
    _raises(payload, code="invalid_sheet_name")
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["destination_sheet_base_name"] = "x" * 32
    _raises(payload, code="invalid_sheet_name")


def test_reject_operation_output_path(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["output_path"] = str(tmp_path / "stolen.xlsx")
    _raises(payload, code="invalid_output_path")


def test_reject_unknown_fields(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["user_prompt"] = "rewrite me"
    _raises(payload, code="unknown_field")


def test_reject_analyze_payload(tmp_path: Path) -> None:
    path = _source_book(tmp_path)
    analyze_payload = base_request(tmp_path, paths=[path], prompt="요약해줘")
    _raises(analyze_payload, code="unknown_field")


def test_analyze_parser_still_rejects_transform(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    with pytest.raises(ContractError) as exc:
        parse_analyze_request(payload)
    assert exc.value.code in {
        "invalid_operation",
        "missing_user_prompt",
        "unknown_field",
        "invalid_analysis_mode",
    }


def test_analyze_parser_behavior_unchanged(tmp_path: Path) -> None:
    path = _source_book(tmp_path)
    parsed = parse_analyze_request(
        base_request(tmp_path, paths=[path], prompt="이 파일의 주요 내용을 요약해줘")
    )
    assert parsed.operation == "analyze"
    assert parsed.user_prompt == "이 파일의 주요 내용을 요약해줘"
    assert parsed.contract_version == CONTRACT_VERSION


def test_zero_based_sheet_index_rejected(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["transformations"][0]["sheet"] = 0
    _raises(payload, code="invalid_sheet")


def test_source_must_not_include_sheet(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["source"]["sheet"] = "Sheet1"
    _raises(payload, code="unknown_field")


def test_operation_sheet_is_required(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    del payload["transformations"][0]["sheet"]
    _raises(payload, code="invalid_sheet")


def test_distinct_operation_sheets_preserved(tmp_path: Path) -> None:
    payload = _base_transform(
        tmp_path,
        transformations=[
            {
                "op": UNMERGE_CELLS_OP,
                "sheet": "Summary",
                "scope": "all_merged_ranges",
                "fill_policy": FILL_POLICY_FILL_ALL,
            },
            {
                "op": EXTRACT_TO_SHEET_OP,
                "sheet": 2,
                "rows": [4, 9],
                "columns": [1, 3],
                "destination_sheet_base_name": "Extracted",
            },
        ],
    )
    parsed = parse_transform_request(payload)
    assert parsed.transformations[0].sheet == "Summary"
    assert parsed.transformations[1].sheet == 2
    dumped = parsed.to_dict()
    assert dumped["source"] == {"path": parsed.source.path}
    assert dumped["transformations"][0]["sheet"] == "Summary"
    assert dumped["transformations"][1]["sheet"] == 2


def test_xlsx_uppercase_extension_accepted(tmp_path: Path) -> None:
    source = _source_book(tmp_path, "BOOK.XLSX")
    payload = _base_transform(tmp_path, source=source)
    parsed = parse_transform_request(payload)
    assert Path(parsed.source.path).suffix.lower() == ".xlsx"


def test_xls_xlsm_csv_rejected(tmp_path: Path) -> None:
    for name in ("book.xls", "book.xlsm", "table.csv"):
        path = tmp_path / name
        if name.endswith(".csv"):
            write_table(path)
        else:
            path.write_bytes(b"placeholder")
        payload = _base_transform(tmp_path, source=path)
        _raises(payload, code="unsupported_extension")


def test_analyze_parser_keeps_legacy_extensions(tmp_path: Path) -> None:
    for name in ("legacy.xls", "macro.xlsm", "table.csv"):
        path = tmp_path / name
        if name.endswith(".csv"):
            write_table(path)
        else:
            path.write_bytes(b"placeholder")
        parsed = parse_analyze_request(
            base_request(tmp_path, paths=[path], prompt="이 파일의 주요 내용을 요약해줘")
        )
        assert parsed.operation == "analyze"
        assert parsed.inputs[0].path.endswith(name) or Path(parsed.inputs[0].path).suffix.lower() in {
            ".xls",
            ".xlsm",
            ".csv",
        }


def test_csv_source_rejected(tmp_path: Path) -> None:
    csv_path = write_table(tmp_path / "table.csv")
    payload = _base_transform(tmp_path, source=csv_path)
    _raises(payload, code="unsupported_extension")


def test_missing_source_file(tmp_path: Path) -> None:
    payload = _base_transform(tmp_path)
    payload["source"]["path"] = str((tmp_path / "gone.xlsx").resolve())
    _raises(payload, code="missing_file")


def test_transform_module_has_no_demo_hardcoding() -> None:
    text = TRANSFORM_MODULE.read_text(encoding="utf-8")
    for needle in ("당해예산", "내부인건비", "재료비", "예실대비표", "04_분산"):
        assert needle not in text


def test_transform_module_import_isolation() -> None:
    text = TRANSFORM_MODULE.read_text(encoding="utf-8")
    assert "import streamlit" not in text
    assert "from streamlit" not in text
    assert "core.routing" not in text
    assert "core.integrate" not in text
    assert "run_integration_pipeline" not in text
    assert "openpyxl" not in text
    import core.application.transform_contracts as module
    from dataclasses import fields

    assert not hasattr(module, "transform_excel")
    assert not hasattr(module, "analyze_excel")
    assert module.TRANSFORM_ALLOWED_EXTENSIONS == frozenset({".xlsx"})
    assert [item.name for item in fields(module.TransformSource)] == ["path"]


def test_existing_request_instance_revalidated(tmp_path: Path) -> None:
    parsed = parse_transform_request(_base_transform(tmp_path))
    assert isinstance(parsed, TransformRequest)
    again = parse_transform_request(parsed)
    assert again == parsed
