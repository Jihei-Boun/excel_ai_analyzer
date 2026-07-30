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


def test_column_list_is_not_data_list() -> None:
    prompt = "각 시트의 행 수와 컬럼 목록을 비교해줘"
    assert is_list_request(prompt) is False
    assert expects_list_display(prompt) is False
    assert expects_list_display("결제수단 리스트로 뽑아줘") is True


def test_schema_kind() -> None:
    assert schema_kind("공통 컬럼을 알려줘") == "common"
    assert schema_kind("데이터 타입과 결측치") == "dtypes"
    assert schema_kind("행 수와 컬럼 목록 비교") == "compare"


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
