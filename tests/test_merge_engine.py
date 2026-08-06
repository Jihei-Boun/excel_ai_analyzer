"""병합 엔진·export 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.io.export_utils import dataframe_to_xlsx_bytes, export_dataframe_xlsx, safe_download_name
from core.io.merge_engine import infer_common_keys, merge_named_frames
from core.io.normalize import normalize_dataframe


def test_infer_common_keys_and_outer_merge() -> None:
    left = normalize_dataframe(
        pd.DataFrame({"코드": ["A", "B", "C"], "금액": [10, 20, 30]})
    )
    right = normalize_dataframe(
        pd.DataFrame({"코드": ["B", "C", "D"], "수량": [1, 2, 3]})
    )
    keys = infer_common_keys([("left.xlsx", left), ("right.xlsx", right)])
    assert "코드" in keys

    result = merge_named_frames(
        [("left.xlsx", left), ("right.xlsx", right)],
        keys=["코드"],
        how="outer",
    )
    assert result.report.result_rows == 4
    assert 0.0 < result.report.match_rate < 1.0
    assert "금액" in result.dataframe.columns
    assert any(str(c).startswith("수량") for c in result.dataframe.columns)


def test_inner_merge_match_rate_and_duplicate_warning() -> None:
    left = pd.DataFrame({"id": [1, 1, 2], "x": [10, 11, 20]})
    right = pd.DataFrame({"id": [1, 3], "y": [100, 300]})
    result = merge_named_frames(
        [("a.xlsx", left), ("b.xlsx", right)],
        keys=["id"],
        how="inner",
    )
    assert result.report.result_rows >= 2
    assert result.report.duplicate_key_warnings
    assert result.report.match_rate > 0


def test_merge_requires_two_frames() -> None:
    with pytest.raises(ValueError, match="최소 2개"):
        merge_named_frames([("only", pd.DataFrame({"a": [1]}))], keys=["a"])


def test_export_dataframe_xlsx_writes_file(tmp_path: Path) -> None:
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    path = export_dataframe_xlsx(df, filename="merged_test.xlsx", directory=tmp_path)
    assert path.exists()
    loaded = pd.read_excel(path)
    assert list(loaded.columns) == ["a", "b"]
    assert len(loaded) == 2

    payload = dataframe_to_xlsx_bytes(df)
    assert payload[:2] == b"PK"
    assert safe_download_name("a/b:c") == "a_b_c.xlsx"
