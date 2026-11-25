"""시제품: LLM이 표를 보면서 한 단계씩 일하는 엔진.

앱과 분리된 실험이다. 한 번에 계획을 다 쓰는 대신, 모델이 매 단계 하나의 행동을 고르고
코드가 실행해 중간 표를 돌려준다. 모델은 그 결과를 보고 다음 단계를 정한다.

행동 (JSON 한 개):
  {"action": "look", "table": "<이름>", "rows": "all" | N}
  {"action": "python", "code": "...", "save_as": "<새 표 이름>", "why": "..."}
      code 안에서 tables[...] 로 표를 읽고, 마지막에 result 에 DataFrame 을 넣는다.
  {"action": "answer", "table": "<이름>", "note": "..."}

코드가 하는 일은 실행과 사실 확인(행 수 변화, 빈 결과, 오류)을 알려 주는 것뿐이다.
"""

from __future__ import annotations

import json
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from core.analysis import sandbox

MAX_STEPS = 24
PREVIEW_ROWS = 40

SYSTEM_PROMPT = """You are a careful spreadsheet analyst. You answer the user's question about Excel tables
by working step by step with pandas, the way a person works in Excel: look, transform, check, continue.

Each turn you reply with exactly ONE JSON object, one of:
{"action": "look", "table": "<name>", "rows": "all"}
{"action": "python", "why": "<what this step does>", "code": "<python>", "save_as": "<new table name>"}
{"action": "answer", "table": "<name of the final table>", "note": "<one or two sentences for the user>"}
{"action": "ask", "message": "<to the user: what is unclear or missing, and what you need to answer>"}
Use "ask" instead of answering when the question can be read in more than one materially different way,
or when the data it needs is not in the tables. Do not guess and do not answer from made-up data.

Rules for python steps:
- `tables` is a dict of DataFrames; `pd` and `np` are available. Do not import anything.
- Read inputs from `tables[...]`. Put the step's output DataFrame in a variable named `result`.
- Keep each step small: one idea per step. Build helper tables when useful.
- Never type numbers from the table into the code; compute them from the data.
- You will see the step's output (or the error). Check it before moving on. If it is wrong, fix it.

Tables you start with:
- "표": the sheet as the app read it (header guessed by the app; it may be wrong).
- "원본_셀": the raw sheet cells with no header (row 0 = first sheet row). Use it when the header,
  merged cells, subtotal/total rows or notes need your own judgment.

The final table must contain exactly what the question asks for, in the requested layout and order.
Leave out rows and columns the question did not ask for. Answer in the user's language."""



@dataclass
class Step:
    action: str
    detail: dict[str, Any]
    observation: str
    seconds: float


@dataclass
class RunResult:
    answer: pd.DataFrame | None
    note: str
    steps: list[Step] = field(default_factory=list)
    stopped: str = ""


def _column_names(df: pd.DataFrame) -> list[Any]:
    """열 이름을 원래 자료형 그대로 돌려준다. 숫자 열 3을 '3'으로 보여주면 모델이 tables[...]['3']으로 찾다 실패한다."""
    return [c.item() if isinstance(c, np.generic) else c for c in df.columns]


def _preview(df: pd.DataFrame, rows: int | str = PREVIEW_ROWS) -> str:
    shown = df if rows == "all" or len(df) <= int(rows) else df.head(int(rows))
    with pd.option_context("display.max_columns", 60, "display.width", 250, "display.max_colwidth", 40):
        text = shown.to_string()
    more = "" if len(shown) == len(df) else f"\n... ({len(df) - len(shown)} more rows)"
    return f"shape={df.shape}\ncolumns={_column_names(df)}\n{text}{more}"


