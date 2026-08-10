"""업로드 파일/시트 품질 진단 — 의도별 응답 렌더링."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

import pandas as pd

from core.io.normalize import suggest_key_columns
from core.io.text_normalize import normalize_text

QualityIntent = Literal[
    "quality_summary",
    "quality_issues_only",
    "fix_recommendations",
]

_QUALITY_SIGNAL_PHRASES = (
    "데이터품질",
    "품질분석",
    "품질진단",
    "품질검사",
    "품질평가",
    "품질확인",
    "품질보고",
    "품질점검",
    "품질이슈",
    "dataquality",
    "qualitycheck",
    "qualityreport",
    "dataqualitycheck",
    # 분석 전 점검·수정 요청 (PandasAI 우회)
    "분석전에",
    "분석전",
    "수정하면좋은",
    "고치면좋은",
    "고칠부분",
    "수정할부분",
    "개선할부분",
    "개선제안",
    "전처리",
    "데이터정리",
    "데이터클린",
    "클렌징",
    "클리닝",
    "datacleaning",
    "dataclean",
    "preprocessing",
    "주의할점",
    "확인할부분",
    "문제되는부분",
    "문제가있는",
    "문제점",
    "이상치",
    "outlier",
)

_QUALITY_SIGNAL_COMBOS = (
    ("수정", "분석"),
    ("고치", "분석"),
    ("결측", "처리"),
    ("결측", "채우"),
    ("결측", "문제"),
    ("결측", "확인"),
    ("중복", "문제"),
    ("중복", "확인"),
    ("누락", "처리"),
    ("누락", "채우"),
    ("누락", "문제"),
    ("clean", "data"),
    ("fix", "before"),
    # "문제"+"알려" 같은 짧은 토큰 조합은 '주문제외' 오탐을 만든다 → 제거.
    # 명시적 품질 문구(_QUALITY_SIGNAL_PHRASES / 경계 매칭)만 사용.
)

# 단독 토큰처럼 쓰일 때만 quality로 본다 (부분문자열 금지).
_QUALITY_BOUNDED_TOKENS = (
    "문제점",
    "이상치",
    "outlier",
    "품질",
    "전처리",
    "클렌징",
    "클리닝",
)

_FIX_INTENT_PHRASES = (
    "수정하면좋은",
    "고치면좋은",
    "고칠부분",
    "수정할부분",
    "개선할부분",
    "개선제안",
    "전처리",
    "데이터정리",
    "데이터클린",
    "클렌징",
    "클리닝",
    "datacleaning",
    "dataclean",
    "preprocessing",
    "주의할점",
    "확인할부분",
    "분석전에",
    "분석전",
)

_FIX_INTENT_COMBOS = (
    ("수정", "분석"),
    ("고치", "분석"),
    ("결측", "처리"),
    ("결측", "채우"),
    ("누락", "처리"),
    ("누락", "채우"),
    ("clean", "data"),
    ("fix", "before"),
)

_ISSUE_INTENT_PHRASES = (
    "문제가있는",
    "문제되는부분",
    "문제점",
    "이슈만",
    "이상치",
    "outlier",
    "문제만",
    "이슈있는",
)


@dataclass
class QualityReport:
    """단일 DataFrame 품질 진단 결과."""

    row_count: int = 0
    col_count: int = 0
    empty_col_count: int = 0
    empty_col_ratio: float = 0.0
    empty_columns: list[str] = field(default_factory=list)
    missing_cells: int = 0
    missing_ratio: float = 0.0
    duplicate_row_count: int = 0
    mixed_type_columns: list[str] = field(default_factory=list)
    high_missing_columns: list[str] = field(default_factory=list)
    missing_columns: list[dict] = field(default_factory=list)
    suspected_key_columns: list[str] = field(default_factory=list)
    key_duplicate_info: list[dict] = field(default_factory=list)
    severity: str = "ok"  # ok | warn | error
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def is_quality_request(prompt: str) -> bool:
    """데이터 품질 진단·분석 전 수정 요청인지 판별한다.

    짧은 토큰(예: '문제')의 부분문자열 매칭으로 도메인 값(주문제외)을
    system command로 오탐하지 않도록, 명시 phrase / 경계 토큰 위주로 판정한다.
    """
    compact = _compact_prompt(prompt)
    if not compact:
        return False

    from core.filter.value_filter import is_missing_rows_request

    if is_missing_rows_request(prompt):
        return False

    if "품질" in compact:
        return True
    if any(phrase in compact for phrase in _QUALITY_SIGNAL_PHRASES):
        return True
    if any(all(token in compact for token in combo) for combo in _QUALITY_SIGNAL_COMBOS):
        return True
    # 경계 토큰: 앞뒤가 한글/영문/숫자가 아닐 때만 (또는 문자열 끝)
    for token in _QUALITY_BOUNDED_TOKENS:
        if _has_bounded_token(compact, token):
            return True
    # '데이터에 문제가' / '문제가 있는지' 등 명시 패턴
    if re.search(r"(데이터)?(에|의)?문제가(있|있나|있는지|있는지)", compact):
        return True
    if re.search(r"이상한데이터|데이터이상|이상한다|이상해요", compact):
        return True
    return False


def _has_bounded_token(compact: str, token: str) -> bool:
    """compact 문자열에서 token이 '단어 경계'에 가깝게 등장하는지."""
    if not token or token not in compact:
        return False
    # 한글/영문/숫자 연속 글자 안쪽이면 오탐 후보
    pattern = rf"(?<![0-9A-Za-z가-힣]){re.escape(token)}(?![0-9A-Za-z가-힣])"
    return re.search(pattern, compact) is not None


def detect_quality_intent(prompt: str) -> QualityIntent:
    """품질 관련 질문의 응답 유형을 판별한다."""
    compact = _compact_prompt(prompt)
    if not compact:
        return "quality_summary"

    if any(phrase in compact for phrase in _FIX_INTENT_PHRASES):
        return "fix_recommendations"
    if any(all(token in compact for token in combo) for combo in _FIX_INTENT_COMBOS):
        return "fix_recommendations"

    if any(phrase in compact for phrase in _ISSUE_INTENT_PHRASES):
        return "quality_issues_only"
    if ("문제" in compact or "이슈" in compact) and (
        "만" in compact or "부분" in compact or "알려" in compact
    ):
        return "quality_issues_only"

    return "quality_summary"


def diagnose_dataframe(
    df: pd.DataFrame,
    *,
    label: str = "데이터",
    missing_col_threshold: float = 0.4,
    missing_overall_threshold: float = 0.25,
) -> QualityReport:
    """DataFrame 품질을 점검하고 경고/제안을 생성한다."""
    report = QualityReport()
    if df is None:
        report.severity = "error"
        report.warnings.append(f"`{label}`를 읽을 수 없습니다.")
        report.suggestions.append("파일이 손상되었거나 시트가 비어 있는지 확인하세요.")
        return report

    report.row_count = int(len(df))
    report.col_count = int(len(df.columns))

    if report.row_count == 0 or report.col_count == 0:
        report.severity = "error"
        report.warnings.append(f"`{label}`에 분석할 행/열이 없습니다.")
        report.suggestions.append(
            "헤더 위치·시트 선택·병합 셀 구조를 확인한 뒤 다시 업로드하세요."
        )
        return report

    empty_cols = [str(c) for c in df.columns if df[c].isna().all()]
    report.empty_columns = empty_cols
    report.empty_col_count = len(empty_cols)
    report.empty_col_ratio = len(empty_cols) / max(report.col_count, 1)

    missing = int(df.isna().sum().sum())
    report.missing_cells = missing
    report.missing_ratio = missing / max(df.size, 1)

    try:
        report.duplicate_row_count = int(df.duplicated().sum())
    except TypeError:
        report.duplicate_row_count = 0

    report.mixed_type_columns = _find_mixed_type_columns(df)
    report.missing_columns = [
        {
            "column": str(col),
            "missing_count": int(df[col].isna().sum()),
            "missing_ratio": float(df[col].isna().mean()),
        }
        for col in df.columns
        if bool(df[col].isna().any())
    ]
    report.high_missing_columns = [
        item["column"]
        for item in report.missing_columns
        if float(item["missing_ratio"]) >= missing_col_threshold
    ]
    report.suspected_key_columns = suggest_key_columns(df)
    report.key_duplicate_info = _key_duplicate_info(df, report.suspected_key_columns)

    _fill_warnings_and_suggestions(
        report,
        label=label,
        empty_cols=empty_cols,
        missing_overall_threshold=missing_overall_threshold,
    )
    return report


# 사용자 요청 예시의 별칭
build_quality_report = diagnose_dataframe


def format_quality_summary(report: QualityReport, *, label: str = "데이터") -> str:
    """짧은 한 줄 요약 (미리보기 캡션용)."""
    return (
        f"`{label}` 품질: {report.severity.upper()} · "
        f"{report.row_count}행×{report.col_count}열 · "
        f"결측 {report.missing_ratio:.1%} · "
        f"중복행 {report.duplicate_row_count}"
    )


def render_quality_summary(report: QualityReport, *, label: str = "데이터") -> str:
    """전체 품질 현황 + 개선 제안."""
    lines = [
        f"### `{label}` 데이터 품질",
        "",
        f"- 판정: **{report.severity.upper()}**",
        f"- 크기: {report.row_count:,}행 × {report.col_count}열",
        f"- 결측 셀: {report.missing_cells:,}개 ({report.missing_ratio:.1%})",
        f"- 완전 중복 행: {report.duplicate_row_count:,}개",
        f"- 빈 열: {report.empty_col_count}개",
    ]
    if report.mixed_type_columns:
        sample = ", ".join(f"`{c}`" for c in report.mixed_type_columns[:8])
        more = (
            f" 외 {len(report.mixed_type_columns) - 8}개"
            if len(report.mixed_type_columns) > 8
            else ""
        )
        lines.append(f"- 혼합 타입 열: {sample}{more}")
    if report.high_missing_columns:
        sample = ", ".join(f"`{c}`" for c in report.high_missing_columns[:8])
        lines.append(f"- 결측 많은 열: {sample}")
    elif report.missing_columns:
        sample = ", ".join(
            f"`{item['column']}`({item['missing_count']}개)"
            for item in report.missing_columns[:8]
        )
        lines.append(f"- 결측이 있는 열: {sample}")
    if report.suspected_key_columns:
        keys = ", ".join(f"`{c}`" for c in report.suspected_key_columns[:5])
        lines.append(f"- 키 후보: {keys}")

    if report.warnings:
        lines.extend(["", "**경고**"])
        lines.extend(f"- {item}" for item in report.warnings)

    if report.suggestions:
        lines.extend(["", "**개선 제안**"])
        lines.extend(f"- {item}" for item in report.suggestions)

    return "\n".join(lines)


# 하위 호환
format_quality_report = render_quality_summary


def render_quality_issues(report: QualityReport, *, label: str = "데이터") -> str:
    """실제 문제가 있는 항목만 출력한다."""
    issues = _collect_issue_items(report)
    if not issues:
        return "특별한 데이터 품질 문제를 발견하지 못했습니다."

    lines = [f"**`{label}`에서 발견한 문제**", ""]
    lines.extend(f"- {item}" for item in issues)
    return "\n".join(lines)


def render_fix_recommendations(
    report: QualityReport,
    *,
    label: str = "데이터",
) -> str:
    """수정이 필요한 항목과 방법만 출력한다."""
    items = _collect_fix_items(report)
    if not items:
        return "분석 전에 특별히 수정할 항목은 없습니다."

    lines = [
        "**분석 전에 수정하면 좋은 항목입니다.**",
        "",
    ]
    for title, action in items:
        lines.append(f"- {title}")
        lines.append(f"  - {action}")
    lines.append("")
    lines.append(f"**수정이 필요한 항목은 위 {len(items)}가지입니다.**")
    return "\n".join(lines)


def build_quality_outcome(
    named_frames: list[tuple[str, pd.DataFrame]],
    *,
    unit_label: str = "파일",
    prompt: str | None = None,
) -> tuple[str, pd.DataFrame | None]:
    """단일/다중 단위 품질 진단 (reply, 요약 표)."""
    if not named_frames:
        return f"진단할 {unit_label}이(가) 없습니다.", None

    intent = detect_quality_intent(prompt or "")
    render = _renderer_for_intent(intent)

    reports: list[tuple[str, QualityReport]] = [
        (name, diagnose_dataframe(df, label=name)) for name, df in named_frames
    ]

    if len(reports) == 1:
        name, report = reports[0]
        return render(report, label=name), None

    parts: list[str] = []
    if intent == "quality_summary":
        parts.append(
            f"선택된 {unit_label} {len(reports)}개의 데이터 품질을 진단했습니다."
        )
        parts.append("")
        for name, report in reports:
            parts.append(render(report, label=name))
            parts.append("")
        table = build_quality_compare_table(reports, unit_label=unit_label)
        return "\n".join(parts).rstrip(), table

    for name, report in reports:
        parts.append(f"### `{name}`")
        parts.append("")
        parts.append(render(report, label=name))
        parts.append("")
    return "\n".join(parts).rstrip(), None


def build_quality_compare_table(
    reports: list[tuple[str, QualityReport]],
    *,
    unit_label: str = "파일",
) -> pd.DataFrame:
    """단위별 품질 요약 표."""
    rows: list[dict[str, object]] = []
    for name, report in reports:
        rows.append(
            {
                unit_label: name,
                "판정": report.severity.upper(),
                "행 수": report.row_count,
                "열 수": report.col_count,
                "결측 비율": round(report.missing_ratio, 4),
                "중복 행": report.duplicate_row_count,
                "빈 열": report.empty_col_count,
                "경고 수": len(report.warnings),
            }
        )
    return pd.DataFrame(rows)


def friendly_load_error(exc: Exception, *, path: str | None = None) -> str:
    """파싱 실패 시 사용자 친화 메시지."""
    detail = str(exc).strip() or exc.__class__.__name__
    target = f" (`{path}`)" if path else ""
    return (
        f"엑셀을 읽는 중 문제가 발생했습니다{target}.\n"
        f"- 원인 요약: {detail}\n"
        "- 확인 포인트: 시트 보호/암호, 손상된 파일, 비정상 헤더/병합 셀\n"
        "- 조치: 다른 시트를 선택하거나, 헤더 행을 정리한 뒤 다시 업로드하세요."
    )


def _compact_prompt(prompt: str | None) -> str:
    if not prompt or not str(prompt).strip():
        return ""
    return re.sub(r"\s+", "", normalize_text(prompt))


def _renderer_for_intent(intent: QualityIntent):
    if intent == "quality_issues_only":
        return render_quality_issues
    if intent == "fix_recommendations":
        return render_fix_recommendations
    return render_quality_summary


def _collect_issue_items(report: QualityReport) -> list[str]:
    """문제가 되는 사실만 나열 (수정 방법 없음)."""
    issues: list[str] = []

    if report.severity == "error" and report.warnings:
        issues.extend(report.warnings)
        return issues

    for item in report.missing_columns:
        issues.append(
            f"`{item['column']}` 결측 {item['missing_count']}개 "
            f"({float(item['missing_ratio']):.0%})"
        )

    if report.duplicate_row_count:
        issues.append(f"완전 중복 행 {report.duplicate_row_count}개")

    if report.empty_columns:
        sample = ", ".join(f"`{c}`" for c in report.empty_columns[:8])
        more = (
            f" 외 {len(report.empty_columns) - 8}개"
            if len(report.empty_columns) > 8
            else ""
        )
        issues.append(f"완전히 비어 있는 열: {sample}{more}")

    if report.mixed_type_columns:
        sample = ", ".join(f"`{c}`" for c in report.mixed_type_columns[:8])
        issues.append(f"문자/숫자가 섞인 열: {sample}")

    for item in report.key_duplicate_info:
        if item["duplicate_ratio"] >= 0.3 and item["non_null"] >= 5:
            issues.append(
                f"키 후보 `{item['column']}` 중복 값 비율 "
                f"{item['duplicate_ratio']:.0%}"
            )

    if (
        not report.suspected_key_columns
        and report.row_count >= 5
        and report.severity != "ok"
    ):
        # 경고로만 잡힌 키 부재 — severity가 warn/error일 때만
        if any("조인 키" in w for w in report.warnings):
            issues.append("유니크한 조인 키 후보를 찾기 어렵습니다.")

    return issues


def _collect_fix_items(report: QualityReport) -> list[tuple[str, str]]:
    """수정이 필요한 항목 (제목, 조치) 목록."""
    items: list[tuple[str, str]] = []

    if report.severity == "error" and report.suggestions:
        for suggestion in report.suggestions:
            items.append(("**조치 필요**", suggestion))
        return items

    for item in report.missing_columns:
        col = str(item["column"])
        count = int(item["missing_count"])
        title = f"**{col}** — 결측값 **{count}개**"
        if _looks_like_date_column(col):
            action = "날짜를 확인하거나 결측 처리 기준을 정하세요."
        else:
            action = "값을 입력하거나 해당 행을 제외하세요."
        items.append((title, action))

    if report.empty_columns:
        sample = ", ".join(f"**{c}**" for c in report.empty_columns[:5])
        items.append(
            (
                f"**빈 열** — {sample}",
                "완전히 비어 있는 열은 제거하거나 헤더명을 확인하세요.",
            )
        )

    if report.duplicate_row_count:
        items.append(
            (
                f"**완전 중복 행** — **{report.duplicate_row_count}개**",
                "중복을 제거하면 합계/평균 왜곡을 줄일 수 있습니다.",
            )
        )

    if report.mixed_type_columns:
        sample = ", ".join(f"**{c}**" for c in report.mixed_type_columns[:5])
        items.append(
            (
                f"**혼합 타입 열** — {sample}",
                "숫자만 남기거나 문자로 통일하세요.",
            )
        )

    for item in report.key_duplicate_info:
        if item["duplicate_ratio"] >= 0.3 and item["non_null"] >= 5:
            items.append(
                (
                    f"**{item['column']}** — 키 중복 **{item['duplicate_ratio']:.0%}**",
                    "병합 시 단독 키 대신 복합 키를 고려하세요.",
                )
            )

    if not report.suspected_key_columns and report.row_count >= 5:
        if any("조인 키" in w for w in report.warnings):
            items.append(
                (
                    "**조인 키 후보 없음**",
                    "파일 간 비교·병합을 위해 ID/코드 성격의 열을 추가하는 것이 좋습니다.",
                )
            )

    return items


def _looks_like_date_column(name: str) -> bool:
    compact = re.sub(r"\s+", "", str(name)).lower()
    return any(
        token in compact
        for token in ("일자", "날짜", "일시", "date", "time", "ymd", "연월일")
    )


def _find_mixed_type_columns(df: pd.DataFrame) -> list[str]:
    mixed: list[str] = []
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        sample = series.dropna()
        if sample.empty:
            continue
        texts = sample.map(lambda v: str(v).strip())
        numeric_like = texts.str.replace(",", "", regex=False).str.fullmatch(
            r"-?\d+(\.\d+)?"
        )
        ratio = float(numeric_like.fillna(False).mean()) if len(texts) else 0.0
        if 0.15 < ratio < 0.85:
            mixed.append(str(col))
    return mixed


def _key_duplicate_info(df: pd.DataFrame, key_cols: list[str]) -> list[dict]:
    info: list[dict] = []
    for col in key_cols[:3]:
        if col not in df.columns:
            continue
        series = df[col]
        non_null = int(series.notna().sum())
        if non_null == 0:
            continue
        dup = int(series.duplicated(keep=False).sum())
        info.append(
            {
                "column": col,
                "non_null": non_null,
                "duplicate_values": dup,
                "duplicate_ratio": dup / max(non_null, 1),
            }
        )
    return info


def _fill_warnings_and_suggestions(
    report: QualityReport,
    *,
    label: str,
    empty_cols: list[str],
    missing_overall_threshold: float,
) -> None:
    severity_rank = 0  # 0 ok, 1 warn, 2 error

    if report.empty_col_count:
        severity_rank = max(severity_rank, 1)
        sample = ", ".join(empty_cols[:5])
        more = f" 외 {len(empty_cols) - 5}개" if len(empty_cols) > 5 else ""
        report.warnings.append(f"비어 있는 열 {report.empty_col_count}개: {sample}{more}")
        report.suggestions.append("완전히 비어 있는 열은 제거하거나 헤더명을 확인하세요.")

    if report.missing_ratio >= missing_overall_threshold:
        severity_rank = max(severity_rank, 1)
        report.warnings.append(
            f"전체 결측 비율이 높습니다 ({report.missing_ratio:.1%})."
        )
        report.suggestions.append(
            "결측이 많은 열을 제외하거나, 분석 전에 값을 채운 뒤 다시 시도하세요."
        )

    if report.high_missing_columns:
        severity_rank = max(severity_rank, 1)
        sample = ", ".join(report.high_missing_columns[:5])
        report.warnings.append(f"결측이 많은 열: {sample}")
        report.suggestions.append(
            f"`{report.high_missing_columns[0]}` 열의 결측 원인을 확인하세요."
        )
    elif report.missing_columns:
        # 비율이 낮아도 전체 요약의 개선 제안에 구체적인 결측 열을 안내한다.
        sample = ", ".join(
            f"`{item['column']}`({item['missing_count']}개)"
            for item in report.missing_columns[:8]
        )
        report.suggestions.append(
            f"결측이 있는 열: {sample}. 분석 전에 값을 채우거나 해당 행을 제외하세요."
        )

    if report.mixed_type_columns:
        severity_rank = max(severity_rank, 1)
        sample = ", ".join(report.mixed_type_columns[:5])
        report.warnings.append(f"문자/숫자가 섞인 열: {sample}")
        report.suggestions.append(
            "혼합 타입 열은 숫자만 남기거나 문자로 통일하면 집계·병합이 안정됩니다."
        )

    if report.duplicate_row_count:
        severity_rank = max(severity_rank, 1)
        report.warnings.append(f"완전 중복 행 {report.duplicate_row_count}개")
        report.suggestions.append("중복 행을 제거하면 합계/평균 왜곡을 줄일 수 있습니다.")

    for item in report.key_duplicate_info:
        if item["duplicate_ratio"] >= 0.3 and item["non_null"] >= 5:
            severity_rank = max(severity_rank, 1)
            report.warnings.append(
                f"키 후보 `{item['column']}`에 중복 값이 많습니다 "
                f"({item['duplicate_ratio']:.0%})."
            )
            report.suggestions.append(
                f"병합 시 `{item['column']}` 단독 키 대신 복합 키를 고려하세요."
            )

    if not report.suspected_key_columns and report.row_count >= 5:
        severity_rank = max(severity_rank, 1)
        report.warnings.append("유니크한 조인 키 후보를 찾기 어렵습니다.")
        report.suggestions.append(
            "파일 간 비교·병합을 위해 ID/코드 성격의 열을 추가하는 것이 좋습니다."
        )

    if severity_rank >= 2:
        report.severity = "error"
    elif severity_rank == 1:
        report.severity = "warn"
    else:
        report.severity = "ok"
        if report.suggestions:
            report.suggestions.append(
                "위 항목만 정리하면 바로 분석하거나 다른 파일과 병합할 수 있습니다."
            )
        else:
            report.suggestions.append(
                f"`{label}` 품질이 양호합니다. 바로 분석하거나 다른 파일과 병합할 수 있습니다."
            )
