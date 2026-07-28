"""차트 파일 저장·폴백 생성 유틸."""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from core.constants import CHART_MAX_CATEGORIES, CHARTS_DIR as _CHARTS_DIR

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
_CONTEXT_LABEL_SEP = " · "
_DATA_URI_RE = re.compile(
    r"^data:image/(png|jpeg|jpg|gif|webp|svg\+xml);base64,(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def charts_dir() -> Path:
    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    return _CHARTS_DIR


def materialize_chart(value: Any) -> str | None:
    """plot 결과(파일 경로·base64·bytes)를 디스크 PNG 경로로 만든다."""
    if value is None or isinstance(value, (pd.DataFrame, pd.Series)):
        return None
    if isinstance(value, (bytes, bytearray)):
        return _save_chart_bytes(bytes(value))

    if _is_number(value):
        return None

    text = str(value).strip()
    if not text:
        return None

    match = _DATA_URI_RE.match(text)
    if match:
        try:
            raw = base64.b64decode(match.group(2))
        except Exception:
            return None
        suffix = match.group(1).lower().replace("jpeg", "jpg").replace("svg+xml", "svg")
        return _save_chart_bytes(raw, suffix=f".{suffix}")

    # 순수 base64 (헤더 없음) — PNG 시그니처로 시도
    if len(text) > 200 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text[:80] or ""):
        try:
            raw = base64.b64decode(text, validate=False)
            if raw.startswith(b"\x89PNG") or raw[:3] == b"\xff\xd8\xff":
                return _save_chart_bytes(raw)
        except Exception:
            pass

    if len(text) > 1000:
        return None

    path = Path(text)
    candidates = [path]
    if not path.is_absolute():
        candidates.append(charts_dir() / path.name)
        candidates.append(Path.cwd() / path)
        candidates.append(charts_dir() / path)
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.suffix.lower() in _IMAGE_SUFFIXES:
                return str(candidate.resolve())
        except OSError:
            continue
    return None


def generate_fallback_chart(df: pd.DataFrame, prompt: str = "") -> str | None:
    """LLM 차트 실패 시 범주×수치 막대그래프로 폴백한다."""
    if df is None or df.empty:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.ticker import FuncFormatter
    except ImportError:
        return None

    from core.pandasai_config import exclude_total_rows, prepare_dataframe_for_ai

    font_prop = _configure_korean_font(font_manager, plt)

    work = exclude_total_rows(prepare_dataframe_for_ai(df)).reset_index(drop=True)
    if work.empty:
        return None

    cat_col, num_col = _pick_chart_columns(work, prompt)
    if num_col is None:
        return None
    if cat_col is None:
        work = work.copy()
        work.insert(0, "항목", [str(i + 1) for i in range(len(work))])
        cat_col = "항목"

    labels = work[cat_col].astype(str).fillna("")
    values = pd.to_numeric(work[num_col], errors="coerce")
    plot_df = pd.DataFrame({"label": labels, "value": values, "_order": range(len(work))})
    plot_df = plot_df.dropna(subset=["value"])

    # 이미 집계된 표(행마다 다른 라벨)는 재합산하지 않는다
    if plot_df["label"].nunique() == len(plot_df):
        plot_df = plot_df.sort_values("_order").head(CHART_MAX_CATEGORIES)
    else:
        plot_df = (
            plot_df.groupby("label", as_index=False)
            .agg(value=("value", "sum"), _order=("_order", "min"))
            .sort_values("_order")
            .head(CHART_MAX_CATEGORIES)
        )
    if plot_df.empty:
        return None

    raw_labels = plot_df["label"].astype(str).tolist()
    x_labels, context = _simplify_axis_labels(raw_labels)
    y_values = plot_df["value"].astype(float).tolist()

    fig, ax = plt.subplots(figsize=(9, 5.2), facecolor="white")
    ax.set_facecolor("white")
    bars = ax.bar(x_labels, y_values, color="#2563eb", width=0.65)

    title, x_axis_label = _chart_title_and_xlabel(cat_col, num_col, context)
    ax.set_title(title, fontproperties=font_prop, fontsize=13, pad=12)
    ax.set_xlabel(x_axis_label, fontproperties=font_prop, fontsize=11)
    ax.set_ylabel(str(num_col), fontproperties=font_prop, fontsize=11)

    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: _format_axis_number(value)))

    for label in ax.get_xticklabels():
        if font_prop is not None:
            label.set_fontproperties(font_prop)
        label.set_rotation(28)
        label.set_horizontalalignment("right")
        label.set_fontsize(9)
    for label in ax.get_yticklabels():
        if font_prop is not None:
            label.set_fontproperties(font_prop)
        label.set_fontsize(9)

    y_max = max(y_values) if y_values else 0
    for rect, value in zip(bars, y_values):
        height = rect.get_height()
        ax.text(
            rect.get_x() + rect.get_width() / 2,
            height + (y_max * 0.015 if y_max else 0),
            _format_axis_number(value),
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            fontproperties=font_prop,
            color="#0f172a",
        )

    if y_max:
        ax.set_ylim(0, y_max * 1.18)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    out = charts_dir() / f"fallback_{uuid.uuid4().hex[:12]}.png"
    try:
        fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
    finally:
        plt.close(fig)

    return str(out.resolve()) if out.is_file() else None


