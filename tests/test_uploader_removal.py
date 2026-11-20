"""Uploader X removal stays in step with the app's file list; answers name their file."""

from __future__ import annotations

from ui.upload import files_removed_in_uploader


def test_removing_the_last_file_with_x_is_seen() -> None:
    """The widget going empty used to be ignored, so the preview and analysis stayed."""
    assert files_removed_in_uploader(set(), ["a.xlsx"]) == ["a.xlsx"]


def test_replacing_a_file_removes_only_the_old_one() -> None:
    assert files_removed_in_uploader({"b.xlsx"}, ["a.xlsx"]) == ["a.xlsx"]


def test_files_still_in_the_widget_stay() -> None:
    assert files_removed_in_uploader({"a.xlsx", "b.xlsx"}, ["a.xlsx", "b.xlsx"]) == []


def test_pruning_runs_only_after_the_user_changed_the_uploader() -> None:
    """Another widget's rerun can hand back an empty uploader value; that must not delete files."""
    import inspect

    from ui import upload

    source = inspect.getsource(upload.render_upload_section)
    assert "on_change=_mark_uploader_changed" in source
    assert 'if st.session_state.pop("_uploader_user_changed", False):' in source


def test_source_label_names_the_files_an_answer_came_from() -> None:
    from ui.file_state import format_source_label

    assert format_source_label(["a.xlsx"]) == "a.xlsx"
    assert format_source_label(["a.xlsx", "b.xlsx"]) == "a.xlsx + b.xlsx"
    assert format_source_label(["a.xlsx", "b.xlsx", "c.xlsx"]) == "a.xlsx 외 2개"
    assert format_source_label([""]) == "파일 없음"


def test_controls_drawn_before_the_uploader_defer_their_rerun() -> None:
    """st.rerun() before the uploader is drawn makes Streamlit drop its files (and fire on_change).

    The sidebar and header run before the uploader, so they ask for a rerun that app.py
    performs once at the end of the script.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("ui/sidebar.py", "ui/header.py"):
        assert "st.rerun()" not in (root / name).read_text(encoding="utf-8"), name
    app = (root / "app.py").read_text(encoding="utf-8").rstrip().splitlines()
    assert app[-1] == "flush_requested_rerun()"
