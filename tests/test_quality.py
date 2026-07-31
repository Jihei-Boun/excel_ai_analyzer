"""품질 진단 단위 테스트."""

from __future__ import annotations

import pandas as pd

from core.prompt_router import route_multi_prompt, route_single_prompt
from core.quality import (
    build_quality_outcome,
    detect_quality_intent,
    diagnose_dataframe,
    friendly_load_error,
    is_quality_request,
    render_fix_recommendations,
    render_quality_issues,
    render_quality_summary,
)


def test_diagnose_warns_on_high_missing_and_duplicates() -> None:
    df = pd.DataFrame(
        {
            "id": [1, 1, 2, 3, 4],
            "값": [10, 10, None, None, None],
            "빈열": [None, None, None, None, None],
        }
    )
    report = diagnose_dataframe(df, label="샘플")
    assert report.severity in {"warn", "error"}
    assert report.empty_col_count == 1
    assert report.duplicate_row_count >= 1
    assert report.warnings
    assert report.suggestions


def test_diagnose_ok_for_clean_frame() -> None:
    df = pd.DataFrame({"코드": ["A", "B", "C"], "금액": [1, 2, 3]})
    report = diagnose_dataframe(df, label="깨끗")
    assert report.severity == "ok"
    assert report.row_count == 3
    assert "코드" in report.suspected_key_columns


def test_diagnose_empty_is_error() -> None:
    report = diagnose_dataframe(pd.DataFrame(), label="빈표")
    assert report.severity == "error"


def test_friendly_load_error_contains_guidance() -> None:
    message = friendly_load_error(ValueError("bad zip"), path="a.xlsx")
    assert "a.xlsx" in message
    assert "확인 포인트" in message


def test_is_quality_request() -> None:
    assert is_quality_request("이 파일의 데이터 품질을 분석해줘")
    assert is_quality_request("품질 진단해줘")
    assert is_quality_request("data quality check")
    assert is_quality_request("이 파일에서 분석 전에 수정하면 좋은 부분만 알려줘")
    assert is_quality_request("전처리가 필요한 부분이 있나요?")
    assert is_quality_request("문제가 있는 부분만 알려줘")
    assert not is_quality_request("파일을 요약해줘")
    assert not is_quality_request("각 컬럼의 데이터 타입과 결측치 개수를 알려줘")


def test_detect_quality_intent() -> None:
    assert (
        detect_quality_intent("이 파일의 데이터 품질을 분석해줘") == "quality_summary"
    )
    assert detect_quality_intent("문제가 있는 부분만 알려줘") == "quality_issues_only"
    assert (
        detect_quality_intent("이 파일에서 분석 전에 수정하면 좋은 부분만 알려줘")
        == "fix_recommendations"
    )


def test_diagnose_lists_sparse_missing_columns() -> None:
    """비율이 낮아도 결측 열을 개선 제안에 포함한다."""
    df = pd.DataFrame(
        {
            "항목_코드": ["A-001", "A-002", "A-003", "A-004", "A-005", "A-006"],
            "집행_금액": [1, 2, 3, 4, 5, None],
            "집행_일자": ["2026-01-01"] * 5 + [None],
        }
    )
    report = diagnose_dataframe(df, label="집행")
    assert report.severity == "ok"
    assert {item["column"] for item in report.missing_columns} == {
        "집행_금액",
        "집행_일자",
    }
    joined = " ".join(report.suggestions)
    assert "집행_금액" in joined
    assert "집행_일자" in joined


