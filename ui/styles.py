"""앱 전역 스타일 — 다크/라이트 테마를 각각 독립 지정."""

from __future__ import annotations

import streamlit as st

from ui.styles_dark import dark_theme_css
from ui.styles_light import light_theme_css
from ui.styles_shared import shared_layout_css

THEMES = ("dark", "light")


def _theme_script(theme: str) -> str:
    header_bg = "#f3f4f6" if theme == "light" else "#151b27"
    header_fg = "#111827" if theme == "light" else "#e8edf5"
    chip_bg = "#ffffff" if theme == "light" else "#1a2233"
    return f"""
<script>
(() => {{
  const theme = "{theme}";
  const headerBg = "{header_bg}";
  const headerFg = "{header_fg}";
  const chipBg = "{chip_bg}";
  let observer = null;

  const paint = (el, bg, fg) => {{
    if (!el) return;
    el.style.setProperty("background", bg, "important");
    el.style.setProperty("background-color", bg, "important");
    el.style.setProperty("color", fg, "important");
    el.style.setProperty("-webkit-text-fill-color", fg, "important");
  }};

  const paintWidgets = () => {{
    document.querySelectorAll('[data-testid="stExpander"] summary').forEach((el) => {{
      paint(el, headerBg, headerFg);
      el.querySelectorAll("div, span, p, label").forEach((child) => {{
        child.style.setProperty("background", "transparent", "important");
        child.style.setProperty("color", headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
      }});
      el.querySelectorAll("svg, path").forEach((child) => {{
        child.style.setProperty("fill", headerFg, "important");
        child.style.setProperty("stroke", headerFg, "important");
      }});
    }});

    document.querySelectorAll('[data-testid="stFileChip"]').forEach((el) => {{
      paint(el, chipBg, headerFg);
      el.style.setProperty(
        "border",
        theme === "light" ? "1px solid #d0d7e2" : "1px solid #2a3347",
        "important"
      );
      el.querySelectorAll("span, div, p, small").forEach((child) => {{
        child.style.setProperty("color", headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
        child.style.setProperty("background", "transparent", "important");
      }});
      el.querySelectorAll("svg, path").forEach((child) => {{
        child.style.setProperty("fill", headerFg, "important");
        child.style.setProperty("stroke", headerFg, "important");
      }});
    }});

    document.querySelectorAll(
      '[data-testid="stFileUploaderDropzone"], [data-testid="stFileUploaderDropzone"] section, [data-testid="stFileUploaderDropzoneInstructions"]'
    ).forEach((el) => {{
      paint(el, chipBg, headerFg);
      el.querySelectorAll("span, div, p, small, button").forEach((child) => {{
        if (child.tagName === "BUTTON") {{
          paint(child, chipBg, headerFg);
          child.style.setProperty("border", "1px solid #d0d7e2", "important");
        }} else {{
          child.style.setProperty("color", headerFg, "important");
          child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
        }}
      }});
    }});

    document.querySelectorAll(
      '[data-testid="stSelectbox"] [data-baseweb="select"] > div, '
      + '[data-testid="stMultiSelect"] [data-baseweb="select"] > div'
    ).forEach((el) => {{
      paint(el, chipBg, headerFg);
      el.style.setProperty(
        "border-color",
        theme === "light" ? "#d0d7e2" : "#2a3347",
        "important"
      );
      el.querySelectorAll("*").forEach((child) => {{
        if (child.getAttribute && child.getAttribute("data-baseweb") === "tag") {{
          return;
        }}
        if (child.closest && child.closest('[data-baseweb="tag"]')) {{
          return;
        }}
        child.style.setProperty("color", headerFg, "important");
        child.style.setProperty("-webkit-text-fill-color", headerFg, "important");
      }});
    }});

    // 라이트 모드 커스텀 표 헤더/셀 — Streamlit 다크 잔여 스타일 덮어쓰기
    if (theme === "light") {{
      document.querySelectorAll("table.light-df th").forEach((el) => {{
        paint(el, "#f3f4f6", "#111827");
      }});
      document.querySelectorAll("table.light-df td").forEach((el) => {{
        el.style.setProperty("color", "#111827", "important");
        el.style.setProperty("-webkit-text-fill-color", "#111827", "important");
      }});
      document.querySelectorAll(
        '[data-testid="stMultiSelect"] [data-baseweb="tag"], '
        + '[data-baseweb="tag"]'
      ).forEach((el) => {{
        paint(el, "#dbeafe", "#1e3a8a");
        el.style.setProperty("border", "1px solid #93c5fd", "important");
        el.querySelectorAll("span, div, p").forEach((child) => {{
          child.style.setProperty("color", "#1e3a8a", "important");
          child.style.setProperty("-webkit-text-fill-color", "#1e3a8a", "important");
          child.style.setProperty("background", "transparent", "important");
        }});
        el.querySelectorAll("svg, path").forEach((child) => {{
          child.style.setProperty("fill", "#1e40af", "important");
          child.style.setProperty("stroke", "#1e40af", "important");
        }});
      }});
      document.querySelectorAll(
        ".chat-inline-code, .chat-assistant code, .chat-user code"
      ).forEach((el) => {{
        paint(el, "#eff6ff", "#1e3a8a");
        el.style.setProperty("border", "1px solid #bfdbfe", "important");
      }});
      document.querySelectorAll(
        '[data-testid="stPills"] button, [data-testid="stBaseButton-pills"]'
      ).forEach((el) => {{
        const active =
          el.getAttribute("aria-pressed") === "true"
          || el.getAttribute("kind") === "primary"
          || (el.getAttribute("data-testid") || "").includes("Active");
        if (active) {{
          paint(el, "#dbeafe", "#1e3a8a");
          el.style.setProperty("border", "1px solid #93c5fd", "important");
        }} else {{
          paint(el, "#ffffff", "#111827");
          el.style.setProperty("border", "1px solid #d0d7e2", "important");
        }}
      }});
    }}
  }};

  const apply = () => {{
    if (observer) observer.disconnect();
    document.documentElement.setAttribute("data-app-theme", theme);
    document.documentElement.style.colorScheme = theme;
    if (document.body) {{
      document.body.setAttribute("data-app-theme", theme);
      document.body.style.colorScheme = theme;
    }}
    // Streamlit 네이티브 테마 키만 맞춰 둔다 (새로고침 없이 CSS/JS로 위젯 보정)
    try {{
      const desired = theme === "light" ? "Light" : "Dark";
      const baseKey = `stActiveTheme-${{window.location.pathname}}`;
      const activeKey = `${{baseKey}}-v2`;
      window.localStorage.setItem(activeKey, JSON.stringify(desired));
    }} catch (_) {{}}
    paintWidgets();
    if (observer) {{
      observer.observe(document.documentElement, {{ childList: true, subtree: true }});
    }}
  }};

  let timer = null;
  observer = new MutationObserver(() => {{
    if (timer) return;
    timer = setTimeout(() => {{
      timer = null;
      apply();
    }}, 50);
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


def inject_styles() -> None:
    theme = st.session_state.get("theme", "dark")
    if theme not in THEMES:
        theme = "dark"
    css = f"<style>\n{_build_css(theme)}\n</style>"
    script = _theme_script(theme)
    # st.html + unsafe_allow_javascript: DOMPurify 우회 · 테마 보정 스크립트 실행
    if hasattr(st, "html"):
        st.html(css + script, unsafe_allow_javascript=True)
    else:
        st.markdown(css + script, unsafe_allow_html=True)
