"""LLM table-layout step: the LLM's answer is faked; the checks are real."""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from core.io import table_structure as ts
from core.io.excel_loader import load_excel


def _answer(header, first, last, rows, special=(), group=""):
    return {
        "header_rows": header,
        "first_column": first,
        "last_column": last,
        "first_data_row": rows[0],
        "last_data_row": rows[1],
        "special_rows": [{"row": r, "role": role} for r, role in special],
        "group_column_name": group,
    }


def _ask(answer):
    seen = {}

    def ask(system, prompt):
        seen["prompt"] = prompt
        return answer

    ask.seen = seen
    return ask


def _form(tmp_path):
    """Title rows above the header, a note below the table."""
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "분기 현황"
    ws.merge_cells("A1:C1")
    ws["A2"] = "단위: 개"
    ws.append([])
    ws.append(["코드", "이름", "수량"])
    ws.append(["X1", "가", 10])
    ws.append(["X2", "나", 20])
    ws.append(["X3", "다", 30])
    ws.append([])
    ws.append(["비고: 예시"])
    path = tmp_path / "form.xlsx"
    wb.save(path)
    return path


def test_title_rows_are_left_out_when_the_answer_checks_out(tmp_path) -> None:
    path = _form(tmp_path)
    ask = _ask(_answer([4], "A", "C", (5, 7)))
    raw, info = ts.read_with_llm(path, 0, ask=ask)
    assert list(raw.columns) == ["코드", "이름", "수량"]
    assert raw["수량"].tolist() == [10, 20, 30]
    assert info["source"] == "llm" and info["record_rows"] == 3
    assert "A1:C1" in ask.seen["prompt"] and "R4: A4=코드" in ask.seen["prompt"]


def test_bounds_that_drop_a_numeric_row_are_rejected(tmp_path) -> None:
    path = _form(tmp_path)
    with pytest.raises(ts.StructureError, match="number_outside_table"):
        ts.read_with_llm(path, 0, ask=_ask(_answer([4], "A", "C", (6, 7))))


def test_single_row_merged_header_keeps_the_first_record(tmp_path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["과제", None, "예산"])
    ws.merge_cells("A1:B1")
    ws.append(["P-1", "첫 과제", 100])
    ws.append(["P-2", "둘째 과제", 200])
    path = tmp_path / "merged.xlsx"
    wb.save(path)
    raw, _ = ts.read_with_llm(path, 0, ask=_ask(_answer([1], "A", "C", (2, 3))))
    assert raw.values.tolist() == [["P-1", "첫 과제", 100], ["P-2", "둘째 과제", 200]]
    assert list(raw.columns) == ["과제", "과제", "예산"]  # the loader makes these unique


def _payroll(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["부서", "이름", "금액"])
    ws.append(["A", "갑", 100])
    ws.append(["A", "을", 50])
    ws.append(["A", "합", 150])
    ws.append(["B", "병", 70])
    ws.append(["B", "합", 70])
    ws.append(["전체", None, 220])
    path = tmp_path / "payroll.xlsx"
    wb.save(path)
    return path


def test_subtotal_and_total_rows_that_add_up_are_excluded(tmp_path) -> None:
    path = _payroll(tmp_path)
    answer = _answer([1], "A", "C", (2, 7), special=[(4, "subtotal"), (6, "subtotal"), (7, "total")])
    raw, info = ts.read_with_llm(path, 0, ask=_ask(answer))
    assert raw["금액"].tolist() == [100, 50, 70]
    assert info["excluded_rows"] == {"subtotal": [4, 6], "total": [7]}


def test_group_name_without_group_rows_adds_no_column(tmp_path) -> None:
    path = _payroll(tmp_path)
    answer = _answer([1], "A", "C", (2, 7), special=[(4, "subtotal"), (6, "subtotal"), (7, "total")], group="부서")
    raw, info = ts.read_with_llm(path, 0, ask=_ask(answer))
    assert list(raw.columns) == ["부서", "이름", "금액"]
    assert info["group_column"] is None


def test_group_column_name_clash_gets_a_number(tmp_path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["항목", "금액"])
    ws.append(["북쪽"])
    ws.append(["가", 1])
    path = tmp_path / "clash.xlsx"
    wb.save(path)
    answer = _answer([1], "A", "B", (2, 3), special=[(2, "group_label")], group="항목")
    raw, _ = ts.read_with_llm(path, 0, ask=_ask(answer))
    assert list(raw.columns) == ["항목_2", "항목", "금액"]


def test_a_summary_row_that_does_not_add_up_is_rejected(tmp_path) -> None:
    path = _payroll(tmp_path)
    answer = _answer([1], "A", "C", (2, 7), special=[(3, "subtotal")])
    with pytest.raises(ts.StructureError, match="subtotal_does_not_add_up"):
        ts.read_with_llm(path, 0, ask=_ask(answer))


