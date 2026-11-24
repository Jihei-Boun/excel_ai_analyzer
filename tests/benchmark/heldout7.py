"""Seventh held-out benchmark: tables and questions written by the user (2026-11-24).

The user made four office tables (customer support log, lot inspection report,
equipment inspection log, document review log) and fifteen questions: simple
ones, compound ones, one that should be asked back, and two whose data is not in
the table. For each question the user wrote the method as numbered steps; those
steps are the `requirements` in cases/heldout7.yaml, each with one cell check, so
an answer can be scored by how many requirements it meets.

The expected values were confirmed by the user and recomputed independently.
tests/test_heldout7_cases.py checks that the confirmed answers pass and that
answers missing one condition fail that requirement.

Do not tune the app or the step-agent prototype to these files or questions.

  python -m tests.benchmark.heldout7    # rewrite the four xlsx files
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import openpyxl

from tests.benchmark import DATASETS_DIR

SOURCE = Path(__file__).resolve().parent / "sources" / "heldout7_office_tables.xlsx"

SHEETS = {
    "표_고객지원": "office_support.xlsx",
    "표_품질검사": "office_quality.xlsx",
    "표_설비점검": "office_equipment.xlsx",
    "표_문서검토": "office_docs.xlsx",
}


def _writer(sheet: str) -> Callable[[Path], None]:
    def write(path: Path) -> None:
        # data_only: 합계·불량률 칸은 수식이다. 수식을 그대로 다시 저장하면 계산값이 사라져
        # 엑셀로 열기 전에는 빈 칸으로 읽힌다. 사용자가 본 값을 그대로 남긴다.
        wb = openpyxl.load_workbook(SOURCE, data_only=True)
        for name in list(wb.sheetnames):
            if name != sheet:
                del wb[name]
        wb.active = 0
        wb.worksheets[0].title = sheet.removeprefix("표_")
        wb.save(path)

    return write


WRITERS: dict[str, Callable[[Path], None]] = {file: _writer(sheet) for sheet, file in SHEETS.items()}


def write_all(datasets_dir: Path | None = None) -> None:
    root = datasets_dir or DATASETS_DIR
    root.mkdir(parents=True, exist_ok=True)
    for name, writer in WRITERS.items():
        writer(root / name)


if __name__ == "__main__":
    write_all()
    print(f"wrote {len(WRITERS)} datasets")
