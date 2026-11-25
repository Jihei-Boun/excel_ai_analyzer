"""새 엔진의 코드 실행 상자 (core/analysis/sandbox.py). 모델은 부르지 않는다.

코드 내용은 검사하지 않고 운영체제 한도로 막으므로, 막혀야 할 일을 실제로 시켜 본다.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core.analysis import sandbox, step_engine

TABLES = {
    "표": pd.DataFrame(
        {
            "부서": ["가", "나", "나"],
            "값": [1, 2, 3],
            "섞임": ["x", 3, None],
            "날짜": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
        }
    )
}


def test_pandas_code_runs_and_keeps_index_and_types() -> None:
    out = sandbox.run_code("result = tables['표'].groupby('부서')['값'].sum().to_frame()", TABLES)
    assert out.index.name == "부서"
    assert out["값"].tolist() == [1, 5]
    assert str(out["값"].dtype) == "int64"


def test_mixed_and_date_columns_come_back_unchanged() -> None:
    out = sandbox.run_code("result = tables['표']", TABLES)
    pd.testing.assert_frame_equal(out, TABLES["표"])
    assert out["섞임"].tolist()[:2] == ["x", 3]  # 문자열 "3"이 되면 안 된다


def test_missing_result_takes_the_last_new_table() -> None:
    out = sandbox.run_code("a = tables['표'].head(1)\nb = tables['표'].tail(2)", TABLES)
    assert len(out) == 2


def test_error_reports_message_and_line() -> None:
    with pytest.raises(sandbox.CodeError) as err:
        sandbox.run_code("x = 1\nresult = tables['표']['없는 열']", TABLES)
    assert "KeyError" in str(err.value)
    assert err.value.line == 2


def test_reading_a_file_is_blocked() -> None:
    with pytest.raises(sandbox.CodeError, match="Too many open files"):
        sandbox.run_code("result = pd.DataFrame({'v': [open('/etc/hostname').read()]})", TABLES)


def test_writing_a_file_is_blocked(tmp_path) -> None:
    target = tmp_path / "out.csv"
    with pytest.raises(sandbox.CodeError):
        sandbox.run_code(f"tables['표'].to_csv({str(target)!r})\nresult = tables['표']", TABLES)
    assert not target.exists()


def test_network_is_blocked() -> None:
    with pytest.raises(sandbox.CodeError, match="Too many open files"):
        sandbox.run_code("import socket\nsocket.create_connection(('127.0.0.1', 9), timeout=1)", TABLES)


def test_starting_a_program_is_blocked() -> None:
    # os.system은 새 프로세스를 못 만들면 0이 아닌 값을 돌려준다.
    with pytest.raises(sandbox.CodeError, match="blocked"):
        sandbox.run_code("import os\nassert os.system('true') == 0, 'blocked'", TABLES)


def test_endless_loop_is_stopped() -> None:
    with pytest.raises(sandbox.CodeError, match="seconds"):
        sandbox.run_code("while True:\n    pass", TABLES, timeout=2)


def test_memory_bomb_is_stopped() -> None:
    with pytest.raises(sandbox.CodeError, match="MemoryError"):
        sandbox.run_code("x = 'a' * (8 * 1024**3)", TABLES)


@pytest.mark.parametrize(
    "value",
    [None, True, 3, 2.5, "글자", pd.Timestamp("2024-01-02 03:04"), dt.date(2024, 1, 2), pd.Timedelta(minutes=5), ("a", 1)],
)
def test_values_survive_the_round_trip(value) -> None:
    frame = pd.DataFrame({"v": pd.Series([value], dtype=object)})
    assert sandbox.decode_frame(sandbox.encode_frame(frame))["v"].tolist() == [value]


def test_engine_shows_the_failing_line_to_the_model() -> None:
    replies = iter(
        [
            '{"action": "python", "code": "x = 1\\nresult = tables[\'표\'][\'없음\']", "save_as": "t"}',
            '{"action": "answer", "table": "표", "note": "끝"}',
        ]
    )
    seen: list[str] = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return next(replies)

    res = step_engine.run("질문", TABLES, chat)
    assert res.stopped == "answered"
    assert seen[-1].startswith("Observation:\nERROR while running the code: KeyError: '없음'")
    assert "failing line 2: result = tables['표']['없음']" in seen[-1]


def test_code_text_is_not_checked() -> None:
    """막는 근거는 운영체제 한도다. 이미 올라온 모듈을 import하는 정상 코드는 돈다."""
    out = step_engine._run_python("import math\nresult = tables['표'].assign(r=math.sqrt(4))", TABLES)
    assert out["r"].tolist() == [2.0, 2.0, 2.0]
