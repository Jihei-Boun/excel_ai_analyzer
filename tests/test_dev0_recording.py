"""기준 실행 기록의 측정값·복원값·판정 구분."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "docs" / "development" / "dev0" / "run_baseline.py"
    spec = importlib.util.spec_from_file_location("dev0_run_baseline", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def baseline():
    return _load()


def _write_case(tmp_path: Path, name: str, response: dict, meta: dict | None = None, stdout: str | None = None) -> Path:
    case = tmp_path / name
    case.mkdir()
    (case / "response.json").write_text(json.dumps(response), encoding="utf-8")
    if meta is not None:
        (case / "execution_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if stdout is not None:
        (case / "stdout.txt").write_text(stdout, encoding="utf-8")
    return case


def test_stored_meta_keeps_measured_exit_code(baseline, tmp_path: Path) -> None:
    case = _write_case(
        tmp_path,
        "bad-sheet",
        {"status": "success", "data": {}, "artifacts": []},
        {
            "exit_code": 2,
            "elapsed_seconds": 0.01,
            "external_timeout": False,
            "stdout_bytes": 12,
            "input_hashes_before": {"a": "1"},
            "input_hashes_after": {"a": "1"},
            "input_hash_unchanged": True,
            "code_sha": "abc",
            "worktree_dirty": True,
        },
    )
    record = baseline.build_record("bad_sheet", case, evidence_source="stored")
    assert record["exit_code"] == 2
    assert record["exit_code_source"] == "stored"
    assert record["stdout_bytes"] == 12
    assert record["input_hash_unchanged"] is True
    judged = baseline.judge("bad_sheet", record)
    assert judged["expectation_passed"] is False


def test_missing_meta_stays_unknown(baseline, tmp_path: Path) -> None:
    case = _write_case(
        tmp_path,
        "single-summary",
        {
            "status": "success",
            "text": "5행 × 3열 합 240",
            "data": {"shape": [0, 0], "preview_records": []},
            "artifacts": [],
        },
    )
    record = baseline.build_record("single_summary", case, evidence_source="stored")
    assert record["exit_code"] is None
    assert record["exit_code_source"] == "unknown"
    assert record["stdout_bytes"] is None
    assert record["input_hash_unchanged"] is None
    assert record["elapsed_seconds"] is None
    assert record["measurement_note"] == "미확인"
    judged = baseline.judge("single_summary", record)
    assert judged["expectation_passed"] is None


def test_connection_success_is_defect_not_pass(baseline, tmp_path: Path) -> None:
    case = _write_case(
        tmp_path,
        "integrate-down",
        {"status": "success", "text": "연결 거부", "data": {}, "artifacts": [], "error": None},
        {
            "exit_code": 0,
            "external_timeout": False,
            "stdout_bytes": 40,
            "input_hash_unchanged": True,
            "input_hashes_before": {},
            "input_hashes_after": {},
            "elapsed_seconds": 0.05,
            "code_sha": "abc",
            "worktree_dirty": False,
        },
    )
    judged = baseline.judge(
        "integrate_model_down",
        baseline.build_record("integrate_model_down", case, evidence_source="direct"),
    )
    assert judged["case_kind"] == "defect_reproduction"
    assert judged["defect_reproduced"] is True
    assert judged["expectation_passed"] is None


def test_live_integrate_observation_is_not_accuracy_pass(baseline, tmp_path: Path) -> None:
    case = _write_case(
        tmp_path,
        "integrate-live",
        {"status": "success", "text": "검증 통과", "data": {"preview_records": []}, "artifacts": []},
    )
    judged = baseline.judge(
        "integrate_live",
        baseline.build_record("integrate_live", case, evidence_source="stored"),
    )
    assert judged["case_kind"] == "observation"
    assert judged["expectation_passed"] is None
    assert judged["observation_complete"] is True


def test_groupby_requires_file_and_preview(baseline, tmp_path: Path) -> None:
    rows = [{"상품": "사과", "매출": 140}, {"상품": "배", "매출": 100}]
    wrong = [{"상품": "사과", "매출": 999}, {"상품": "배", "매출": 100}]
    record = {
        "executed": True,
        "evidence_source": "direct",
        "response_source": "direct",
        "response_status": "success",
        "response_error": None,
        "text": "분석 계획 결과: 2행",
        "exit_code": 0,
        "input_hash_unchanged": True,
        "external_timeout": False,
        "artifacts": [
            {
                "kind": "table",
                "exists": True,
                "records": wrong,
                "hash_match": True,
                "size_match": True,
            }
        ],
        "table_check": baseline.table_check(artifacts=[{"records": wrong}], preview=rows, expected_rows=rows),
        "data_shape": [2, 2],
    }
    judged = baseline.judge("groupby_live_model", record)
    assert record["table_check"]["preview_matches"] is True
    assert record["table_check"]["full_file_matches"] is False
    assert judged["expectation_passed"] is False


def test_decimal_is_not_truncated_to_expected_integer(baseline) -> None:
    expected = ({"상품": "사과", "매출": 140},)
    assert baseline.rows_equal([{"상품": "사과", "매출": 140.0}], expected) is True
    assert baseline.rows_equal([{"상품": "사과", "매출": 140.4}], expected) is False


def test_artifact_hash_mismatch_fails_expectation(baseline) -> None:
    record = {
        "executed": True,
        "response_source": "direct",
        "response_status": "success",
        "response_error": None,
        "text": "",
        "exit_code": 0,
        "input_hash_unchanged": True,
        "external_timeout": False,
        "artifacts": [
            {
                "kind": "table",
                "exists": True,
                "records": [{"행 수": 4, "열 수": 3, "컬럼 목록": "코드, 금액, 메모"}],
                "hash_match": False,
                "size_match": True,
            }
        ],
        "table_check": {
            "full_file_matches": True,
            "preview_matches": True,
        },
        "data_shape": [1, 3],
    }
    judged = baseline.judge("schema_quality_schema", record)
    assert judged["expectation_passed"] is False


def test_summary_without_artifact_can_pass(baseline) -> None:
    record = {
        "executed": True,
        "response_source": "direct",
        "response_status": "success",
        "response_error": None,
        "text": "이 파일은 5행 × 3열 표입니다. 매출 합 240",
        "exit_code": 0,
        "input_hash_unchanged": True,
        "external_timeout": False,
        "artifacts": [],
        "table_check": None,
        "data_shape": [0, 0],
    }
    judged = baseline.judge("single_summary", record)
    assert judged["expectation_passed"] is True
