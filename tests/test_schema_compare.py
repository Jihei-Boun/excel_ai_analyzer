"""스키마·메타 규칙 경로 단위 테스트."""

from __future__ import annotations

import pandas as pd

from core.prompt_intent import is_list_request
from core.prompt_router import route_multi_prompt, route_single_prompt
from core.result_format import expects_list_display
from core.schema_compare import (
    build_schema_compare_table,
    build_schema_outcome,
    is_schema_request,
    schema_kind,
)


def _sales_frames() -> list[tuple[str, pd.DataFrame]]:
    cols = ["판매일", "지역", "상품", "담당자", "수량", "단가", "매출", "결제수단"]
    jan = pd.DataFrame([{c: 1 if c in ("수량", "단가", "매출") else "x" for c in cols}] * 15)
    feb = pd.DataFrame([{c: 2 if c in ("수량", "단가", "매출") else "y" for c in cols}] * 15)
    mar = pd.DataFrame([{c: 3 if c in ("수량", "단가", "매출") else "z" for c in cols}] * 10)
    return [("1월", jan), ("2월", feb), ("3월", mar)]


def test_is_schema_request_for_sheet_compare() -> None:
    assert is_schema_request("각 시트의 행 수와 컬럼 목록을 비교해줘") is True
    assert is_schema_request("시트 간 공통 컬럼을 알려줘") is True
    assert is_schema_request("각 컬럼의 데이터 타입과 결측치 개수를 알려줘") is True
    assert is_schema_request("각 파일의 행 수와 컬럼 목록을 비교해줘") is True


def test_is_schema_request_excludes_analysis() -> None:
    assert is_schema_request("지역별 매출 합계를 구해줘") is False
    assert is_schema_request("시트별로 숫자형 컬럼 합계를 표로 비교해줘") is False
    assert is_schema_request("결제수단 리스트로 뽑아줘") is False
    assert is_schema_request("범주형 컬럼별 행 개수를 표로 보여줘") is False
    assert is_schema_request("지역별 매출 차트로 보여줘") is False
    assert is_schema_request("결측값이 있는 행만 보여줘") is False


def test_route_missing_rows_shows_rows_not_schema() -> None:
    df = pd.DataFrame(
        {
            "항목_코드": ["A-001", "A-006"],
            "비용_명": ["연구활동비", "인쇄·제본비"],
            "집행_금액": [1280000.0, None],
            "집행_일자": pd.to_datetime(["2026-07-03", None]),
        }
    )
    outcome = route_single_prompt(
        "결측값이 있는 행만 보여줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost:11434",
        model="dummy",
    )
    assert outcome.dataframe is not None
    assert len(outcome.dataframe) == 1
    assert outcome.dataframe.iloc[0]["항목_코드"] == "A-006"
    assert "결측값이 있는 행" in outcome.reply
    assert "데이터 타입" not in outcome.reply
    assert outcome.keep_as_filter is False
    assert outcome.replace_selection is False


