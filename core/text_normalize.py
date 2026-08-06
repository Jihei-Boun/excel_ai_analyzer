"""텍스트 정규화·키워드 매칭 유틸."""

from __future__ import annotations

import re

# 한글 단어 중간에 끼어도 접미사로 허용 (X별 집계 등)
_SHORT_SUFFIX_OK = frozenset({"별"})

# 단일 글자 키워드 뒤에 올 수 있는 조사·어미
_JOSA_AFTER = "을를이가은는만로에의과와으로부터까지도"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def keyword_in_text(text: str, keyword: str) -> bool:
    """의도 키워드가 프롬프트에 실제로 쓰였는지 판별한다.

    - 영문 알파벳 토큰: 단어 경계(``\\b``) 매칭
    - 2글자 이상: 부분 문자열 허용
    - 1글자 한글(행/열/표 등): 한글 단어 *중간* 삽입 오탐 방지.
      ``표로``, ``행 개수``는 허용하고 ``집행률``의 ``행``은 거부.
    - ``별``: 접미사(비용명별) 허용
    """
    if not text or not keyword:
        return False
    key = str(keyword)
    key_l = key.lower()
    text_l = text.lower()

    # 영문 단어(공백 포함 구는 부분문자열)
    if key_l.isascii() and all(ch.isalpha() or ch.isspace() or ch == "-" for ch in key_l):
        if " " in key_l or "-" in key_l:
            return key_l in text_l
        return bool(re.search(rf"\b{re.escape(key_l)}\b", text_l))

    if len(key) >= 2:
        return key in text or key_l in text_l

    # 단일 글자
    if key in _SHORT_SUFFIX_OK:
        return key in text

    pattern = rf"(?<![가-힣]){re.escape(key)}(?:[{_JOSA_AFTER}]|(?![가-힣]))"
    return bool(re.search(pattern, text))


def any_keyword_in_text(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    return any(keyword_in_text(text, kw) for kw in keywords)


_normalize_text = normalize_text
