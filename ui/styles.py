"""앱 전역 스타일 — 단일 theme 상태 + CSS 변수 기반 테마 적용."""

from __future__ import annotations

import json

import streamlit as st

from ui.styles_dark import dark_theme_css
from ui.styles_light import light_theme_css
from ui.styles_shared import shared_layout_css

THEMES = ("dark", "light")


def _theme_script(theme: str) -> str:
    """단일 전역 테마 컨트롤러.

    Streamlit rerun마다 script가 다시 삽입되면 이전 MutationObserver가
    옛 테마 색으로 계속 칠해 업로더/필/멀티셀렉트가 반대로 보이는
    문제가 생긴다. window.__excelAiTheme 싱글톤으로 교체한다.
    """
    excluded = sorted(st.session_state.get("_uploader_excluded_names") or [])
    excluded_json = json.dumps(excluded, ensure_ascii=False)
    return f"""
<script>
(() => {{
  const NEXT = "{theme}";
  const EXCLUDED_FILES = {excluded_json};
  const TOKENS = {{
    light: {{
      headerBg: "#f3f4f6",
      headerFg: "#111827",
      chipBg: "#ffffff",
      border: "#d0d7e2",
      tagBg: "#dbeafe",
      tagFg: "#1e3a8a",
      tagBorder: "#93c5fd",
      codeBg: "#eff6ff",
      codeFg: "#1e3a8a",
      codeBorder: "#bfdbfe",
      pillBg: "#ffffff",
      pillFg: "#111827",
      pillActiveBg: "#dbeafe",
      pillActiveFg: "#1e3a8a",
      pillActiveBorder: "#93c5fd",
    }},
    dark: {{
      headerBg: "#151b27",
      headerFg: "#e8edf5",
      chipBg: "#1a2233",
      border: "#2a3347",
      tagBg: "#1e3a5f",
      tagFg: "#93c5fd",
      tagBorder: "rgba(59, 130, 246, 0.45)",
      codeBg: "#1e293b",
      codeFg: "#e2e8f0",
      codeBorder: "#334155",
      pillBg: "#151b27",
      pillFg: "#e8edf5",
      pillActiveBg: "rgba(59, 130, 246, 0.18)",
      pillActiveFg: "#93c5fd",
      pillActiveBorder: "rgba(59, 130, 246, 0.4)",
    }},
  }};

  const root = window.__excelAiTheme || (window.__excelAiTheme = {{}});
  root.theme = NEXT;
  root.tokens = TOKENS;
  root.excludedFiles = EXCLUDED_FILES;
  root.generation = (root.generation || 0) + 1;
  const myGen = root.generation;

  const paint = (el, bg, fg) => {{
    if (!el) return;
    el.style.setProperty("background", bg, "important");
    el.style.setProperty("background-color", bg, "important");
    el.style.setProperty("color", fg, "important");
    el.style.setProperty("-webkit-text-fill-color", fg, "important");
  }};

  const paintChildrenText = (el, fg) => {{
    el.querySelectorAll("span, div, p, small, label").forEach((child) => {{
      child.style.setProperty("color", fg, "important");
      child.style.setProperty("-webkit-text-fill-color", fg, "important");
      child.style.setProperty("background", "transparent", "important");
    }});
    el.querySelectorAll("svg, path").forEach((child) => {{
      child.style.setProperty("fill", fg, "important");
      child.style.setProperty("stroke", fg, "important");
      child.style.setProperty("color", fg, "important");
    }});
  }};

  const purgeStaleStyles = () => {{
    const marker = "app-theme:" + root.theme;
    document.querySelectorAll("style").forEach((el) => {{
      const text = el.textContent || "";
      if (!text.includes("app-theme:")) return;
      if (!text.includes(marker)) el.remove();
    }});
  }};

  const hideExcludedChips = () => {{
    const excluded = root.excludedFiles || [];
    document.querySelectorAll('[data-testid="stFileChip"]').forEach((chip) => {{
      const nameEl = chip.querySelector('[data-testid="stFileChipName"]');
      const name = (nameEl && nameEl.textContent) ? nameEl.textContent.trim() : "";
      const hide = excluded.some((ex) => name === ex || name.startsWith(ex));
      if (hide) {{
        chip.style.setProperty("display", "none", "important");
      }} else {{
        chip.style.removeProperty("display");
      }}
    }});
  }};

  const paintWidgets = () => {{
    const theme = root.theme;
    const t = root.tokens[theme] || root.tokens.dark;

    document.querySelectorAll('[data-testid="stExpander"] summary').forEach((el) => {{
      paint(el, t.headerBg, t.headerFg);
      paintChildrenText(el, t.headerFg);
    }});

    document.querySelectorAll(
      '[data-testid="stFileUploader"], '
      + '[data-testid="stFileUploader"] section, '
      + '[data-testid="stFileUploaderDropzone"], '
      + '[data-testid="stFileUploaderDropzone"] section, '
      + '[data-testid="stFileUploaderDropzoneInstructions"]'
    ).forEach((el) => {{
      paint(el, t.chipBg, t.headerFg);
      el.style.setProperty("border-color", t.border, "important");
    }});

    document.querySelectorAll('[data-testid="stFileChips"]').forEach((el) => {{
      el.style.setProperty("background", "transparent", "important");
      el.style.setProperty("background-color", "transparent", "important");
      el.style.setProperty("border", "none", "important");
      el.style.setProperty("display", "flex", "important");
      el.style.setProperty("gap", "0.5rem", "important");
      el.style.setProperty("flex-wrap", "wrap", "important");
    }});

    document.querySelectorAll('[data-testid="stFileChip"]').forEach((el) => {{
      paint(el, t.chipBg, t.headerFg);
      el.style.setProperty("border", `1px solid ${{t.border}}`, "important");
      el.style.setProperty("border-radius", "8px", "important");
      el.style.setProperty("padding", "0.45rem 0.7rem", "important");
    }});

    document.querySelectorAll('[data-testid="stFileChipName"]').forEach((el) => {{
      el.style.setProperty("background", "transparent", "important");
      el.style.setProperty("color", t.headerFg, "important");
      el.style.setProperty("-webkit-text-fill-color", t.headerFg, "important");
      paintChildrenText(el, t.headerFg);
    }});

    // 깨진 아이콘·칩 삭제 버튼만 숨김 (파일명 칩 자체는 업로드 박스 안에 유지)
    document.querySelectorAll('[data-testid="stFileChip"]').forEach((chip) => {{
      const first = chip.firstElementChild;
      if (first && !first.querySelector('[data-testid="stFileChipName"]')) {{
        first.style.setProperty("display", "none", "important");
      }}
    }});
    document.querySelectorAll(
      '[data-testid="stFileChipImagePreview"], '
      + '[data-testid="stFileChipDeleteBtn"], '
      + '[data-testid="stFileChipIconSpinner"], '
      + '[data-testid="stFileChipIconError"]'
    ).forEach((el) => {{
      el.style.setProperty("display", "none", "important");
    }});

    hideExcludedChips();

    document.querySelectorAll(
      '[data-testid="stFileUploader"] button, '
      + '[data-testid="stFileUploaderDropzone"] button'
    ).forEach((el) => {{
      if (el.closest && el.closest('[data-testid="stFileChip"]')) return;
      paint(el, t.chipBg, t.headerFg);
      el.style.setProperty("border", `1px solid ${{t.border}}`, "important");
    }});

    document.querySelectorAll(
      '[data-testid="stSelectbox"] [data-baseweb="select"], '
      + '[data-testid="stSelectbox"] [data-baseweb="select"] > div, '
      + '[data-testid="stMultiSelect"] [data-baseweb="select"], '
      + '[data-testid="stMultiSelect"] [data-baseweb="select"] > div'
    ).forEach((el) => {{
      paint(el, t.chipBg, t.headerFg);
      el.style.setProperty("border-color", t.border, "important");
      el.querySelectorAll("*").forEach((child) => {{
        if (child.closest && child.closest('[data-baseweb="tag"]')) return;
        child.style.setProperty("color", t.headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", t.headerFg, "important");
      }});
    }});

    document.querySelectorAll(
      '[data-testid="stMultiSelect"] [data-baseweb="tag"], [data-baseweb="tag"]'
    ).forEach((el) => {{
      paint(el, t.tagBg, t.tagFg);
      el.style.setProperty("border", `1px solid ${{t.tagBorder}}`, "important");
      paintChildrenText(el, t.tagFg);
    }});

    document.querySelectorAll(
      ".chat-inline-code, .chat-assistant code, .chat-user code"
    ).forEach((el) => {{
      paint(el, t.codeBg, t.codeFg);
      el.style.setProperty("border", `1px solid ${{t.codeBorder}}`, "important");
    }});

    document.querySelectorAll(
      '[data-testid="stPills"] button, '
      + '[data-testid="stBaseButton-pills"], '
      + '[data-testid="stBaseButton-pillsActive"], '
      + '[data-testid="stBaseButton-segmentedControl"], '
      + '[data-testid="stBaseButton-segmentedControlActive"]'
    ).forEach((el) => {{
      const active =
        el.getAttribute("aria-pressed") === "true"
        || el.getAttribute("kind") === "primary"
        || (el.getAttribute("data-testid") || "").toLowerCase().includes("active");
      if (active) {{
        paint(el, t.pillActiveBg, t.pillActiveFg);
        el.style.setProperty("border", `1px solid ${{t.pillActiveBorder}}`, "important");
      }} else {{
        paint(el, t.pillBg, t.pillFg);
        el.style.setProperty("border", `1px solid ${{t.border}}`, "important");
      }}
      paintChildrenText(el, active ? t.pillActiveFg : t.pillFg);
    }});
  }};

  const apply = () => {{
    if (myGen !== root.generation) return;
    if (root.observer) {{
      try {{ root.observer.disconnect(); }} catch (_) {{}}
    }}
    const theme = root.theme;
    document.documentElement.setAttribute("data-app-theme", theme);
    document.documentElement.style.colorScheme = theme;
    if (document.body) {{
      document.body.setAttribute("data-app-theme", theme);
      document.body.style.colorScheme = theme;
    }}
    purgeStaleStyles();
    paintWidgets();
    if (root.observer && myGen === root.generation) {{
      root.observer.observe(document.documentElement, {{ childList: true, subtree: true }});
    }}
  }};

  root.apply = apply;

  if (root.observer) {{
    try {{ root.observer.disconnect(); }} catch (_) {{}}
  }}
  let timer = null;
  root.observer = new MutationObserver(() => {{
    if (myGen !== root.generation) return;
    if (timer) return;
    timer = setTimeout(() => {{
      timer = null;
      apply();
    }}, 40);
  }});
  apply();
}})();
</script>
"""


def _build_css(theme: str) -> str:
    color_css = light_theme_css() if theme == "light" else dark_theme_css()
    return f"""
{shared_layout_css()}
{color_css}
"""


def current_theme() -> str:
    """단일 테마 상태. session_state['theme']만 사용한다."""
    theme = st.session_state.get("theme", "dark")
    return theme if theme in THEMES else "dark"


def inject_styles() -> None:
    theme = current_theme()
    st.session_state.theme = theme
    marker = f"/* app-theme:{theme} */"
    css = f"<style data-app-theme-style=\"{theme}\">\n{marker}\n{_build_css(theme)}\n</style>"
    script = _theme_script(theme)
    if hasattr(st, "html"):
        st.html(css + script, unsafe_allow_javascript=True)
    else:
        st.markdown(css + script, unsafe_allow_html=True)