def generate_multi_file_chart(
    named_dfs: list[tuple[str, pd.DataFrame]],
    prompt: str = "",
) -> str | None:
    """파일별 수치 합계 막대그래프를 만든다 (소계/합계 행 제외)."""
    if not named_dfs:
        return None

    from core.analyzer import _resolve_metric_column, find_mentioned_numeric_column
    from core.pandasai_config import sum_metric_excluding_totals

    probe = next((frame for _, frame in named_dfs if frame is not None and not frame.empty), None)
    if probe is None:
        return None

    metric_col = find_mentioned_numeric_column(probe, prompt)
    if metric_col is None:
        for _name, frame in named_dfs:
            metric_col = find_mentioned_numeric_column(frame, prompt)
            if metric_col is not None:
                break
    if metric_col is None:
        return None

    rows: list[dict[str, object]] = []
    for name, frame in named_dfs:
        if frame is None or frame.empty:
            continue
        resolved = _resolve_metric_column(frame, metric_col) or metric_col
        total = sum_metric_excluding_totals(frame, resolved)
        if total is None:
            continue
        rows.append({"출처파일": name, resolved: total})

    if not rows:
        return None
    return generate_fallback_chart(pd.DataFrame(rows), prompt)


def _pick_metric_column(df: pd.DataFrame, prompt: str) -> str | None:
    normalized_prompt = re.sub(r"\s+", "", prompt).lower()
    numeric_cols: list[str] = []
    for col in df.columns:
        if pd.to_numeric(df[col], errors="coerce").notna().any():
            numeric_cols.append(col)
    if not numeric_cols:
        return None

    scored: list[tuple[int, str]] = []
    for column in numeric_cols:
        norm = re.sub(r"\s+", "", str(column)).lower()
        if len(norm) >= 2 and norm in normalized_prompt:
            scored.append((len(norm), column))
    if scored:
        scored.sort(reverse=True)
        return scored[0][1]
    return numeric_cols[0]


