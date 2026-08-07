"""스키마·메타 요청 (행 수·컬럼 목록·공통 컬럼·dtype/결측·타입 분류) 규칙 경로.

컬럼 의미 추정은 규칙으로 답하지 않고 LLM 경로로 보낸다.
"""

from __future__ import annotations

import re

import pandas as pd

from core.routing.prompt_intent import detect_aggregate_op, expects_plot
from core.io.text_normalize import normalize_text

_SCHEMA_SIGNAL_PHRASES = (
    "행수",
    "열수",
    "컬럼목록",
    "컬럼리스트",
    "열목록",
    "컬럼명",
    "공통컬럼",
    "공통으로있는컬럼",
    "데이터타입",
    "결측치",
    "결측",
    "스키마",
    "구조",
    "dtype",
    "null",
    "missing",
    "columnlist",
    "colnames",
    "commoncolumns",
    # 타입 분류
    "숫자컬럼",
    "문자컬럼",
    "숫자형컬럼",
    "문자형컬럼",
    "수치형컬럼",
    "날짜형컬럼",
    "날짜컬럼",
    "컬럼을구분",
    "컬럼구분",
    "타입구분",
    "형구분",
)

# 스키마가 아닌 분석 요청으로 보이는 표현 (컬럼별 집계 등)
_NON_SCHEMA_MARKERS = (
    "컬럼별",
    "별로",
    "합계",
    "합산",
    "총합",
    "평균",
    "차트",
    "그래프",
    "매출",
    "뽑아",
    "나열",
)

_TYPE_GROUP_PHRASES = (
    "숫자컬럼",
    "문자컬럼",
    "숫자형컬럼",
    "문자형컬럼",
    "수치형컬럼",
    "날짜형컬럼",
    "날짜컬럼",
    "컬럼을구분",
    "컬럼구분",
    "타입구분",
    "형구분",
    "숫자와문자",
    "숫자문자",
)

_MEANING_PHRASES = (
    "컬럼의미",
    "컬럼설명",
    "의미인지",
    "의미추측",
    "추측해서설명",
    "의미를설명",
    "무엇을의미",
    "어떤의미",
    "컬럼의도",
    "의미알려",
    "columnmeaning",
    "columnmeans",
    "whatcolumnmeans",
    "whateachcolumnmeans",
    "explaincolumn",
    "explaincolumns",
    "explainwhateachcolumn",
    "columndefinition",
    "columndefinitions",
    "describecolumn",
    "describecolumns",
)

