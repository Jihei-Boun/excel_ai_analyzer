"""Phase 5: AnalysisPlan 주엔진 · legacy analytical shortcut 축소 · fallback 계층."""

from __future__ import annotations

import pandas as pd

from core.analysis.analysis_pipeline import AnalysisPipelineResult, try_analysis_pipeline
from core.analysis.analysis_plan_types import AnalysisPlan, analysis_plan_from_dict
from core.analysis.legacy_fallback import (
    LEGACY_FALLBACK_CLASSIFICATION,
    try_legacy_simple_groupby_fallback,
)
from core.analysis.analysis_executor import execute_analysis_plan
from core.integrate.plan_types import ValidationReport
from core.routing.prompt_intent import (
    is_analytical_request,
    is_system_data_command,
)
from core.schema.row_classify import classify_rows


def _sales() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "부서": ["영업", "연구", "영업", "연구", "기획"],
            "상품": ["A001", "B002", "C003", "A001", "D004"],
            "매출": [1000, 200, 500, 300, 800],
            "단가": [10, 20, 15, 12, 25],
        }
    )


def _budget() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비", "내부인건비", "연구활동비"],
            "실행예산_합계": [1000, 2000, 500, 800],
            "집행계_합계": [800, 500, 400, 200],
            "팀": ["A팀", "B팀", "A팀", "B팀"],
        }
    )


# ---------------------------------------------------------------------------
# Coverage: Planner ops express legacy analytical intents
# ---------------------------------------------------------------------------


def test_planner_coverage_groupby_aggregate() -> None:
    df = _sales()
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {"op": "sort", "by": ["매출"], "ascending": [False]},
            ]
        },
        available_columns=list(df.columns),
    )
    result, _ = execute_analysis_plan(classify_rows(df), plan)
    assert set(result["부서"]) == {"영업", "연구", "기획"}
    assert float(result.loc[result["부서"] == "영업", "매출"].iloc[0]) == 1500


def test_planner_coverage_avg_by_product() -> None:
    df = _sales()
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "단가", "fn": "mean"}],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    result, _ = execute_analysis_plan(classify_rows(df), plan)
    assert "단가" in result.columns
    assert len(result) >= 4


def test_planner_coverage_filter_and_vs_mean() -> None:
    df = _sales()
    plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "filter_rows",
                    "numeric_filters": [{"column": "매출", "op": ">=", "value": 1000}],
                }
            ]
        },
        available_columns=list(df.columns),
    )
    filtered, _ = execute_analysis_plan(classify_rows(df), plan)
    assert (filtered["매출"] >= 1000).all()

    plan2 = analysis_plan_from_dict(
        {
            "steps": [
                {"op": "filter_vs_mean", "column": "매출", "relation": "above"},
            ]
        },
        available_columns=list(df.columns),
    )
    above, meta = execute_analysis_plan(classify_rows(df), plan2)
    assert len(above) >= 1
    assert (meta.get("vs_mean") or {}).get("mean") is not None


def test_planner_coverage_ratio_ranking_group_compare() -> None:
    df = _budget()
    ratio_plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "비목분류",
            "groups": ["내부인건비", "연구활동비"],
            "numerator": "집행계_합계",
            "denominator": "실행예산_합계",
            "rate_name": "집행률",
            "prefer_subtotals": True,
            "interpret": False,
        },
        available_columns=list(df.columns),
        profile_name="budget",
    )
    result, _ = execute_analysis_plan(classify_rows(df), ratio_plan)
    assert "집행률" in result.columns

    rank_plan = analysis_plan_from_dict(
        {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["상품"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                },
                {"op": "sort", "by": ["매출"], "ascending": [False]},
                {"op": "limit", "n": 5},
            ]
        },
        available_columns=list(_sales().columns),
    )
    top, _ = execute_analysis_plan(classify_rows(_sales()), rank_plan)
    assert len(top) <= 5

    cmp_plan = analysis_plan_from_dict(
        {
            "operation": "group_comparison",
            "group_column": "팀",
            "groups": ["A팀", "B팀"],
            "numerator": "집행계_합계",
            "denominator": "실행예산_합계",
            "rate_name": "집행률",
            "prefer_subtotals": True,
        },
        available_columns=list(df.columns),
        profile_name="budget",
    )
    cmp, _ = execute_analysis_plan(classify_rows(df), cmp_plan)
    assert len(cmp) == 2


def test_exact_retrieval_value_match_path(monkeypatch) -> None:
    """상품코드 exact retrieval은 Planner 밖 deterministic path."""
    from core.analysis import analyzer as analyzer_mod

    df = _sales()
    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", lambda *_a, **_k: None)
    monkeypatch.setattr(
        analyzer_mod,
        "chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no pandasai")),
    )

    result, summary, meta = analyzer_mod.run_analysis(
        df,
        "상품코드 A001 찾아줘",
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        skip_aggregate_shortcuts=True,
    )
    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert meta.get("aggregation", {}).get("operation") == "value_match"
    assert "A001" in result["상품"].astype(str).tolist()


# ---------------------------------------------------------------------------
# Architecture routing
# ---------------------------------------------------------------------------


def test_system_commands_remain_deterministic() -> None:
    assert is_system_data_command("파일 요약해줘")
    assert is_system_data_command("컬럼 목록 보여줘")
    assert is_system_data_command("결측값이 있는 행 보여줘")
    assert is_system_data_command("스키마 확인해줘")
    assert is_system_data_command("품질 진단해줘")
    for prompt in (
        "파일 요약해줘",
        "컬럼 목록 보여줘",
        "결측값이 있는 행 보여줘",
        "스키마 확인해줘",
    ):
        assert is_analytical_request(prompt) is False