def test_resolve_filter_resets_for_collapsed_groupby() -> None:
    from core.value_filter import resolve_filter_source

    full = pd.DataFrame(
        {
            "담당자": ["김지혜", "박민수", "최유나"],
            "집행_금액": [100, 200, None],
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


def test_column_list_is_not_data_list() -> None:
    prompt = "각 시트의 행 수와 컬럼 목록을 비교해줘"
    assert is_list_request(prompt) is False
    assert expects_list_display(prompt) is False
    assert expects_list_display("결제수단 리스트로 뽑아줘") is True


def test_schema_kind() -> None:
    assert schema_kind("공통 컬럼을 알려줘") == "common"
    assert schema_kind("데이터 타입과 결측치") == "dtypes"
    assert schema_kind("행 수와 컬럼 목록 비교") == "compare"
    assert schema_kind("각 컬럼이 어떤 의미인지 추측해서 설명해줘") == "meanings"
    assert schema_kind("숫자 컬럼과 문자 컬럼을 구분해서 보여줘") == "type_groups"


def test_is_schema_request_for_meanings_and_type_groups() -> None:
    assert is_schema_request("각 컬럼이 어떤 의미인지 추측해서 설명해줘") is True
    assert is_schema_request("숫자 컬럼과 문자 컬럼을 구분해서 보여줘") is True
    assert is_schema_request("숫자형 컬럼과 문자형 컬럼을 나눠줘") is True


def test_column_meanings_outcome() -> None:
    df = pd.DataFrame(
        {
            "항목_코드": ["A-001"],
            "비용_명": ["재료비"],
            "실행_예산": [100],
            "집행_금액": [80],
            "담당자": ["김"],
            "집행_일자": pd.to_datetime(["2026-07-01"]),
            "비고": ["정상"],
        }
    )
    reply, table = build_schema_outcome(
        "각 컬럼이 어떤 의미인지 추측해서 설명해줘",
        [("샘플", df)],
        use_budget_profile=True,
    )
    assert "추정" in reply
    assert table is not None
    assert list(table.columns) == ["컬럼", "추정 의미"]
    meanings = dict(zip(table["컬럼"], table["추정 의미"], strict=True))
    assert "식별자" in meanings["항목_코드"]
    assert "금액" in meanings["집행_금액"]
    assert "날짜" in meanings["집행_일자"]
    assert "예산" in meanings["실행_예산"]


def test_column_meanings_generic_avoids_budget_wording() -> None:
    from core.schema_compare import estimate_column_meaning

    assert "예산" not in estimate_column_meaning("비용명", use_budget_profile=False)
    assert "예산" in estimate_column_meaning("비용명", use_budget_profile=True)


def test_type_groups_outcome() -> None:
    df = pd.DataFrame(
        {
            "항목_코드": ["A-001", "A-002"],
            "비용_명": ["재료비", "회의비"],
            "실행_예산": [100, 200],
            "집행_금액": [80.0, None],
            "담당자": ["김", "이"],
            "집행_일자": pd.to_datetime(["2026-07-01", None]),
            "비고": ["정상", ""],
        }
    )
    reply, table = build_schema_outcome(
        "숫자 컬럼과 문자 컬럼을 구분해서 보여줘",
        [("샘플", df)],
    )
    assert "숫자형 컬럼" in reply
    assert "문자형 컬럼" in reply
    assert "날짜형 컬럼" in reply
    assert "실행_예산" in reply
    assert "집행_금액" in reply
    assert "항목_코드" in reply
    assert "집행_일자" in reply
    assert table is None


def test_route_type_groups_bypasses_pandasai() -> None:
    df = pd.DataFrame({"지역": ["서울"], "매출": [100], "판매일": pd.to_datetime(["2026-01-01"])})
    outcome = route_single_prompt(
        "숫자 컬럼과 문자 컬럼을 구분해서 보여줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost:11434",
        model="dummy",
    )
    assert "숫자형 컬럼" in outcome.reply
    assert "매출" in outcome.reply
    assert "지역" in outcome.reply
    assert "판매일" in outcome.reply
    assert outcome.operation_name is None


def test_route_column_meanings_bypasses_pandasai() -> None:
    df = pd.DataFrame({"담당자": ["김"], "집행_금액": [1]})
    outcome = route_single_prompt(
        "각 컬럼이 어떤 의미인지 추측해서 설명해줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost:11434",
        model="dummy",
    )
    assert outcome.dataframe is not None
    assert "추정 의미" in outcome.dataframe.columns
    assert "추정" in outcome.reply
    assert outcome.operation_name is None


def test_build_schema_compare_table() -> None:
    frames = _sales_frames()
    table = build_schema_compare_table(frames, unit_label="시트")
    assert list(table.columns) == ["시트", "행 수", "열 수", "컬럼 목록"]
    assert table["행 수"].tolist() == [15, 15, 10]
    assert table["열 수"].tolist() == [8, 8, 8]
    assert "판매일" in table.iloc[0]["컬럼 목록"]
    assert "결제수단" in table.iloc[0]["컬럼 목록"]


def test_build_schema_outcome_common_columns() -> None:
    frames = _sales_frames()
    # 한 시트에만 추가 컬럼
    frames[2] = (
        "3월",
        frames[2][1].assign(비고=["a"] * len(frames[2][1])),
    )
    reply, table = build_schema_outcome(
        "시트 간 공통 컬럼을 알려줘",
        frames,
        unit_label="시트",
    )
    assert table is not None
    assert "공통 컬럼" in table.columns
    assert len(table) == 8
    assert "3월만" in reply


def test_route_multi_schema_compare_skips_llm() -> None:
    frames = _sales_frames()
    outcome = route_multi_prompt(
        "각 시트의 행 수와 컬럼 목록을 비교해줘",
        named_frames=frames,
        base_url="http://localhost:11434",
        model="dummy",
        context_label=None,
        filter_df=None,
        unit_label="시트",
    )
    assert outcome.dataframe is not None
    assert list(outcome.dataframe["시트"]) == ["1월", "2월", "3월"]
    assert outcome.dataframe["행 수"].tolist() == [15, 15, 10]
    assert "비교" in outcome.reply


def test_route_single_dtypes() -> None:
    df = pd.DataFrame({"지역": ["서울", None], "매출": [100, 200]})
    outcome = route_single_prompt(
        "각 컬럼의 데이터 타입과 결측치 개수를 알려줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost:11434",
        model="dummy",
    )
    assert outcome.dataframe is not None
    assert set(outcome.dataframe["컬럼"]) == {"지역", "매출"}
    assert int(outcome.dataframe.loc[outcome.dataframe["컬럼"] == "지역", "결측치"].iloc[0]) == 1
