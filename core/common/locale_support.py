"""응답 언어(locale) 프리셋 — 프로필 ``locale`` / ``language_instruction`` 주입."""

from __future__ import annotations

from typing import Any

# 지원 locale. 미지정·미지원은 ko.
SUPPORTED_LOCALES = frozenset({"ko", "en"})

_LOCALE_PRESETS: dict[str, dict[str, Any]] = {
    "ko": {
        "language_name": "Korean",
        "language_instruction": (
            "반드시 한국어만 사용하세요. 중국어·영어·기타 언어로 쓰지 마세요. "
            "데이터에 포함된 고유명·코드는 원문 그대로 두세요."
        ),
        "interpret_role": "당신은 표 계산 결과를 해석하는 분석가입니다.",
        "interpret_style": "한국어로 간결히 작성하세요.",
        "interpret_user_prefix": "다음 계산 결과만 근거로 사용자 요청에 답하세요.",
        "meaning_system": (
            "You explain spreadsheet column meanings in Korean. "
            "Return plain text only (no JSON, no code, no tables). "
            "For each column write one short bullet: `컬럼명`: 의미. "
            "Use dtype and samples as hints; mark uncertain items as 추정. "
            "Do not filter, aggregate, or invent columns."
        ),
        "meaning_user_suffix": "Explain each column briefly in Korean.",
        "dataframe_request_hint": (
            "'리스트', '목록', '표', '보여줘', 'list', 'table', 'show' "
            "요청은 반드시 DataFrame으로 반환하세요."
        ),
        "plan_language_note": (
            "criteria_note and any natural-language fields in the JSON "
            "should be written in Korean when describing the plan."
        ),
        "display_labels": {
            "rate": "비율",
            "item": "항목",
            "denominator": "분모",
            "item_count": "항목수",
            "zero_rate_count": "0%비율수",
            "zero_denominator_sum": "0%분모합",
            "max_rate": "최대비율",
            "min_rate": "최소비율",
            "diff": "차이",
            "split_label": "구분",
        },
        "intent_keywords_extra": (),
    },
    "en": {
        "language_name": "English",
        "language_instruction": (
            "Respond only in English. Do not use Korean, Chinese, or other "
            "languages unless quoting data values or column names."
        ),
        "interpret_role": "You are an analyst who explains tabular calculation results.",
        "interpret_style": "Write concisely in English.",
        "interpret_user_prefix": (
            "Answer the user request using only the calculation results below."
        ),
        "meaning_system": (
            "You explain spreadsheet column meanings in English. "
            "Return plain text only (no JSON, no code, no tables). "
            "For each column write one short bullet: `column`: meaning. "
            "Use dtype and samples as hints; mark uncertain items as estimated. "
            "Do not filter, aggregate, or invent columns."
        ),
        "meaning_user_suffix": "Explain each column briefly in English.",
        "dataframe_request_hint": (
            "Requests with 'list', 'table', 'show', 'display' "
            "must return a DataFrame."
        ),
        "plan_language_note": (
            "criteria_note and any natural-language fields in the JSON "
            "should be written in English when describing the plan."
        ),
        "display_labels": {
            "rate": "rate",
            "item": "item",
            "denominator": "denominator",
            "item_count": "item count",
            "zero_rate_count": "zero-rate count",
            "zero_denominator_sum": "zero-rate denominator sum",
            "max_rate": "max rate",
            "min_rate": "min rate",
            "diff": "difference",
            "split_label": "split",
        },
        "intent_keywords_extra": (
            "explain",
            "find",
            "calculate",
            "versus",
            "vs",
            "difference",
            "share",
            "remaining",
            "balance",
            "per group",
            "top",
            "bottom",
        ),
    },
}


def normalize_locale(value: str | None) -> str:
    key = str(value or "ko").strip().lower().replace("_", "-")
    if key.startswith("en"):
        return "en"
    if key.startswith("ko") or key in {"kr", "kor"}:
        return "ko"
    if key in SUPPORTED_LOCALES:
        return key
    return "ko"


def locale_preset(locale: str | None) -> dict[str, Any]:
    return dict(_LOCALE_PRESETS[normalize_locale(locale)])