def _configure_korean_font(font_manager: Any, plt: Any) -> Any:
    """한글 폰트를 찾고 rcParams + FontProperties를 반환한다."""
    candidates = (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumSquareR.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    )
    for path in candidates:
        font_path = Path(path)
        if not font_path.is_file():
            continue
        try:
            font_manager.fontManager.addfont(str(font_path))
            prop = font_manager.FontProperties(fname=str(font_path))
            font_name = prop.get_name()
            plt.rcParams["font.family"] = font_name
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return prop
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _format_axis_number(value: float | int) -> str:
    """차트 축·막대 라벨용 — 콤마 구분 정확한 숫자."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number)):,}"
    return f"{number:,.2f}"


def _simplify_axis_labels(labels: list[str]) -> tuple[list[str], str | None]:
    """``맥락 · 파일명`` 형태면 X축은 짧은 이름만, 공통 맥락은 제목용으로 반환.

    표의 ``출처파일`` 열은 ``내부인건비 · 4예실대비표.xlsx``처럼 맥락을 포함하지만,
    차트 X축은 파일명만 두고 맥락은 제목으로 옮긴다.
    모든 라벨이 같은 맥락 접두사를 가질 때만 단순화한다.
    """
    if not labels:
        return labels, None

    parsed: list[tuple[str | None, str]] = []
    for label in labels:
        text = str(label).strip()
        if _CONTEXT_LABEL_SEP in text:
            ctx, short = text.split(_CONTEXT_LABEL_SEP, 1)
            ctx, short = ctx.strip(), short.strip()
            if ctx and short:
                parsed.append((ctx, short))
                continue
        parsed.append((None, text))

    contexts = {ctx for ctx, _ in parsed if ctx}
    if len(contexts) == 1 and all(ctx is not None for ctx, _ in parsed):
        return [short for _, short in parsed], next(iter(contexts))
    return [str(label).strip() for label in labels], None


def _chart_title_and_xlabel(
    cat_col: str,
    num_col: str,
    context: str | None,
) -> tuple[str, str]:
    """맥락이 있으면 제목에 넣고, X축 라벨은 짧게 유지한다."""
    if context:
        title = f"{context} · {num_col}"
        if cat_col in {"출처파일", "파일"}:
            return title, "파일"
        return title, str(cat_col)
    return f"{cat_col}별 {num_col}", str(cat_col)


def _pick_chart_columns(
    df: pd.DataFrame,
    prompt: str,
) -> tuple[str | None, str | None]:
    from core.analyzer import (
        _looks_like_code_metric_column,
        find_mentioned_numeric_columns,
    )

    numeric_cols: list[str] = []
    for col in df.columns:
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.notna().any() and not _looks_like_code_metric_column(df, col):
            numeric_cols.append(col)

    cat_cols = [
        col
        for col in df.columns
        if col not in numeric_cols
        and (
            pd.api.types.is_string_dtype(df[col])
            or df[col].dtype == object
            or str(df[col].dtype) == "category"
        )
    ]

    # 프롬프트에 수치 컬럼이 있으면 우선
    mentioned_nums = [
        col for col in find_mentioned_numeric_columns(df, prompt) if col in numeric_cols
    ]
    num_col = mentioned_nums[0] if mentioned_nums else (numeric_cols[0] if numeric_cols else None)

    normalized_prompt = re.sub(r"\s+", "", prompt).lower()

    def _mentioned(columns: list[str]) -> str | None:
        scored: list[tuple[int, str]] = []
        for column in columns:
            norm = re.sub(r"\s+", "", str(column)).lower()
            if len(norm) >= 2 and norm in normalized_prompt:
                scored.append((len(norm), column))
        if not scored:
            return None
        scored.sort(reverse=True)
        return scored[0][1]

    cat_col = _mentioned(cat_cols) or (cat_cols[0] if cat_cols else None)

    # 집계 표(범주 1열 + 금액 1열)면 첫 문자열·첫 금액 열
    if len(numeric_cols) == 1 and cat_cols and num_col is None:
        num_col = numeric_cols[0]
    if cat_col is None and num_col is not None:
        for col in cat_cols:
            if col != num_col:
                cat_col = col
                break

    return cat_col, num_col


def _save_chart_bytes(data: bytes, *, suffix: str = ".png") -> str | None:
    if not data:
        return None
    if suffix.lower() not in _IMAGE_SUFFIXES:
        suffix = ".png"
    out = charts_dir() / f"chart_{uuid.uuid4().hex[:12]}{suffix}"
    out.write_bytes(data)
    return str(out.resolve()) if out.is_file() else None


def _is_number(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    try:
        import numpy as np

        return isinstance(value, (np.integer, np.floating))
    except ImportError:
        return False
