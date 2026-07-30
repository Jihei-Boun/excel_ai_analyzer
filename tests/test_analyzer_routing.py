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
    resolve_filter_source,
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
    assert _is_complex_analysis("비용명별 실행예산의 합을 보여줘") is True
    assert _is_complex_analysis("연구활동비 보여줘") is False


def test_detect_aggregate_op() -> None:
    assert detect_aggregate_op("계획예산 합계 구해줘") == "sum"
    assert detect_aggregate_op("비용명별 실행예산의 합을 보여줘") == "sum"
    assert detect_aggregate_op("평균을 알려줘") == "mean"
    assert detect_aggregate_op("목록만 보여줘") is None


def test_chart_prompt_skips_aggregate_and_routes_to_plot() -> None:
    prompt = "각각 파일별로 계획예산의 종합을 막대그래프로 보여줘"
    assert _expects_plot(prompt) is True
    assert _resolve_output_type(prompt) == "plot"
    # '종합을' 안의 '합을' 오탐으로 집계 단축되면 안 됨
    assert detect_aggregate_op(prompt) is None


def test_detect_aggregate_op_does_not_match_compound_words() -> None:
    assert detect_aggregate_op("실행예산 통합표를 보여줘") is None


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


def test_build_multi_all_numeric_column_sums_by_sheet() -> None:
    """'숫자형 컬럼 합계'는 구체 컬럼명 없이도 시트별 규칙 집계로 처리한다."""
    prompt = "시트별로 숫자형 컬럼 합계를 표로 비교해줘"
    named = [
        (
            "1월",
            pd.DataFrame(
                {
                    "지역": ["서울", "부산"],
                    "수량": [2, 3],
                    "단가": [100, 200],
                    "매출": [200, 600],
                }
            ),
        ),
        (
            "2월",
            pd.DataFrame(
                {
                    "지역": ["서울"],
                    "수량": [5],
                    "단가": [300],
                    "매출": [1500],
                }
            ),
        ),
    ]
    assert is_metric_aggregate_request(prompt, named_dfs=named) is True
    result = build_multi_context_aggregate_table(named, prompt, unit_label="시트")
    assert result is not None
    table, summary = result
    assert list(table["출처파일"]) == ["1월", "2월"]
    assert set(table.columns) >= {"출처파일", "수량", "단가", "매출"}
    assert table.loc[table["출처파일"] == "1월", "매출"].iloc[0] == 800
    assert table.loc[table["출처파일"] == "2월", "매출"].iloc[0] == 1500
    assert "시트별" in summary

    from core.prompt_router import route_multi_prompt

    outcome = route_multi_prompt(
        prompt,
        named_frames=named,
        base_url="http://localhost:11434",
        model="dummy",
        context_label=None,
        filter_df=None,
        unit_label="시트",
    )
    assert outcome.dataframe is not None
    assert list(outcome.dataframe["출처파일"]) == ["1월", "2월"]
    assert "시트별숫자형" not in str(outcome.dataframe["출처파일"].iloc[0])


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
    result = build_multi_context_aggregate_table(
        filtered,
        prompt,
        context_label="내부인건비",
    )
    assert result is not None
    table, _ = result
    assert table["실행예산_합계"].tolist() == [100, 200]
    assert table["출처파일"].tolist() == [
        "내부인건비 · 4예실.xlsx",
        "내부인건비 · 5예실.xlsx",
    ]

    from core.chart_utils import _simplify_axis_labels

    short, context = _simplify_axis_labels(table["출처파일"].astype(str).tolist())
    assert context == "내부인건비"
    assert short == ["4예실.xlsx", "5예실.xlsx"]

    path = generate_fallback_chart(table, prompt)
    assert path is not None


def test_chart_follow_up_uses_aggregate_table_not_raw_codes() -> None:
    """'차트로 보여줘' 후속 요청은 집계 표(계획예산)를 쓰고 비용명 코드를 쓰지 않는다."""
    from core.analyzer import build_groupby_aggregate_table
    from core.chart_utils import _pick_chart_columns

    raw = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "내부인건비", "연구활동비", "간접비"],
            "비용명": [121, 201, 146, 123],
            "비용명_2": ["내부인건비", "계약직내부인건비", "연구용SW", "간접비"],
            "계획예산": [7_000_000, 10_914_000, 4_800_000, 10_839_000],
        }
    )
    prior = "비목분류별 계획예산의 합을 보여줘"
    grouped = build_groupby_aggregate_table(raw, prior)
    assert grouped is not None
    table, _ = grouped

    cat, num = _pick_chart_columns(table, prior)
    assert cat == "비목분류"
    assert num == "계획예산"
    assert table["계획예산"].max() > 1_000_000

    # 원본+차트-only 프롬프트면 코드 컬럼을 고르지 않도록 집계 표를 써야 함
    wrong_cat, wrong_num = _pick_chart_columns(raw, "차트로 보여줘")
    assert wrong_num == "계획예산" or wrong_num != "비용명"


