"""Phase 2: Router system/analytical 경계 · Planner 우선순위."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_pipeline import AnalysisPipelineResult
from core.analysis.analysis_plan_types import AnalysisPlan
from core.integrate.plan_types import ValidationReport
from core.routing.prompt_intent import (
    is_analytical_request,
    is_system_data_command,
    wants_structured_analysis,
)
from core.routing.prompt_router import route_single_prompt


def _sales_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "부서": ["영업", "연구", "영업"],
            "상품": ["A", "B", "C"],
            "매출": [100, 200, 50],
        }
    )


def _budget_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "비목": ["내부인건비", "연구활동비"],
            "실행예산_합계": [1000, 2000],
            "집행계_합계": [800, 500],
        }
    )


def test_system_commands_are_deterministic() -> None:
    prompts = [
        "파일 요약해줘",
        "컬럼 목록 보여줘",
        "데이터 타입 알려줘",
        "결측값이 있는 행 보여줘",
        "품질 진단해줘",
    ]
    for prompt in prompts:
        assert is_system_data_command(prompt) is True, prompt
        assert is_analytical_request(prompt) is False, prompt


def test_analytical_requests_are_not_system_commands() -> None:
    prompts = [
        "부서별 매출을 비교해줘",
        "어떤 상품의 매출이 가장 높은지 알려줘",
        "내부인건비와 연구활동비 중 집행이 더 잘 된 곳을 비교해줘",
        "평균보다 높은 항목을 찾아줘",
        "A와 B의 차이를 분석해줘",
        "어디가 더 효율적인가",
        "특징을 설명해줘",
    ]
    for prompt in prompts:
        assert is_system_data_command(prompt) is False, prompt
        assert is_analytical_request(prompt) is True, prompt
        assert wants_structured_analysis(prompt) is True, prompt


def test_analytical_gate_ignores_domain_profile_keywords() -> None:
    """집행률 등 domain keyword 없이도 분석 후보가 된다."""
    assert is_analytical_request("매출 비율을 알려줘", profile_name="generic") is True
    assert is_analytical_request("집행률 비교", profile_name="generic") is True


def test_route_system_summary_bypasses_planner(monkeypatch) -> None:
    from core.analysis import analyzer as analyzer_mod

    def _boom(*_a, **_k):
        raise AssertionError("system command는 run_analysis/Planner를 호출하면 안 됨")

    monkeypatch.setattr(analyzer_mod, "run_analysis", _boom)
    df = _sales_df()
    outcome = route_single_prompt(
        "이 파일 요약해줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost",
        model="dummy",
    )
    assert outcome.dataframe is None
    assert outcome.reply
    assert len(outcome.reply) > 10


def test_route_system_schema_bypasses_planner(monkeypatch) -> None:
    from core.analysis import analyzer as analyzer_mod

    monkeypatch.setattr(
        analyzer_mod,
        "run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("schema는 Planner 경로가 아님")
        ),
    )
    df = _sales_df()
    outcome = route_single_prompt(
        "컬럼 목록 보여줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost",
        model="dummy",
    )
    assert outcome.dataframe is not None or outcome.reply


def test_analytical_tries_planner_before_legacy(monkeypatch) -> None:
    """의미 분석은 groupby legacy보다 Planner를 먼저 시도한다."""
    from core.analysis import analyzer as analyzer_mod

    df = _sales_df()
    prompt = "부서별 매출을 비교해줘"
    calls: list[str] = []

    def _fake_pipeline(*_a, **_k):
        calls.append("planner")
        plan = AnalysisPlan(steps=[], interpret=False)
        return AnalysisPipelineResult(
            dataframe=pd.DataFrame({"부서": ["연구"], "매출": [200]}),
            reply="Planner 결과",
            plan=plan,
            validation=ValidationReport(ok=True, issues=[]),
            meta={"aggregation": {"operation": "analysis_plan"}},
        )

    def _fail_groupby(*_a, **_k):
        calls.append("groupby")
        raise AssertionError("Planner 성공 시 legacy groupby를 호출하면 안 됨")

    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", _fake_pipeline)
    monkeypatch.setattr(
        analyzer_mod, "try_legacy_simple_groupby_fallback", _fail_groupby
    )
    monkeypatch.setattr(
        analyzer_mod,
        "chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("PandasAI 호출 금지")),
    )

    result, summary, meta = analyzer_mod.run_analysis(
        df,
        prompt,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
    )
    assert calls == ["planner"]
    assert isinstance(result, pd.DataFrame)
    assert summary == "Planner 결과"
    assert meta.get("aggregation", {}).get("operation") == "analysis_plan"


def test_efficiency_compare_tries_planner_first(monkeypatch) -> None:
    """효율 비교도 domain Rule Path가 아니라 Planner 우선."""
    from core.analysis import analyzer as analyzer_mod

    df = _budget_df()
    prompt = "내부인건비와 연구활동비 중 집행이 더 잘 된 곳을 비교해줘"
    seen: dict[str, bool] = {"planner": False}

    def _fake_pipeline(*_a, **_k):
        seen["planner"] = True
        return AnalysisPipelineResult(
            dataframe=pd.DataFrame(
                {
                    "비목": ["내부인건비", "연구활동비"],
                    "집행률": [0.8, 0.25],
                }
            ),
            reply="내부인건비 집행률이 더 높습니다.",
            plan=AnalysisPlan(steps=[], interpret=True),
            validation=ValidationReport(ok=True, issues=[]),
            meta={"aggregation": {"operation": "analysis_plan"}},
        )

    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", _fake_pipeline)
    monkeypatch.setattr(
        analyzer_mod,
        "chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no pandasai")),
    )
    result, summary, meta = analyzer_mod.run_analysis(
        df,
        prompt,
        base_url="http://localhost",
        model="dummy",
        profile_name="budget",
    )
    assert seen["planner"] is True
    assert meta.get("aggregation", {}).get("operation") == "analysis_plan"
    assert "내부인건비" in summary or isinstance(result, pd.DataFrame)


def test_planner_failure_falls_back_to_legacy_groupby(monkeypatch) -> None:
    from core.analysis import analyzer as analyzer_mod

    df = _sales_df()
    prompt = "부서별 매출 합계를 알려줘"

    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", lambda *_a, **_k: None)

    def _fail_chat(*_a, **_k):
        raise AssertionError("legacy groupby로 충분하면 PandasAI 금지")

    monkeypatch.setattr(analyzer_mod, "chat", _fail_chat)
    result, summary, meta = analyzer_mod.run_analysis(
        df,
        prompt,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
    )
    assert isinstance(result, pd.DataFrame)
    assert meta.get("aggregation", {}).get("operation") == "legacy_simple_groupby_fallback"
    assert "합계" in summary or "총합" in summary or len(result) >= 1


def test_planner_failure_falls_back_to_pandasai(monkeypatch) -> None:
    from core.analysis import analyzer as analyzer_mod

    df = _sales_df()
    prompt = "비표준 피벗으로 교차표를 만들어줘"

    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", lambda *_a, **_k: None)
    monkeypatch.setattr(
        analyzer_mod,
        "try_legacy_simple_groupby_fallback",
        lambda *_a, **_k: None,
    )

    def _fake_chat(*_a, **_k):
        return pd.DataFrame({"x": [1]}), "pandasai ok", {"source": "pandasai"}

    monkeypatch.setattr(analyzer_mod, "chat", _fake_chat)
    result, summary, meta = analyzer_mod.run_analysis(
        df,
        prompt,
        base_url="http://localhost",
        model="dummy",
    )
    assert isinstance(result, pd.DataFrame)
    assert summary == "pandasai ok"
    assert meta.get("source") == "pandasai"


def test_route_analytical_does_not_call_groupby_before_run_analysis(monkeypatch) -> None:
    """route_single이 groupby를 선점하지 않고 run_analysis로 위임한다."""
    from core.routing import route_single as route_mod

    df = _sales_df()
    prompt = "부서별 매출 합계를 알려줘"
    order: list[str] = []

    def _fake_run(*_a, **_k):
        order.append("run_analysis")
        return (
            pd.DataFrame({"부서": ["영업"], "매출": [150]}),
            "ok",
            {"aggregation": {"operation": "analysis_plan"}},
        )

    monkeypatch.setattr(route_mod, "run_analysis", _fake_run)
    # Phase 2: route_single은 build_groupby_aggregate_table을 직접 호출하지 않음
    assert not hasattr(route_mod, "build_groupby_aggregate_table")

    outcome = route_single_prompt(
        prompt,
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
    )
    assert order == ["run_analysis"]
    assert outcome.dataframe is not None
    assert outcome.reply == "ok"
    assert list(outcome.dataframe["부서"]) == ["영업"]
