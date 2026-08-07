"""분석 라우팅·필터 요약 단위 테스트 (LLM 호출 없음)."""

from __future__ import annotations

import pandas as pd

from core.aggregates import (
    build_context_aggregate_table,
    build_groupby_aggregate_table,
    build_multi_context_aggregate_table,
)
from core.schema.column_match import (
    find_mentioned_numeric_columns,
    looks_like_code_metric_column,
    wants_all_numeric_metrics,
    wants_first_numeric_metric,
)
from core.routing.prompt_intent import (
    detect_aggregate_op,
    expects_dataframe,
    expects_plot,
    is_complex_analysis,
    is_list_request,
    resolve_output_type,
    wants_full_dataset,
)
from core.filter.value_filter import (
    _filter_by_mentioned_value,
    build_filter_summary,
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
    assert expects_plot("카테고리별 매출 차트를 보여줘") is True
    assert resolve_output_type("카테고리별 매출 차트를 보여줘") == "plot"
    assert resolve_output_type("연구활동비 목록 보여줘") == "dataframe"
    assert resolve_output_type("합계는?") is None


def test_list_and_complex_routing_flags() -> None:
    assert is_list_request("항목 리스트로 뽑아줘") is True
    assert expects_dataframe("표로 보여줘") is True
    assert is_complex_analysis("상위 10개 정렬") is True
    assert is_complex_analysis("비용명별 실행예산의 합을 보여줘") is True
    assert is_complex_analysis("연구활동비 보여줘") is False


def test_detect_aggregate_op() -> None:
    assert detect_aggregate_op("계획예산 합계 구해줘") == "sum"
    assert detect_aggregate_op("비용명별 실행예산의 합을 보여줘") == "sum"
    assert detect_aggregate_op("평균을 알려줘") == "mean"
    assert detect_aggregate_op("목록만 보여줘") is None
    assert detect_aggregate_op("Sum the first numeric column") == "sum"


def test_first_numeric_column_sum_not_all_metrics() -> None:
    """'Sum the first numeric column'은 전 수치열이 아니라 첫 수치열만 합산한다."""
    from core.profile_loader import use_profile

    df = pd.DataFrame(
        {
            "비목분류": ["A", "B"],
            "계획예산": [100, 200],
            "실행예산_합계": [10, 20],
            "당년도집행": [1, 2],
        }
    )
    prompt = "Sum the first numeric column"
    assert wants_first_numeric_metric(prompt) is True
    assert wants_all_numeric_metrics(prompt) is False
    assert find_mentioned_numeric_columns(df, prompt) == ["계획예산"]

    with use_profile("generic_en"):
        result = build_context_aggregate_table(
            df, prompt, profile_name="generic_en"
        )
    assert result is not None
    table, summary = result
    assert list(table.columns) == ["", "계획예산"]
    assert int(table.iloc[0]["계획예산"]) == 300
    assert "실행예산" not in summary
    assert "Total" in summary or "sum" in summary.lower()
    assert "총합" not in summary


def test_sum_excludes_budget_footer_rows_even_on_generic() -> None:
    """내부흡수액·외부유출액은 세부 합과 중복이므로 합산에서 제외한다."""
    from core.profile_loader import use_profile

    df = pd.DataFrame(
        {
            "비목분류": [
                "내부인건비",
                "연구활동비",
                "소 계",
                "내부흡수액",
                "외부유출액",
                "합 계",
            ],
            "비용명": [121, 201, None, None, None, None],
            "계획예산": [100, 200, 300, 100, 200, 300],
        }
    )
    with use_profile("generic_en"):
        assert looks_like_code_metric_column(df, "비용명") is True
        assert find_mentioned_numeric_columns(
            df, "Sum the first numeric column"
        ) == ["계획예산"]
        from core.pai.pandasai_config import (
            footer_labels_present_in_frame,
            sum_metric_excluding_totals,
        )

        footers = footer_labels_present_in_frame(df, profile_name="generic_en")
        total = sum_metric_excluding_totals(
            df, "계획예산", footer_labels=footers
        )
        result = build_context_aggregate_table(
            df,
            "Sum the first numeric column",
            profile_name="generic_en",
        )
    assert total == 300  # 100+200, footer/소계/합계 제외
    assert result is not None
    assert int(result[0].iloc[0]["계획예산"]) == 300


def test_chart_prompt_skips_aggregate_and_routes_to_plot() -> None:
    prompt = "각각 파일별로 계획예산의 종합을 막대그래프로 보여줘"
    assert expects_plot(prompt) is True
    assert resolve_output_type(prompt) == "plot"
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
    from core.analysis import analyzer as analyzer_mod

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

    from core.routing.prompt_router import route_multi_prompt

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
    from core.display.chart_utils import generate_fallback_chart

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

    from core.display.chart_utils import _simplify_axis_labels

    short, context = _simplify_axis_labels(table["출처파일"].astype(str).tolist())
    assert context == "내부인건비"
    assert short == ["4예실.xlsx", "5예실.xlsx"]

    path = generate_fallback_chart(table, prompt)
    assert path is not None


def test_chart_follow_up_uses_aggregate_table_not_raw_codes() -> None:
    """'차트로 보여줘' 후속 요청은 집계 표(계획예산)를 쓰고 비용명 코드를 쓰지 않는다."""
    from core.display.chart_utils import _pick_chart_columns
    from core.profile_loader import use_profile

    raw = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "내부인건비", "연구활동비", "간접비"],
            "비용명": [121, 201, 146, 123],
            "비용명_2": ["내부인건비", "계약직내부인건비", "연구용SW", "간접비"],
            "계획예산": [7_000_000, 10_914_000, 4_800_000, 10_839_000],
        }
    )
    prior = "비목분류별 계획예산의 합을 보여줘"
    with use_profile("budget"):
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