def _column_meaning_rules(
    *,
    profile_name: str | None = None,
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """컬럼 의미 규칙. YAML(profiles/)에서 로드한다."""
    from core.profile_loader import load_meaning_rules

    return load_meaning_rules(
        profile_name=profile_name,
    )


def is_column_meaning_request(prompt: str) -> bool:
    """컬럼 의미 추정·설명 요청인지. 규칙 스키마가 아니라 LLM으로 보낸다."""
    if not prompt or not str(prompt).strip():
        return False
    compact = re.sub(r"\s+", "", normalize_text(prompt))
    return _is_meaning_request(compact)


def is_schema_request(prompt: str) -> bool:
    """행·열·컬럼 구조/메타 질문인지 판별한다. 집계·차트·의미 추정은 제외."""
    if not prompt or not str(prompt).strip():
        return False
    if expects_plot(prompt):
        return False
    if detect_aggregate_op(prompt) is not None:
        return False

    # 결측 '행' 필터 요청은 스키마(컬럼별 결측 개수)가 아님
    from core.filter.value_filter import is_missing_rows_request

    if is_missing_rows_request(prompt):
        return False

    normalized = normalize_text(prompt)
    compact = re.sub(r"\s+", "", normalized)

    # 컬럼 의미 설명은 LLM 경로
    if _is_meaning_request(compact):
        return False

    if _looks_like_groupby_row_count(compact):
        return False

    if schema_kind(prompt) == "type_groups":
        return True

    if any(phrase in compact for phrase in _SCHEMA_SIGNAL_PHRASES):
        return True

    # "각 시트의 행과 컬럼을 비교"처럼 신호 구가 약해도 비교+구조 단서면 인정
    has_compare = any(k in compact for k in ("비교", "알려", "보여", "어때", "무엇"))
    has_struct = any(k in compact for k in ("행", "열", "컬럼", "시트", "파일", "컬럼명"))
    if has_compare and has_struct and not any(m in compact for m in _NON_SCHEMA_MARKERS):
        if "행" in compact and ("컬럼" in compact or "열" in compact):
            return True
    return False


def schema_kind(prompt: str) -> str:
    """스키마 하위 유형: type_groups | common | dtypes | compare."""
    compact = re.sub(r"\s+", "", normalize_text(prompt))

    from core.filter.value_filter import is_missing_rows_request

    if is_missing_rows_request(prompt):
        return "compare"

    if _is_type_group_request(compact):
        return "type_groups"
    if any(k in compact for k in ("공통컬럼", "공통으로있는컬럼", "commoncolumns")):
        return "common"
    if any(
        k in compact
        for k in ("데이터타입", "결측", "결측치", "dtype", "null", "missing")
    ):
        return "dtypes"
    return "compare"


def build_schema_outcome(
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str = "파일",
    profile_name: str | None = None,
) -> tuple[str, pd.DataFrame | None]:
    """스키마 요청에 대한 (reply, dataframe)을 만든다."""
    from core.profile_loader import schema_ui_for

    ui = schema_ui_for(profile_name=profile_name)
    if not named_frames:
        return ui["empty_compare"].format(unit=unit_label), None

    kind = schema_kind(prompt)
    if kind == "common":
        return _common_columns_result(
            named_frames, unit_label=unit_label, ui=ui
        )
    if kind == "dtypes":
        return _dtypes_result(named_frames, unit_label=unit_label, ui=ui)
    if kind == "type_groups":
        return _type_groups_result(
            named_frames, unit_label=unit_label, ui=ui
        )
    return _compare_result(named_frames, unit_label=unit_label, ui=ui)


def build_schema_compare_table(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str = "파일",
    profile_name: str | None = None,
    ui: dict[str, str] | None = None,
) -> pd.DataFrame:
    """이름 | 행 수 | 열 수 | 컬럼 목록 비교 표."""
    if ui is None:
        from core.profile_loader import schema_ui_for

        ui = schema_ui_for(profile_name=profile_name)
    rows: list[dict] = []
    for name, frame in named_frames:
        cols = [str(c) for c in frame.columns]
        rows.append(
            {
                unit_label: name,
                ui["rows"]: int(len(frame)),
                ui["cols"]: int(len(frame.columns)),
                ui["col_list"]: ", ".join(cols),
            }
        )
    return pd.DataFrame(rows)


def classify_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    """컬럼을 숫자형/문자형/날짜형/기타로 분류한다."""
    groups: dict[str, list[str]] = {
        "numeric": [],
        "string": [],
        "datetime": [],
        "other": [],
    }
    for col in df.columns:
        kind = _column_type_kind(df[col])
        groups[kind].append(str(col))
    return groups


def estimate_column_meaning(
    column: str,
    series: pd.Series | None = None,
    *,
    profile_name: str | None = None,
) -> str:
    """컬럼명(·샘플)으로 의미를 추정한다."""
    compact = re.sub(r"[\s_\-]+", "", str(column)).lower()
    for hints, meaning in _column_meaning_rules(profile_name=profile_name):
        for hint in hints:
            hint_compact = re.sub(r"[\s_\-]+", "", hint).lower()
            if hint_compact and hint_compact in compact:
                return meaning

    if series is not None:
        if pd.api.types.is_datetime64_any_dtype(series):
            return "날짜/시간 값"
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(
            series
        ):
            return "수치 값 (용도는 컬럼명만으로 특정하기 어려움)"
        if pd.api.types.is_bool_dtype(series):
            return "예/아니오 또는 참/거짓 플래그"

    return "용도를 컬럼명만으로 특정하기 어려워 추가 확인이 필요합니다"


def build_column_meaning_inventory(
    df: pd.DataFrame,
    *,
    max_cols: int = 40,
    sample_n: int = 5,
) -> list[dict[str, object]]:
    """LLM/규칙 설명용 컬럼 인벤토리."""
    rows: list[dict[str, object]] = []
    for col in list(df.columns)[:max_cols]:
        series = df[col]
        sample = (
            series.dropna()
            .astype(str)
            .head(sample_n)
            .tolist()
        )
        rows.append(
            {
                "column": str(col),
                "dtype": str(series.dtype),
                "non_null": int(series.notna().sum()),
                "unique": int(series.nunique(dropna=True)),
                "samples": sample,
            }
        )
    return rows


def build_rule_based_column_meanings(
    df: pd.DataFrame,
    *,
    profile_name: str | None = None,
) -> str:
    """프로필 규칙으로 컬럼 의미 목록을 만든다 (LLM 폴백)."""
    lines = ["컬럼 의미 추정(규칙 기반):", ""]
    for col in df.columns:
        meaning = estimate_column_meaning(
            str(col),
            df[col],
            profile_name=profile_name,
        )
        lines.append(f"- `{col}`: {meaning}")
    if len(df.columns) == 0:
        return "설명할 컬럼이 없습니다."
    lines.append("")
    lines.append("확신이 낮은 항목은 도메인 문맥에 따라 달라질 수 있습니다.")
    return "\n".join(lines)


def explain_column_meanings(
    df: pd.DataFrame,
    prompt: str,
    *,
    base_url: str,
    model: str,
    profile_name: str | None = None,
) -> str:
    """컬럼 의미를 텍스트로 설명한다. PandasAI를 쓰지 않고 Ollama 텍스트 API를 사용한다."""
    from core.llm_client import chat_text
    from core.profile_loader import active_profile

    if df is None or df.empty or len(df.columns) == 0:
        return "설명할 컬럼이 없습니다."

    inventory = build_column_meaning_inventory(df)
    profile = active_profile(profile_name=profile_name)
    domain = str(profile.get("domain") or profile.get("name") or "generic")
    from core.profile_loader import meaning_prompts_for

    system, user_suffix = meaning_prompts_for(profile_name=profile_name)
    user = (
        f"User request: {prompt}\n"
        f"Active domain profile: {domain}\n"
        f"Column inventory (JSON):\n{inventory}\n"
        f"{user_suffix}"
    )
    try:
        text = chat_text(
            user,
            system=system,
            base_url=base_url,
            model=model,
        )
        if text and len(text.strip()) >= 20:
            return text.strip()
    except Exception:  # noqa: BLE001
        pass
    return build_rule_based_column_meanings(df, profile_name=profile_name)


def _is_meaning_request(compact: str) -> bool:
    """컬럼 의미 설명 요청인지 (한국어·영어)."""
    has_column = "컬럼" in compact or "column" in compact
    if not has_column:
        return False

    if any(p in compact for p in _MEANING_PHRASES):
        return True

    # 의미·추측이 분명한 경우
    if any(
        k in compact
        for k in ("의미", "추측", "해석", "용도", "means", "meaning", "meanings")
    ):
        if any(
            k in compact
            for k in (
                "타입",
                "결측",
                "dtype",
                "숫자",
                "문자",
                "구분",
                "비교",
                "missing",
                "datatype",
                "datatypes",
            )
        ):
            return False
        return True

    # '설명' / explain / describe — 구조·목록 질문과 구분
    if any(k in compact for k in ("설명", "explain", "describe")):
        if any(
            k in compact
            for k in (
                "목록",
                "리스트",
                "행수",
                "열수",
                "비교",
                "타입",
                "결측",
                "숫자",
                "문자",
                "구분",
                "missing",
                "datatype",
                "datatypes",
                "list",
                "table",
            )
        ):
            return False
        return True
    return False


def _is_type_group_request(compact: str) -> bool:
    if any(p in compact for p in _TYPE_GROUP_PHRASES):
        return True
    has_num = any(k in compact for k in ("숫자", "수치", "numeric", "number"))
    has_str = any(k in compact for k in ("문자", "문자열", "텍스트", "string", "text"))
    has_date = any(k in compact for k in ("날짜", "일자", "datetime", "date"))
    if "컬럼" in compact and (
        (has_num and has_str)
        or (has_num and has_date)
        or (has_str and has_date)
        or ("구분" in compact and (has_num or has_str or has_date))
    ):
        return True
    return False


def _compare_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
    ui: dict[str, str],
) -> tuple[str, pd.DataFrame]:
    table = build_schema_compare_table(
        named_frames, unit_label=unit_label, ui=ui
    )
    if len(named_frames) == 1:
        name = named_frames[0][0]
        reply = ui["compare_one"].format(
            name=name,
            rows=int(table.iloc[0][ui["rows"]]),
            cols=int(table.iloc[0][ui["cols"]]),
        )
        table = table.drop(columns=[unit_label], errors="ignore")
    else:
        reply = ui["compare_multi"].format(unit=unit_label, n=len(named_frames))
    return reply, table


