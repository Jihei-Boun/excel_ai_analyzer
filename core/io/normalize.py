"""업로드 DataFrame 공통 정규화 — 컬럼명·타입·키 후보."""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

from core.io.text_normalize import normalize_text

_MULTI_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_EDGE_RE = re.compile(r"^[\s_\-./]+|[\s_\-./]+$")
_INTERNAL_SEP_RE = re.compile(r"[\s\-./]+")


def canonicalize_column_name(name: object) -> str:
    """컬럼명을 비교·매칭용으로 정규화한다 (공백/기호 정리)."""
    text = str(name or "").strip()
    if not text:
        return "column"
    text = unicodedata.normalize("NFKC", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _NON_ALNUM_EDGE_RE.sub("", text)
    text = _INTERNAL_SEP_RE.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "column"


def column_match_key(name: object) -> str:
    """파일 간 컬럼 매칭용 키 (대소문자·공백 무시)."""
    return normalize_text(canonicalize_column_name(name))


def canonicalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """표시용 컬럼명을 정리하고 중복을 안전하게 유일화한다."""
    result = df.copy()
    cleaned = [canonicalize_column_name(col) for col in result.columns]
    result.columns = _unique_names(cleaned)
    return result


def drop_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    """모든 값이 결측인 열을 제거한다."""
    if df.empty or len(df.columns) == 0:
        return df
    keep = [col for col in df.columns if not df[col].isna().all()]
    if len(keep) == len(df.columns):
        return df
    return df.loc[:, keep].copy()


def coerce_mixed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """문자/숫자 혼합 열을 가능한 범위에서 일관된 타입으로 맞춘다."""
    result = df.copy()
    for col in result.columns:
        series = result[col]
        if pd.api.types.is_bool_dtype(series) or pd.api.types.is_datetime64_any_dtype(
            series
        ):
            continue
        if pd.api.types.is_numeric_dtype(series):
            continue

        numeric = _try_numeric(series)
        if numeric is not None:
            result[col] = numeric
            continue

        # 키 매칭을 위해 앞뒤 공백만 정리한 문자열로 통일
        result[col] = series.map(_clean_cell_text).astype("string")
    return result


def suggest_key_columns(df: pd.DataFrame, *, max_candidates: int = 5) -> list[str]:
    """조인 키 후보를 uniqueness·결측률 기준으로 추천한다."""
    if df.empty or len(df.columns) == 0:
        return []

    scored: list[tuple[float, str]] = []
    n = len(df)
    for col in df.columns:
        series = df[col]
        non_null = int(series.notna().sum())
        if non_null == 0:
            continue
        null_ratio = 1.0 - (non_null / n)
        nunique = int(series.nunique(dropna=True))
        uniqueness = nunique / max(non_null, 1)
        # 거의 전부 유니크하고 결측이 적을수록 키에 적합
        if uniqueness < 0.5 and n > 3:
            continue
        score = uniqueness * (1.0 - null_ratio)
        # 코드/ID류 이름 가산점
        key_name = column_match_key(col)
        if any(token in key_name for token in ("id", "코드", "code", "번호", "키", "key")):
            score += 0.15
        scored.append((score, str(col)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:max_candidates]]


def normalize_dataframe(
    df: pd.DataFrame,
    *,
    drop_empty_cols: bool = False,
) -> pd.DataFrame:
    """sanitize 이후 공통 정규화 파이프라인.

    빈 열 제거는 기본 OFF — 품질 진단에서 경고하고, 필요 시 호출측에서 켠다.
    """
    if df is None:
        return pd.DataFrame()
    result = canonicalize_dataframe_columns(df)
    if drop_empty_cols:
        result = drop_empty_columns(result)
    result = coerce_mixed_columns(result)
    result = result.dropna(how="all").reset_index(drop=True)
    return result


def align_column_names(
    frames: list[pd.DataFrame],
) -> list[pd.DataFrame]:
    """여러 프레임의 동일 의미 컬럼명을 첫 프레임 기준으로 맞춘다."""
    if not frames:
        return []
    if len(frames) == 1:
        return [frames[0].copy()]

    primary = frames[0]
    primary_map = {column_match_key(c): str(c) for c in primary.columns}
    aligned: list[pd.DataFrame] = [primary.copy()]
    for frame in frames[1:]:
        renamed = {}
        used_targets: set[str] = set()
        for col in frame.columns:
            key = column_match_key(col)
            target = primary_map.get(key)
            if target and target not in used_targets and str(col) != target:
                renamed[col] = target
                used_targets.add(target)
        aligned.append(frame.rename(columns=renamed) if renamed else frame.copy())
    return aligned


def _unique_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, name in enumerate(names, start=1):
        base = name or f"column_{index}"
        count = counts.get(base, 0)
        counts[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def _try_numeric(series: pd.Series) -> pd.Series | None:
    cleaned = (
        series.map(_clean_cell_text)
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"": pd.NA})
    )
    non_empty = int(cleaned.notna().sum())
    if non_empty == 0:
        return None
    numeric = pd.to_numeric(cleaned, errors="coerce")
    if int(numeric.notna().sum()) == non_empty:
        return numeric
    return None


def _clean_cell_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
