"""중앙 워크스페이스 — 업로드 · 미리보기 · 병합."""

from __future__ import annotations

from ui.merge_panel import render_merge_section
from ui.preview import render_preview_section
from ui.upload import render_upload_section


def render_workspace() -> None:
    render_upload_section()
    render_preview_section()
    render_merge_section()