def test_total_row_filter_still_works_when_explicitly_requested() -> None:
    df = _budget_sample_df()
    filtered = _filter_by_mentioned_value(df, "합계 행만 보여줘")
    assert filtered is not None
    assert len(filtered) == 1
    assert filtered.iloc[0]["비목분류"] == "합 계"


def test_partial_value_match_ingunbi() -> None:
    """'인건비'처럼 짧은 표현도 '내부인건비' 행에 매칭된다."""
    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "내부인건비", "연구활동비"],
            "비용명_2": ["내부인건비", "계약직내부인건비", "국내여비"],
            "집행계_합계": [100, 50, 10],
        }
    )
    filtered = _filter_by_mentioned_value(df, "인건비만 보여줘")
    assert filtered is not None
    assert len(filtered) == 2
    assert set(filtered["비용명_2"].tolist()) == {"내부인건비", "계약직내부인건비"}


def test_resolve_filter_source_falls_back_to_full_data() -> None:
    """이전 필터에 없는 값을 요청하면 원본 데이터로 전환한다."""
    full = pd.DataFrame(
        {
            "비용명_2": ["국내여비", "내부인건비", "계약직내부인건비", "회의비"],
            "비목분류": ["연구활동비", "내부인건비", "내부인건비", "연구활동비"],
        }
    )
    filtered = full[full["비용명_2"] == "국내여비"].reset_index(drop=True)

    source, reset = resolve_filter_source(full, filtered, "인건비만 보여줘")
    assert reset is True
    assert len(source) == len(full)

    # 원본에서 실제로 인건비 행을 찾을 수 있어야 한다
    found = _filter_by_mentioned_value(source, "인건비만 보여줘")
    assert found is not None
    assert len(found) == 2

    # 집계 요청은 필터를 유지한다
    source2, reset2 = resolve_filter_source(
        full,
        filtered,
        "집행계_합계 합계 구해줘",
    )
    assert reset2 is False
    assert len(source2) == 1


def test_groupby_preserves_file_order() -> None:
    """그룹 집계 결과는 파일 등장 순서를 유지한다 (가나다/금액 정렬 금지)."""
    from core.analyzer import build_groupby_aggregate_table

    df = pd.DataFrame(
        {
            "비목분류": [
                "내부인건비",
                "연구활동비",
                "간접비",
                "기타",
                "내부흡수액",
                "외부유출액",
                "합 계",
            ],
            "계획예산": [100, 200, 50, 30, 100, 280, 380],
        }
    )
    # '합계' 단어가 없어도 X별 Y 요청이면 파일 순서로 합산한다.
    result = build_groupby_aggregate_table(
        df,
        "비목분류별 계획예산을 알려줘",
        use_budget_profile=True,
    )
    assert result is not None
    table, _ = result
    assert table["비목분류"].tolist() == ["내부인건비", "연구활동비", "간접비", "기타"]


def test_groupby_without_budget_profile_keeps_footer_labels() -> None:
    """예산 표 모드 OFF면 footer 라벨도 그룹에 포함된다 (합계/소계만 제외)."""
    from core.analyzer import build_groupby_aggregate_table

    df = pd.DataFrame(
        {
            "비목분류": [
                "내부인건비",
                "연구활동비",
                "내부흡수액",
                "외부유출액",
                "합 계",
            ],
            "계획예산": [100, 200, 100, 280, 380],
        }
    )
    result = build_groupby_aggregate_table(
        df,
        "비목분류별 계획예산을 알려줘",
        use_budget_profile=False,
    )
    assert result is not None
    table, _ = result
    assert table["비목분류"].tolist() == [
        "내부인건비",
        "연구활동비",
        "내부흡수액",
        "외부유출액",
    ]


def test_groupby_execution_total_not_sum_expense_codes() -> None:
    """'비용명별 집행계 합계'는 비용명 코드(121+201)가 아니라 집행계를 합산한다."""
    from core.analyzer import build_groupby_aggregate_table, find_mentioned_numeric_columns

    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "내부인건비"],
            "비용명": [121, 201],
            "비용명_2": ["내부인건비", "계약직내부인건비"],
            "집행계_합계": [10_990_230, 5_523_600],
            "실행예산_합계": [22_828_822, 17_849_160],
        }
    )
    prompt = "비용명별 집행계 합계를 알려줘"
    assert find_mentioned_numeric_columns(df, prompt) == ["집행계_합계"]

    result = build_groupby_aggregate_table(df, prompt)
    assert result is not None
    table, summary = result
    assert list(table.columns) == ["비용명_2", "집행계_합계"]
    assert table["집행계_합계"].tolist() == [10_990_230, 5_523_600]
    assert 322 not in table["집행계_합계"].tolist()
    assert "10,990,230" in summary


def test_groupby_shortcut_skips_topn_ranking_prompt() -> None:
    """'상위 N개 ...'는 강제 그룹합이 아니라 일반 분석 경로로 보낸다."""
    from core.analyzer import build_groupby_aggregate_table

    df = pd.DataFrame(
        {
            "지역": ["서울", "서울", "부산", "대전"],
            "매출": [100000, 200000, 300000, 400000],
        }
    )
    result = build_groupby_aggregate_table(df, "상위 3개 매출 지역 보여줘")
    assert result is None
