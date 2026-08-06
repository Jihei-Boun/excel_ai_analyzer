"""집계 맥락 라벨 오염 방지 테스트."""

from __future__ import annotations

import pandas as pd

from core.aggregates import build_context_aggregate_table
from core.filter.value_filter import (
    _label_from_prompt_text,
    format_context_label,
    infer_context_label,
)


def test_label_from_prompt_keeps_entity_names() -> None:
    assert _label_from_prompt_text("연구활동비항목을 리스트로 보여줘") == "연구활동비"


def test_label_from_prompt_rejects_meta_questions() -> None:
    assert _label_from_prompt_text("각 컬럼이 어떤 의미인지 추측해서 설명해줘") is None
    assert _label_from_prompt_text("숫자 컬럼과 문자 컬럼을 구분해서 보여줘") is None
    assert _label_from_prompt_text("데이터 품질을 분석해줘") is None


def test_infer_skips_prompt_text_when_disabled() -> None:
    df = pd.DataFrame(
        {
            "비용_명": ["연구활동비", "재료비"],
            "집행_금액": [100, 200],
        }
    )
    assert (
        infer_context_label(
            prompt="각 컬럼이 어떤 의미인지 추측해서 설명해줘",
            result_df=df,
            full_df=df,
            allow_prompt_text=False,
        )
        is None
    )


def test_aggregate_without_context_uses_합계_label() -> None:
    df = pd.DataFrame(
        {
            "비용_명": ["연구활동비", "재료비"],
            "집행_금액": [1_280_000, 1_850_000],
        }
    )
    result = build_context_aggregate_table(
        df,
        "집행 금액 합계를 계산해줘",
        context_label=None,
    )
    assert result is not None
    table, summary = result
    assert format_context_label(None) == "합계"
    assert table.iloc[0, 0] == "합계"
    assert "각컬럼" not in summary
    assert "집행_금액" in summary


def test_aggregate_keeps_real_filter_context() -> None:
    df = pd.DataFrame(
        {
            "비용_명": ["연구활동비", "연구활동비"],
            "집행_금액": [100, 200],
        }
    )
    result = build_context_aggregate_table(
        df,
        "집행 금액 합계를 계산해줘",
        context_label="연구활동비",
    )
    assert result is not None
    table, summary = result
    assert table.iloc[0, 0] == "연구활동비"
    assert "연구활동비" in summary
