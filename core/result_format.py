"""분석 결과 후처리·리스트 표시 판별."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.column_match import find_mentioned_column
from core.value_filter import format_context_label
from core.constants import (
    AMOUNT_COLUMN_HINTS,
    CODE_COLUMN_HINTS,
    CODE_PAIR_SAMPLE_SIZE,
    GROUP_COLUMN_EXACT,
    GROUP_COLUMN_HINTS,
    GROUP_COLUMN_SUFFIXES,
    ITEM_COLUMN_HINTS,
)
from core.excel_loader import find_merged_header_pair, merged_header_base
from core.pandasai_config import is_total_label, prepare_dataframe_for_ai

_SOURCE_COL = "출처파일"

_LIST_DISPLAY_KEYWORDS = (
    "리스트",
    "목록",
    "뽑아",
    "나열",
    "list",
)


@dataclass(frozen=True)
class ListDisplayResult:
    """리스트 UI에 쓸 평면·그룹 목록."""

    label: str
    values: list[str]
    groups: dict[str, list[str]] | None = None


def expects_list_display(prompt: str) -> bool:
    """단일 값 나열 형태로 보여줄 요청인지 판별한다."""
    from core.schema_compare import is_schema_request
    from core.text_normalize import normalize_text

    if is_schema_request(prompt):
        return False
    lowered = prompt.lower()
    if not any(keyword in lowered for keyword in _LIST_DISPLAY_KEYWORDS):
        return False
    if "표로" in lowered or "표 형" in lowered:
        return False
    compact = normalize_text(prompt)
    # '컬럼 목록 비교'처럼 스키마 문맥의 '목록'만 있는 경우 제외
    if ("컬럼목록" in compact or "열목록" in compact) and not any(
        k in compact for k in ("리스트", "뽑아", "나열", "list")
    ):
        return False
    return True


def exclude_aggregate_rows(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str = _SOURCE_COL,
) -> tuple[pd.DataFrame, int]:
    """합계·소계 등 집계 행을 제거한다."""
    if df is None or df.empty:
        return df, 0

    check_columns = _aggregate_check_columns(df, prompt, source_col=source_col)
    if not check_columns:
        return df, 0

    exclude_mask = pd.Series(False, index=df.index)
    for column in check_columns:
        exclude_mask |= df[column].map(_cell_is_total_label)

    if not exclude_mask.any():
        return df, 0

    filtered = df.loc[~exclude_mask].reset_index(drop=True)
    return filtered, int(exclude_mask.sum())


def to_list_display(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str = _SOURCE_COL,
    source_df: pd.DataFrame | None = None,
) -> ListDisplayResult | None:
    """리스트 표시용 값(평면·분류별 그룹)을 추출한다."""
    if df is None or df.empty or not expects_list_display(prompt):
        return None

    work = enrich_for_grouped_list(df, source_df, prompt, source_col=source_col)
    prepared = prepare_dataframe_for_ai(work)
    mentioned = find_mentioned_column(prepared, prompt)
    merged_pair = find_merged_header_pair(prepared.columns, mentioned)
    if merged_pair:
        code_col, item_col = merged_pair
    else:
        item_col = _list_item_column(prepared, prompt, source_col=source_col)
        if item_col is None:
            return None
        code_col = None

    group_col = _list_group_column(prepared, prompt, item_col, source_col=source_col)
    # 분류 컬럼이 없어도 여러 파일이면 출처파일로 묶는다 (파일별 리스트).
    if group_col is None and _wants_file_grouped_list(prepared, prompt, source_col=source_col):
        group_col = source_col

    if code_col is None:
        code_col = _list_code_column(
            prepared,
            item_col,
            group_col=group_col if group_col != source_col else None,
            source_col=source_col,
        )

    list_label = format_context_label(merged_header_base(item_col))
    groups = (
        _build_grouped_list(
            prepared,
            group_col,
            item_col,
            code_col=code_col,
            source_col=source_col,
        )
        if group_col is not None
        else None
    )

    if groups:
        values = [item for items in groups.values() for item in items]
        if not values:
            return None
        return ListDisplayResult(
            label=list_label,
            values=values,
            groups=groups,
        )

    values = _build_flat_list(prepared, item_col, code_col=code_col)
    if not values:
        return None

    return ListDisplayResult(
        label=list_label,
        values=values,
        groups=None,
    )


def enrich_for_grouped_list(
    result: pd.DataFrame,
    source: pd.DataFrame | None,
    prompt: str,
    *,
    source_col: str = _SOURCE_COL,
) -> pd.DataFrame:
    """결과에 분류·코드 컬럼이 빠졌을 때 원본 데이터에서 보강한다."""
    if source is None or source.empty or result is None or result.empty:
        return result
    if not expects_list_display(prompt):
        return result

    mentioned = find_mentioned_column(source, prompt)
    merged_pair = find_merged_header_pair(source.columns, mentioned)
    if merged_pair:
        code_col, item_col = merged_pair
    else:
        item_col = _list_item_column(result, prompt, source_col=source_col)
        if item_col is None:
            item_col = _list_item_column(source, prompt, source_col=source_col)
        if item_col is None:
            return result
        group_col = _list_group_column(source, prompt, item_col, source_col=source_col)
        code_col = _list_code_column(
            source,
            item_col,
            group_col=group_col,
            source_col=source_col,
        )

    group_col = _list_group_column(source, prompt, item_col, source_col=source_col)

    needs_group = (
        group_col is not None
        and group_col not in result.columns
        and group_col in source.columns
    )
    needs_pair = merged_pair is not None and (
        code_col not in result.columns or item_col not in result.columns
    )
    needs_code = (
        not merged_pair
        and code_col is not None
        and code_col not in result.columns
        and code_col in source.columns
    )
    if not needs_group and not needs_code and not needs_pair:
        return result

    prepared = prepare_dataframe_for_ai(source)
    if item_col not in prepared.columns and (
        not merged_pair or code_col not in prepared.columns
    ):
        return result

    match_col = item_col
    if merged_pair and item_col not in result.columns and code_col in result.columns:
        match_col = code_col

    if match_col in result.columns:
        item_keys = {
            _format_item_text(value)
            for value in result[match_col].tolist()
            if _format_item_text(value)
        }
        if not item_keys:
            return result
        mask = prepared[match_col].map(_format_item_text).isin(item_keys)
    elif group_col and group_col in result.columns:
        # LLM이 분류명만 반환한 경우에도 원본에서 해당 분류의 세부 비용명을 다시 확장한다.
        group_keys = {
            _format_item_text(value)
            for value in result[group_col].tolist()
            if _format_item_text(value)
        }
        if not group_keys:
            return result
        filled_groups = pd.Series(
            _forward_fill_group_labels(prepared[group_col]),
            index=prepared.index,
        ).map(_format_item_text)
        mask = filled_groups.isin(group_keys)
    elif len(result) == len(prepared):
        mask = pd.Series(True, index=prepared.index)
    else:
        return result

    columns = list(
        dict.fromkeys(
            column
            for column in (source_col, group_col, code_col, item_col)
            if column and column in prepared.columns
        )
    )
    subset = prepared.loc[mask, columns].reset_index(drop=True)
    return subset if not subset.empty else result


def _build_flat_list(
    df: pd.DataFrame,
    column: str,
    *,
    code_col: str | None = None,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        text = _format_list_entry(row, item_col=column, code_col=code_col)
        if not text or _cell_is_total_label(row.get(column)):
            continue
        if text in seen:
            continue
        seen.add(text)
        values.append(text)
    return values


def _build_grouped_list(
    df: pd.DataFrame,
    group_col: str,
    item_col: str,
    *,
    code_col: str | None = None,
    source_col: str,
) -> dict[str, list[str]] | None:
    multi_source = source_col in df.columns and df[source_col].nunique() > 1
    group_by_source = group_col == source_col
    group_labels = (
        [_clean_cell_text(v) for v in df[group_col].tolist()]
        if group_by_source
        else _forward_fill_group_labels(df[group_col])
    )
    groups: dict[str, list[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()

    for position, (_, row) in enumerate(df.iterrows()):
        group_text = group_labels[position]
        if not group_text or _cell_is_total_label(group_text):
            continue

        group_name = group_text
        if multi_source and not group_by_source:
            file_name = _clean_cell_text(row.get(source_col))
            if file_name:
                group_name = f"{file_name} · {group_text}"

        item_text = _format_list_entry(row, item_col=item_col, code_col=code_col)
        if not item_text or _cell_is_total_label(row.get(item_col)):
            continue
        if item_text == group_text:
            continue

        pair = (group_name, item_text)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        groups.setdefault(group_name, []).append(item_text)

    if not groups:
        return None
    return groups


def _format_list_entry(
    row: pd.Series,
    *,
    item_col: str,
    code_col: str | None,
) -> str:
    """한 행을 '코드: 비용명' 형태로 포맷한다."""
    name = _format_item_text(row.get(item_col))
    if not code_col or code_col not in row.index:
        return name

    code = _format_item_text(row.get(code_col))
    if code and name:
        return f"{code}: {name}"
    return code or name


def _list_code_column(
    df: pd.DataFrame,
    item_col: str,
    *,
    group_col: str | None,
    source_col: str,
) -> str | None:
    """비용명과 짝을 이루는 코드(숫자) 컬럼을 찾는다."""
    skip = {item_col, source_col}
    if group_col:
        skip.add(group_col)

    scored: list[tuple[int, int, str]] = []
    columns = list(df.columns)
    item_index = columns.index(item_col) if item_col in columns else len(columns)

    for index, column in enumerate(columns):
        if column in skip:
            continue
        if _is_amount_like_column(column):
            continue

        series = df[column]
        is_numeric = pd.api.types.is_numeric_dtype(series)
        name_score = 0
        if _column_name_matches(column, CODE_COLUMN_HINTS):
            name_score = 20
        elif is_numeric:
            name_score = 5

        if not is_numeric and name_score == 0:
            continue
        if not is_numeric and name_score > 0:
            # 문자열이지만 코드명인 열(예: '121' 텍스트)도 허용
            sample = series.dropna().head(CODE_PAIR_SAMPLE_SIZE).map(_format_item_text)
            if sample.empty or not all(text.isdigit() for text in sample if text):
                continue

        distance = abs(index - item_index)
        left_bonus = 10 if index < item_index else 0
        scored.append((name_score + left_bonus, -distance, column))

    if not scored:
        return None

    scored.sort(reverse=True)
    return scored[0][2]


def _is_amount_like_column(name: str) -> bool:
    normalized = str(name).replace(" ", "").lower()
    return any(hint.lower() in normalized for hint in AMOUNT_COLUMN_HINTS)


def _forward_fill_group_labels(series: pd.Series) -> list[str]:
    """병합 셀로 비어 있는 분류명을 위 행 값으로 채운다."""
    labels: list[str] = []
    current: str | None = None

    for value in series.tolist():
        if _cell_is_total_label(value):
            current = None
            labels.append("")
            continue

        text = _clean_cell_text(value)
        if text:
            current = text
        labels.append(current or "")

    return labels


def _list_group_column(
    df: pd.DataFrame,
    prompt: str,
    item_col: str,
    *,
    source_col: str,
) -> str | None:
    if _is_group_like_column(item_col):
        return None

    text_columns = _text_columns(df, source_col=source_col)

    mentioned = find_mentioned_column(df, prompt)
    if mentioned and _is_group_like_column(mentioned) and mentioned != item_col:
        return mentioned

    for column in text_columns:
        if column == item_col:
            continue
        if _is_group_like_column(column):
            return column

    if len(text_columns) < 2:
        return None

    if item_col in text_columns:
        index = text_columns.index(item_col)
        if index > 0:
            return text_columns[index - 1]

    others = [column for column in text_columns if column != item_col]
    return others[0] if len(others) == 1 else None


def _wants_file_grouped_list(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str,
) -> bool:
    """여러 파일이 합쳐진 리스트는 출처파일로 묶어 보여준다."""
    del prompt  # 멀티파일 리스트는 항상 파일 단위로 표시
    return source_col in df.columns and df[source_col].nunique(dropna=True) > 1


def _list_item_column(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str,
) -> str | None:
    mentioned = find_mentioned_column(df, prompt)
    text_columns = _text_columns(df, source_col=source_col)
    detail_columns = [
        column for column in text_columns if not _is_group_like_column(column)
    ]
    item_hint_columns = [
        column
        for column in df.columns
        if column != source_col and _column_name_matches(column, ITEM_COLUMN_HINTS)
    ]

    # 금액 컬럼이 언급돼도 리스트 항목은 비용명/항목 쪽을 우선한다.
    # 필터로 값이 거의 고정된 컬럼(예: 비용명=121만)은 목록 항목으로 쓰지 않는다.
    if (
        mentioned
        and mentioned in df.columns
        and not _is_amount_like_column(mentioned)
        and df[mentioned].nunique(dropna=True) > 1
    ):
        return mentioned

    if item_hint_columns:
        # 코드·명칭 쌍이면 명칭(오른쪽) 우선. 값이 다양한 컬럼을 선호.
        ranked = sorted(
            item_hint_columns,
            key=lambda col: (
                0 if pd.api.types.is_numeric_dtype(df[col]) else 1,
                df[col].nunique(dropna=True),
            ),
            reverse=True,
        )
        for column in ranked:
            if df[column].nunique(dropna=True) > 1 or len(ranked) == 1:
                return column
        return ranked[0]

    if detail_columns:
        for column in reversed(detail_columns):
            if df[column].nunique(dropna=True) > 1:
                return column
        return detail_columns[-1]

    if mentioned and mentioned in df.columns and not _is_amount_like_column(mentioned):
        return mentioned

    numeric_columns = [
        column
        for column in df.columns
        if column != source_col
        and pd.api.types.is_numeric_dtype(df[column])
        and not _is_amount_like_column(column)
    ]
    if len(numeric_columns) == 1 and not text_columns:
        return numeric_columns[0]

    if len(text_columns) == 1:
        return text_columns[0]
    if len(df.columns) == 1:
        return str(df.columns[0])
    return None


def _text_columns(df: pd.DataFrame, *, source_col: str) -> list[str]:
    return [
        column
        for column in df.columns
        if column != source_col and not pd.api.types.is_numeric_dtype(df[column])
    ]


def _is_group_like_column(name: str) -> bool:
    normalized = str(name).replace(" ", "").lower()
    if normalized in {hint.lower() for hint in GROUP_COLUMN_EXACT}:
        return True
    if any(hint.lower() in normalized for hint in GROUP_COLUMN_HINTS):
        return True
    return any(normalized.endswith(suffix) for suffix in GROUP_COLUMN_SUFFIXES)


def _column_name_matches(name: str, hints: tuple[str, ...]) -> bool:
    normalized = str(name).replace(" ", "").lower()
    return any(hint.lower() in normalized for hint in hints)


def _format_item_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int,)):
        return str(value)
    return _clean_cell_text(value)


def _aggregate_check_columns(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_col: str,
) -> list[str]:
    columns: list[str] = []

    mentioned = find_mentioned_column(df, prompt)
    if mentioned:
        columns.append(mentioned)

    item_col = _list_item_column(df, prompt, source_col=source_col)
    if item_col and item_col not in columns:
        columns.append(item_col)

    if item_col:
        group_col = _list_group_column(df, prompt, item_col, source_col=source_col)
        if group_col and group_col not in columns:
            columns.append(group_col)

    if columns:
        return columns

    text_columns = [
        column
        for column in df.columns
        if column != source_col and not pd.api.types.is_numeric_dtype(df[column])
    ]
    if len(text_columns) == 1:
        return text_columns
    if text_columns:
        return [text_columns[0]]
    return []


def _cell_is_total_label(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return is_total_label(value)


def _clean_cell_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return ""
    return text