def test_group_label_rows_become_a_column(tmp_path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["항목", "금액"])
    ws.append(["북쪽"])
    ws.append(["가", 1])
    ws.append(["나", 2])
    ws.append([])
    ws.append(["남쪽"])
    ws.append(["다", 3])
    path = tmp_path / "groups.xlsx"
    wb.save(path)
    answer = _answer([1], "A", "B", (2, 7), special=[(2, "group_label"), (5, "blank"), (6, "group_label")], group="권역")
    raw, _ = ts.read_with_llm(path, 0, ask=_ask(answer))
    assert raw.to_dict("records") == [
        {"권역": "북쪽", "항목": "가", "금액": 1},
        {"권역": "북쪽", "항목": "나", "금액": 2},
        {"권역": "남쪽", "항목": "다", "금액": 3},
    ]


def test_three_header_rows_join_with_merges(tmp_path) -> None:
    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["B1"] = "이름", "연도"
    ws.merge_cells("A1:A3")
    ws.merge_cells("B1:C1")
    ws["B2"] = "상반기"
    ws.merge_cells("B2:C2")
    ws["B3"], ws["C3"] = "계획", "실적"
    ws.append(["가", 1, 2])
    path = tmp_path / "three.xlsx"
    wb.save(path)
    raw, _ = ts.read_with_llm(path, 0, ask=_ask(_answer([1, 2, 3], "A", "C", (4, 4))))
    assert list(raw.columns) == ["이름", "연도_상반기_계획", "연도_상반기_실적"]


@pytest.mark.parametrize(
    "answer",
    [
        "not a dict",
        {"header_rows": [1]},
        _answer([1, 3], "A", "C", (4, 7)),
        _answer([5], "A", "C", (5, 7)),
        _answer([4], "A", "C", (5, 7), special=[(6, "mystery")]),
        _answer([4], "A", "C", (5, 7), special=[(6, "note")]),
        _answer([4], "A", "C", (5, 7), special=[(6, "group_label")]),
    ],
)
def test_malformed_answers_are_rejected(tmp_path, answer) -> None:
    with pytest.raises(ts.StructureError):
        ts.read_with_llm(_form(tmp_path), 0, ask=_ask(answer))


def test_loader_falls_back_and_says_why(tmp_path, monkeypatch) -> None:
    path = _form(tmp_path)
    monkeypatch.setenv(ts.ENABLE_ENV, "on")

    def broken(path, sheet_name, ask=None):
        raise ts.StructureError("llm_unavailable: ConnectionError")

    monkeypatch.setattr(ts, "read_with_llm", broken)
    df = load_excel(path)
    assert df.attrs[ts.ATTRS_KEY] == {"source": "fallback", "reason": "llm_unavailable: ConnectionError"}


def test_loader_uses_the_verified_layout(tmp_path, monkeypatch) -> None:
    path = _form(tmp_path)
    monkeypatch.setenv(ts.ENABLE_ENV, "on")
    real = ts.read_with_llm
    monkeypatch.setattr(ts, "read_with_llm", lambda p, s: real(p, s, ask=_ask(_answer([4], "A", "C", (5, 7)))))
    df = load_excel(path)
    assert list(df.columns) == ["코드", "이름", "수량"]
    assert len(df) == 3
    assert df.attrs[ts.ATTRS_KEY]["source"] == "llm"


def test_loader_reports_disabled_step(tmp_path) -> None:
    df = load_excel(_form(tmp_path))
    assert df.attrs[ts.ATTRS_KEY] == {"source": "fallback", "reason": "disabled"}


def test_a_failed_check_is_sent_back_once(tmp_path) -> None:
    path = _form(tmp_path)
    answers = [_answer([4], "A", "C", (6, 7)), _answer([4], "A", "C", (5, 7))]
    prompts = []

    def ask(system, prompt):
        prompts.append(prompt)
        return answers[len(prompts) - 1]

    raw, info = ts.read_with_llm(path, 0, ask=ask)
    assert len(raw) == 3 and info["attempts"] == 2
    assert "It failed this check: number_outside_table: C5" in prompts[1]


def test_two_failed_checks_give_up(tmp_path) -> None:
    calls = []

    def ask(system, prompt):
        calls.append(prompt)
        return _answer([4], "A", "C", (6, 7))

    with pytest.raises(ts.StructureError, match="number_outside_table.*; number_outside_table"):
        ts.read_with_llm(_form(tmp_path), 0, ask=ask)
    assert len(calls) == 2


