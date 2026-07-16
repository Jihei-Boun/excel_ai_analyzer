"""중앙 워크스페이스 — 업로드 · 미리보기."""

from __future__ import annotations

from ui.preview import render_preview_section
from ui.upload import render_upload_section


def render_workspace() -> None:
    render_upload_section()
    render_preview_section()
