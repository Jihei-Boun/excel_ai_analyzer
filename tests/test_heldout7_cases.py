"""heldout7 채점 기준 자체를 확인한다 (모델은 부르지 않는다).

- 사용자가 확인한 정답 표는 전체 판정과 모든 요구사항 검사를 통과해야 한다.
- 조건 하나를 빠뜨린 오답 표는 그 요구사항에서 떨어지고, 전체 판정도 오답이어야 한다.
- 빈 답은 어떤 요구사항도 통과하면 안 된다 (부재 검사만으로 점수가 부풀지 않게).
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests.benchmark import CASES_DIR
from tests.benchmark.evaluate import _eval_cells, score_requirements
from tests.benchmark.heldout7 import SHEETS, SOURCE, WRITERS, write_all
from tests.benchmark.schema import load_cases_from_file

CASES = {c.id[9:11]: c for c in load_cases_from_file(CASES_DIR / "heldout7.yaml")}

RIGHT = {
    "01": pd.DataFrame({"건수": [4]}),
    "02": pd.DataFrame({"로트 번호": ["가-03", "가-05", "나-02", "나-05"]}),
    "03": pd.DataFrame({"정지 시간 합계(분)": [187]}),
    "04": pd.DataFrame({"문서명": ["치수 샘플링 기준", "절연 검사 기준"]}),
    "05": pd.DataFrame({"접수번호": ["문의-737"], "초과 시간": [30], "처리 시간": [210]}),
    "06": pd.DataFrame({"생산 라인": ["라인 2", "라인 1"], "검사": [175, 320], "불량": [6, 7], "불량률(%)": [3.43, 2.19]}),
    "07": pd.DataFrame({"설비명": ["순환 펌프", "집진기", "열교환기"], "건수": [2, 2, 1], "정지 합계": [85, 75, 42]}),
    "08": pd.DataFrame(
        {
            "문서 번호": ["문서-532", "문서-535", "문서-530"],
            "문서명": ["배선 고정 절차", "치공구 세척 절차", "센서 조립 절차"],
            "지연 일수": [3, 2, 1],
        }
    ),
    "09": pd.DataFrame({"문의 유형": ["접속 오류", "기능 문의", "계정 변경"], "평균 처리 시간": [131.0, 71.0, 59.0]}),
    "10": pd.DataFrame({"로트 수": [2]}),
    "11": pd.DataFrame({"점검 번호": ["점검-303", "점검-309"], "설비명": ["온도 제어기", "온도 제어기"]}),
    "14": pd.DataFrame({"생산 라인": ["라인 1", "라인 2"], "불량 수량": [11, 12]}),
}

# (문항, 빠뜨린 요구사항 번호(0부터), 그 실수로 나오는 표). 값은 원본 표에서 계산한 것.
WRONG = [
    ("01", 0, pd.DataFrame({"건수": [6]})),  # 채널 조건 없음
    ("03", 1, pd.DataFrame({"점검 시간 합계": [361]})),  # 정지 시간 대신 점검 시간
    ("05", 2, pd.DataFrame({"접수번호": ["문의-737", "문의-739"], "초과": [30, 25], "처리": [210, None]})),  # 처리 중 포함
    ("06", 1, pd.DataFrame({"라인": ["라인 2", "라인 1"], "검사": [235, 320], "불량": [10, 7]})),  # 기간 조건 없음
    ("06", 6, pd.DataFrame({"라인": ["라인 1", "라인 2"], "검사": [320, 175], "불량": [7, 6], "불량률": [0.021875, 0.0342857]})),
    ("07", 2, pd.DataFrame({"설비": ["열교환기", "순환 펌프", "집진기", "공기 압축기"], "건수": [2, 2, 2, 1], "합계": [92, 85, 75, 55]})),
    ("07", 4, pd.DataFrame({"설비": ["순환 펌프", "열교환기", "집진기"], "건수": [2, 1, 1], "합계": [85, 42, 45]})),  # 30 초과로 거름
    ("08", 1, pd.DataFrame({"문서 번호": ["문서-532", "문서-535", "문서-530", "문서-534"], "지연": [3, 2, 1, 1]})),
    ("10", 0, pd.DataFrame({"로트 수": [3]})),
    ("14", 0, pd.DataFrame({"생산 라인": ["라인 1", "라인 2"], "불량 수량": [22, 24]})),  # 소계까지 더함
]


def test_case_file_loads_and_every_dataset_has_a_writer() -> None:
    assert len(CASES) == 15
    for case in CASES.values():
        assert case.dataset in WRITERS
        assert case.expected.requirements


def test_written_tables_keep_the_values_the_user_saw(tmp_path) -> None:
    """합계·불량률 칸은 수식이다. 다시 저장해도 계산값이 남아 있어야 한다."""
    write_all(tmp_path)
    for sheet, file in SHEETS.items():
        want = pd.read_excel(SOURCE, sheet_name=sheet, header=None)
        got = pd.read_excel(tmp_path / file, header=None)
        assert got.shape == want.shape
        assert got.isna().equals(want.isna()), file
        for a, b in zip(want.stack(), got.stack()):
            assert a == pytest.approx(b) if isinstance(a, float) else a == b, (file, a, b)


# 수식 칸 중 일부. 원본과 결과를 서로만 비교하면 둘 다 비었을 때도 통과한다
# (실제로 그랬다: 원본을 다시 저장하면서 수식의 계산값이 지워졌다). 그래서 사용자가
# 표에서 본 값을 직접 적어 둔다. (엑셀 행, 열) 기준.
FORMULA_VALUES = {
    "office_quality.xlsx": {(6, "F"): 79, (6, "H"): 0.0125, (12, "E"): 480, (19, "G"): 12, (20, "E"): 935},
    "office_equipment.xlsx": {(18, "F"): 689, (18, "G"): 410},
}


@pytest.mark.parametrize("file", sorted(FORMULA_VALUES))
def test_formula_cells_have_their_values(file: str, tmp_path) -> None:
    write_all(tmp_path)
    grid = pd.read_excel(tmp_path / file, header=None)
    for (row, col), want in FORMULA_VALUES[file].items():
        got = grid.iat[row - 1, ord(col) - ord("A")]
        assert got == pytest.approx(want), f"{file} {col}{row}: {got!r}, want {want}"


@pytest.mark.parametrize("num", sorted(RIGHT))
def test_confirmed_answer_passes_everything(num: str) -> None:
    case = CASES[num]
    ok, detail, _ = _eval_cells(case.expected.expected_result, RIGHT[num])
    assert ok, detail
    score = score_requirements(case.expected.requirements, RIGHT[num])
    assert score["met"] == score["checked"] == len(case.expected.requirements), score["items"]


@pytest.mark.parametrize(("num", "missed", "table"), WRONG)
def test_missing_one_condition_fails_that_requirement(num: str, missed: int, table: pd.DataFrame) -> None:
    case = CASES[num]
    assert not _eval_cells(case.expected.expected_result, table)[0]
    score = score_requirements(case.expected.requirements, table)
    assert score["items"][missed]["ok"] is False, score["items"][missed]


@pytest.mark.parametrize("num", sorted(RIGHT))
def test_empty_answer_meets_nothing(num: str) -> None:
    for empty in (None, pd.DataFrame({"x": []})):
        assert score_requirements(CASES[num].expected.requirements, empty)["met"] == 0


def test_ask_back_cases_are_left_to_a_person() -> None:
    for num in ("12", "13", "15"):
        case = CASES[num]
        assert case.expected.manual_grade
        assert score_requirements(case.expected.requirements, None)["checked"] == 0
