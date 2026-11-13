"""계획 검증을 거치지 않은 결과와 계획 실패를 성공으로 포장하지 않는다."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.analysis import analyzer as analyzer_mod
from core.application import analyze_excel
from tests.application_support import base_request, write_table


def _budget() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비", "내부인건비"],
            "계획예산": [1000, 2000, 500],
            "실행예산_합계": [900, 1800, 500],
            "집행계_합계": [800, 500, 400],
        }
    )


def _exhausted(errors: list[str]):
    def fake_pipeline(*_a, exhaust_meta=None, **_k):
        exhaust_meta.update(
            {
                "fallback_reason": "plan_validation_exhausted",
                "retry_log": [{"attempt": 1, "validation_errors": errors}],
            }
        )
        return None

    return fake_pipeline


def test_rate_without_operands_asks_instead_of_pandasai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        analyzer_mod,
        "try_analysis_pipeline",
        _exhausted(["missing_ratio_composition: The request appears to need a rate"]),
    )
    monkeypatch.setattr(
        analyzer_mod,
        "chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no pandasai")),
    )
    result, reply, meta = analyzer_mod.run_analysis(
        _budget(), "비목분류별 집행률을 구해줘", base_url="http://localhost", model="dummy"
    )
    assert isinstance(result, pd.DataFrame) and result.empty
    assert "대비" in reply
    assert meta["fallback_reason"] == "rate_needs_operands"


def test_other_plan_failures_still_reach_pandasai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        analyzer_mod, "try_analysis_pipeline", _exhausted(["misused_top_per_group: x"])
    )
    monkeypatch.setattr(
        analyzer_mod,
        "chat",
        lambda *_a, **_k: (pd.DataFrame({"x": [1]}), "pandasai", {"source": "pandasai"}),
    )
    _, reply, meta = analyzer_mod.run_analysis(
        _budget(), "복잡한 비표준 교차 피벗을 만들어줘", base_url="http://localhost", model="dummy"
    )
    assert reply == "pandasai"
    assert meta["fallback_reason"] == "pandasai_final_fallback"


def _respond(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome) -> dict:
    monkeypatch.setattr("core.routing.route_single.run_analysis", lambda *_a, **_k: outcome)
    path = write_table(tmp_path / "budget.xlsx", {"비목분류": ["A"], "집행계_합계": [1]})
    return analyze_excel(base_request(tmp_path, paths=[path], prompt="비목분류별 집행률을 분석해줘"))


@pytest.mark.parametrize("reason", ["rate_needs_operands", "safe_plan_failure"])
def test_plan_failure_is_cannot_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    response = _respond(
        tmp_path,
        monkeypatch,
        (pd.DataFrame(), "기준 열을 함께 적어 주세요.", {"fallback_reason": reason}),
    )
    assert response["status"] == "cannot_plan"
    assert response["error"]["code"] == reason
    assert response["text"] == "기준 열을 함께 적어 주세요."
    assert response["artifacts"] == []


@pytest.mark.parametrize(
    "reason", ["pandasai_final_fallback", "simple_groupby_fallback", "retrieval_fallback"]
)
def test_fallback_result_is_not_marked_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    response = _respond(
        tmp_path,
        monkeypatch,
        (pd.DataFrame({"비목분류": ["A"], "집행률": [0.5]}), "결과", {"fallback_reason": reason}),
    )
    assert response["status"] == "success"
    assert response["safety"]["validation_status"] == "not_run"
    assert "unverified_fallback" in [w["code"] for w in response["warnings"]]


def test_planned_result_stays_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _respond(
        tmp_path,
        monkeypatch,
        (
            pd.DataFrame({"비목분류": ["A"], "집행률": [50.0]}),
            "결과",
            {"aggregation": {"operation": "analysis_plan"}},
        ),
    )
    assert response["safety"]["validation_status"] == "passed"
    assert "unverified_fallback" not in [w["code"] for w in response["warnings"]]