def test_wants_full_dataset_phrases() -> None:
    assert wants_full_dataset("전체데이터에서 당년도집행과 가집행금액의 상관관계를 분석해줘")
    assert wants_full_dataset("전체 데이터에서 상관관계를 분석해줘")
    assert wants_full_dataset("필터 초기화 후 상관관계 분석해줘")
    assert wants_full_dataset("원본에서 합계 구해줘")
    assert wants_full_dataset("당년도집행과 가집행금액의 상관관계를 분석해줘") is False
    assert wants_full_dataset("잔액이 가장 큰 비용명 뽑아줘") is False


def test_resolve_filter_source_resets_on_full_dataset_request() -> None:
    """'전체 데이터에서' 요청은 이전 필터를 무시하고 원본으로 분석한다."""
    full = pd.DataFrame(
        {
            "비용명_2": ["국내여비", "내부인건비", "계약직내부인건비", "회의비"],
            "당년도집행": [100, 200, 50, 0],
            "가집행금액": [10, 0, 5, 0],
        }
    )
    filtered = full.head(1).reset_index(drop=True)

    source, reset = resolve_filter_source(
        full,
        filtered,
        "전체데이터에서 당년도집행과 가집행금액의 상관관계를 분석해줘",
    )
    assert reset is True
    assert len(source) == len(full)

    # 필터가 없으면 reset 신호는 False (불필요한 안내 문구 방지)
    source2, reset2 = resolve_filter_source(
        full,
        None,
        "전체 데이터에서 상관관계를 분석해줘",
    )
    assert reset2 is False
    assert len(source2) == len(full)

    # 집계 요청이어도 '전체 데이터'가 있으면 필터를 해제한다
    source3, reset3 = resolve_filter_source(
        full,
        filtered,
        "전체 데이터에서 당년도집행 합계 구해줘",
    )
    assert reset3 is True
    assert len(source3) == len(full)


def test_resolve_filter_source_resets_when_groupby_collapsed() -> None:
    """결측행 등으로 그룹이 1명만 남으면 '담당자별' 집계는 원본으로 돌린다."""
    full = pd.DataFrame(
        {
            "담당자": ["김지혜", "박민수", "이서연", "정우진", "최유나"],
            "집행_금액": [1, 2, 3, 4, None],
        }
    )
    filtered = full[full["담당자"] == "최유나"].reset_index(drop=True)
    source, reset = resolve_filter_source(
        full,
        filtered,
        "담당자별 집행 금액을 집계해줘",
    )
    assert reset is True
    assert len(source) == len(full)


def test_groupby_preserves_file_order() -> None:
    """그룹 집계 결과는 파일 등장 순서를 유지한다 (가나다/금액 정렬 금지)."""

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
        profile_name="budget",
    )
    assert result is not None
    table, _ = result
    assert table["비목분류"].tolist() == ["내부인건비", "연구활동비", "간접비", "기타"]


def test_groupby_without_budget_profile_keeps_footer_labels() -> None:
    """예산 표 모드 OFF면 footer 라벨도 그룹에 포함된다 (합계/소계만 제외)."""

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
        profile_name="generic",
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

    df = pd.DataFrame(
        {
            "지역": ["서울", "서울", "부산", "대전"],
            "매출": [100000, 200000, 300000, 400000],
        }
    )
    result = build_groupby_aggregate_table(df, "상위 3개 매출 지역 보여줘")
    assert result is None