def test_render_intents_differ() -> None:
    df = pd.DataFrame(
        {
            "항목_코드": ["A-001", "A-002", "A-003", "A-004", "A-005", "A-006"],
            "집행_금액": [1, 2, 3, 4, 5, None],
            "집행_일자": pd.to_datetime(["2026-01-01"] * 5 + [None]),
        }
    )
    report = diagnose_dataframe(df, label="집행")

    summary = render_quality_summary(report, label="집행")
    assert "판정" in summary
    assert "키 후보" in summary
    assert "개선 제안" in summary

    issues = render_quality_issues(report, label="집행")
    assert "집행_금액" in issues
    assert "판정" not in issues
    assert "키 후보" not in issues

    fixes = render_fix_recommendations(report, label="집행")
    assert "집행_금액" in fixes
    assert "집행_일자" in fixes
    assert "결측값" in fixes
    assert "수정하면 좋은 항목" in fixes
    assert "수정이 필요한 항목은 위 2가지" in fixes
    assert "판정" not in fixes
    assert "키 후보" not in fixes
    assert "완전 중복 행과 빈 열" not in fixes
    assert "**1개**" in fixes
    assert "값을 입력하거나 해당 행을 제외하세요." in fixes
    assert "날짜를 확인하거나 결측 처리 기준을 정하세요." in fixes


def test_render_issues_empty_when_clean() -> None:
    report = diagnose_dataframe(
        pd.DataFrame({"코드": ["A", "B"], "금액": [1, 2]}),
        label="깨끗",
    )
    assert "발견하지 못했습니다" in render_quality_issues(report, label="깨끗")
    assert "특별히 수정할 항목은 없습니다" in render_fix_recommendations(
        report, label="깨끗"
    )


def test_route_preanalysis_fix_bypasses_pandasai() -> None:
    df = pd.DataFrame(
        {
            "항목_코드": ["A-001", "A-002"],
            "집행_금액": [100, None],
        }
    )
    outcome = route_single_prompt(
        "이 파일에서 분석 전에 수정하면 좋은 부분만 알려줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost",
        model="dummy",
    )
    assert "집행_금액" in outcome.reply
    assert "결측값" in outcome.reply
    assert "판정" not in outcome.reply
    assert "키 후보" not in outcome.reply
    assert "PandasAI" not in outcome.reply
    assert outcome.operation_name is None


def test_route_issues_only() -> None:
    df = pd.DataFrame({"코드": ["A", "B"], "금액": [1, None]})
    outcome = route_single_prompt(
        "문제가 있는 부분만 알려줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost",
        model="dummy",
    )
    assert "금액" in outcome.reply
    assert "판정" not in outcome.reply
    assert "키 후보" not in outcome.reply


def test_build_quality_outcome_single() -> None:
    df = pd.DataFrame({"코드": ["A", "B"], "금액": [1, None]})
    reply, table = build_quality_outcome(
        [("샘플.xlsx", df)],
        unit_label="파일",
        prompt="데이터 품질을 분석해줘",
    )
    assert "데이터 품질" in reply
    assert "샘플.xlsx" in reply
    assert table is None


def test_build_quality_outcome_multi() -> None:
    frames = [
        ("A", pd.DataFrame({"코드": ["A"], "금액": [1]})),
        ("B", pd.DataFrame({"코드": ["B", "B"], "금액": [None, None]})),
    ]
    reply, table = build_quality_outcome(
        frames,
        unit_label="시트",
        prompt="데이터 품질을 분석해줘",
    )
    assert "시트 2개" in reply
    assert table is not None
    assert list(table["시트"]) == ["A", "B"]
    assert "판정" in table.columns


def test_route_single_quality_bypasses_pandasai() -> None:
    df = pd.DataFrame({"코드": ["A", "B", "C"], "금액": [1, 2, 3]})
    outcome = route_single_prompt(
        "이 파일의 데이터 품질을 분석해줘",
        full_df=df,
        source_df=df,
        context_label=None,
        base_url="http://localhost",
        model="dummy",
    )
    assert "데이터 품질" in outcome.reply
    assert "판정" in outcome.reply
    assert "PandasAI" not in outcome.reply
    assert outcome.operation_name is None


def test_route_multi_quality() -> None:
    frames = [
        ("1월", pd.DataFrame({"금액": [1, 2]})),
        ("2월", pd.DataFrame({"금액": [3, None]})),
    ]
    outcome = route_multi_prompt(
        "데이터 품질을 분석해줘",
        named_frames=frames,
        base_url="http://localhost",
        model="dummy",
        context_label=None,
        filter_df=None,
        unit_label="시트",
    )
    assert "시트 2개" in outcome.reply
    assert outcome.dataframe is not None
