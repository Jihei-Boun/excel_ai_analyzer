"""분석 라우팅·필터 요약 단위 테스트 (LLM 호출 없음)."""

from __future__ import annotations

import pandas as pd

from core.analyzer import (
    _expects_dataframe,
    _expects_plot,
    _filter_by_mentioned_value,
    _is_complex_analysis,
    _is_list_request,
    _resolve_output_type,
    build_filter_summary,
    build_multi_context_aggregate_table,
    detect_aggregate_op,
    extract_matched_detail,
    is_metric_aggregate_request,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "예산과목": ["연구활동비", "인건비", "연구활동비"],
            "금액": [100, 200, 50],
        }
    )


def test_resolve_output_type_prefers_plot_over_dataframe() -> None:
    assert _expects_plot("카테고리별 매출 차트를 보여줘") is True
    assert _resolve_output_type("카테고리별 매출 차트를 보여줘") == "plot"
    assert _resolve_output_type("연구활동비 목록 보여줘") == "dataframe"
    assert _resolve_output_type("합계는?") is None


def test_list_and_complex_routing_flags() -> None:
    assert _is_list_request("항목 리스트로 뽑아줘") is True
    assert _expects_dataframe("표로 보여줘") is True
    assert _is_complex_analysis("상위 10개 정렬") is True
    assert _is_complex_analysis("연구활동비 보여줘") is False


def test_detect_aggregate_op() -> None:
    assert detect_aggregate_op("계획예산 합계 구해줘") == "sum"
    assert detect_aggregate_op("평균을 알려줘") == "mean"
    assert detect_aggregate_op("목록만 보여줘") is None


def test_chart_prompt_skips_aggregate_and_routes_to_plot() -> None:
    prompt = "각각 파일별로 계획예산의 종합을 막대그래프로 보여줘"
    assert _expects_plot(prompt) is True
    assert _resolve_output_type(prompt) == "plot"
    # '종합을' 안의 '합을' 오탐으로 집계 단축되면 안 됨
    assert detect_aggregate_op(prompt) is None


def test_filter_by_mentioned_value_and_detail() -> None:
    df = _sample_df()
    filtered = _filter_by_mentioned_value(df, "연구활동비 행을 보여줘")
    assert filtered is not None
    assert len(filtered) == 2
    assert set(filtered["예산과목"].unique()) == {"연구활동비"}

    detail = extract_matched_detail(df, "연구활동비 보여줘")
    assert detail == ("예산과목", "연구활동비")


def test_filter_numeric_code_value() -> None:
    """숫자형 비용명 코드(121)도 값 필터로 잡힌다."""
    df = pd.DataFrame(
        {
            "비용명": [121, 201, 121, 142],
            "항목": ["노트북", "인건비", "모니터", "출장비"],
            "예산잔액": [1000, 2000, 500, 300],
        }
    )
    prompt = "비용명이 121인 것만 파일별로 리스트로 뽑아줘"
    filtered = _filter_by_mentioned_value(df, prompt)
    assert filtered is not None
    assert len(filtered) == 2
    assert set(filtered["비용명"].unique()) == {121}
    assert set(filtered["항목"].tolist()) == {"노트북", "모니터"}

    detail = extract_matched_detail(df, prompt)
    assert detail is not None
    assert detail[0] == "비용명"
    assert detail[1] == "121"


def test_run_analysis_filters_before_list_seed(monkeypatch) -> None:
    """리스트 요청이라도 값 필터가 리스트 시드보다 먼저 적용된다."""
    from core import analyzer as analyzer_mod

    df = pd.DataFrame(
        {
            "비용명": [121, 201, 142],
            "항목": ["A", "B", "C"],
        }
    )
    prompt = "비용명이 121인 것만 리스트로 뽑아줘"

    def _fail_chat(*_args, **_kwargs):
        raise AssertionError("값 필터로 충분하면 LLM chat을 호출하면 안 됨")

    monkeypatch.setattr(analyzer_mod, "chat", _fail_chat)
    result, summary, _meta = analyzer_mod.run_analysis(
        df,
        prompt,
        base_url="http://localhost",
        model="dummy",
    )
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 1
    assert int(result.iloc[0]["비용명"]) == 121
    assert "값 일치" in summary


