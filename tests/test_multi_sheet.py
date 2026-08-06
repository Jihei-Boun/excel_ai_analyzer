"""단일 파일 다중 시트 · 다중 파일×시트 분석 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.summary.file_summary import build_multi_file_summary
from ui.file_state import (
    _normalize_active_sheets,
    activate_files,
    get_active_named_frames,
    get_analysis_unit_label,
    is_cross_file_sheet_analysis,
    is_multi_analysis_mode,
    is_multi_file_analysis,
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


@pytest.fixture
def two_multi_sheet_workbooks(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    for path, prefix in ((a, "A"), (b, "B")):
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame({"항목": [f"{prefix}1"], "금액": [1]}).to_excel(
                writer, sheet_name="예산", index=False
            )
            pd.DataFrame({"항목": [f"{prefix}2"], "금액": [2]}).to_excel(
                writer, sheet_name="집행", index=False
            )
    return a, b


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
    assert get_analysis_unit_label() == "시트"


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


def test_multi_file_multi_sheet_expands_units(
    monkeypatch: pytest.MonkeyPatch,
    two_multi_sheet_workbooks: tuple[Path, Path],
) -> None:
    import ui.file_state as file_state_mod

    path_a, path_b = two_multi_sheet_workbooks
    state = _Session(
        uploaded_files=[
            {
                "id": "a.xlsx",
                "name": "a.xlsx",
                "path": str(path_a),
                "size": "1 KB",
                "sheet_names": ["예산", "집행"],
                "current_sheet": "예산",
                "active_sheets": ["예산"],
            },
            {
                "id": "b.xlsx",
                "name": "b.xlsx",
                "path": str(path_b),
                "size": "1 KB",
                "sheet_names": ["예산", "집행"],
                "current_sheet": "예산",
                "active_sheets": ["예산"],
            },
        ],
        file_frames={},
        sheet_frames={},
        active_file_id=None,
        active_file_ids=[],
        analysis_mode="single",
        preview_file_id=None,
    )
    monkeypatch.setattr(file_state_mod.st, "session_state", state)

    activate_files(["a.xlsx", "b.xlsx"], reset_analysis=True)
    assert is_multi_file_analysis()
    assert not is_cross_file_sheet_analysis()
    assert [name for name, _ in get_active_named_frames()] == ["a.xlsx", "b.xlsx"]
    assert get_analysis_unit_label() == "파일"

    set_active_sheets(["예산", "집행"], file_id="a.xlsx", reset_analysis=True)
    set_active_sheets(["예산", "집행"], file_id="b.xlsx", reset_analysis=True)

    assert is_multi_file_analysis()
    assert is_cross_file_sheet_analysis()
    assert not is_multi_sheet_analysis()
    named = get_active_named_frames()
    assert [name for name, _ in named] == [
        "a.xlsx / 예산",
        "a.xlsx / 집행",
        "b.xlsx / 예산",
        "b.xlsx / 집행",
    ]
    assert named[0][1]["금액"].tolist() == [1]
    assert named[1][1]["금액"].tolist() == [2]
    assert state.work_target == "다중 파일·시트"
    assert get_analysis_unit_label() == "시트"
    # 시트 변경이 다중 파일 모드를 깨지 않음
    assert state.active_file_ids == ["a.xlsx", "b.xlsx"]
    assert state.analysis_mode == "multi"


def test_multi_file_single_sheet_each_keeps_file_labels(
    monkeypatch: pytest.MonkeyPatch,
    two_multi_sheet_workbooks: tuple[Path, Path],
) -> None:
    import ui.file_state as file_state_mod

    path_a, path_b = two_multi_sheet_workbooks
    state = _Session(
        uploaded_files=[
            {
                "id": "a.xlsx",
                "name": "a.xlsx",
                "path": str(path_a),
                "size": "1 KB",
                "sheet_names": ["예산", "집행"],
                "current_sheet": "집행",
                "active_sheets": ["집행"],
            },
            {
                "id": "b.xlsx",
                "name": "b.xlsx",
                "path": str(path_b),
                "size": "1 KB",
                "sheet_names": ["예산", "집행"],
                "current_sheet": "예산",
                "active_sheets": ["예산"],
            },
        ],
        file_frames={},
        sheet_frames={},
        active_file_id=None,
        active_file_ids=[],
        analysis_mode="single",
        preview_file_id=None,
    )
    monkeypatch.setattr(file_state_mod.st, "session_state", state)

    activate_files(["a.xlsx", "b.xlsx"], reset_analysis=True)
    named = get_active_named_frames()
    assert [name for name, _ in named] == ["a.xlsx", "b.xlsx"]
    assert named[0][1]["금액"].tolist() == [2]  # a 집행
    assert named[1][1]["금액"].tolist() == [1]  # b 예산
    assert state.work_target == "다중 파일"
