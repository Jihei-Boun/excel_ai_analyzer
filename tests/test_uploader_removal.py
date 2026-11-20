"""Uploader X removal stays in step with the app's file list; answers name their file."""

from __future__ import annotations

from ui.upload import files_removed_in_uploader


def test_removing_the_last_file_with_x_is_seen() -> None:
    """The widget going empty used to be ignored, so the preview and analysis stayed."""
    assert files_removed_in_uploader({"a.xlsx"}, set(), ["a.xlsx"]) == ["a.xlsx"]


def test_replacing_a_file_removes_only_the_old_one() -> None:
    assert files_removed_in_uploader({"a.xlsx"}, {"b.xlsx"}, ["a.xlsx"]) == ["a.xlsx"]


def test_a_fresh_or_reset_widget_removes_nothing() -> None:
    assert files_removed_in_uploader(set(), set(), ["a.xlsx"]) == []


def test_files_already_deleted_with_the_button_are_left_alone() -> None:
    assert files_removed_in_uploader({"a.xlsx", "b.xlsx"}, {"b.xlsx"}, ["b.xlsx"]) == []


def test_source_label_names_the_files_an_answer_came_from() -> None:
    from ui.file_state import format_source_label

    assert format_source_label(["a.xlsx"]) == "a.xlsx"
    assert format_source_label(["a.xlsx", "b.xlsx"]) == "a.xlsx + b.xlsx"
    assert format_source_label(["a.xlsx", "b.xlsx", "c.xlsx"]) == "a.xlsx 외 2개"
    assert format_source_label([""]) == "파일 없음"
