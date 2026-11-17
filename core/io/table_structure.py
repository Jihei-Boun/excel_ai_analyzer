"""Read a sheet's table layout with an LLM, then verify it before trusting it.

The LLM decides where the header rows are, where the data starts and ends, and
which rows inside the table are group labels, subtotals, totals or blank.
Code adds no layout rules of its own. It only checks facts that must hold for
any correct reading:

- every row outside the table holds no numbers (no data was left out)
- columns outside the table hold no numbers inside the table rows
- a row called a subtotal or total equals the sum or mean of the rows it covers
  in at least one numeric column
- a group label row has exactly one non-empty cell
- a blank row is empty
- every column that holds data gets a non-empty name

Only rows whose role these checks can prove are dropped. A row the LLM might
call a remark stays a record, because a record with empty values looks the same.
When a check fails, the LLM is told which one and asked once more.

If the LLM is unavailable, answers in the wrong shape, or a check fails, the
caller falls back to the old loader and reports that the layout is unverified.
"""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from openpyxl.utils import column_index_from_string, get_column_letter

from core.constants import DEFAULT_OLLAMA_BASE_URL

ATTRS_KEY = "table_structure"
ENABLE_ENV = "EXCEL_ANALYZER_STRUCTURE"  # "off" disables the LLM step
MODEL_ENV = "EXCEL_ANALYZER_STRUCTURE_MODEL"
BASE_URL_ENV = "EXCEL_ANALYZER_OLLAMA_URL"
DEFAULT_MODEL = "qwen2.5-coder:32b"
TIMEOUT_SEC = 180

HEAD_ROWS = 40
TAIL_ROWS = 15
FULL_SHEET_ROWS = HEAD_ROWS + TAIL_ROWS
MAX_COLUMNS = 40
MAX_CELL_CHARS = 40

ROLES = ("group_label", "subtotal", "total", "blank")
RETRIES = 1
_SUMMARY_ROLES = ("subtotal", "total")

AskFn = Callable[[str, str], dict[str, Any]]


class StructureError(Exception):
    """The LLM answer is unusable or fails verification."""


@dataclass
class Grid:
    values: dict[tuple[int, int], Any]
    max_row: int
    max_col: int
    merges: list[tuple[int, int, int, int]]  # min_row, min_col, max_row, max_col

    def get(self, row: int, col: int) -> Any:
        return self.values.get((row, col))

    def row_cells(self, row: int, first: int = 1, last: int | None = None) -> list[tuple[int, Any]]:
        last = self.max_col if last is None else last
        return [(c, self.values[(row, c)]) for c in range(first, last + 1) if (row, c) in self.values]


@dataclass
class Structure:
    header_rows: list[int]
    first_col: int
    last_col: int
    first_row: int
    last_row: int
    roles: dict[int, str] = field(default_factory=dict)
    group_column: str = ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() not in {"off", "0", "false", "no"}


_CACHE: dict[tuple[str, str, str], tuple[pd.DataFrame, dict[str, Any]]] = {}