def _common_columns_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
    ui: dict[str, str],
) -> tuple[str, pd.DataFrame]:
    if len(named_frames) == 1:
        cols = [str(c) for c in named_frames[0][1].columns]
        table = pd.DataFrame({ui["column"]: cols})
        return ui["common_one"].format(name=named_frames[0][0], n=len(cols)), table

    col_sets = [set(str(c) for c in frame.columns) for _, frame in named_frames]
    common = set.intersection(*col_sets) if col_sets else set()
    ordered = [str(c) for c in named_frames[0][1].columns if str(c) in common]
    only_by_unit: list[dict] = []
    for name, frame in named_frames:
        unique = sorted(set(str(c) for c in frame.columns) - common)
        only_by_unit.append(
            {
                unit_label: name,
                ui["unique_col_count"]: len(unique),
                ui["unique_cols"]: ", ".join(unique) if unique else ui["none"],
            }
        )

    common_table = pd.DataFrame({ui["common_col"]: ordered})
    reply = ui["common_multi"].format(
        n=len(named_frames), unit=unit_label, count=len(ordered)
    )
    if ordered:
        reply = reply + f": {', '.join(ordered)}"
    extras = []
    for row in only_by_unit:
        if row[ui["unique_col_count"]]:
            extras.append(
                ui["only_unit"].format(
                    name=row[unit_label], cols=row[ui["unique_cols"]]
                )
            )
    if extras:
        reply = reply + " · " + " / ".join(extras)
    return reply, common_table if ordered else pd.DataFrame(
        {
            unit_label: [r[unit_label] for r in only_by_unit],
            ui["unique_cols"]: [r[ui["unique_cols"]] for r in only_by_unit],
        }
    )