def test_analytical_goes_to_analysis_plan_first(monkeypatch) -> None:
    from core.analysis import analyzer as analyzer_mod

    for prompt in (
        "부서별 매출 분석",
        "A와 B 비교",
        "상위 항목",
        "평균 대비",
        "집행률",
    ):
        assert is_analytical_request(prompt) is True

    seen: list[str] = []

    def _fake_pipeline(*_a, **_k):
        seen.append("planner")
        return AnalysisPipelineResult(
            dataframe=pd.DataFrame({"부서": ["영업"], "매출": [100]}),
            reply="ok",
            plan=AnalysisPlan(steps=[], interpret=False),
            validation=ValidationReport(ok=True, issues=[]),
            meta={"aggregation": {"operation": "analysis_plan"}},
        )

    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", _fake_pipeline)
    monkeypatch.setattr(
        analyzer_mod,
        "try_legacy_simple_groupby_fallback",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no groupby")),
    )
    monkeypatch.setattr(
        analyzer_mod,
        "chat",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no pandasai")),
    )
    analyzer_mod.run_analysis(
        _sales(),
        "부서별 매출 분석",
        base_url="http://localhost",
        model="dummy",
    )
    assert seen == ["planner"]


def test_condition_and_context_not_on_single_file_analytical_path(monkeypatch) -> None:
    """Phase 5: condition/context는 single-file analytical production path에 없음."""
    from core.analysis import analyzer as analyzer_mod
    import core.analysis.analyzer as mod

    assert not hasattr(mod, "build_context_aggregate_table")
    assert not hasattr(mod, "build_groupby_aggregate_table")

    # exhausted → retrieval/groupby/pandasai only
    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", lambda *_a, **_k: None)
    monkeypatch.setattr(
        analyzer_mod,
        "try_legacy_simple_groupby_fallback",
        lambda *_a, **_k: None,
    )

    def _fake_chat(*_a, **_k):
        return pd.DataFrame({"x": [1]}), "pandasai", {"source": "pandasai"}

    monkeypatch.setattr(analyzer_mod, "chat", _fake_chat)
    _, summary, meta = analyzer_mod.run_analysis(
        _sales(),
        "복잡한 비표준 교차 피벗을 만들어줘",
        base_url="http://localhost",
        model="dummy",
    )
    assert summary == "pandasai"
    assert meta.get("source") == "pandasai"


def test_legacy_classification_documented() -> None:
    assert LEGACY_FALLBACK_CLASSIFICATION["try_condition_row_filter"] == "A"
    assert LEGACY_FALLBACK_CLASSIFICATION["build_groupby_aggregate_table"] == "B"
    assert LEGACY_FALLBACK_CLASSIFICATION["value_match"] == "C"
    assert LEGACY_FALLBACK_CLASSIFICATION["chart_fallback"] == "D"


def test_legacy_simple_groupby_fallback_wrapper() -> None:
    df = _sales()
    out = try_legacy_simple_groupby_fallback(df, "부서별 매출 합계 알려줘")
    assert out is not None
    table, summary = out
    assert isinstance(table, pd.DataFrame)
    assert len(table) >= 1


def test_chart_only_vs_analysis_plus_chart(monkeypatch) -> None:
    from core.analysis.analyzer import _is_chart_only_display
    from core.analysis import analyzer as analyzer_mod

    assert _is_chart_only_display("이 데이터를 막대그래프로 보여줘") is True
    assert _is_chart_only_display("매출이 높은 상품을 분석해서 그래프로 보여줘") is False

    seen: list[str] = []

    def _fake_pipeline(*_a, **_k):
        seen.append("planner")
        return AnalysisPipelineResult(
            dataframe=pd.DataFrame({"상품": ["A001"], "매출": [1000]}),
            reply="분석 결과",
            plan=AnalysisPlan(steps=[], interpret=False),
            validation=ValidationReport(ok=True, issues=[]),
            meta={"aggregation": {"operation": "analysis_plan"}},
        )

    monkeypatch.setattr(analyzer_mod, "try_analysis_pipeline", _fake_pipeline)
    monkeypatch.setattr(
        analyzer_mod,
        "generate_fallback_chart",
        lambda *_a, **_k: "/tmp/fake.png",
    )
    _, _, meta = analyzer_mod.run_analysis(
        _sales(),
        "매출이 높은 상품을 분석해서 그래프로 보여줘",
        base_url="http://localhost",
        model="dummy",
    )
    assert seen == ["planner"]
    assert meta.get("chart_path") == "/tmp/fake.png"


def test_force_prefs_rewrite_removed_from_api() -> None:
    import inspect
    from core.analysis.analysis_pipeline import try_analysis_pipeline
    from core.analysis.analysis_plan_builder import build_analysis_plan

    assert "enable_force_prefs_rewrite" not in inspect.signature(
        try_analysis_pipeline
    ).parameters
    assert "enable_force_prefs_rewrite" not in inspect.signature(
        build_analysis_plan
    ).parameters


def test_pipeline_success_with_correct_sales_plan() -> None:
    df = _sales()

    def good(*_a, **_k):
        return {
            "steps": [
                {
                    "op": "aggregate",
                    "group_by": ["부서"],
                    "metrics": [{"column": "매출", "fn": "sum"}],
                }
            ]
        }

    result = try_analysis_pipeline(
        "부서별 매출 합계 알려줘",
        df,
        base_url="http://localhost",
        model="dummy",
        profile_name="generic",
        chat_json_fn=good,
        chat_text_fn=lambda *_a, **_k: "",
    )
    assert result is not None
    assert result.meta.get("aggregation", {}).get("operation") == "analysis_plan"
    assert len(result.dataframe) >= 1
