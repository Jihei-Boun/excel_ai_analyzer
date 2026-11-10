"""DEV-0 합성 데이터 생성, CLI 측정, 저장 기록 재조립.

생산 코드는 호출만 한다. 종료 코드와 입력 해시는 실행 시 측정한 값만 기록한다.
응답 상태에서 종료 코드를 역산하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HISTORICAL_RUNS = ROOT / "benchmark_results" / "dev0"
REMEASURE_RUNS = ROOT / "benchmark_results" / "dev0-remeasure-20261014"
CLOSED_PORT_AFTER_RUNS = ROOT / "benchmark_results" / "dev1-closed-port-20261014"
CLOSED_PORT_AFTER_RESULTS = ROOT / "docs" / "development" / "dev1" / "closed_port_after.json"
PYTHON = ROOT / ".venv" / "bin" / "python"
MODEL_URL = "http://127.0.0.1:11434"
MODEL_NAME = "qwen2.5:7b"
PREVIEW_LIMIT = 10
HISTORICAL_RESULTS = Path(__file__).resolve().parent / "results.json"
REINTERPRET_PATH = Path(__file__).resolve().parent / "historical_reinterpretation.json"
REMEASURE_RESULTS = Path(__file__).resolve().parent / "remeasure_results.json"

SALES_BY_PRODUCT = ({"상품": "사과", "매출": 140}, {"상품": "배", "매출": 100})
SCHEMA_ROWS = ({"행 수": 4, "열 수": 3, "컬럼 목록": "코드, 금액, 메모"},)
COMPARE_ROWS = ({"출처파일": "상반기", "매출": 30}, {"출처파일": "하반기", "매출": 20})

CASE_KIND = {
    "single_summary": "expectation",
    "schema_quality_schema": "expectation",
    "schema_quality_quality": "expectation",
    "budget_summary": "expectation",
    "multi_summary": "expectation",
    "multi_compare": "expectation",
    "bad_sheet": "expectation",
    "bad_profile": "expectation",
    "bad_path": "expectation",
    "zero_row_missing": "expectation",
    "timeout_not_enforced": "expectation",
    "groupby_live_model": "expectation",
    "integrate_model_down": "defect_reproduction",
    "integrate_live": "observation",
}

HISTORICAL_DIRS = {
    "single_summary": "single-summary",
    "schema_quality_schema": "schema",
    "schema_quality_quality": "quality",
    "budget_summary": "budget-summary",
    "multi_summary": "multi-summary",
    "multi_compare": "multi-compare",
    "bad_sheet": "bad-sheet",
    "bad_profile": "bad-profile",
    "bad_path": "bad-path",
    "zero_row_missing": "zero-row",
    "timeout_not_enforced": "tiny-timeout",
    "integrate_model_down": "integrate-down",
    "groupby_live_model": "groupby-live",
    "integrate_live": "integrate-live",
}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def git_snapshot() -> dict[str, Any]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"code_sha": None, "worktree_dirty": None, "source": "unknown"}
    return {"code_sha": sha, "worktree_dirty": dirty, "source": "direct"}


def cell_equal(actual: Any, expected: Any) -> bool:
    """숫자 동등 비교. int()로 소수부를 잘라 맞추지 않는다."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected and isinstance(actual, bool) == isinstance(expected, bool)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return actual == expected
    return actual == expected


def rows_equal(actual: list[dict[str, Any]] | None, expected: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> bool:
    if actual is None or len(actual) != len(expected):
        return False
    for got, want in zip(actual, expected):
        got_keys = [str(key) for key in got.keys()]
        want_keys = [str(key) for key in want.keys()]
        if got_keys != want_keys:
            return False
        for key, value in want.items():
            if not cell_equal(got[key], value):
                return False
    return True


def read_table(path: Path) -> dict[str, Any]:
    book = pd.ExcelFile(path)
    sheets: dict[str, list[dict[str, Any]]] = {}
    for sheet in book.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet)
        sheets[sheet] = json.loads(frame.to_json(orient="records", force_ascii=False))
    first = sheets[book.sheet_names[0]] if book.sheet_names else []
    columns = list(first[0].keys()) if first else []
    return {
        "sheets": book.sheet_names,
        "columns": columns,
        "rows": len(first),
        "records": first,
        "all_sheets": sheets,
    }


