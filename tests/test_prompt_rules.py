"""지시문(프롬프트) 관리 기준을 테스트로 지킨다 (CLAUDE.md "Changing a prompt").

1. 유출 검사: 지시문에 시험 문항·시험 데이터에서 온 단어가 들어가면 실패한다.
   - 한글 단어는 전부 본다.
   - 영어는 `order_amount`, `P001`처럼 밑줄이나 숫자가 든 열 이름·코드만 본다.
     category, status 같은 평범한 영어 단어까지 막으면 지시문을 쓸 수 없어서다.
   - 평범한 영어 문장으로 처리법을 적어 넣는 경우("compute the rate per channel")는
     이 검사가 잡지 못한다. 그건 리뷰에서 본다.
2. 길이 상한: 지시문마다 상한을 둔다. 늘리려면 아래 숫자를 바꿔야 하므로
   커밋에서 눈에 띄고, 커밋 메시지에 이유를 적는다.

예전 계획 작성기 지시문(PLANNER_SYSTEM_PROMPT)은 시험 데이터 단어가 이미 들어 있어
대상에서 뺐다. 새 엔진으로 바꾼 뒤 옛 엔진과 함께 지운다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from core.io import table_structure
from core.routing import request_kind
from tests.benchmark.generate_datasets import ensure_datasets
from tests.benchmark.schema import iter_case_files, load_cases_from_file

# 이름: (지시문, 길이 상한). 상한은 만들 때 길이에 약 10% 여유를 둔 값이다.
GUARDED_PROMPTS = {
    "request_kind": (request_kind.REQUEST_KIND_SYSTEM_PROMPT, 1300),
    "table_structure": (table_structure.SYSTEM_PROMPT, 1800),
}

_HANGUL = re.compile(r"[가-힣][가-힣0-9]+")
_CODE_LIKE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+|[A-Za-z]+[0-9]+[A-Za-z0-9]*")


def _words(text: str) -> set[str]:
    return set(_HANGUL.findall(text)) | set(_CODE_LIKE.findall(text))


@pytest.fixture(scope="module")
def benchmark_words(tmp_path_factory) -> set[str]:
    words: set[str] = set()
    for path in ensure_datasets(Path(tmp_path_factory.mktemp("datasets"))).values():
        for frame in pd.read_excel(path, sheet_name=None, header=None).values():
            for value in frame.to_numpy().ravel():
                if isinstance(value, str):
                    words |= _words(value)
    for case_file in iter_case_files():
        for case in load_cases_from_file(case_file):
            words |= _words(case.question)
    return words


@pytest.mark.parametrize("name", sorted(GUARDED_PROMPTS))
def test_prompt_has_no_benchmark_words(name: str, benchmark_words: set[str]) -> None:
    prompt, _ = GUARDED_PROMPTS[name]
    leaked = sorted(_words(prompt) & benchmark_words)
    assert not leaked, f"{name} prompt contains words from benchmark questions or datasets: {leaked}"


@pytest.mark.parametrize("name", sorted(GUARDED_PROMPTS))
def test_prompt_stays_within_its_length_limit(name: str) -> None:
    prompt, limit = GUARDED_PROMPTS[name]
    assert len(prompt) <= limit, (
        f"{name} prompt is {len(prompt)} chars, limit {limit}. "
        "If the growth is justified, raise the limit here and say why in the commit."
    )


def test_word_check_catches_a_leak(benchmark_words: set[str]) -> None:
    """검사가 실제로 잡는지: 시험 데이터의 업무 용어와 열 이름을 넣은 지시문은 걸려야 한다."""
    leaky = "When the user asks about 집행률, sum order_amount per row."
    assert {"집행률", "order_amount"} <= _words(leaky) & benchmark_words


def test_plain_english_is_not_flagged(benchmark_words: set[str]) -> None:
    """평범한 영어 단어는 시험 데이터에 있어도 막지 않는다."""
    plain = "Each handler returns a category of output; check the status of each step."
    assert not _words(plain) & benchmark_words