def test_pivot_request_does_not_use_shortcut(monkeypatch) -> None:
    """피벗 질의는 단축 경로 없이 LLM chat으로 보낸다."""
    import core.analysis.analyzer as analyzer_mod

    df = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "", "소 계"],
            "비용명_2": ["내부인건비", "계약직내부인건비", ""],
            "집행계_합계": [10, 20, 30],
        }
    )
    called: dict[str, object] = {}

    def _fake_chat(frame, prompt, **kwargs):
        called["prompt"] = prompt
        return (
            pd.DataFrame({"비목분류": ["내부인건비"], "내부인건비": [10]}),
            "ok",
            {},
        )

    monkeypatch.setattr(analyzer_mod, "chat", _fake_chat)
    result, _summary, _meta = analyzer_mod.run_analysis(
        df,
        "비목분류와 비용명을 교차해서 집행계를 피벗해줘",
        base_url="http://localhost",
        model="dummy",
    )
    assert called.get("prompt") is not None
    assert isinstance(result, pd.DataFrame)


def test_schema_hints_expose_compound_metric_without_rewrite() -> None:
    """복합 지표는 rewrite하지 않고 힌트로만 노출한다."""
    from core.schema.column_match import resolve_metric_column
    from core.profile_loader import use_profile
    from core.schema.schema_hints import build_schema_hints, format_schema_hints_for_prompt

    df = pd.DataFrame(
        {
            "집행계_이월집행": [1, 2],
            "집행계_당해집행": [3, 4],
            "집행계_합계": [4, 6],
        }
    )
    with use_profile("budget"):
        assert resolve_metric_column(df, "집행계") == "집행계_이월집행"

        hints = build_schema_hints(df)
        group = hints["__metric_group__집행계"]
        assert "집행계_합계" in group["total_candidates"]
        text = format_schema_hints_for_prompt(df, hints)
        assert "집행계_합계" in text
        assert "강제 규칙이 아닙니다" in text


def test_hierarchical_fill_only_on_analysis_copy() -> None:
    """원본은 유지하고 분석용 복사본만 forward-fill한다."""
    from core.schema.schema_hints import prepare_analysis_frame

    raw = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "", "소 계", "연구활동비", "", "소 계"],
            "비용명_2": ["A", "B", "", "C", "D", ""],
            "집행계_합계": [10, 20, 30, 40, 50, 90],
        }
    )
    analysis = prepare_analysis_frame(raw)
    assert raw["비목분류"].tolist() == ["내부인건비", "", "소 계", "연구활동비", "", "소 계"]
    assert analysis["비목분류"].tolist() == [
        "내부인건비",
        "내부인건비",
        "소 계",
        "연구활동비",
        "연구활동비",
        "소 계",
    ]


def test_code_guardrails_flag_pivot_without_forcing_rewrite() -> None:
    from core.code_guardrails import (
        build_regeneration_prompt,
        extract_aggregation_meta,
        format_aggregation_notice,
        inspect_generated_code,
        validate_pandasai_result,
    )

    # 성공한 pivot() 자체는 하드 이슈가 아니다.
    issues = inspect_generated_code(
        "result = df.pivot(index='비목분류', columns='비용명_2', values='집행계_합계')",
        available_columns=["비목분류", "비용명_2", "집행계_합계"],
    )
    assert not any("pivot_table" in issue for issue in issues)

    # reshape 실패 안내는 재생성 프롬프트에 명시적으로 넣는다.
    regen = build_regeneration_prompt(
        "사용자 요청",
        [
            "중복 키로 pivot/unstack가 실패했습니다. "
            "소계·합계 행 제외와 pivot_table(aggfunc=...) 사용을 검토하세요."
        ],
    )
    assert "자동 치환하지 말고" in regen
    assert "pivot_table" in regen

    missing = inspect_generated_code(
        "result = df['없는열']",
        available_columns=["비목분류", "집행계_합계"],
    )
    assert any("존재하지 않는 컬럼" in issue for issue in missing)

    no_agg = inspect_generated_code(
        "result = df.pivot_table(index='비목분류', values='집행계_합계')",
        available_columns=["비목분류", "집행계_합계"],
    )
    assert any("aggfunc" in issue for issue in no_agg)

    hard, soft = validate_pandasai_result(pd.DataFrame())
    assert hard == []
    assert soft

    hard_nan, _soft = validate_pandasai_result(pd.DataFrame({"a": [None, None]}))
    assert hard_nan

    agg = extract_aggregation_meta(
        "out = df.groupby('비목분류')['집행계_합계'].sum()"
    )
    assert agg.get("aggregation_used") == "sum"
    assert "비목분류" in (agg.get("group_keys") or [])
    notice = format_aggregation_notice(agg)
    assert notice is not None
    assert "비목분류" in notice