def test_numbers_stored_as_text_count_as_numbers(tmp_path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["항목", "금액"])
    ws.append(["가", "1,000"])
    ws.append(["나", "2,500"])
    ws.append(["모두", "3,500"])
    path = tmp_path / "text_numbers.xlsx"
    wb.save(path)
    with pytest.raises(ts.StructureError, match="number_outside_table: B4"):
        ts.read_with_llm(path, 0, ask=_ask(_answer([1], "A", "B", (2, 3))))
    raw, info = ts.read_with_llm(path, 0, ask=_ask(_answer([1], "A", "B", (2, 4), special=[(4, "total")])))
    assert raw["항목"].tolist() == ["가", "나"] and info["excluded_rows"] == {"total": [4]}


def test_notice_reports_header_and_excluded_rows() -> None:
    info = {"source": "llm", "header_rows": [3, 4], "excluded_rows": {"subtotal": [8, 12], "total": [13], "blank": [9]},
            "group_column": "권역"}
    code, message = ts.notice(info)
    assert code == ts.NOTICE_EXCLUDED
    assert message == (
        "원본 3~4행을 머리글로 읽고 그 위 줄은 제외했습니다. "
        "소계 2행, 총계 1행(원본 8, 12, 13행)은 계산에서 제외했습니다. "
        "그룹 제목 줄은 '권역' 열로 옮겼습니다."
    )


@pytest.mark.parametrize(
    "info",
    [None, {"source": "fallback", "reason": "disabled"}, {"source": "llm", "header_rows": [1], "excluded_rows": {"blank": [5]}}],
)
def test_notice_is_quiet_when_nothing_changes_the_count(info) -> None:
    assert ts.notice(info) is None


def test_notice_warns_when_layout_is_unverified() -> None:
    code, message = ts.notice({"source": "fallback", "reason": "subtotal_does_not_add_up: R4"})
    assert code == ts.NOTICE_UNVERIFIED and "원본과 대조" in message


def test_sanitize_keeps_table_structure_info(tmp_path) -> None:
    from core.io.excel_loader import sanitize_dataframe

    df = load_excel(_form(tmp_path))
    assert sanitize_dataframe(df).attrs[ts.ATTRS_KEY] == df.attrs[ts.ATTRS_KEY]


def test_headless_turns_the_notice_into_a_warning() -> None:
    import pandas as pd
    from types import SimpleNamespace

    from core.application.headless import _table_structure_warnings

    frame = pd.DataFrame({"a": [1]})
    frame.attrs[ts.ATTRS_KEY] = {"source": "llm", "header_rows": [1], "excluded_rows": {"total": [5]}}
    quiet = pd.DataFrame({"a": [1]})
    one = _table_structure_warnings([SimpleNamespace(frame=frame, label="a.xlsx")])
    assert [w.to_dict() for w in one] == [
        {"code": "table_rows_excluded", "message": "총계 1행(원본 5행)은 계산에서 제외했습니다.", "stage": "file_loading"}
    ]
    two = _table_structure_warnings([SimpleNamespace(frame=frame, label="a.xlsx"), SimpleNamespace(frame=quiet, label="b.xlsx")])
    assert [w.message for w in two] == ["[a.xlsx] 총계 1행(원본 5행)은 계산에서 제외했습니다."]


def _grouped(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["항목", "금액"])
    ws.append(["북쪽"])
    ws.append(["가", 1])
    ws.append(["나", 2])
    ws.append(["남쪽"])
    ws.append(["다", 3])
    path = tmp_path / "grouped.xlsx"
    wb.save(path)
    return path


def test_records_above_the_first_group_label_are_rejected(tmp_path) -> None:
    # The first label (R2) was missed, so R2-R4 would have no group.
    answer = _answer([1], "A", "B", (2, 6), special=[(5, "group_label")], group="권역")
    with pytest.raises(ts.StructureError, match="records_without_group: R2-R4"):
        ts.read_with_llm(_grouped(tmp_path), 0, ask=_ask(answer))


def test_missed_group_label_is_corrected_on_retry(tmp_path) -> None:
    wrong = _answer([1], "A", "B", (2, 6), special=[(5, "group_label")], group="권역")
    right = _answer([1], "A", "B", (2, 6), special=[(2, "group_label"), (5, "group_label")], group="권역")
    answers = iter([wrong, right])
    prompts = []

    def ask(system, prompt):
        prompts.append(prompt)
        return next(answers)

    raw, info = ts.read_with_llm(_grouped(tmp_path), 0, ask=ask)
    assert "records_without_group" in prompts[1]
    assert raw["권역"].tolist() == ["북쪽", "북쪽", "남쪽"]
    assert info["attempts"] == 2
