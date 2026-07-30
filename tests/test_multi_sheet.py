"""단일 파일 다중 시트 분석 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.file_summary import build_multi_file_summary
from ui.file_state import (
    _normalize_active_sheets,
    get_active_named_frames,
    is_multi_analysis_mode,
    is_multi_sheet_analysis,
    set_active_sheets,
)


class _Session(dict):
    """streamlit.session_state 최소 호환 더블."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value) -> None:
        self[name] = value

    def setdefault(self, key, default=None):
        return dict.setdefault(self, key, default)

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def pop(self, key, default=None):
        return dict.pop(self, key, default)


@pytest.fixture
def multi_sheet_workbook(tmp_path: Path) -> Path:
    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"항목": ["a"], "금액": [10]}).to_excel(
            writer, sheet_name="예산", index=False
        )
        pd.DataFrame({"항목": ["b"], "금액": [20]}).to_excel(
            writer, sheet_name="집행", index=False
        )
        pd.DataFrame({"항목": ["c"], "금액": [30]}).to_excel(
            writer, sheet_name="잔액", index=False
        )
    return path


def test_normalize_active_sheets_filters_invalid() -> None:
    meta = {
        "sheet_names": ["예산", "집행"],
        "current_sheet": "예산",
        "active_sheets": ["집행", "없는시트", "예산"],
    }
    assert _normalize_active_sheets(meta) == ["집행", "예산"]


def test_normalize_active_sheets_defaults_to_current() -> None:
    meta = {"sheet_names": ["A", "B"], "current_sheet": "B"}
    assert _normalize_active_sheets(meta) == ["B"]


def test_set_active_sheets_enables_multi_sheet(
    monkeypatch: pytest.MonkeyPatch,
    multi_sheet_workbook: Path,
) -> None:
    import ui.file_state as file_state_mod

    state = _Session(
        uploaded_files=[
            {
                "id": "multi.xlsx",
                "name": "multi.xlsx",
                "path": str(multi_sheet_workbook),
                "size": "1 KB",
                "sheet_names": ["예산", "집행", "잔액"],
                "current_sheet": "예산",
                "active_sheets": ["예산"],
            }
        ],
        file_frames={},
        sheet_frames={},
        active_file_id="multi.xlsx",
        active_file_ids=["multi.xlsx"],
        analysis_mode="single",
        preview_file_id="multi.xlsx",
    )
    monkeypatch.setattr(file_state_mod.st, "session_state", state)

    set_active_sheets(["집행", "잔액"], file_id="multi.xlsx", reset_analysis=True)

    assert is_multi_sheet_analysis()
    assert is_multi_analysis_mode()
    named = get_active_named_frames()
    assert [name for name, _ in named] == ["집행", "잔액"]
    assert named[0][1]["금액"].tolist() == [20]
    assert named[1][1]["금액"].tolist() == [30]
    assert state.work_target == "다중 시트"


def test_single_sheet_is_not_multi(
    monkeypatch: pytest.MonkeyPatch,
    multi_sheet_workbook: Path,
) -> None:
    import ui.file_state as file_state_mod

    state = _Session(
        uploaded_files=[
            {
                "id": "multi.xlsx",
                "name": "multi.xlsx",
                "path": str(multi_sheet_workbook),
                "size": "1 KB",
                "sheet_names": ["예산", "집행", "잔액"],
                "current_sheet": "예산",
                "active_sheets": ["예산"],
            }
        ],
        file_frames={},
        sheet_frames={},
        active_file_id="multi.xlsx",
        active_file_ids=["multi.xlsx"],
        analysis_mode="single",
    )
    monkeypatch.setattr(file_state_mod.st, "session_state", state)

    assert not is_multi_sheet_analysis()
    assert not is_multi_analysis_mode()
    named = get_active_named_frames()
    assert len(named) == 1
    assert named[0][0] == "multi.xlsx"


def test_multi_summary_unit_label_sheet() -> None:
    frames = [
        ("예산", pd.DataFrame({"금액": [1]})),
        ("집행", pd.DataFrame({"금액": [2]})),
    ]
    text = build_multi_file_summary(frames, unit_label="시트")
    assert "선택된 시트 2개를 요약합니다" in text
    assert "### 예산" in text
    assert "### 집행" in text
