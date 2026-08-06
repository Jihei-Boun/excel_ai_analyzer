"""조건형 행 필터 — 값 일치 오탐 방지 및 ==0 & >0 규칙."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.analyzer import run_analysis
from core.excel_loader import load_excel
from core.prompt_intent import is_complex_analysis, is_condition_filter_request
from core.value_filter import _filter_by_mentioned_value, try_condition_row_filter


def test_condition_filter_detected() -> None:
    prompt = "집행계가 0인데 실행예산이 있는 행만 골라줘"
    assert is_condition_filter_request(prompt) is True
    assert is_complex_analysis(prompt) is True


def test_label_lookup_not_condition() -> None:
    assert is_condition_filter_request("연구활동비 보여줘") is False
    assert is_condition_filter_request("비용명이 121인 것만 보여줘") is False
    assert is_complex_analysis("연구활동비 보여줘") is False


def test_value_filter_skips_condition_prompt() -> None:
    df = pd.DataFrame(
        {
            "계획예산": [0, 100, 0],
            "실행예산_합계": [10, 0, 50],
            "집행계_합계": [0, 0, 20],
            "비용명": [1, 2, 3],
            "비용명_2": ["A", "B", "C"],
        }
    )
    prompt = "집행계가 0인데 실행예산이 있는 행만 골라줘"
    assert _filter_by_mentioned_value(df, prompt) is None


def test_try_condition_zero_and_exists() -> None:
    df = pd.DataFrame(
        {
            "비목분류": ["연구활동비", "연구활동비", "연구수당"],
            "비용명": [222, 271, 368],
            "비용명_2": ["사무용소모품비", "국내여비", "연구수당"],
            "실행예산_합계": [163832, 806700, 3582000],
            "집행계_합계": [0, 473960, 0],
        }
    )
    prompt = "집행계가 0인데 실행예산이 있는 행만 골라줘"
    result = try_condition_row_filter(df, prompt, use_budget_profile=True)
    assert result is not None
    assert len(result) == 2
    assert set(result["비용명_2"]) == {"사무용소모품비", "연구수당"}


def test_twin_file_zero_exec_with_budget(monkeypatch) -> None:
    path = Path(__file__).resolve().parents[1] / "data/uploads/03_트윈_예실대비표.xlsx"
    if not path.is_file():
        return
    df = load_excel(path)
    prompt = "집행계가 0인데 실행예산이 있는 행만 골라줘"

    def _fail_chat(*_a, **_k):
        raise AssertionError("조건 필터로 충분하면 LLM을 호출하면 안 됨")

    monkeypatch.setattr("core.analyzer.chat", _fail_chat)
    result, summary, _meta = run_analysis(
        df,
        prompt,
        base_url="http://localhost",
        model="dummy",
        use_budget_profile=True,
    )
    assert isinstance(result, pd.DataFrame)
    assert "조건 필터" in summary
    assert len(result) == 6
    names = set(result["비용명_2"].astype(str).str.strip())
    assert names == {
        "사무용소모품비",
        "문헌구입비",
        "국외여비",
        "전문가활용비",
        "세미나비",
        "연구수당",
    }
