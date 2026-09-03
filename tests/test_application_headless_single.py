"""Headless single-file production-router tests."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

from core.application import analyze_excel
from tests.application_support import base_request, write_table


def test_single_summary_early_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.routing.route_single.run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("summary must not call run_analysis")),
    )
    path = write_table(tmp_path / "sales.xlsx", {"지역": ["서울", "부산"], "매출": [10, 20]})
    response = analyze_excel(
        base_request(tmp_path, paths=[path], prompt="이 파일의 주요 내용을 요약해줘")
    )
    assert response["status"] == "success"
    assert response["text"]
    assert response["data"]["route"] == "route_single_prompt"
    assert response["data"]["preview_records"] == []


def test_single_schema_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.routing.route_single.run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("schema must not call run_analysis")),
    )
    path = write_table(tmp_path / "sales.xlsx", {"지역": ["서울"], "매출": [10]})
    response = analyze_excel(base_request(tmp_path, paths=[path], prompt="컬럼 목록 보여줘"))
    assert response["status"] == "success"
    assert response["data"]["columns"]
    assert response["data"]["shape"][0] >= 1
    table_artifacts = [item for item in response["artifacts"] if item["kind"] == "table"]
    assert table_artifacts
    assert Path(table_artifacts[0]["path"]).is_file()
    assert table_artifacts[0]["sha256"]
    assert Path(table_artifacts[0]["path"]).resolve().is_relative_to((tmp_path / "out").resolve())


def test_single_quality_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.routing.route_single.run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("quality must not call run_analysis")),
    )
    path = write_table(tmp_path / "sales.xlsx", {"코드": ["A", "B"], "금액": [1, None]})
    response = analyze_excel(
        base_request(tmp_path, paths=[path], prompt="이 파일의 데이터 품질을 분석해줘")
    )
    assert response["status"] == "success"
    assert "품질" in response["text"] or "판정" in response["text"]


def test_single_analysis_uses_fake_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = pd.DataFrame({"항목": [f"r{i}" for i in range(15)], "값": list(range(15))})

    def fake_run_analysis(*_a, **_k):
        return table, "분석 결과 요약", {"aggregation": {"operation": "analysis_plan"}}

    monkeypatch.setattr("core.routing.route_single.run_analysis", fake_run_analysis)
    path = write_table(tmp_path / "sales.xlsx", {"항목": ["A"], "값": [1]})
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = base_request(
        tmp_path,
        paths=[path],
        prompt="항목별 값을 분석해줘",
        request_id="caller/../id",
    )
    response = analyze_excel(payload)
    assert response["status"] == "success"
    assert response["text"] == "분석 결과 요약"
    assert response["data"]["shape"] == [15, 2]
    assert len(response["data"]["preview_records"]) == 10
    assert "bytes" not in str(type(response["data"]["preview_records"][0].get("항목")))
    table_artifacts = [item for item in response["artifacts"] if item["kind"] == "table"]
    assert table_artifacts
    artifact_path = Path(table_artifacts[0]["path"])
    assert artifact_path.is_file()
    loaded = pd.read_excel(artifact_path)
    assert len(loaded) == 15
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    # request_id is not used as a raw path component
    assert ".." not in artifact_path.parts
    assert artifact_path.resolve().is_relative_to((tmp_path / "out").resolve())


def test_headless_single_does_not_import_streamlit(tmp_path: Path) -> None:
    retained = {name: mod for name, mod in sys.modules.items() if name == "streamlit" or name.startswith("streamlit.")}
    for name in list(retained):
        del sys.modules[name]
    from core.application.headless import analyze_excel as fresh_analyze

    path = write_table(tmp_path / "sales.xlsx")
    response = fresh_analyze(base_request(tmp_path, paths=[path], prompt="이 파일 요약해줘"))
    assert response["status"] == "success"
    assert "streamlit" not in sys.modules
    sys.modules.update(retained)
