"""앱 전역 스타일 — Streamlit 네이티브 테마 + 업로더 칩 동기화만 보조."""

from __future__ import annotations

import json

import streamlit as st

# 테마 색상은 Streamlit 설정을 따른다.
# 업로더 칩 숨김만 최소한의 JS로 처리한다 (부분 삭제 시 key 초기화 방지).


def _minimal_layout_css() -> str:
    """레이아웃·여백만 — 고정 배경색/글자색/테마 감지 없음."""
    return """
    /* 상단 툴바(⋮)와 겹치지 않도록 Streamlit 기본에 가깝게 확보 */
    .block-container {
        padding-top: 6rem !important;
        padding-bottom: 1.5rem;
        max-width: 100%;
    }

    [data-testid="stSidebar"] .block-container {
        padding-top: 1.5rem;
    }

    h1 {
        overflow: visible;
        line-height: 1.35;
        margin-top: 0 !important;
        padding-top: 0.25rem;
    }

    div[data-testid="stMetric"],
    div[data-testid="stMetric"] > div,
    div[data-testid="stMetricLabel"],
    div[data-testid="stMetricLabel"] p {
        overflow: visible !important;
        line-height: 1.4;
    }

    div[data-testid="stMetric"] {
        padding-top: 0.35rem;
    }

    img.chart-image,
    [data-testid="stImage"] img {
        max-width: 100%;
        height: auto;
    }
    """


def _uploader_chip_sync_script() -> str:
    """session_state에서 삭제한 파일 칩만 업로더 UI에서 숨긴다.

    st.file_uploader는 개별 칩을 Python에서 제거할 수 없어,
    부분 삭제 시 key를 바꾸지 않고 제외 목록에 해당하는 칩만 숨긴다.
    """
    excluded = sorted(st.session_state.get("_uploader_excluded_names") or [])
    excluded_json = json.dumps(excluded, ensure_ascii=False)
    return f"""
<script>
(() => {{
  const EXCLUDED = {excluded_json};
  const root = window.__excelAiUploaderChips || (window.__excelAiUploaderChips = {{}});
  root.excluded = EXCLUDED;
  root.generation = (root.generation || 0) + 1;
  const myGen = root.generation;

  const hideExcludedChips = () => {{
    if (myGen !== root.generation) return;
    const excluded = root.excluded || [];
    document.querySelectorAll('[data-testid="stFileChip"]').forEach((chip) => {{
      const nameEl = chip.querySelector('[data-testid="stFileChipName"]');
      const name = (nameEl && nameEl.textContent) ? nameEl.textContent.trim() : "";
      const shouldHide = excluded.some((ex) => name === ex || name.startsWith(ex));
      if (shouldHide) {{
        chip.style.setProperty("display", "none", "important");
      }} else {{
        chip.style.removeProperty("display");
      }}
    }});
  }};

  if (root.observer) {{
    try {{ root.observer.disconnect(); }} catch (_) {{}}
  }}
  let timer = null;
  root.observer = new MutationObserver(() => {{
    if (myGen !== root.generation) return;
    if (timer) return;
    timer = setTimeout(() => {{
      timer = null;
      hideExcludedChips();
    }}, 40);
  }});
  hideExcludedChips();
  root.observer.observe(document.documentElement, {{ childList: true, subtree: true }});
}})();
</script>
"""


def inject_styles() -> None:
    """최소 레이아웃 CSS + 업로더 삭제 칩 동기화 스크립트."""
    css = f"<style>\n{_minimal_layout_css()}\n</style>"
    script = _uploader_chip_sync_script()
    payload = css + script
    if hasattr(st, "html"):
        st.html(payload, unsafe_allow_javascript=True)
    else:
        st.markdown(payload, unsafe_allow_html=True)
