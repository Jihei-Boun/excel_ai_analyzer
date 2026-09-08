"""Natural-language transform facade and prompt-contract tests (Phase I2-D3B)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import requests
from openpyxl import Workbook, load_workbook

from core.application import (
    analyze_excel,
    parse_analyze_request,
    parse_transform_prompt_request,
    parse_transform_request,
    transform_excel,
    transform_excel_from_prompt,
)
from core.application.artifacts import sha256_file
from core.application.contracts import ContractError, WarningItem
from core.application.transform_contracts import TRANSFORM_CONTRACT_VERSION, TRANSFORM_OPERATION
from core.application.transform_observer import observe_workbook as real_observe
from core.application.transform_planner import PlannerResult
from core.application.transform_prompt_contracts import MAX_PROMPT_CHARS
from tests.application_support import base_request, write_table

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "core" / "application"
PROMPT = "Copy rows 1-3 and columns A and C onto a new sheet named Extracted."


def _save(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "H1"
    ws["B1"] = "H2"
    ws["C1"] = "H3"
    ws["A2"] = "r2"
    ws["C2"] = "c2"
    ws["A3"] = "r3"
    ws["C3"] = "c3"
    wb.save(path)
    wb.close()
    return path


def _policies(**overrides: Any) -> dict[str, str]:
    payload = {
        "group_scope": "include_descendants_and_subtotals",
        "output_mode": "copy_with_new_sheet",
        "unmerge_fill_policy": "fill_all",
    }
    payload.update(overrides)
    return payload


def _prompt_payload(tmp_path: Path, source: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": "req-d3b-1",
        "source": {"path": str(source.resolve())},
        "user_prompt": PROMPT,
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "model": "qwen2.5:7b",
        "policies": _policies(),
    }
    payload.update(overrides)
    return payload


def _extract_plan() -> dict[str, Any]:
    return {
        "op": "extract_to_sheet",
        "sheet": 1,
        "rows": [1, 2, 3],
        "columns": [1, 3],
        "header_rows": [1],
        "include_header_rows": True,
        "destination_sheet_base_name": "Extracted",
        "preserve_selection_order": True,
    }


def test_valid_prompt_request_parse(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    parsed = parse_transform_prompt_request(_prompt_payload(tmp_path, source))
    assert parsed.user_prompt == PROMPT
    assert parsed.policies.group_scope == "include_descendants_and_subtotals"
    assert parsed.model.name == "qwen2.5:7b"


def test_xlsx_only_and_single_source(tmp_path: Path) -> None:
    source = tmp_path / "book.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ContractError) as caught:
        parse_transform_prompt_request(_prompt_payload(tmp_path, source))
    assert caught.value.code == "unsupported_extension"

    xlsx = _save(tmp_path / "ok.xlsx")
    payload = _prompt_payload(tmp_path, xlsx)
    payload["source"] = {"path": str(xlsx.resolve()), "extra": 1}
    with pytest.raises(ContractError):
        parse_transform_prompt_request(payload)


def test_blank_and_oversized_prompt_rejected(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    with pytest.raises(ContractError) as blank:
        parse_transform_prompt_request(_prompt_payload(tmp_path, source, user_prompt="   "))
    assert blank.value.code == "missing_user_prompt"
    with pytest.raises(ContractError) as huge:
        parse_transform_prompt_request(
            _prompt_payload(tmp_path, source, user_prompt="x" * (MAX_PROMPT_CHARS + 1))
        )
    assert huge.value.code == "invalid_user_prompt"


def test_policies_missing_and_unknown_rejected(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    payload = _prompt_payload(tmp_path, source)
    payload.pop("policies")
    with pytest.raises(ContractError) as missing:
        parse_transform_prompt_request(payload)
    assert missing.value.code == "invalid_policies"
    with pytest.raises(ContractError) as unknown:
        parse_transform_prompt_request(
            _prompt_payload(tmp_path, source, policies=_policies(group_scope="prompt_guess"))
        )
    assert unknown.value.code == "invalid_policies"


def test_unknown_fields_and_overwrite_rejected(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    with pytest.raises(ContractError) as unknown:
        parse_transform_prompt_request(_prompt_payload(tmp_path, source, extra_field=True))
    assert unknown.value.code == "unknown_field" or "unknown" in unknown.value.message.lower()
    nested = tmp_path / "out"
    nested.mkdir()
    inside = nested / "book.xlsx"
    _save(inside)
    with pytest.raises(ContractError) as overwrite:
        parse_transform_prompt_request(
            _prompt_payload(tmp_path, inside, output_directory=str(nested.resolve()))
        )
    assert overwrite.value.code == "overwrite_forbidden"


def test_prompt_verbatim_preserved(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    raw = "  Keep spacing and 한글 tokens.  "
    parsed = parse_transform_prompt_request(_prompt_payload(tmp_path, source, user_prompt=raw))
    assert parsed.user_prompt == raw


def test_analyze_and_explicit_transform_parsers_unchanged(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    prompt_payload = _prompt_payload(tmp_path, source)
    with pytest.raises(ContractError):
        parse_analyze_request(prompt_payload)
    explicit = {
        "contract_version": TRANSFORM_CONTRACT_VERSION,
        "operation": TRANSFORM_OPERATION,
        "request_id": "req-explicit",
        "source": {"path": str(source.resolve())},
        "output_directory": str((tmp_path / "out").resolve()),
        "timeout_seconds": 90,
        "output_policy": {"source_immutable": True, "create_copy": True},
        "transformations": [_extract_plan()],
    }
    parsed = parse_transform_request(explicit)
    assert parsed.transformations[0].op == "extract_to_sheet"
    analyze = parse_analyze_request(
        base_request(tmp_path, paths=[write_table(tmp_path / "input.xlsx")], prompt="요약해줘")
    )
    assert analyze.operation == "analyze"


def test_mocked_plan_executes_existing_transform_excel(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    before = sha256_file(source)
    response = transform_excel_from_prompt(
        _prompt_payload(tmp_path, source),
        chat_json_fn=lambda *_a, **_k: {
            "status": "planned",
            "transformations": [_extract_plan()],
            "warnings": [],
        },
    )
    assert response["status"] == "success"
    assert response["artifacts"]
    wb = load_workbook(response["artifacts"][0]["path"])
    assert wb["Extracted"]["A2"].value == "r2"
    wb.close()
    assert sha256_file(source) == before


def test_trusted_fields_not_replaced_by_llm(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    response = transform_excel_from_prompt(
        _prompt_payload(tmp_path, source),
        chat_json_fn=lambda *_a, **_k: {
            "status": "planned",
            "transformations": [_extract_plan()],
            "warnings": [],
            "request_id": "llm-hijack",
            "output_directory": str(tmp_path / "evil-out"),
            "source": {"path": "/tmp/not-the-source.xlsx"},
        },
    )
    assert response["status"] == "cannot_plan"
    assert response["artifacts"] == []
    assert list((tmp_path / "out").glob("*.xlsx")) == []


def test_cannot_plan_skips_executor(tmp_path: Path, monkeypatch: Any) -> None:
    source = _save(tmp_path / "book.xlsx")
    import core.application.transform_prompt_headless as facade

    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("transform_excel must not run")

    monkeypatch.setattr(facade, "transform_excel", boom)
    response = transform_excel_from_prompt(
        _prompt_payload(tmp_path, source),
        chat_json_fn=lambda *_a, **_k: {
            "status": "cannot_plan",
            "reason_code": "ambiguous_request",
            "message": "Safely fixing coordinates is not possible.",
            "warnings": [{"code": "planner_note", "message": "truncated observation"}],
        },
    )
    assert response["status"] == "cannot_plan"
    assert response["artifacts"] == []
    assert response["error"]["code"] == "ambiguous_request"


def test_observer_and_planner_warnings_propagate(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")

    def observe_with_warning(path: str) -> Any:
        observation = real_observe(path)
        return observation.__class__(
            sheets=observation.sheets,
            truncated=True,
            warnings=observation.warnings
            + (WarningItem(code="observation_truncated_cells", message="capped", stage="observation"),),
            source_sha256=observation.source_sha256,
            sheet_count_total=observation.sheet_count_total,
            cell_count=observation.cell_count,
            merge_count=observation.merge_count,
        )

    response = transform_excel_from_prompt(
        _prompt_payload(tmp_path, source),
        observe_fn=observe_with_warning,
        plan_fn=lambda *_a, **_k: PlannerResult(
            status="planned",
            transformations=(_extract_plan(),),
            warnings=(WarningItem(code="planner_warning", message="note", stage="planning"),),
        ),
    )
    assert response["status"] == "success"
    codes = {item["code"] for item in response["warnings"]}
    assert "observation_truncated_cells" in codes
    assert "planner_warning" in codes


def test_model_unavailable_is_not_validation_failed(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")

    def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise requests.exceptions.ConnectionError("offline")

    response = transform_excel_from_prompt(_prompt_payload(tmp_path, source), chat_json_fn=fake)
    assert response["status"] == "model_unavailable"
    assert response["error"]["code"] == "model_unavailable"
    assert response["artifacts"] == []


def test_retry_exhausted_has_no_artifacts(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    response = transform_excel_from_prompt(
        _prompt_payload(tmp_path, source),
        chat_json_fn=lambda *_a, **_k: {"status": "nope"},
    )
    assert response["status"] == "cannot_plan"
    assert response["artifacts"] == []
    assert list((tmp_path / "out").glob("*.xlsx")) == []


def test_explicit_transform_excel_smoke_still_works(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    response = transform_excel(
        {
            "contract_version": TRANSFORM_CONTRACT_VERSION,
            "operation": TRANSFORM_OPERATION,
            "request_id": "req-explicit-smoke",
            "source": {"path": str(source.resolve())},
            "output_directory": str((tmp_path / "out").resolve()),
            "timeout_seconds": 90,
            "output_policy": {"source_immutable": True, "create_copy": True},
            "transformations": [_extract_plan()],
        }
    )
    assert response["status"] == "success"
    assert response["artifacts"]


def test_cannot_plan_status_is_transform_specific(tmp_path: Path) -> None:
    source = _save(tmp_path / "book.xlsx")
    response = transform_excel_from_prompt(
        _prompt_payload(tmp_path, source),
        chat_json_fn=lambda *_a, **_k: {
            "status": "cannot_plan",
            "reason_code": "ambiguous_request",
            "message": "cannot",
            "warnings": [],
        },
    )
    assert response["status"] == "cannot_plan"
    assert response["contract_version"] == TRANSFORM_CONTRACT_VERSION
    analyzed = analyze_excel(
        base_request(tmp_path, paths=[write_table(tmp_path / "input.xlsx")], prompt="이 파일의 주요 내용을 요약해줘")
    )
    assert analyzed["status"] == "success"


def test_streamlit_routing_integrate_and_demo_strings_absent() -> None:
    needles = ("당해예산", "내부인건비", "재료비", "예실대비표", "04_분산")
    files = [
        APP_DIR / "transform_observer.py",
        APP_DIR / "transform_planner.py",
        APP_DIR / "transform_prompt_contracts.py",
        APP_DIR / "transform_prompt_headless.py",
        APP_DIR / "transform_contracts.py",
        APP_DIR / "transform_executor.py",
        APP_DIR / "transform_headless.py",
        APP_DIR / "__init__.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text
        assert "import streamlit" not in text
        assert "from streamlit" not in text
        assert "core.routing" not in text
        assert "core.integrate" not in text
        assert "run_integration_pipeline" not in text
        assert "value_match" not in text
    retained = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "streamlit" or name.startswith("streamlit.")
    }
    for name in retained:
        sys.modules.pop(name, None)
    from core.application import transform_excel_from_prompt as imported

    assert imported is transform_excel_from_prompt
    assert "streamlit" not in sys.modules