def inspect_artifacts(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not response:
        return checks
    for artifact in response.get("artifacts") or []:
        path = Path(str(artifact.get("path") or ""))
        exists = path.is_file()
        actual_size = path.stat().st_size if exists else None
        actual_sha = sha256_file(path) if exists else None
        declared_size = artifact.get("size_bytes")
        declared_sha = artifact.get("sha256")
        record: dict[str, Any] = {
            "kind": artifact.get("kind"),
            "filename": path.name,
            "exists": exists,
            "actual_size": actual_size,
            "declared_size": declared_size,
            "size_match": None
            if actual_size is None or declared_size is None
            else int(actual_size) == int(declared_size),
            "actual_sha256": actual_sha,
            "declared_sha256": declared_sha,
            "hash_match": None
            if actual_sha is None or not declared_sha
            else actual_sha == declared_sha,
        }
        if exists and path.suffix.lower() == ".xlsx":
            record.update(read_table(path))
        checks.append(record)
    return checks


def table_check(
    *,
    artifacts: list[dict[str, Any]],
    preview: list[dict[str, Any]] | None,
    expected_rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    table = next((item for item in artifacts if item.get("records") is not None), None)
    full_records = None if table is None else table.get("records")
    preview_rows = list(preview or [])
    preview_expected = list(expected_rows[:PREVIEW_LIMIT])
    return {
        "full_file_matches": rows_equal(full_records, expected_rows),
        "preview_matches": rows_equal(preview_rows, preview_expected),
        "full_row_count": None if full_records is None else len(full_records),
        "preview_row_count": len(preview_rows),
        "expected_row_count": len(expected_rows),
        "preview_limit": PREVIEW_LIMIT,
        "preview_covers_all_rows": len(expected_rows) <= PREVIEW_LIMIT,
    }


def artifact_declarations_ok(artifacts: list[dict[str, Any]]) -> bool | None:
    if not artifacts:
        return True
    flags = []
    for item in artifacts:
        if item.get("hash_match") is None or item.get("size_match") is None:
            return None
        flags.append(bool(item["hash_match"] and item["size_match"] and item["exists"]))
    return all(flags)


def _unknown_measurement() -> dict[str, Any]:
    return {
        "exit_code": None,
        "elapsed_seconds": None,
        "external_timeout": None,
        "stdout_bytes": None,
        "stderr_bytes": None,
        "input_hashes_before": None,
        "input_hashes_after": None,
        "input_hash_unchanged": None,
        "code_sha": None,
        "worktree_dirty": None,
        "exit_code_source": "unknown",
        "input_hash_source": "unknown",
        "stdout_source": "unknown",
        "elapsed_source": "unknown",
        "timeout_source": "unknown",
        "code_sha_source": "unknown",
        "measurement_note": "미확인",
    }


def load_measurement(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "execution_meta.json"
    base = _unknown_measurement()
    if not path.is_file():
        return base
    raw = json.loads(path.read_text(encoding="utf-8"))
    measured = dict(base)
    for key in (
        "exit_code",
        "elapsed_seconds",
        "external_timeout",
        "stdout_bytes",
        "stderr_bytes",
        "input_hashes_before",
        "input_hashes_after",
        "input_hash_unchanged",
        "code_sha",
        "worktree_dirty",
    ):
        if key in raw:
            measured[key] = raw[key]
    measured["exit_code_source"] = "stored"
    measured["input_hash_source"] = "stored"
    measured["stdout_source"] = "stored"
    measured["elapsed_source"] = "stored"
    measured["timeout_source"] = "stored"
    measured["code_sha_source"] = "stored"
    measured["measurement_note"] = "실행 시 저장한 execution_meta.json"
    return measured


def load_response(case_dir: Path) -> tuple[dict[str, Any] | None, str]:
    path = case_dir / "response.json"
    if not path.is_file():
        return None, "unknown"
    return json.loads(path.read_text(encoding="utf-8")), "stored"


def judge(case_id: str, record: dict[str, Any]) -> dict[str, Any]:
    kind = CASE_KIND[case_id]
    status = record.get("response_status")
    text = record.get("text") or ""
    exit_code = record.get("exit_code")
    artifacts = record.get("artifacts") or []
    notes: list[str] = []
    contradiction = False
    incomplete = False

    def require(condition: bool | None, message: str) -> None:
        nonlocal contradiction, incomplete
        if condition is None:
            incomplete = True
            notes.append(f"미확인: {message}")
        elif not condition:
            contradiction = True
            notes.append(message)

    declarations = artifact_declarations_ok(artifacts)
    no_success_files = not any(item.get("kind") in {"table", "workbook"} for item in artifacts)

    if case_id == "single_summary":
        require(status == "success", "요약 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "요약 종료 코드")
        require("5행 × 3열" in text and "합 240" in text, "요약 문구에 헤더 포함 5행×3열 또는 매출 합 240이 없다.")
        require(no_success_files, "텍스트 요약에 table·workbook 산출물이 있다.")
        require(declarations, "산출물 선언값 불일치")
    elif case_id == "schema_quality_schema":
        check = record.get("table_check") or {}
        require(status == "success", "스키마 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "스키마 종료 코드")
        require(check.get("full_file_matches"), "스키마 산출물 표가 기대 행과 다르다.")
        require(check.get("preview_matches"), "스키마 미리보기가 기대 행과 다르다.")
        require(declarations, "산출물 선언값 불일치")
    elif case_id == "schema_quality_quality":
        require(status == "success", "품질 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "품질 종료 코드")
        require("결측 셀: 1개" in text and "완전 중복 행: 1개" in text, "품질 문구가 결측 1·중복 1과 다르다.")
        require(no_success_files, "단일 품질 텍스트에 표 산출물이 있다.")
    elif case_id == "budget_summary":
        require(status == "success", "예산 요약 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "예산 요약 종료 코드")
        require("180,000원" in text and "70,000원" in text, "상세행 기준 금액 문구가 없다.")
        require("360,000원" not in text and "140,000원" not in text, "소계가 한 번 더 더해진 금액이 있다.")
        require(no_success_files, "예산 요약에 표 산출물이 있다.")
    elif case_id == "multi_summary":
        require(status == "success", "다중 요약 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "다중 요약 종료 코드")
        require("합 30" in text and "합 20" in text, "시트별 매출 합 30·20 문구가 없다.")
        require(no_success_files, "다중 요약에 표 산출물이 있다.")
    elif case_id == "multi_compare":
        check = record.get("table_check") or {}
        require(status == "success", "비교 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "비교 종료 코드")
        require(check.get("full_file_matches"), "비교 산출물이 상반기 30·하반기 20과 다르다.")
        require(check.get("preview_matches"), "비교 미리보기가 기대 행과 다르다.")
        require(declarations, "산출물 선언값 불일치")
    elif case_id == "bad_sheet":
        error = record.get("response_error") or {}
        require(status == "invalid_request", "없는 시트 상태가 invalid_request가 아니다.")
        require(None if exit_code is None else exit_code == 2, "없는 시트 종료 코드")
        require(error.get("code") == "invalid_sheet", "오류 코드가 invalid_sheet가 아니다.")
        require(no_success_files, "없는 시트 응답에 산출물이 있다.")
    elif case_id == "bad_profile":
        error = record.get("response_error") or {}
        require(status == "invalid_request", "없는 프로필 상태가 invalid_request가 아니다.")
        require(None if exit_code is None else exit_code == 2, "없는 프로필 종료 코드")
        require(error.get("code") == "invalid_profile", "오류 코드가 invalid_profile이 아니다.")
    elif case_id == "bad_path":
        error = record.get("response_error") or {}
        require(status == "invalid_request", "없는 파일 상태가 invalid_request가 아니다.")
        require(None if exit_code is None else exit_code == 2, "없는 파일 종료 코드")
        require(error.get("code") == "missing_file", "오류 코드가 missing_file이 아니다.")
    elif case_id == "zero_row_missing":
        shape = record.get("data_shape") or [None, None]
        require(status == "success", "0행 조회 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "0행 조회 종료 코드")
        require(shape[0] == 0, "0행 조회 shape가 0이 아니다.")
        require("결측값이 있는 행을 찾지 못했습니다" in text, "0행 안내 문구가 없다.")
        require(no_success_files, "정상 빈 결과에 표 산출물이 있다.")
    elif case_id == "timeout_not_enforced":
        require(status == "success", "짧은 timeout 요청이 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "짧은 timeout 종료 코드")
        require(
            None if record.get("external_timeout") is None else record.get("external_timeout") is False,
            "외부 timeout",
        )
        require(no_success_files, "요약에 표 산출물이 있다.")
    elif case_id == "groupby_live_model":
        check = record.get("table_check") or {}
        require(status == "success", "그룹 집계 상태가 success가 아니다.")
        require(None if exit_code is None else exit_code == 0, "그룹 집계 종료 코드")
        require(check.get("full_file_matches"), "그룹 집계 산출물이 상품·매출 기대값과 다르다.")
        require(check.get("preview_matches"), "그룹 집계 미리보기가 상품·매출 기대값과 다르다.")
        require(declarations, "산출물 선언값 불일치")
    elif case_id == "integrate_model_down":
        if status == "success" and (exit_code is None or exit_code == 0):
            notes.append("연결 실패의 success 응답은 결함 재현이다. 정상 동작 통과로 세지 않는다.")
        elif status is not None and status != "success":
            notes.append("연결 실패가 비성공 상태다. 성공 오표시는 재현되지 않았고, 기대값 통과로도 세지 않는다.")
    elif case_id == "integrate_live":
        notes.append("실모델 통합은 관찰이다. 수치 일치 통과로 세지 않는다.")

    if record.get("input_hash_unchanged") is False:
        contradiction = True
        notes.append("측정된 입력 해시가 실행 후 달라졌다.")
    elif record.get("input_hash_unchanged") is None and kind == "expectation":
        incomplete = True
        notes.append("미확인: 실행 전후 입력 해시")

    expectation_passed: bool | None
    defect_reproduced: bool | None
    observation_complete: bool | None
    if kind == "expectation":
        if contradiction:
            expectation_passed = False
        elif incomplete or record.get("response_source") == "unknown":
            expectation_passed = None
        else:
            expectation_passed = True
        defect_reproduced = None
        observation_complete = None
    elif kind == "defect_reproduction":
        expectation_passed = None
        if status is None:
            defect_reproduced = None
            notes.append("미확인: 연결 실패 응답 상태")
        else:
            defect_reproduced = status == "success" and (exit_code is None or exit_code == 0)
            if status == "success" and exit_code not in (None, 0):
                defect_reproduced = False
                notes.append("상태는 success이지만 종료 코드가 0이 아니다.")
        observation_complete = status is not None
    else:
        expectation_passed = None
        defect_reproduced = None
        if record.get("external_timeout") is True or record.get("response_source") == "unknown":
            observation_complete = False
        else:
            observation_complete = True

    return {
        "case_id": case_id,
        "case_kind": kind,
        "executed": bool(record.get("executed")),
        "evidence_source": record.get("evidence_source"),
        "expectation_passed": expectation_passed,
        "defect_reproduced": defect_reproduced,
        "observation_complete": observation_complete,
        "notes": notes,
        **{key: value for key, value in record.items() if key not in {"executed"}},
    }


def build_record(case_id: str, case_dir: Path, *, evidence_source: str) -> dict[str, Any]:
    measured = load_measurement(case_dir)
    response, response_source = load_response(case_dir)
    if evidence_source == "direct" and measured["exit_code_source"] == "stored":
        for key in (
            "exit_code_source",
            "input_hash_source",
            "stdout_source",
            "elapsed_source",
            "timeout_source",
            "code_sha_source",
        ):
            measured[key] = "direct"
        measured["measurement_note"] = "이번 프로세스에서 측정했다."
    artifacts = inspect_artifacts(response)
    preview = None if response is None else (response.get("data") or {}).get("preview_records")
    table = None
    if case_id == "schema_quality_schema":
        table = table_check(artifacts=artifacts, preview=preview, expected_rows=SCHEMA_ROWS)
    elif case_id == "multi_compare":
        table = table_check(artifacts=artifacts, preview=preview, expected_rows=COMPARE_ROWS)
    elif case_id == "groupby_live_model":
        table = table_check(artifacts=artifacts, preview=preview, expected_rows=SALES_BY_PRODUCT)
    executed = response is not None or measured["exit_code_source"] != "unknown"
    sources = {
        measured["exit_code_source"],
        measured["input_hash_source"],
        response_source,
    }
    if evidence_source == "direct":
        overall = "direct"
    elif "stored" in sources and "unknown" in sources:
        overall = "stored"
    elif sources == {"unknown"}:
        overall = "unknown"
    elif "stored" in sources:
        overall = "stored"
    else:
        overall = "unknown"
    stderr_path = case_dir / "stderr.txt"
    stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.is_file() else ""
    return {
        "executed": executed,
        "evidence_source": overall,
        "response_source": response_source,
        "response_status": None if response is None else response.get("status"),
        "response_error": None if response is None else response.get("error"),
        "safety": None if response is None else response.get("safety"),
        "text": None if response is None else response.get("text"),
        "data_shape": None if response is None else (response.get("data") or {}).get("shape"),
        "data_columns": None if response is None else (response.get("data") or {}).get("columns"),
        "preview_records": preview,
        "operation_name": None if response is None else (response.get("data") or {}).get("operation_name"),
        "route": None if response is None else (response.get("data") or {}).get("route"),
        "artifact_count": 0 if response is None else len(response.get("artifacts") or []),
        "artifacts": artifacts,
        "table_check": table,
        "stderr_excerpt": stderr[-1500:],
        "response_timing_ms": None if response is None else (response.get("timing") or {}).get("elapsed_ms"),
        **measured,
    }


def write_table_xlsx(path: Path, rows: list[dict[str, Any]] | None = None, **sheets: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if sheets:
            for name, frame in sheets.items():
                frame.to_excel(writer, index=False, sheet_name=name)
        else:
            pd.DataFrame(rows or []).to_excel(writer, index=False)


def generate_fixtures() -> dict[str, str]:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    write_table_xlsx(
        FIXTURES / "sales.xlsx",
        [
            {"상품": "사과", "지역": "서울", "매출": 100},
            {"상품": "사과", "지역": "부산", "매출": 40},
            {"상품": "배", "지역": "서울", "매출": 70},
            {"상품": "배", "지역": "부산", "매출": 30},
        ],
    )
    write_table_xlsx(
        FIXTURES / "quality.xlsx",
        [
            {"코드": "A", "금액": 10, "메모": "ok"},
            {"코드": "A", "금액": 10, "메모": "ok"},
            {"코드": "B", "금액": None, "메모": "missing"},
            {"코드": "C", "금액": 20, "메모": "ok"},
        ],
    )
    write_table_xlsx(
        FIXTURES / "budget.xlsx",
        [
            {"비목분류": "인건비", "비용명": "내부인건비", "실행예산": 100_000, "집행계": 40_000},
            {"비목분류": "인건비", "비용명": "소계", "실행예산": 100_000, "집행계": 40_000},
            {"비목분류": "연구활동비", "비용명": "회의비", "실행예산": 50_000, "집행계": 10_000},
            {"비목분류": "연구활동비", "비용명": "출장비", "실행예산": 30_000, "집행계": 20_000},
            {"비목분류": "연구활동비", "비용명": "소계", "실행예산": 80_000, "집행계": 30_000},
            {"비목분류": "합계", "비용명": None, "실행예산": None, "집행계": None},
        ],
    )
    write_table_xlsx(
        FIXTURES / "periods.xlsx",
        상반기=pd.DataFrame([{"상품": "사과", "매출": 10}, {"상품": "배", "매출": 20}]),
        하반기=pd.DataFrame([{"상품": "사과", "매출": 5}, {"상품": "배", "매출": 15}]),
    )
    write_table_xlsx(
        FIXTURES / "customers.xlsx",
        [{"고객ID": "C1", "매출": 10}, {"고객ID": "C2", "매출": 20}, {"고객ID": "C3", "매출": 30}],
    )
    write_table_xlsx(
        FIXTURES / "grades.xlsx",
        [{"고객ID": "C2", "등급": "A"}, {"고객ID": "C3", "등급": "B"}, {"고객ID": "C4", "등급": "C"}],
    )
    return {path.name: sha256_file(path) for path in sorted(FIXTURES.glob("*.xlsx"))}


def ensure_fixtures() -> dict[str, str]:
    """이미 있는 합성 파일은 다시 쓰지 않는다. openpyxl 저장은 바이트가 고정되지 않는다."""
    names = (
        "sales.xlsx",
        "quality.xlsx",
        "budget.xlsx",
        "periods.xlsx",
        "customers.xlsx",
        "grades.xlsx",
    )
    if all((FIXTURES / name).is_file() for name in names):
        return {name: sha256_file(FIXTURES / name) for name in names}
    return generate_fixtures()


def request_body(
    *,
    request_id: str,
    paths: list[tuple[Path, str | int, str]],
    prompt: str,
    output_root: Path,
    profile_name: str = "generic",
    timeout_seconds: float = 30,
    model_url: str = MODEL_URL,
    model_name: str = MODEL_NAME,
) -> dict[str, Any]:
    mode = "single" if len(paths) == 1 else "multi"
    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "operation": "analyze",
        "inputs": [
            {
                "source_id": f"src-{index + 1}",
                "path": str(path.resolve()),
                "sheet": sheet,
                "display_name": display,
            }
            for index, (path, sheet, display) in enumerate(paths)
        ],
        "user_prompt": prompt,
        "analysis_mode": mode,
        "profile_name": profile_name,
        "model": {"base_url": model_url, "name": model_name},
        "timeout_seconds": timeout_seconds,
        "output_directory": str((output_root / request_id / "out").resolve()),
    }


def run_cli(payload: dict[str, Any], *, run_root: Path, limit_seconds: float) -> Path:
    request_id = str(payload["request_id"])
    case_dir = run_root / request_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    before = {item["path"]: sha256_file(Path(item["path"])) for item in payload["inputs"]}
    env = os.environ.copy()
    env.pop("MULTI_SHADOW_ENABLED", None)
    started = time.perf_counter()
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "core.application.cli"],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(
            json.dumps(payload, ensure_ascii=False),
            timeout=limit_seconds,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    elapsed = round(time.perf_counter() - started, 3)
    stdout = stdout or ""
    stderr = stderr or ""
    (case_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (case_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    after = {item["path"]: sha256_file(Path(item["path"])) for item in payload["inputs"]}
    if stdout.strip():
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError:
            response = None
        if response is not None:
            (case_dir / "response.json").write_text(
                json.dumps(response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    identity = git_snapshot()
    meta = {
        "exit_code": proc.returncode,
        "elapsed_seconds": elapsed,
        "external_timeout": timed_out,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "input_hashes_before": before,
        "input_hashes_after": after,
        "input_hash_unchanged": before == after,
        "code_sha": identity["code_sha"],
        "worktree_dirty": identity["worktree_dirty"],
    }
    (case_dir / "execution_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return case_dir


def assemble_tree(run_root: Path, mapping: dict[str, str], *, evidence_source: str) -> list[dict[str, Any]]:
    judged = []
    for case_id, dirname in mapping.items():
        judged.append(judge(case_id, build_record(case_id, run_root / dirname, evidence_source=evidence_source)))
    return judged


def internal_checks() -> dict[str, Any]:
    from core.integrate.plan_engine import execute_plan
    from core.integrate.plan_types import ExecutionPlan
    from core.io.merge_engine import merge_named_frames

    left = pd.DataFrame(
        [{"고객ID": "C1", "매출": 10}, {"고객ID": "C2", "매출": 20}, {"고객ID": "C3", "매출": 30}]
    )
    right = pd.DataFrame(
        [{"고객ID": "C2", "등급": "A"}, {"고객ID": "C3", "등급": "B"}, {"고객ID": "C4", "등급": "C"}]
    )
    expected = {"inner": 2, "left": 3, "outer": 4}
    joins = {}
    for how in ("inner", "left", "outer"):
        merged = merge_named_frames(
            [("customers", left), ("grades", right)],
            keys=["고객ID"],
            how=how,
        )
        joins[how] = {
            "rows": int(len(merged.dataframe)),
            "expected_rows": expected[how],
            "matched": int(len(merged.dataframe)) == expected[how],
            "match_rate": merged.report.match_rate,
            "matched_rows": merged.report.matched_rows,
        }
    rejected = False
    message = ""
    try:
        execute_plan(ExecutionPlan(operation="join", sources=["customers"]), {"customers": left})
    except ValueError as exc:
        rejected = True
        message = str(exc)
    return {
        "evidence_source": "direct",
        "joins": joins,
        "plan_engine_rejects_join": rejected,
        "plan_engine_message": message,
    }


def remeasure_requests(output_root: Path) -> list[tuple[str, dict[str, Any], float]]:
    sales = FIXTURES / "sales.xlsx"
    quality = FIXTURES / "quality.xlsx"
    missing = Path("/tmp/dev0-missing-workbook.xlsx")
    return [
        (
            "single_summary",
            request_body(
                request_id="remeasure-summary",
                paths=[(sales, 0, "sales.xlsx")],
                prompt="이 파일의 주요 내용을 요약해줘",
                output_root=output_root,
            ),
            30,
        ),
        (
            "schema_quality_schema",
            request_body(
                request_id="remeasure-schema",
                paths=[(quality, 0, "quality.xlsx")],
                prompt="컬럼 목록을 보여줘",
                output_root=output_root,
            ),
            30,
        ),
        (
            "zero_row_missing",
            request_body(
                request_id="remeasure-zero-row",
                paths=[(sales, 0, "sales.xlsx")],
                prompt="결측값이 있는 행만 보여줘",
                output_root=output_root,
            ),
            30,
        ),
        (
            "bad_sheet",
            request_body(
                request_id="remeasure-bad-sheet",
                paths=[(sales, "없는시트", "sales.xlsx")],
                prompt="이 파일의 주요 내용을 요약해줘",
                output_root=output_root,
            ),
            30,
        ),
        (
            "integrate_model_down",
            request_body(
                request_id="remeasure-integrate-down",
                paths=[
                    (FIXTURES / "customers.xlsx", 0, "customers.xlsx"),
                    (FIXTURES / "grades.xlsx", 0, "grades.xlsx"),
                ],
                prompt="선택한 파일들을 통합해줘",
                output_root=output_root,
                model_url="http://127.0.0.1:9",
                timeout_seconds=5,
            ),
            40,
        ),
    ]


def command_reinterpret() -> int:
    mapping = HISTORICAL_DIRS
    cases = assemble_tree(HISTORICAL_RUNS, mapping, evidence_source="stored")
    report = {
        "label": "historical_reinterpretation",
        "source_runs": str(HISTORICAL_RUNS),
        "note": (
            "과거 실행에는 execution_meta.json이 없다. "
            "종료 코드·전후 입력 해시·stdout 길이·경과 시간은 미확인이다. "
            "응답 JSON에 있는 상태만 저장 기록이다."
        ),
        "cases": cases,
    }
    REINTERPRET_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {REINTERPRET_PATH}")
    return 0


def command_remeasure() -> int:
    if not PYTHON.is_file():
        print("missing venv python", file=sys.stderr)
        return 2
    missing = Path("/tmp/dev0-missing-workbook.xlsx")
    if missing.exists():
        missing.unlink()
    ensure_fixtures()
    mapping: dict[str, str] = {}
    for case_id, payload, limit in remeasure_requests(REMEASURE_RUNS):
        print(f"RUN {case_id}", flush=True)
        case_dir = run_cli(payload, run_root=REMEASURE_RUNS, limit_seconds=limit)
        mapping[case_id] = case_dir.name
        print(f"DONE {case_id} dir={case_dir.name}", flush=True)
    cases = assemble_tree(REMEASURE_RUNS, mapping, evidence_source="direct")
    report = {
        "label": "remeasure-20261014",
        "source_runs": str(REMEASURE_RUNS),
        "git": git_snapshot(),
        "cases": cases,
    }
    REMEASURE_RESULTS.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {REMEASURE_RESULTS}")
    return 0


def command_closed_port_after() -> int:
    """DEV-1 수정 후 닫힌 포트만 새 요청 ID로 측정한다."""
    if not PYTHON.is_file():
        print("missing venv python", file=sys.stderr)
        return 2
    ensure_fixtures()
    payload = request_body(
        request_id="dev1-integrate-down",
        paths=[
            (FIXTURES / "customers.xlsx", 0, "customers.xlsx"),
            (FIXTURES / "grades.xlsx", 0, "grades.xlsx"),
        ],
        prompt="선택한 파일들을 통합해줘",
        output_root=CLOSED_PORT_AFTER_RUNS,
        model_url="http://127.0.0.1:9",
        timeout_seconds=5,
    )
    case_dir = run_cli(payload, run_root=CLOSED_PORT_AFTER_RUNS, limit_seconds=40)
    cases = assemble_tree(
        CLOSED_PORT_AFTER_RUNS,
        {"integrate_model_down": case_dir.name},
        evidence_source="direct",
    )
    report = {
        "label": "dev1-closed-port-after",
        "source_runs": str(CLOSED_PORT_AFTER_RUNS),
        "git": git_snapshot(),
        "cases": cases,
    }
    CLOSED_PORT_AFTER_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    CLOSED_PORT_AFTER_RESULTS.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"WROTE {CLOSED_PORT_AFTER_RESULTS}")
    return 0


def main() -> int:
    if "--reinterpret-historical" in sys.argv:
        return command_reinterpret()
    if "--remeasure" in sys.argv:
        return command_remeasure()
    if "--closed-port-after" in sys.argv:
        return command_closed_port_after()
    print(
        "사용법: --remeasure, --reinterpret-historical, --closed-port-after. "
        "전체 실모델 호출은 이 진입점에서 반복하지 않는다.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
