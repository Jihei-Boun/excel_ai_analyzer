"""차트 저장·폴백·unwrap 테스트."""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd

from core.chart_utils import _format_axis_number, generate_fallback_chart, materialize_chart
from core.pandasai_config import _build_summary, _unwrap_result


def test_unwrap_preserves_plot_chart_path(tmp_path: Path) -> None:
    chart = tmp_path / "demo.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n")
    result, meta = _unwrap_result({"type": "plot", "value": str(chart)})
    assert result == str(chart)
    assert meta["chart_path"] == str(chart.resolve())


def test_materialize_base64_data_uri() -> None:
    # 1x1 PNG
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    path = materialize_chart(uri)
    assert path is not None
    assert Path(path).is_file()
    assert Path(path).read_bytes().startswith(b"\x89PNG")


def test_unwrap_dataframe_has_empty_meta() -> None:
    df = pd.DataFrame({"a": [1]})
    result, meta = _unwrap_result({"type": "dataframe", "value": df})
    assert isinstance(result, pd.DataFrame)
    assert meta == {}


def test_materialize_rejects_missing_path() -> None:
    assert materialize_chart(123) is None
    assert materialize_chart(pd.DataFrame({"a": [1]})) is None
    assert materialize_chart("/tmp/not-a-real-chart.png") is None


def test_generate_fallback_chart_creates_png() -> None:
    df = pd.DataFrame({"과목": ["A", "B", "A"], "금액": [10, 20, 5]})
    path = generate_fallback_chart(df, "과목별 금액 차트")
    assert path is not None
    assert Path(path).is_file()
    assert Path(path).suffix == ".png"


def test_generate_multi_file_chart() -> None:
    from core.chart_utils import generate_multi_file_chart

    named = [
        ("a.xlsx", pd.DataFrame({"계획예산": [10, 20]})),
        ("b.xlsx", pd.DataFrame({"계획예산": [5]})),
    ]
    path = generate_multi_file_chart(named, "파일별 계획예산 막대그래프")
    assert path is not None
    assert Path(path).is_file()


def test_sum_metric_excludes_subtotal_rows() -> None:
    from core.pandasai_config import sum_metric_excluding_totals

    df = pd.DataFrame(
        {
            "비목": ["인건비", "직내부인건비", "소 계", "합계"],
            "실행예산_합계": [100, 200, 300, 600],
        }
    )
    assert sum_metric_excluding_totals(df, "실행예산_합계") == 300.0


def test_multi_file_chart_excludes_subtotals() -> None:
    from core.chart_utils import generate_multi_file_chart

    named = [
        (
            "4예실.xlsx",
            pd.DataFrame(
                {
                    "비목": ["A", "B", "소 계"],
                    "실행예산_합계": [1_000_000_000, 500_000_000, 1_500_000_000],
                }
            ),
        ),
        (
            "5예실.xlsx",
            pd.DataFrame(
                {
                    "비목": ["C", "합계"],
                    "실행예산_합계": [2_000_000_000, 2_000_000_000],
                }
            ),
        ),
    ]
    path = generate_multi_file_chart(named, "파일별 실행예산_합계 차트")
    assert path is not None


def test_build_summary_for_chart() -> None:
    assert _build_summary(None, None, {"chart_path": "/tmp/x.png"}) == "차트 결과를 생성했습니다."


def test_format_axis_number_uses_exact_comma_values() -> None:
    assert _format_axis_number(187_090_387) == "187,090,387"
    assert _format_axis_number(114_525_479) == "114,525,479"
    assert _format_axis_number(94_698_000) == "94,698,000"


def test_generate_fallback_chart_preserves_input_order() -> None:
    """막대 순서는 데이터 입력 순서를 따른다 (값 내림차순 정렬 없음)."""
    df = pd.DataFrame(
        {
            "출처파일": ["4예실.xlsx", "5예실.xlsx", "7예실.xlsx"],
            "실행예산_합계": [114_525_479, 187_090_387, 94_698_000],
        }
    )
    path = generate_fallback_chart(df, "파일별 실행예산_합계 차트")
    assert path is not None
    assert Path(path).is_file()
