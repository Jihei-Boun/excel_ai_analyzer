"""Executor and facade tests for workbook transformation (Phase I2-D2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

from core.application import analyze_excel, transform_excel
from core.application.artifacts import sha256_file
from core.application.transform_contracts import (
    EXTRACT_TO_SHEET_OP,
    FILL_POLICY_FILL_ALL,
    FILL_POLICY_KEEP_TOP_LEFT,
    FILL_POLICY_LEAVE_EMPTY,
    TRANSFORM_CONTRACT_VERSION,
    TRANSFORM_OPERATION,
    UNMERGE_CELLS_OP,
)
from tests.application_support import base_request, write_table

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "core" / "application"


def _save_book(path: Path, build) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook: Workbook = build()
    workbook.save(path)
    workbook.close()
    return path


def _basic_book(path: Path) -> Path:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "H1"
        ws["B1"] = "H2"
        ws["C1"] = "H3"
        ws["A2"] = "r2c1"
        ws["B2"] = "r2c2"
        ws["C2"] = "한글"
        ws["A3"] = "r3c1"
        ws["B3"] = "r3c2"
        ws["C3"] = "r3c3"
        ws["A4"] = "r4c1"
        ws["B4"] = 12.5
        ws["B4"].number_format = "0.00"
        ws["B4"].font = Font(bold=True, color="0000FF")
        ws["C4"] = "=A4"
        ws["C4"].font = Font(italic=True)
        return wb

    return _save_book(path, build)


def _merged_book(path: Path) -> Path:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "H1"
        ws["B1"] = "H2"
        ws["C1"] = "H3"
        ws.merge_cells("A2:C2")
        ws["A2"] = "GROUP"
        ws["A2"].font = Font(bold=True)
        ws["A2"].fill = PatternFill(fill_type="solid", fgColor="FFC000")
        ws["A3"] = 1
        ws["B3"] = "=A3+1"
        ws["C3"] = 3
        extra = wb.create_sheet("Extra")
        extra["A1"] = "other"
        extra.merge_cells("A1:B1")
        return wb

    return _save_book(path, build)


def _payload(
    tmp_path: Path,
    source: Path,
    transformations: list[dict[str, Any]],
    *,
    request_id: str = "req-d2-1",
) -> dict[str, Any]:
    return {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": request_id,
        "source": {"path": str(source.resolve())},
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "output_policy": {"source_immutable": True, "create_copy": True},
        "transformations": transformations,
    }


def _extract_op(**overrides: Any) -> dict[str, Any]:
    payload = {
        "op": EXTRACT_TO_SHEET_OP,
        "sheet": "Sheet1",
        "rows": [2, 4],
        "columns": [1, 3],
        "header_rows": [1],
        "include_header_rows": True,
        "destination_sheet_base_name": "Extracted",
        "preserve_selection_order": True,
    }
    payload.update(overrides)
    return payload


def _unmerge_op(**overrides: Any) -> dict[str, Any]:
    payload = {
        "op": UNMERGE_CELLS_OP,
        "sheet": "Sheet1",
        "scope": "explicit_ranges",
        "merge_ranges": [
            {"start_row": 2, "start_column": 1, "end_row": 2, "end_column": 3}
        ],
        "fill_policy": FILL_POLICY_FILL_ALL,
    }
    payload.update(overrides)
    return payload


def _open_output(response: dict[str, Any]):
    path = Path(response["artifacts"][0]["path"])
    return load_workbook(path, data_only=False)


def test_extract_copies_selected_cells_only(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    before = sha256_file(source)
    response = transform_excel(_payload(tmp_path, source, [_extract_op(include_header_rows=False, header_rows=[])]))
    assert response["status"] == "success"
    assert sha256_file(source) == before
    wb = _open_output(response)
    extracted = wb["Extracted"]
    assert extracted["A1"].value == "r2c1"
    assert extracted["B1"].value == "한글"
    assert extracted["A2"].value == "r4c1"
    assert extracted["B2"].value == "=A4"
    assert extracted["A3"].value is None
    original = wb["Sheet1"]
    assert original["A2"].value == "r2c1"
    assert original["C3"].value == "r3c3"
    wb.close()


def test_extract_header_dedupe_and_order(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [_extract_op(rows=[4, 2, 1], columns=[1, 2], include_header_rows=True, header_rows=[1])],
        )
    )
    wb = _open_output(response)
    extracted = wb["Extracted"]
    assert [extracted.cell(1, 1).value, extracted.cell(2, 1).value, extracted.cell(3, 1).value] == [
        "H1",
        "r4c1",
        "r2c1",
    ]
    wb.close()


def test_extract_copies_unicode_formula_and_style(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [_extract_op(rows=[4], columns=[2, 3], include_header_rows=False, header_rows=[])],
        )
    )
    wb = _open_output(response)
    extracted = wb["Extracted"]
    assert extracted["A1"].value == 12.5
    assert extracted["A1"].number_format == "0.00"
    assert extracted["A1"].font.bold is True
    assert extracted["B1"].value == "=A4"
    assert extracted["B1"].font.italic is True
    wb.close()


def test_destination_collision_and_truncation(tmp_path: Path) -> None:
    def build() -> Workbook:
        wb = Workbook()
        wb.active.title = "Sheet1"
        wb.active["A1"] = "a"
        wb.active["A2"] = "b"
        existing = "X" * 31
        extra = wb.create_sheet(existing)
        extra["A1"] = "keep"
        wb.create_sheet("Extracted")
        return wb

    source = _save_book(tmp_path / "book.xlsx", build)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [
                _extract_op(
                    rows=[1, 2],
                    columns=[1],
                    include_header_rows=False,
                    header_rows=[],
                    destination_sheet_base_name="Extracted",
                ),
                _extract_op(
                    rows=[1],
                    columns=[1],
                    include_header_rows=False,
                    header_rows=[],
                    destination_sheet_base_name="X" * 31,
                ),
            ],
        )
    )
    assert response["status"] == "success"
    names = [item["metadata"]["destination_sheet"] for item in response["applied_operations"]]
    assert names[0] == "Extracted_2"
    assert names[1].startswith("X")
    assert names[1].endswith("_2")
    assert len(names[1]) <= 31
    wb = _open_output(response)
    assert "Extracted" in wb.sheetnames
    assert "Extracted_2" in wb.sheetnames
    wb.close()


def test_extract_out_of_bounds(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    before = sha256_file(source)
    response = transform_excel(_payload(tmp_path, source, [_extract_op(rows=[99], columns=[1])]))
    assert response["status"] == "validation_failed"
    assert response["error"]["code"] == "coordinate_out_of_bounds"
    assert response["artifacts"] == []
    assert sha256_file(source) == before
    assert list((tmp_path / "out").rglob("*.xlsx")) == []


def test_merged_interior_is_none_without_unmerge(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [_extract_op(rows=[2], columns=[1, 2, 3], include_header_rows=False, header_rows=[])],
        )
    )
    wb = _open_output(response)
    extracted = wb["Extracted"]
    assert extracted["A1"].value == "GROUP"
    assert extracted["B1"].value is None
    assert extracted["C1"].value is None
    wb.close()


def test_unmerge_fill_all_then_extract(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    before = sha256_file(source)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [
                _unmerge_op(),
                _extract_op(rows=[2], columns=[1, 2, 3], include_header_rows=False, header_rows=[]),
            ],
        )
    )
    assert response["status"] == "success"
    assert [item["op"] for item in response["applied_operations"]] == [
        UNMERGE_CELLS_OP,
        EXTRACT_TO_SHEET_OP,
    ]
    wb = _open_output(response)
    sheet = wb["Sheet1"]
    assert sheet["A2"].value == "GROUP"
    assert sheet["B2"].value == "GROUP"
    assert sheet["C2"].value == "GROUP"
    assert sheet["B2"].font.bold is True
    extracted = wb["Extracted"]
    assert [extracted["A1"].value, extracted["B1"].value, extracted["C1"].value] == [
        "GROUP",
        "GROUP",
        "GROUP",
    ]
    wb.close()
    assert sha256_file(source) == before
    source_wb = load_workbook(source)
    assert "A2:C2" in source_wb["Sheet1"].merged_cells
    source_wb.close()


def test_extract_before_unmerge_sees_empty_interior(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [
                _extract_op(rows=[2], columns=[1, 2], include_header_rows=False, header_rows=[]),
                _unmerge_op(),
            ],
        )
    )
    wb = _open_output(response)
    assert wb["Extracted"]["B1"].value is None
    assert wb["Sheet1"]["B2"].value == "GROUP"
    wb.close()


def test_unmerge_keep_top_left_and_leave_empty(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "keep.xlsx")
    keep = transform_excel(
        _payload(
            tmp_path / "keep",
            source,
            [_unmerge_op(fill_policy=FILL_POLICY_KEEP_TOP_LEFT)],
            request_id="req-keep",
        )
    )
    wb = _open_output(keep)
    assert wb["Sheet1"]["A2"].value == "GROUP"
    assert wb["Sheet1"]["B2"].value is None
    assert wb["Sheet1"]["C2"].value is None
    wb.close()
    source2 = _merged_book(tmp_path / "leave.xlsx")
    leave = transform_excel(
        _payload(
            tmp_path / "leave",
            source2,
            [_unmerge_op(fill_policy=FILL_POLICY_LEAVE_EMPTY)],
            request_id="req-leave",
        )
    )
    wb = _open_output(leave)
    assert wb["Sheet1"]["A2"].value == "GROUP"
    assert wb["Sheet1"]["B2"].value is None
    assert wb["Sheet1"]["C2"].value is None
    wb.close()


def test_unmerge_all_ranges_and_zero_merge_noop(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    payload = _payload(
        tmp_path,
        source,
        [
            {
                "op": UNMERGE_CELLS_OP,
                "sheet": "Sheet1",
                "scope": "all_merged_ranges",
                "fill_policy": FILL_POLICY_FILL_ALL,
            }
        ],
        request_id="req-all",
    )
    response = transform_excel(payload)
    assert response["status"] == "success"
    assert response["applied_operations"][0]["metadata"]["merged_range_count"] == 1
    empty = _basic_book(tmp_path / "empty.xlsx")
    noop = transform_excel(
        _payload(
            tmp_path,
            empty,
            [
                {
                    "op": UNMERGE_CELLS_OP,
                    "sheet": "Sheet1",
                    "scope": "all_merged_ranges",
                    "fill_policy": FILL_POLICY_FILL_ALL,
                }
            ],
            request_id="req-zero",
        )
    )
    assert noop["status"] == "success"
    assert noop["applied_operations"][0]["metadata"]["merged_range_count"] == 0
    assert any(item["code"] == "no_merged_ranges" for item in noop["warnings"])


def test_unmerge_missing_range_fails_before_mutation(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    before = sha256_file(source)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [
                _unmerge_op(
                    merge_ranges=[
                        {"start_row": 1, "start_column": 1, "end_row": 1, "end_column": 2}
                    ]
                )
            ],
        )
    )
    assert response["status"] == "validation_failed"
    assert response["error"]["code"] == "merged_range_not_found"
    assert response["artifacts"] == []
    assert sha256_file(source) == before
    assert list((tmp_path / "out").rglob("*.xlsx")) == []


def test_unmerge_formula_copied_under_fill_all(tmp_path: Path) -> None:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = 10
        ws.merge_cells("B1:D1")
        ws["B1"] = "=A1*2"
        return wb

    source = _save_book(tmp_path / "formula.xlsx", build)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [
                _unmerge_op(
                    merge_ranges=[
                        {"start_row": 1, "start_column": 2, "end_row": 1, "end_column": 4}
                    ]
                )
            ],
        )
    )
    wb = _open_output(response)
    assert wb["Sheet1"]["B1"].value == "=A1*2"
    assert wb["Sheet1"]["C1"].value == "=A1*2"
    assert wb["Sheet1"]["D1"].value == "=A1*2"
    wb.close()


def test_sheet_name_and_index_resolution(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    payload = _payload(
        tmp_path / "idx",
        source,
        [
            {
                "op": UNMERGE_CELLS_OP,
                "sheet": 2,
                "scope": "all_merged_ranges",
                "fill_policy": FILL_POLICY_FILL_ALL,
            }
        ],
        request_id="req-idx",
    )
    by_index = transform_excel(payload)
    assert by_index["status"] == "success"
    wb = _open_output(by_index)
    assert wb["Extra"]["A1"].value == "other"
    assert wb["Extra"]["B1"].value == "other"
    assert "A2:C2" in wb["Sheet1"].merged_cells
    wb.close()
    missing = transform_excel(
        _payload(
            tmp_path / "missing",
            source,
            [_extract_op(sheet="Missing")],
            request_id="req-missing",
        )
    )
    assert missing["status"] == "validation_failed"
    assert missing["error"]["code"] == "sheet_not_found"


def test_output_lifecycle_and_json_safe(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    before = sha256_file(source)
    payload = _payload(tmp_path, source, [_extract_op()])
    response = transform_excel(payload)
    assert response["status"] == "success"
    json.dumps(response)
    artifact = response["artifacts"][0]
    out = Path(artifact["path"])
    expected = (tmp_path / "out" / "req-d2-1_transformed.xlsx").resolve()
    assert out.resolve() == expected
    assert not (tmp_path / "out" / "req-d2-1").exists()
    assert out.parent.resolve() == (tmp_path / "out").resolve()
    assert out.is_file()
    assert out.suffix.lower() == ".xlsx"
    assert artifact["kind"] == "workbook"
    assert artifact["sha256"] == sha256_file(out)
    assert artifact["size_bytes"] == out.stat().st_size
    assert out.resolve().is_relative_to((tmp_path / "out").resolve())
    assert not list((tmp_path / "out").rglob("*.tmp"))
    assert sha256_file(source) == before
    assert response["safety"]["source_immutable_verified"] is True
    assert response["safety"]["output_contained"] is True
    again = transform_excel(payload)
    assert again["status"] == "execution_failed"
    assert again["error"]["code"] == "output_exists"
    assert again["artifacts"] == []


def test_corrupt_workbook_and_invalid_request(tmp_path: Path) -> None:
    corrupt = tmp_path / "bad.xlsx"
    corrupt.write_bytes(b"not-an-xlsx")
    before = sha256_file(corrupt)
    response = transform_excel(_payload(tmp_path, corrupt, [_extract_op()]))
    assert response["status"] == "execution_failed"
    assert response["error"]["code"] == "invalid_file"
    assert response["artifacts"] == []
    assert "traceback" not in json.dumps(response).lower()
    assert sha256_file(corrupt) == before
    invalid = transform_excel({"operation": "transform"})
    assert invalid["status"] == "invalid_request"
    assert invalid["artifacts"] == []


def test_second_operation_failure_does_not_publish(tmp_path: Path) -> None:
    source = _merged_book(tmp_path / "book.xlsx")
    before = sha256_file(source)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [_unmerge_op(), _extract_op(rows=[99], columns=[1])],
        )
    )
    assert response["status"] == "validation_failed"
    assert response["artifacts"] == []
    assert list((tmp_path / "out").rglob("*.xlsx")) == []
    assert sha256_file(source) == before


def test_hyperlink_and_comment_not_copied(tmp_path: Path) -> None:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "link"
        ws["A1"].hyperlink = "https://example.invalid/resource"
        ws["B1"] = "note"
        ws["B1"].comment = Comment("secret", "tester")
        return wb

    source = _save_book(tmp_path / "links.xlsx", build)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [_extract_op(rows=[1], columns=[1, 2], include_header_rows=False, header_rows=[])],
        )
    )
    assert response["status"] == "success"
    codes = {item["code"] for item in response["warnings"]}
    assert "hyperlink_not_copied" in codes
    assert "comment_not_copied" in codes
    wb = _open_output(response)
    assert wb["Extracted"]["A1"].hyperlink is None
    assert wb["Extracted"]["B1"].comment is None
    wb.close()


def test_hidden_sheet_can_be_targeted(tmp_path: Path) -> None:
    def build() -> Workbook:
        wb = Workbook()
        ws = wb.active
        ws.title = "HiddenData"
        ws["A1"] = "secret"
        ws["A2"] = "row"
        ws.sheet_state = "hidden"
        wb.create_sheet("Visible")
        return wb

    source = _save_book(tmp_path / "hidden.xlsx", build)
    response = transform_excel(
        _payload(
            tmp_path,
            source,
            [
                _extract_op(
                    sheet="HiddenData",
                    rows=[1, 2],
                    columns=[1],
                    include_header_rows=False,
                    header_rows=[],
                )
            ],
        )
    )
    assert response["status"] == "success"
    assert response["applied_operations"][0]["metadata"]["source_sheet_hidden"] is True
    wb = _open_output(response)
    assert wb["Extracted"]["A1"].value == "secret"
    wb.close()


def test_streamlit_not_imported_and_analyze_still_works(tmp_path: Path) -> None:
    retained = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "streamlit" or name.startswith("streamlit.")
    }
    for name in retained:
        sys.modules.pop(name, None)
    source = _basic_book(tmp_path / "book.xlsx")
    response = transform_excel(_payload(tmp_path, source, [_extract_op()]))
    assert response["status"] == "success"
    assert "streamlit" not in sys.modules
    analyzed = analyze_excel(
        base_request(tmp_path, paths=[write_table(tmp_path / "input.xlsx")], prompt="이 파일의 주요 내용을 요약해줘")
    )
    assert analyzed["status"] == "success"
    assert analyzed["data"]["route"] == "route_single_prompt"


def test_production_modules_have_no_demo_hardcoding() -> None:
    needles = ("당해예산", "내부인건비", "재료비", "예실대비표", "04_분산")
    for path in (
        APP_DIR / "transform_executor.py",
        APP_DIR / "transform_headless.py",
        APP_DIR / "transform_contracts.py",
    ):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text
        assert "import streamlit" not in text
        assert "from streamlit" not in text
        assert "core.routing" not in text
        assert "core.integrate" not in text
        assert "run_integration_pipeline" not in text


def test_output_is_directly_under_request_scoped_directory(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    output_dir = tmp_path / "request-123"
    payload = _payload(tmp_path, source, [_extract_op()], request_id="request-123")
    payload["output_directory"] = str(output_dir.resolve())
    before = sha256_file(source)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-sentinel")
    response = transform_excel(payload)
    assert response["status"] == "success"
    expected = (output_dir / "request-123_transformed.xlsx").resolve()
    assert Path(response["artifacts"][0]["path"]).resolve() == expected
    assert expected.is_file()
    assert not (output_dir / "request-123").exists()
    assert [path.resolve() for path in output_dir.iterdir()] == [expected]
    nested = [path for path in output_dir.rglob("*") if path.is_dir()]
    assert nested == []
    assert sha256_file(source) == before
    assert outside.read_bytes() == b"outside-sentinel"


def test_preexisting_final_is_preserved_on_output_exists(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    before_source = sha256_file(source)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    final = output_dir / "req-d2-1_transformed.xlsx"
    sentinel = b"PREEXISTING-SENTINEL-BYTES"
    final.write_bytes(sentinel)
    before_final = sha256_file(final)
    keep = output_dir / "keep-me.xlsx"
    keep.write_bytes(b"keep-bytes")
    leftover_tmp = output_dir / ".leftover.tmp"
    leftover_tmp.write_bytes(b"old-tmp")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-sentinel")
    response = transform_excel(_payload(tmp_path, source, [_extract_op()]))
    assert response["status"] == "execution_failed"
    assert response["error"]["code"] == "output_exists"
    assert response["artifacts"] == []
    assert final.exists()
    assert final.read_bytes() == sentinel
    assert sha256_file(final) == before_final
    assert keep.read_bytes() == b"keep-bytes"
    assert leftover_tmp.read_bytes() == b"old-tmp"
    assert outside.read_bytes() == b"outside-sentinel"
    assert sha256_file(source) == before_source
    assert not (output_dir / "req-d2-1").exists()


def test_pre_save_failure_preserves_existing_files(tmp_path: Path) -> None:
    source = _basic_book(tmp_path / "book.xlsx")
    before_source = sha256_file(source)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    keep = output_dir / "keep-me.xlsx"
    keep.write_bytes(b"keep-bytes")
    leftover_tmp = output_dir / ".leftover.tmp"
    leftover_tmp.write_bytes(b"old-tmp")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-sentinel")
    response = transform_excel(_payload(tmp_path, source, [_extract_op(rows=[99], columns=[1])]))
    assert response["status"] == "validation_failed"
    assert response["artifacts"] == []
    assert keep.read_bytes() == b"keep-bytes"
    assert leftover_tmp.read_bytes() == b"old-tmp"
    assert outside.read_bytes() == b"outside-sentinel"
    assert sha256_file(source) == before_source
    assert list(output_dir.glob("*_transformed.xlsx")) == []
    assert list(output_dir.glob("*.tmp")) == [leftover_tmp]
    assert not (output_dir / "req-d2-1").exists()


def test_reopen_failure_removes_only_owned_final(tmp_path: Path, monkeypatch: Any) -> None:
    from core.application.contracts import ContractError
    import core.application.transform_executor as executor

    source = _basic_book(tmp_path / "book.xlsx")
    before_source = sha256_file(source)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    keep = output_dir / "keep-me.xlsx"
    keep.write_bytes(b"keep-bytes")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-sentinel")

    def _boom(_path: Path) -> None:
        raise ContractError(
            "invalid_output",
            "Output workbook could not be reopened.",
            stage="execution",
            retryable=False,
        )

    monkeypatch.setattr(executor, "_reopen_smoke", _boom)
    response = transform_excel(_payload(tmp_path, source, [_extract_op()]))
    assert response["status"] == "execution_failed"
    assert response["error"]["code"] == "invalid_output"
    assert response["artifacts"] == []
    owned = output_dir / "req-d2-1_transformed.xlsx"
    assert not owned.exists()
    assert keep.read_bytes() == b"keep-bytes"
    assert outside.read_bytes() == b"outside-sentinel"
    assert sha256_file(source) == before_source
    assert list(output_dir.glob("*.tmp")) == []
