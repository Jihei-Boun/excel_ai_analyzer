"""Headless multi-file production-router tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from core.application import analyze_excel
from core.integrate.plan_types import ExecutionPlan, IntegrateResult, ValidationIssue, ValidationReport
from core.io.export_utils import dataframe_to_xlsx_bytes
from tests.application_support import base_request, write_table

COMPARE_PROMPT = "두 파일을 비교해서 공통점과 차이점을 알려줘"


def _two_files(tmp_path: Path) -> tuple[Path, Path]:
    left = write_table(tmp_path / "left.xlsx", {"항목": ["A", "B"], "값": [1, 2]})
    right = write_table(tmp_path / "right.xlsx", {"항목": ["A", "C"], "값": [3, 4]})
    return left, right


def test_multi_summary_early_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.routing.route_multi.run_multi_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("summary must not analyze")),
    )
    left, right = _two_files(tmp_path)
    response = analyze_excel(
        base_request(tmp_path, paths=[left, right], prompt="두 파일을 요약해줘")
    )
    assert response["status"] == "success"
    assert response["data"]["route"] == "route_multi_prompt"
    assert response["text"]


def test_comparison_prompt_uses_route_multi_not_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.integrate.integrate_pipeline import looks_like_structural_integrate

    assert looks_like_structural_integrate(COMPARE_PROMPT) is False
    calls = {"multi": 0, "candidate": 0, "legacy_integrate": 0}

    def fake_multi(named_frames, prompt, **kwargs):
        calls["multi"] += 1
        table = pd.DataFrame({"구분": ["공통", "차이"], "내용": ["항목 A", "값"]})
        return table, "비교 결과", {}

    def forbid_candidate(*_a, **_k):
        calls["candidate"] += 1
        raise AssertionError("Candidate pipeline must not be production-called")

    def forbid_legacy(*_a, **_k):
        calls["legacy_integrate"] += 1
        raise AssertionError("comparison prompt must not force structural integrate")

    monkeypatch.setattr("core.routing.route_multi.run_multi_analysis", fake_multi)
    monkeypatch.setattr(
        "core.integrate.integration_pipeline.run_integration_pipeline",
        forbid_candidate,
    )
    monkeypatch.setattr("core.routing.route_multi.try_integrate_pipeline", forbid_legacy)

    left, right = _two_files(tmp_path)
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (left, right)}
    frames_before = [pd.read_excel(path) for path in (left, right)]
    response = analyze_excel(
        base_request(tmp_path, paths=[left, right], prompt=COMPARE_PROMPT)
    )
    assert response["status"] == "success"
    assert response["data"]["route"] == "route_multi_prompt"
    assert response["text"] == "비교 결과"
    assert calls["multi"] == 1
    assert calls["candidate"] == 0
    assert calls["legacy_integrate"] == 0
    for path, digest in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    for path, before in zip((left, right), frames_before):
        assert pd.read_excel(path).equals(before)


def test_structural_integration_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    integrated = pd.DataFrame({"키": [1, 2], "값": [10, 20]})
    workbook = dataframe_to_xlsx_bytes(integrated)

    def fake_try(prompt, named_frames, **kwargs):
        return IntegrateResult(
            integrated=integrated.copy(),
            sheets={"통합": integrated.copy()},
            plan=ExecutionPlan(operation="aggregate_merge", sources=[n for n, _ in named_frames]),
            schemas={},
            validation=ValidationReport(ok=True, issues=[]),
            workbook_path=None,
            workbook_bytes=workbook,
            reply="구조화 실행 계획(aggregate_merge)으로 파일을 통합했습니다 (검증 통과).",
            meta={
                "workbook_bytes": workbook,
                "workbook_sheets": ["통합"],
            },
        )

    monkeypatch.setattr("core.routing.route_multi.try_integrate_pipeline", fake_try)
    left, right = _two_files(tmp_path)
    response = analyze_excel(
        base_request(tmp_path, paths=[left, right], prompt="선택한 파일들을 통합해줘")
    )
    assert response["status"] == "success"
    assert response["data"]["operation_name"] == "structured_integrate"
    assert response["safety"]["validation_status"] == "passed"
    kinds = {item["kind"] for item in response["artifacts"]}
    assert "workbook" in kinds
    workbook_art = next(item for item in response["artifacts"] if item["kind"] == "workbook")
    assert Path(workbook_art["path"]).is_file()
    assert workbook_art["sha256"] == hashlib.sha256(Path(workbook_art["path"]).read_bytes()).hexdigest()
    assert Path(workbook_art["path"]).resolve().is_relative_to((tmp_path / "out").resolve())


def test_structural_validation_failure_blocks_workbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integrated = pd.DataFrame({"키": [1], "값": [10]})
    leaked = b"this-must-not-become-an-artifact"

    def fake_try(prompt, named_frames, **kwargs):
        return IntegrateResult(
            integrated=integrated.copy(),
            sheets={"통합": integrated.copy()},
            plan=ExecutionPlan(operation="aggregate_merge", sources=[n for n, _ in named_frames]),
            schemas={},
            validation=ValidationReport(
                ok=False,
                issues=[
                    ValidationIssue(
                        level="error",
                        code="row_mismatch",
                        message="검증 행 수가 일치하지 않습니다.",
                    )
                ],
            ),
            workbook_path=None,
            workbook_bytes=leaked,
            reply="구조화 통합 계획을 실행했지만 검증에 실패했습니다.",
            meta={
                "workbook_bytes": leaked,
                "workbook_sheets": ["통합"],
                "integrate_validation": "검증 행 수가 일치하지 않습니다.",
            },
        )

    monkeypatch.setattr("core.routing.route_multi.try_integrate_pipeline", fake_try)
    left, right = _two_files(tmp_path)
    response = analyze_excel(
        base_request(tmp_path, paths=[left, right], prompt="파일을 통합해줘")
    )
    assert response["status"] == "validation_failed"
    assert response["error"]["code"] == "structural_validation_failed"
    assert response["error"]["stage"] == "structural_validation"
    assert response["safety"]["validation_status"] == "failed"
    assert response["artifacts"] == []
    out_root = (tmp_path / "out").resolve()
    leaked_files = list(out_root.rglob("*")) if out_root.exists() else []
    assert not any(path.suffix.lower() == ".xlsx" for path in leaked_files if path.is_file())


def test_duplicate_display_name_keeps_stable_source_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_multi(named_frames, prompt, **kwargs):
        captured["labels"] = [name for name, _ in named_frames]
        table = pd.DataFrame({"항목": ["ok"]})
        return table, "ok", {}

    monkeypatch.setattr("core.routing.route_multi.run_multi_analysis", fake_multi)
    left, right = _two_files(tmp_path)
    payload = base_request(
        tmp_path,
        paths=[left, right],
        prompt=COMPARE_PROMPT,
        display_names=["same.xlsx", "same.xlsx"],
    )
    monkeypatch.setattr(
        "core.routing.route_multi.try_integrate_pipeline",
        lambda *_a, **_k: None,
    )
    response = analyze_excel(payload)
    assert response["status"] == "success"
    assert captured["labels"][0] == "same.xlsx"
    assert captured["labels"][1] != captured["labels"][0]
    assert "file-2" in captured["labels"][1]
    assert payload["inputs"][0]["source_id"] == "file-1"
    assert payload["inputs"][1]["source_id"] == "file-2"
