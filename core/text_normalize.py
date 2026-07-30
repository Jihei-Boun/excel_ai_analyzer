"""텍스트 정규화 유틸."""

from __future__ import annotations

import re


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


_normalize_text = normalize_text
