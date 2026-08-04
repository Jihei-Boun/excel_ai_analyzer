"""LLM / SmartDataFrame creation for PandasAI."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from core.chart_utils import charts_dir
from core.constants import (
    MULTI_FILE_INVENTORY_COLS,
    PANDASAI_MAX_RETRIES,
)
from core.pandasai_frame import prepare_dataframe_for_ai

try:
    from pandasai import SmartDataframe, SmartDatalake
    from pandasai.connectors import PandasConnector
    from pandasai.llm.local_llm import LocalLLM
except ImportError:  # pragma: no cover
    SmartDataframe = None  # type: ignore[misc, assignment]
    SmartDatalake = None  # type: ignore[misc, assignment]
    PandasConnector = None  # type: ignore[misc, assignment]
    LocalLLM = None  # type: ignore[misc, assignment]

_SAFE_CODE_RULES = (
    "코드 작성 규칙:\n"
    "- 이미 제공된 DataFrame과 pandas DataFrame API만 사용하세요.\n"
    "- 별도 모듈을 불러오지 말고 메모리 안의 데이터만 분석하세요.\n"
    "- 문자열 검색은 컬럼을 astype(str)로 변환한 뒤 수행하세요.\n"
    "- 데이터에 있는 분류명과 정확히 일치하는 요청은 해당 컬럼의 동등 비교를 사용하세요.\n"
    "- 사용자가 정렬·순위(상위/하위/내림차순 등)를 요청하지 않으면 "
    "원본 행 순서를 유지하세요. sort_values나 가나다순 정렬을 하지 마세요.\n"
    "- 피벗 시 키 조합 유일성을 확인하세요. 중복 가능하면 pivot_table에 "
    "aggfunc를 명시하세요. sum은 금액 열이고 합산이 분명할 때만 사용하세요.\n"
    "- 피벗 전 소계/합계/총계 행을 제외하고, 행 축에는 빈 분류가 없게 하세요.\n"
    "- 컬럼명을 임의로 바꿔 쓰지 말고, 스키마 힌트의 후보를 참고해 선택하세요.\n"
    "- result의 type은 dataframe, number, string, plot 중 하나만 사용하세요.\n"
    "- 목록 결과는 Python list가 아니라 dataframe type의 DataFrame 또는 Series로 반환하세요.\n"
    "- 차트 요청은 matplotlib로 그린 뒤 plt.savefig로 png 파일을 저장하고 "
    'result = {"type": "plot", "value": "저장된파일경로.png"} 형식으로 반환하세요.\n'
    "- plt.show()만 호출하지 마세요. 반드시 savefig로 파일을 저장하세요.\n"
)

_CHARTS_DIR = charts_dir()
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}


def create_llm(base_url: str, model: str) -> Any:
    """Ollama용 PandasAI LocalLLM 인스턴스를 생성한다."""
    if LocalLLM is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )

    api_base = base_url.rstrip("/")
    if not api_base.endswith("/v1"):
        api_base = f"{api_base}/v1"

    return LocalLLM(api_base=api_base, model=model)


def _pandasai_config(base_url: str, model: str) -> dict[str, Any]:
    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "llm": create_llm(base_url, model),
        "enable_cache": False,
        "save_charts": True,
        "save_charts_path": str(_CHARTS_DIR),
        "open_charts": False,
        "save_logs": False,
        "verbose": False,
        "max_retries": PANDASAI_MAX_RETRIES,
        "use_error_correction_framework": True,
    }


def create_smart_dataframe(
    df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    name: str | None = None,
) -> Any:
    """PandasAI SmartDataframe을 생성한다."""
    if SmartDataframe is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )

    return SmartDataframe(
        df,
        name=name,
        config=_pandasai_config(base_url, model),
    )


def create_smart_datalake(
    named_dfs: list[tuple[str, pd.DataFrame]],
    *,
    base_url: str,
    model: str,
) -> Any:
    """여러 DataFrame을 PandasAI SmartDatalake로 묶는다."""
    if SmartDatalake is None or PandasConnector is None:
        raise ImportError(
            "pandasai가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 실행하세요."
        )
    if len(named_dfs) < 2:
        raise ValueError("다중 파일 분석에는 파일 2개 이상이 필요합니다.")

    connectors = []
    used_names: set[str] = set()
    for index, (file_name, df) in enumerate(named_dfs):
        table_name = _unique_table_name(file_name, index, used_names)
        used_names.add(table_name)
        connectors.append(
            PandasConnector(
                {"original_df": prepare_dataframe_for_ai(df)},
                name=table_name,
                description=f"엑셀 파일: {file_name}",
            )
        )

    return SmartDatalake(connectors, config=_pandasai_config(base_url, model))


def _unique_table_name(file_name: str, index: int, used: set[str]) -> str:
    stem = re.sub(r"\.[^.]+$", "", file_name)
    safe = re.sub(r"[^0-9A-Za-z_]", "_", stem).strip("_") or f"file_{index}"
    if safe[0].isdigit():
        safe = f"t_{safe}"
    candidate = safe
    suffix = 1
    while candidate in used:
        candidate = f"{safe}_{suffix}"
        suffix += 1
    return candidate


def _multi_file_inventory(named_dfs: list[tuple[str, pd.DataFrame]]) -> str:
    lines = ["제공된 파일 목록:"]
    used: set[str] = set()
    for index, (file_name, df) in enumerate(named_dfs):
        table = _unique_table_name(file_name, index, used)
        used.add(table)
        cols = ", ".join(str(c) for c in list(df.columns)[:MULTI_FILE_INVENTORY_COLS])
        more = (
            ""
            if len(df.columns) <= MULTI_FILE_INVENTORY_COLS
            else f" 외 {len(df.columns) - MULTI_FILE_INVENTORY_COLS}개"
        )
        lines.append(
            f"- dfs[{index}] 테이블명={table} / 파일={file_name} / "
            f"{len(df):,}행 × {len(df.columns)}열 / 컬럼: {cols}{more}"
        )
    return "\n".join(lines)
