"""다중 DataFrame 비교·병합 엔진."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

import pandas as pd

from core.normalize import (
    align_column_names,
    column_match_key,
    normalize_dataframe,
    suggest_key_columns,
)

JoinHow = Literal["inner", "left", "outer"]


@dataclass
class MergeReport:
    """병합 신뢰도 리포트."""

    how: str
    keys: list[str]
    input_names: list[str] = field(default_factory=list)
    input_rows: list[int] = field(default_factory=list)
    result_rows: int = 0
    matched_rows: int = 0
    match_rate: float = 0.0
    missing_rate: float = 0.0
    duplicate_key_warnings: list[str] = field(default_factory=list)
    conflict_columns: list[str] = field(default_factory=list)
    missing_key_samples: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_text(self) -> str:
        names = ", ".join(self.input_names) if self.input_names else "(없음)"
        return (
            f"{len(self.input_names)}개 병합 ({self.how}) · 키={', '.join(self.keys)} · "
            f"결과 {self.result_rows}행 · 매칭률 {self.match_rate:.1%} · "
            f"입력: {names}"
        )


@dataclass
class MergeResult:
    dataframe: pd.DataFrame
    report: MergeReport


def infer_common_keys(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    max_keys: int = 3,
) -> list[str]:
    """여러 프레임에서 공통으로 쓸 조인 키를 추론한다."""
    if len(named_frames) < 2:
        return []

    aligned = align_column_names([frame for _, frame in named_frames])
    candidate_sets: list[set[str]] = []
    for frame in aligned:
        suggested = suggest_key_columns(frame, max_candidates=8)
        if not suggested:
            suggested = [str(c) for c in frame.columns[:5]]
        candidate_sets.append({column_match_key(c) for c in suggested})

    common_match_keys = (
        set.intersection(*candidate_sets) if candidate_sets else set()
    )
    if not common_match_keys:
        col_sets = [
            {column_match_key(c) for c in frame.columns} for frame in aligned
        ]
        common_match_keys = set.intersection(*col_sets) if col_sets else set()

    primary = aligned[0]
    primary_by_key = {column_match_key(c): str(c) for c in primary.columns}
    ordered = [
        primary_by_key[k]
        for k in (column_match_key(c) for c in primary.columns)
        if k in common_match_keys and k in primary_by_key
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for name in ordered:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique[:max_keys]

def merge_named_frames(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    keys: list[str] | None = None,
    how: JoinHow = "outer",
    normalize: bool = True,
) -> MergeResult:
    """이름 붙은 프레임들을 순차 병합한다."""
    if len(named_frames) < 2:
        raise ValueError("병합에는 최소 2개의 데이터가 필요합니다.")

    prepared: list[tuple[str, pd.DataFrame]] = []
    for name, frame in named_frames:
        df = normalize_dataframe(frame) if normalize else frame.copy()
        prepared.append((str(name), df))

    aligned_frames = align_column_names([frame for _, frame in prepared])
    prepared = [(name, frame) for (name, _), frame in zip(prepared, aligned_frames)]

    resolved_keys = list(keys or infer_common_keys(prepared))
    if not resolved_keys:
        raise ValueError(
            "공통 조인 키를 찾지 못했습니다. 키 컬럼을 직접 선택하세요."
        )

    for name, frame in prepared:
        missing = [k for k in resolved_keys if k not in frame.columns]
        if missing:
            raise ValueError(
                f"`{name}`에 키 컬럼이 없습니다: {', '.join(missing)}"
            )

    report = MergeReport(
        how=how,
        keys=resolved_keys,
        input_names=[name for name, _ in prepared],
        input_rows=[len(frame) for _, frame in prepared],
    )

    # 키 중복 경고
    for name, frame in prepared:
        dup = int(frame.duplicated(subset=resolved_keys).sum())
        if dup:
            msg = f"`{name}` 키 중복 행 {dup}개"
            report.duplicate_key_warnings.append(msg)
            report.warnings.append(msg)

    left_name, left = prepared[0]
    left = _prefix_non_key_columns(left, left_name, resolved_keys, used=set())
    used_names = {left_name}

    for name, right in prepared[1:]:
        right = _prefix_non_key_columns(right, name, resolved_keys, used=used_names)
        before = len(left)
        left = left.merge(right, on=resolved_keys, how=how, suffixes=("", f"_{name}"))
        used_names.add(name)
        if how == "inner" and before and len(left) == 0:
            report.warnings.append(
                f"`{name}`과(와) inner 조인 결과가 0행입니다. 키 값을 확인하세요."
            )

    # 충돌로 suffix가 붙은 컬럼
    conflict = [
        str(c)
        for c in left.columns
        if any(str(c).endswith(f"_{name}") for name in report.input_names[1:])
    ]
    report.conflict_columns = conflict
    if conflict:
        report.notes.append(
            f"동일 이름 컬럼 충돌 {len(conflict)}개 → 파일명 suffix로 구분했습니다."
        )

    report.result_rows = int(len(left))
    report.matched_rows, report.match_rate, report.missing_rate = _match_stats(
        prepared, left, resolved_keys, how=how
    )
    report.missing_key_samples = _missing_key_samples(prepared, left, resolved_keys)

    if report.match_rate < 0.5 and how != "outer":
        report.warnings.append(
            f"매칭률이 낮습니다 ({report.match_rate:.1%}). outer 조인 또는 키 재선택을 검토하세요."
        )

    return MergeResult(dataframe=left, report=report)


def _prefix_non_key_columns(
    df: pd.DataFrame,
    name: str,
    keys: list[str],
    *,
    used: set[str],
) -> pd.DataFrame:
    """키 외 컬럼에 파일명 suffix를 붙여 충돌을 예방한다.

    첫 프레임은 원본 유지, 이후 프레임은 항상 suffix.
    """
    result = df.copy()
    if not used:
        return result

    safe = _safe_suffix(name)
    rename = {
        col: f"{col}_{safe}"
        for col in result.columns
        if col not in keys
    }
    return result.rename(columns=rename) if rename else result


def _safe_suffix(name: str) -> str:
    text = str(name).strip()
    for ext in (".xlsx", ".xls", ".xlsm", ".csv"):
        if text.lower().endswith(ext):
            text = text[: -len(ext)]
            break
    text = re.sub(r"[^\w가-힣]+", "_", text).strip("_")
    return text[:40] or "file"


def _match_stats(
    prepared: list[tuple[str, pd.DataFrame]],
    merged: pd.DataFrame,
    keys: list[str],
    *,
    how: str,
) -> tuple[int, float, float]:
    """대략적 매칭률: 첫 프레임 키 기준."""
    left_name, left = prepared[0]
    left_keys = left[keys].drop_duplicates()
    if left_keys.empty or merged.empty:
        return 0, 0.0, 1.0

    # 병합 결과에 첫 프레임 키가 얼마나 살아남았는지
    merged_keys = merged[keys].drop_duplicates()
    # indicator 없이: left 키 중 merged에 존재하는 비율
    left_index = left_keys.astype(str).agg("|".join, axis=1)
    merged_index = set(merged_keys.astype(str).agg("|".join, axis=1))
    matched = int(sum(1 for item in left_index if item in merged_index))
    total = max(len(left_index), 1)
    match_rate = matched / total
    missing_rate = 1.0 - match_rate
    if how == "outer":
        # outer는 행이 늘어날 수 있어 매칭률을 키 교집합 기준으로 재산출
        key_sets = []
        for _, frame in prepared:
            key_sets.append(set(frame[keys].astype(str).agg("|".join, axis=1)))
        inter = set.intersection(*key_sets) if key_sets else set()
        union = set.union(*key_sets) if key_sets else set()
        match_rate = len(inter) / max(len(union), 1)
        missing_rate = 1.0 - match_rate
        matched = len(inter)
    return matched, float(match_rate), float(missing_rate)


def _missing_key_samples(
    prepared: list[tuple[str, pd.DataFrame]],
    merged: pd.DataFrame,
    keys: list[str],
    *,
    limit: int = 5,
) -> list[str]:
    if len(prepared) < 2:
        return []
    left = prepared[0][1]
    right = prepared[1][1]
    left_set = set(left[keys].astype(str).agg("|".join, axis=1))
    right_set = set(right[keys].astype(str).agg("|".join, axis=1))
    only_left = sorted(left_set - right_set)[:limit]
    only_right = sorted(right_set - left_set)[:limit]
    samples: list[str] = []
    for item in only_left:
        samples.append(f"왼쪽만: {item}")
    for item in only_right:
        samples.append(f"오른쪽만: {item}")
    return samples