def test_code_columns_are_stringified_on_analysis_copy_only() -> None:
    """비용명 코드는 분석 복사본에서만 '121' 문자열로 바뀐다."""
    from core.profile_loader import use_profile
    from core.schema.schema_hints import prepare_analysis_frame

    raw = pd.DataFrame(
        {
            "비용명": [121.0, 201.0],
            "비용명_2": ["내부인건비", "계약직내부인건비"],
            "집행계_합계": [10.0, 20.0],
        }
    )
    with use_profile("budget"):
        analysis = prepare_analysis_frame(raw)
    assert raw["비용명"].tolist() == [121.0, 201.0]
    assert analysis["비용명"].tolist() == ["121", "201"]


def test_friendly_error_explains_code_key_error() -> None:
    from core.pai.pandasai_config import _friendly_error
    from core.profile_loader import use_profile

    with use_profile("budget"):
        message = _friendly_error(KeyError("121.0"))
    assert "비용명" in message or "코드" in message
    assert "121" in message

    with use_profile("generic"):
        generic_msg = _friendly_error(KeyError("121.0"))
    assert "코드" in generic_msg
    assert "비용명_2" not in generic_msg


def test_near_diagonal_sparse_pivot_triggers_axis_swap_issue() -> None:
    """대각선 sparse 피벗은 축 교체 재생성 이슈로 잡는다."""
    from core.code_guardrails import (
        is_near_diagonal_sparse_pivot,
        validate_pandasai_result,
    )

    # 비용명 행 × 비목분류 열 (잘못된 축) — 행마다 값 1개
    bad = pd.DataFrame(
        {
            "간접비": [None, 5_419_500, None, None, None],
            "기타": [None, None, None, None, 0],
            "내부인건비": [10_990_230, None, None, None, None],
            "연구수당": [None, None, None, 0, None],
            "연구시설장비비": [None, None, 2_167_000, None, None],
            "연구재료비": [None, None, None, None, None],
            "연구활동비": [None, None, None, None, None],
        }
    )
    # 연구활동비 행을 하나 더 채워 패턴 유지
    bad.loc[3, "연구활동비"] = 2_025_169
    assert is_near_diagonal_sparse_pivot(bad) is True

    hard, soft = validate_pandasai_result(
        bad,
        code="result = df.pivot_table(index='비용명_2', columns='비목분류', "
        "values='집행계_합계', aggfunc='sum')",
        user_prompt="비목분류와 비용명을 교차해서 집행계를 피벗해줘",
    )
    assert soft == []
    assert any("대각선" in issue for issue in hard)
    assert any("index='비목분류'" in issue for issue in hard)
    assert any("columns='비용명'" in issue for issue in hard)

    # 올바른 축: 비목분류 행 × 비용명 열 — 한 행에 여러 값
    good = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비"],
            "내부인건비": [10_990_230, 0],
            "계약직내부인건비": [5_523_600, 0],
            "연구용SW활용비": [0, 2_025_169],
            "국내여비": [0, 473_960],
        }
    )
    assert is_near_diagonal_sparse_pivot(good) is False
    hard_good, _ = validate_pandasai_result(
        good,
        code="result = df.pivot_table(index='비목분류', columns='비용명_2', "
        "values='집행계_합계', aggfunc='sum')",
        user_prompt="비목분류와 비용명을 교차해서 집행계를 피벗해줘",
    )
    assert not any("대각선" in issue for issue in hard_good)


def test_broken_pivot_row_axis_triggers_regeneration_issue() -> None:
    """행 축이 비거나 금액처럼 보이면 재생성 이슈로 잡는다."""
    from core.code_guardrails import has_broken_pivot_row_axis, validate_pandasai_result

    broken = pd.DataFrame(
        {
            "": [None, None, None, 16_409_730, None, 11_046_239],
            "간접비": [5_419_500, None, None, None, None, None],
            "내부인건비": [None, 10_990_230, None, None, None, None],
            "연구용SW활용비": [None, None, 2_025_169, None, None, None],
            "재료비": [None, None, None, None, 355_510, None],
        }
    )
    assert has_broken_pivot_row_axis(broken) is True
    hard, _ = validate_pandasai_result(
        broken,
        code="result = df.pivot_table(index='비목분류', columns='비용명_2', "
        "values='집행계_합계', aggfunc='sum').reset_index()",
        user_prompt="비목분류와 비용명을 교차해서 집행계를 피벗해줘",
    )
    assert any("행 축" in issue or "왼쪽 열" in issue for issue in hard)

    ok = pd.DataFrame(
        {
            "비목분류": ["내부인건비", "연구활동비", "간접비"],
            "내부인건비": [10_990_230, 0, 0],
            "연구용SW활용비": [0, 2_025_169, 0],
            "간접비": [0, 0, 5_419_500],
        }
    )
    assert has_broken_pivot_row_axis(ok) is False