def _run_python(code: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if re.search(r"\b(import|open|exec|eval|__\w+__|os\.|sys\.|subprocess)\b", code):
        raise ValueError("forbidden statement (import/open/exec/eval/dunder)")
    # 운영체제 한도가 걸린 별도 프로세스에서 실행한다 (core/analysis/sandbox.py).
    return sandbox.run_code(code, tables)


def _error_report(exc: Exception, code: str, tables: dict[str, pd.DataFrame]) -> str:
    """오류와 함께 실패한 코드 줄, 그 시점 표들의 열 이름을 돌려준다."""
    if isinstance(exc, sandbox.CodeError):  # 이미 "KeyError: '열'" 형태로 와 있다
        message = str(exc)
    else:
        message = traceback.format_exception_only(type(exc), exc)[-1].strip()
    line_no = getattr(exc, "line", None)  # 작업용 프로세스가 알려 준 줄 번호
    lines = code.splitlines()
    where = ""
    if line_no and 0 < line_no <= len(lines):
        where = f"\nfailing line {line_no}: {lines[line_no - 1].strip()}"
    cols = "\n".join(f'- "{name}": {_column_names(df)}' for name, df in tables.items())
    return f"ERROR while running the code: {message}{where}\nColumns of the tables you can use:\n{cols}"


def _parse_action(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("no JSON object in reply")
        data = json.loads(match.group(0))
    if not isinstance(data, dict) or "action" not in data:
        raise ValueError("reply must be a JSON object with an `action`")
    return data


def run(
    question: str,
    tables: dict[str, pd.DataFrame],
    chat: Callable[[list[dict[str, str]]], str],
    *,
    max_steps: int = MAX_STEPS,
    on_step: Callable[[Step], None] | None = None,
) -> RunResult:
    tables = dict(tables)
    intro = [f"Question: {question}", "", "Starting tables:"]
    for name, df in tables.items():
        intro.append(f'--- "{name}" ---')
        intro.append(_preview(df, "all" if len(df) <= 60 else PREVIEW_ROWS))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(intro)},
    ]
    steps: list[Step] = []
    last_error = ""
    last_code = None
    for _ in range(max_steps):
        t0 = time.time()
        reply = chat(messages)
        messages.append({"role": "assistant", "content": reply})
        try:
            act = _parse_action(reply)
        except Exception as exc:  # noqa: BLE001
            obs = f"ERROR: {exc}. Reply with one JSON object."
            steps.append(Step("invalid", {"raw": reply[:500]}, obs, time.time() - t0))
            messages.append({"role": "user", "content": obs})
            continue

        kind = str(act.get("action"))
        if kind == "ask":
            # 되묻기·자료 없음: 표 없이 사용자에게 할 말만 돌려준다. 맞는지는 사람이 판정한다.
            steps.append(Step(kind, act, "final", time.time() - t0))
            if on_step:
                on_step(steps[-1])
            return RunResult(None, str(act.get("message") or ""), steps, "asked")
        if kind == "answer":
            name = str(act.get("table"))
            if name not in tables:
                obs = f"ERROR: no table named {name!r}. Tables: {list(tables)}"
                steps.append(Step(kind, act, obs, time.time() - t0))
                messages.append({"role": "user", "content": obs})
                continue
            steps.append(Step(kind, act, "final", time.time() - t0))
            return RunResult(tables[name], str(act.get("note") or ""), steps, "answered")

        if kind == "look":
            name = str(act.get("table"))
            obs = _preview(tables[name], act.get("rows") or "all") if name in tables else (
                f"ERROR: no table named {name!r}. Tables: {list(tables)}"
            )
        elif kind == "python":
            name = str(act.get("save_as") or f"step{len(steps) + 1}")
            code = str(act.get("code") or "")
            repeated = code == last_code
            last_code = code
            try:
                out = _run_python(code, tables)
                tables[name] = out
                checks = []
                if out.empty:
                    checks.append("CHECK: the result is empty.")
                if out.columns.duplicated().any():
                    checks.append("CHECK: duplicate column names.")
                if repeated:
                    # 사실만 알린다: 같은 코드는 같은 결과를 낸다. 다음에 무엇을 할지는 모델이 정한다.
                    checks.append("CHECK: this is exactly the same code as your previous step, so the result is the same as before.")
                obs = f'saved as "{name}"\n' + _preview(out) + ("\n" + "\n".join(checks) if checks else "")
            except Exception as exc:  # noqa: BLE001
                obs = _error_report(exc, code, tables)
                if obs == last_error:
                    obs += "\nThis is the same error as your previous step. Do not repeat that code; inspect the data (look) or take a different approach."
                last_error = obs
        else:
            obs = f"ERROR: unknown action {kind!r}."
        steps.append(Step(kind, act, obs, time.time() - t0))
        if on_step:
            on_step(steps[-1])
        messages.append({"role": "user", "content": "Observation:\n" + obs})
    return RunResult(None, "", steps, "step limit")


def ollama_chat(model: str, base_url: str = "http://localhost:11434", timeout: int = 600, think: bool | None = None):
    import requests

    def _chat(messages: list[dict[str, str]]) -> str:
        payload = {
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_ctx": 32768},
            "messages": messages,
        }
        if think is not None:  # None이면 모델 기본값(qwen3는 켜짐)
            payload["think"] = think
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        return str(resp.json()["message"]["content"] or "")

    return _chat