def test_build_filter_summary_includes_label_rows_and_column() -> None:
    df = _sample_df()
    filtered = df[df["예산과목"] == "연구활동비"].reset_index(drop=True)
    summary = build_filter_summary("연구활동비 보여줘", filtered, df)
    assert summary is not None
    assert "연구활동비" in summary
    assert "2행" in summary
    assert "예산과목 일치" in summary


def test_build_filter_summary_includes_file_count() -> None:
    df = pd.DataFrame(
        {
            "출처파일": ["a.xlsx", "b.xlsx"],
            "항목": ["연구활동비", "연구활동비"],
        }
    )
    summary = build_filter_summary("연구활동비 목록", df, df)
    assert summary is not None
    assert "2개 파일" in summary


def _budget_sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "비목분류": ["내부인건비", "내부인건비", "합 계"],
            "비용명": [121, 201, None],
            "계획예산": [43_732_000, 34_465_000, 394_154_242],
            "실행예산_합계": [10_000, 20_000, 30_000],
        }
    )


def test_metric_aggregate_does_not_filter_total_label_rows() -> None:
    """'실행예산_합계' 컬럼명의 '합계'를 합계 행 필터로 오인하지 않는다."""
    df = _budget_sample_df()
    prompt = "실행예산_합계의 총 합을 파일별로 보여줘"
    assert is_metric_aggregate_request(prompt, df) is True
    assert _filter_by_mentioned_value(df, prompt) is None


def test_build_multi_context_aggregate_table_sums_by_file() -> None:
    prompt = "실행예산_합계의 총 합을 파일별로 보여줘"
    named = [
        ("4예실대비표.xlsx", _budget_sample_df()),
        (
            "5예실대비표.xlsx",
            pd.DataFrame(
                {
                    "비목분류": ["연구활동비", "합 계"],
                    "비용명": [301, None],
                    "계획예산": [50_000_000, 50_000_000],
                    "실행예산_합계": [5_000, 5_000],
                }
            ),
        ),
    ]
    result = build_multi_context_aggregate_table(named, prompt)
    assert result is not None
    table, summary = result
    assert len(table) == 2
    assert list(table.columns) == ["출처파일", "실행예산_합계"]
    assert table.loc[table["출처파일"] == "4예실대비표.xlsx", "실행예산_합계"].iloc[0] == 30_000
    assert table.loc[table["출처파일"] == "5예실대비표.xlsx", "실행예산_합계"].iloc[0] == 5_000
    assert "파일별" in summary


def test_chart_from_aggregate_table_uses_same_totals() -> None:
    """집계 표를 차트로 그릴 때 표와 동일한 파일별 합계를 사용한다."""
    from core.chart_utils import generate_fallback_chart

    prompt = "파일별로 실행예산_합계의 총 합을 차트로 보여줘"
    named = [
        (
            "4예실.xlsx",
            pd.DataFrame(
                {
                    "비목분류": ["내부인건비", "연구활동비", "합 계"],
                    "실행예산_합계": [100, 900, 1_000],
                }
            ),
        ),
        (
            "5예실.xlsx",
            pd.DataFrame(
                {
                    "비목분류": ["내부인건비", "연구활동비", "합 계"],
                    "실행예산_합계": [200, 800, 1_000],
                }
            ),
        ),
    ]
    filtered = [
        (name, frame[frame["비목분류"] == "내부인건비"].reset_index(drop=True))
        for name, frame in named
    ]
    result = build_multi_context_aggregate_table(filtered, prompt)
    assert result is not None
    table, _ = result
    assert table["실행예산_합계"].tolist() == [100, 200]

    path = generate_fallback_chart(table, prompt)
    assert path is not None


def test_total_row_filter_still_works_when_explicitly_requested() -> None:
    df = _budget_sample_df()
    filtered = _filter_by_mentioned_value(df, "합계 행만 보여줘")
    assert filtered is not None
    assert len(filtered) == 1
    assert filtered.iloc[0]["비목분류"] == "합 계"