def _dtypes_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
    ui: dict[str, str],
) -> tuple[str, pd.DataFrame]:
    parts: list[pd.DataFrame] = []
    for name, frame in named_frames:
        rows = []
        for col in frame.columns:
            series = frame[col]
            null_count = int(series.isna().sum())
            rows.append(
                {
                    unit_label: name,
                    ui["column"]: str(col),
                    ui["dtype"]: str(series.dtype),
                    ui["missing"]: null_count,
                }
            )
        parts.append(pd.DataFrame(rows))
    table = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(named_frames) == 1:
        table = table.drop(columns=[unit_label], errors="ignore")
        reply = ui["dtypes_one"].format(name=named_frames[0][0])
    else:
        reply = ui["dtypes_multi"].format(unit=unit_label, n=len(named_frames))
    return reply, table


def _type_groups_result(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str,
    ui: dict[str, str],
) -> tuple[str, pd.DataFrame | None]:
    """숫자/문자/날짜 컬럼을 구분해 마크다운으로 반환한다."""
    if len(named_frames) == 1:
        _name, frame = named_frames[0]
        groups = classify_columns(frame)
        reply = _format_type_groups_markdown(groups, ui=ui)
        return reply, None

    parts: list[str] = [
        ui["type_groups_multi"].format(unit=unit_label, n=len(named_frames)),
        "",
    ]
    rows: list[dict] = []
    kind_labels = (
        ("numeric", ui["numeric"]),
        ("string", ui["string"]),
        ("datetime", ui["datetime"]),
        ("other", ui["other"]),
    )
    for name, frame in named_frames:
        groups = classify_columns(frame)
        parts.append(f"### `{name}`")
        parts.append("")
        parts.append(_format_type_groups_markdown(groups, ui=ui))
        parts.append("")
        for kind, label in kind_labels:
            for col in groups[kind]:
                rows.append(
                    {unit_label: name, ui["column"]: col, ui["type_kind"]: label}
                )
    table = pd.DataFrame(rows) if rows else None
    return "\n".join(parts).rstrip(), table


def _type_groups_table(
    groups: dict[str, list[str]],
    *,
    ui: dict[str, str] | None = None,
) -> pd.DataFrame:
    if ui is None:
        from core.profile_loader import schema_ui_for

        ui = schema_ui_for()
    label_map = {
        "numeric": ui["numeric"],
        "string": ui["string"],
        "datetime": ui["datetime"],
        "other": ui["other"],
    }
    rows: list[dict] = []
    for kind, label in label_map.items():
        for col in groups.get(kind, []):
            rows.append({ui["column"]: col, ui["type_kind"]: label})
    return pd.DataFrame(rows)


def _format_type_groups_markdown(
    groups: dict[str, list[str]],
    *,
    ui: dict[str, str] | None = None,
) -> str:
    if ui is None:
        from core.profile_loader import schema_ui_for

        ui = schema_ui_for()
    sections = [
        ("numeric", ui["numeric_cols"]),
        ("string", ui["string_cols"]),
        ("datetime", ui["datetime_cols"]),
        ("other", ui["other_cols"]),
    ]
    lines: list[str] = []
    for key, title in sections:
        cols = groups.get(key) or []
        if not cols:
            continue
        lines.append(f"**{title}**")
        lines.extend(f"- {col}" for col in cols)
        lines.append("")
    return "\n".join(lines).rstrip() if lines else ui["type_groups_empty"]


def _column_type_kind(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "other"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if (
        pd.api.types.is_string_dtype(series)
        or pd.api.types.is_object_dtype(series)
        or str(series.dtype) == "category"
        or str(series.dtype) == "string"
    ):
        return "string"
    return "other"


def _looks_like_groupby_row_count(compact: str) -> bool:
    """'범주형 컬럼별 행 개수'처럼 그룹 집계로 보이는 경우."""
    if "컬럼별" in compact or "별로" in compact:
        if any(k in compact for k in ("행개수", "개수", "건수", "카운트", "count")):
            return True
    return False