def read_with_llm(
    path: str | Path,
    sheet_name: str | int,
    *,
    ask: AskFn | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return (raw DataFrame, info). Raises StructureError when it cannot be trusted.

    The DataFrame still needs the loader's usual cleaning (sanitize_dataframe).
    """
    path = Path(path)
    model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
    # Only answers from the configured model are cached; a caller-supplied `ask` always runs.
    key = (_file_digest(path), str(sheet_name), model) if ask is None else None
    if key is not None and key in _CACHE:
        frame, info = _CACHE[key]
        return frame.copy(), dict(info)

    grid = read_grid(path, sheet_name)
    if ask is None:
        ask = _ollama_ask(model)
    snapshot = render_snapshot(grid)
    prompt = snapshot
    attempts: list[str] = []
    while True:
        try:
            answer = ask(SYSTEM_PROMPT, prompt)
        except Exception as exc:  # network, timeout, bad JSON
            raise StructureError(f"llm_unavailable: {type(exc).__name__}") from exc
        try:
            structure = parse_answer(answer, grid)
            verify(structure, grid)
            break
        except StructureError as exc:
            attempts.append(str(exc))
            if len(attempts) > RETRIES:
                raise StructureError("; ".join(attempts)) from exc
            prompt = (
                f"{snapshot}\n\nYour previous answer was:\n{_json(answer)}\n"
                f"It failed this check: {exc}\nLook at the cells again and answer with corrected JSON."
            )
    frame, info = build_frame(structure, grid)
    info["model"] = model
    info["attempts"] = len(attempts) + 1
    if key is not None:
        _CACHE[key] = (frame, info)
    return frame.copy(), dict(info)


def _json(answer: Any) -> str:
    import json

    try:
        return json.dumps(answer, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(answer)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ollama_ask(model: str) -> AskFn:
    from core.llm_client import chat_json

    base_url = os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_OLLAMA_BASE_URL

    def ask(system: str, prompt: str) -> dict[str, Any]:
        return chat_json(prompt, system=system, base_url=base_url, model=model, timeout=TIMEOUT_SEC)

    return ask


# ---------------------------------------------------------------------------
# Reading the sheet and showing it to the LLM
# ---------------------------------------------------------------------------


def read_grid(path: Path, sheet_name: str | int) -> Grid:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=False, data_only=True)
    try:
        sheet = workbook.worksheets[sheet_name] if isinstance(sheet_name, int) else workbook[sheet_name]
        values: dict[tuple[int, int], Any] = {}
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                values[(cell.row, cell.column)] = value.strip() if isinstance(value, str) else value
        merges = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in sheet.merged_cells.ranges]
        max_row = max((r for r, _ in values), default=0)
        max_col = max((c for _, c in values), default=0)
        return Grid(values=values, max_row=max_row, max_col=max_col, merges=merges)
    finally:
        workbook.close()


def render_snapshot(grid: Grid) -> str:
    """Rows as `R<n>: A1=... | B1=...`, with merged ranges listed first."""
    lines = [f"Sheet size: {grid.max_row} rows x {grid.max_col} columns (last column {get_column_letter(max(grid.max_col, 1))})."]
    if grid.merges:
        shown = [
            f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}"
            for r1, c1, r2, c2 in sorted(grid.merges)
            if r1 <= HEAD_ROWS or r1 > grid.max_row - TAIL_ROWS
        ]
        if shown:
            lines.append("Merged ranges: " + ", ".join(shown))
    lines.append("Cells (empty cells omitted):")
    if grid.max_row <= FULL_SHEET_ROWS:
        rows = list(range(1, grid.max_row + 1))
    else:
        rows = list(range(1, HEAD_ROWS + 1)) + list(range(grid.max_row - TAIL_ROWS + 1, grid.max_row + 1))
    previous = 0
    for row in rows:
        if row != previous + 1:
            lines.append(f"... rows {previous + 1}-{row - 1} not shown ...")
        previous = row
        cells = grid.row_cells(row, 1, min(grid.max_col, MAX_COLUMNS))
        if not cells:
            lines.append(f"R{row}: (empty)")
            continue
        parts = [f"{get_column_letter(c)}{row}={_show(v)}" for c, v in cells]
        lines.append(f"R{row}: " + " | ".join(parts))
    return "\n".join(lines)


def _show(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    text = text.replace("\n", " ")
    if len(text) > MAX_CELL_CHARS:
        text = text[: MAX_CELL_CHARS - 1] + "…"
    return text


SYSTEM_PROMPT = """You read the layout of one spreadsheet table so that it can be analysed.
You are shown the sheet's non-empty cells as `R<row>: <cell>=<value> | ...`, plus merged ranges.
Cell text may be in any language.

Find the one main table and answer with JSON only, in exactly this shape:
{
  "header_rows": [<row numbers of the column header, top to bottom; one or more consecutive rows>],
  "first_column": "<letter of the table's first column>",
  "last_column": "<letter of the table's last column>",
  "first_data_row": <first row after the header>,
  "last_data_row": <last row that belongs to the table, including any total rows at its end>,
  "special_rows": [{"row": <n>, "role": "group_label" | "subtotal" | "total" | "blank"}],
  "group_column_name": "<name for the group labels as a column, or empty>"
}

Meaning of special_rows (list only rows between first_data_row and last_data_row that are not ordinary records):
- group_label: a row that only names the group the following records belong to (one cell filled, no values).
- subtotal: a row that sums or averages the records of one group.
- total: a row that sums or averages all records of the table.
- blank: an empty row inside the table.
Every other row inside the table is a record, even if some of its cells are empty.
Rows above the header (titles, dates, units) and remarks or sign-off lines below the table are outside the table; do not list them.
group_column_name: if there are group_label rows, a short name for what the labels are, in the language of the sheet; otherwise "".
Rows marked "not shown" are ordinary records; do not guess about them.
"""


# ---------------------------------------------------------------------------
# Parsing and verifying the answer
# ---------------------------------------------------------------------------


def parse_answer(answer: Any, grid: Grid) -> Structure:
    if not isinstance(answer, dict):
        raise StructureError("answer_not_object")
    try:
        header_rows = [int(r) for r in answer["header_rows"]]
        first_col = _col(answer["first_column"])
        last_col = _col(answer["last_column"])
        first_row = int(answer["first_data_row"])
        last_row = int(answer["last_data_row"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StructureError(f"answer_shape: {exc}") from exc
    roles: dict[int, str] = {}
    for item in answer.get("special_rows") or []:
        if not isinstance(item, dict):
            raise StructureError("special_row_shape")
        role = str(item.get("role", "")).strip()
        if role not in ROLES:
            raise StructureError(f"unknown_role: {role}")
        try:
            roles[int(item["row"])] = role
        except (KeyError, TypeError, ValueError) as exc:
            raise StructureError("special_row_shape") from exc
    group = str(answer.get("group_column_name") or "").strip()
    return Structure(header_rows, first_col, last_col, first_row, last_row, roles, group)


def _col(value: Any) -> int:
    text = str(value).strip().upper()
    if not text.isalpha():
        raise ValueError(f"bad column {value!r}")
    return column_index_from_string(text)


def verify(s: Structure, grid: Grid) -> None:
    """Raise StructureError naming the first fact that does not hold."""
    if not s.header_rows:
        raise StructureError("no_header_rows")
    if any(b != a + 1 for a, b in zip(s.header_rows, s.header_rows[1:])):
        raise StructureError("header_rows_not_consecutive")
    if s.header_rows[0] < 1 or s.header_rows[-1] >= s.first_row:
        raise StructureError("header_not_above_data")
    if not (1 <= s.first_col <= s.last_col) or not (s.first_row <= s.last_row <= max(grid.max_row, 1)):
        raise StructureError("bad_bounds")
    if any(r < s.first_row or r > s.last_row for r in s.roles):
        raise StructureError("special_row_outside_table")

    # No data left out: numbers outside the table's rows or columns mean the bounds are wrong.
    header_set = set(s.header_rows)
    for (row, col), value in grid.values.items():
        if not _is_number(value):
            continue
        inside_rows = s.first_row <= row <= s.last_row
        inside_cols = s.first_col <= col <= s.last_col
        if row in header_set and inside_cols:
            continue
        if not (inside_rows and inside_cols):
            raise StructureError(f"number_outside_table: {get_column_letter(col)}{row}")

    # Rows above the first header row must not be part of a multi-row header the LLM skipped:
    # nothing to check here beyond the numbers rule above.

    for row, role in s.roles.items():
        cells = grid.row_cells(row, s.first_col, s.last_col)
        if role == "blank" and cells:
            raise StructureError(f"blank_row_not_empty: R{row}")
        if role == "group_label" and (len(cells) != 1 or _is_number(cells[0][1])):
            raise StructureError(f"group_label_not_single_text: R{row}")
    if any(role == "group_label" for role in s.roles.values()) and not s.group_column:
        raise StructureError("group_column_name_missing")

    records = _record_rows(s)
    if not records:
        raise StructureError("no_records")
    for row, role in s.roles.items():
        if role in _SUMMARY_ROLES:
            covered = _covered_rows(s, row, records)
            if not _summarises(grid, row, covered, s.first_col, s.last_col):
                raise StructureError(f"{role}_does_not_add_up: R{row}")

    names = column_names(s, grid)
    for offset, name in enumerate(names):
        col = s.first_col + offset
        has_data = any((r, col) in grid.values for r in records)
        if has_data and not name:
            raise StructureError(f"unnamed_column: {get_column_letter(col)}")


def _record_rows(s: Structure) -> list[int]:
    return [r for r in range(s.first_row, s.last_row + 1) if r not in s.roles]


def _covered_rows(s: Structure, row: int, records: list[int]) -> list[int]:
    """Records a summary row can cover: for a subtotal, the records since the previous
    group boundary; for a total, every record above it."""
    if s.roles[row] == "total":
        return [r for r in records if r < row]
    start = s.first_row
    for r in range(row - 1, s.first_row - 1, -1):
        if s.roles.get(r) in ("subtotal", "total", "group_label"):
            start = r + 1
            break
    return [r for r in records if start <= r < row]


def _summarises(grid: Grid, row: int, covered: list[int], first_col: int, last_col: int) -> bool:
    if not covered:
        return False
    for col in range(first_col, last_col + 1):
        value = _number(grid.get(row, col))
        if value is None:
            continue
        numbers = [n for n in (_number(grid.get(r, col)) for r in covered) if n is not None]
        if not numbers:
            continue
        total = sum(numbers)
        mean = total / len(numbers)
        if _same(value, total) or _same(value, mean):
            return True
    return False


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def _number(value: Any) -> float | None:
    """The cell's number, also for numbers stored as text such as "1,250" (as the loader reads them)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return None if math.isnan(number) or math.isinf(number) else number
    return None


def _is_number(value: Any) -> bool:
    return _number(value) is not None


# ---------------------------------------------------------------------------
# Building the table
# ---------------------------------------------------------------------------


def column_names(s: Structure, grid: Grid) -> list[str]:
    """Join the header rows top to bottom with "_" (merged cells fill their whole range)."""
    filled: dict[tuple[int, int], Any] = {}
    for r1, c1, r2, c2 in grid.merges:
        value = grid.get(r1, c1)
        if value is None:
            continue
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                filled[(r, c)] = value
    names: list[str] = []
    for col in range(s.first_col, s.last_col + 1):
        parts: list[str] = []
        for row in s.header_rows:
            value = grid.get(row, col)
            if value is None:
                value = filled.get((row, col))
            text = _show_full(value)
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        names.append("_".join(parts))
    return names


def _show_full(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def build_frame(s: Structure, grid: Grid) -> tuple[pd.DataFrame, dict[str, Any]]:
    names = column_names(s, grid)
    records = _record_rows(s)
    groups: dict[int, Any] = {}
    current: Any = None
    for row in range(s.first_row, s.last_row + 1):
        if s.roles.get(row) == "group_label":
            current = grid.row_cells(row, s.first_col, s.last_col)[0][1]
        elif row not in s.roles:
            groups[row] = current

    keep = [i for i, col in enumerate(range(s.first_col, s.last_col + 1))
            if names[i] or any((r, col) in grid.values for r in records)]
    columns = [names[i] for i in keep]
    data = [[grid.get(r, s.first_col + i) for i in keep] for r in records]
    frame = pd.DataFrame(data, columns=columns)
    has_groups = any(role == "group_label" for role in s.roles.values())
    group_column = _free_name(s.group_column, columns) if has_groups else ""
    if group_column:
        frame.insert(0, group_column, [groups[r] for r in records])

    excluded = {role: [r for r, x in sorted(s.roles.items()) if x == role] for role in ROLES}
    info = {
        "source": "llm",
        "header_rows": s.header_rows,
        "range": f"{get_column_letter(s.first_col)}{s.first_row}:{get_column_letter(s.last_col)}{s.last_row}",
        "record_rows": len(records),
        "excluded_rows": {k: v for k, v in excluded.items() if v},
        "group_column": group_column or None,
    }
    return frame, info


def _free_name(name: str, taken: list[str]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name}_{n}" in taken:
        n += 1
    return f"{name}_{n}"
