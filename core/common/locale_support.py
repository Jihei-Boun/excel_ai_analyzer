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
        "schema_ui": {
            "current_data": "현재 데이터",
            "unit_target": "대상",
            "unit_file": "파일",
            "unit_sheet": "시트",
            "column": "컬럼",
            "dtype": "데이터 타입",
            "missing": "결측치",
            "rows": "행 수",
            "cols": "열 수",
            "col_list": "컬럼 목록",
            "common_col": "공통 컬럼",
            "unique_cols": "고유 컬럼",
            "unique_col_count": "고유 컬럼 수",
            "none": "(없음)",
            "type_kind": "유형",
            "numeric": "숫자형",
            "string": "문자형",
            "datetime": "날짜형",
            "other": "기타",
            "numeric_cols": "숫자형 컬럼",
            "string_cols": "문자형 컬럼",
            "datetime_cols": "날짜형 컬럼",
            "other_cols": "기타 컬럼",
            "empty_compare": "비교할 {unit}이(가) 없습니다.",
            "dtypes_one": "`{name}` 컬럼별 데이터 타입·결측치",
            "dtypes_multi": "{unit}별 컬럼 데이터 타입·결측치 ({n}개)",
            "compare_one": "`{name}` 구조: {rows}행 × {cols}열",
            "compare_multi": "{unit}별 행 수·컬럼 목록 비교 ({n}개)",
            "common_one": "`{name}` 컬럼 {n}개",
            "common_multi": "{n}개 {unit} 공통 컬럼 {count}개",
            "only_unit": "{name}만: {cols}",
            "type_groups_multi": "선택된 {unit} {n}개의 컬럼 타입을 구분했습니다.",
            "type_groups_empty": "분류할 컬럼이 없습니다.",
            "missing_rows_none": "`{name}`에서 결측값이 있는 행을 찾지 못했습니다.",
            "missing_rows_found": "결측값이 있는 행 {n:,}개",
        },
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
        "schema_ui": {
            "current_data": "Current data",
            "unit_target": "Target",
            "unit_file": "File",
            "unit_sheet": "Sheet",
            "column": "Column",
            "dtype": "Data type",
            "missing": "Missing",
            "rows": "Rows",
            "cols": "Columns",
            "col_list": "Column list",
            "common_col": "Common columns",
            "unique_cols": "Unique columns",
            "unique_col_count": "Unique column count",
            "none": "(none)",
            "type_kind": "Type",
            "numeric": "Numeric",
            "string": "Text",
            "datetime": "Datetime",
            "other": "Other",
            "numeric_cols": "Numeric columns",
            "string_cols": "Text columns",
            "datetime_cols": "Datetime columns",
            "other_cols": "Other columns",
            "empty_compare": "No {unit} selected to compare.",
            "dtypes_one": "`{name}` — data types and missing counts by column",
            "dtypes_multi": "Data types and missing counts by {unit} ({n})",
            "compare_one": "`{name}` shape: {rows} rows × {cols} columns",
            "compare_multi": "Row counts and column lists by {unit} ({n})",
            "common_one": "`{name}` — {n} columns",
            "common_multi": "{n} {unit}(s): {count} common columns",
            "only_unit": "{name} only: {cols}",
            "type_groups_multi": "Column types for {n} selected {unit}(s).",
            "type_groups_empty": "No columns to classify.",
            "missing_rows_none": "No rows with missing values found in `{name}`.",
            "missing_rows_found": "{n:,} rows with missing values",
        },
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
