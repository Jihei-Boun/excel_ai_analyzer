"""Regression guards: headless layer must not change production routing."""

from __future__ import annotations

from pathlib import Path

from core.shadow.config import load_shadow_config


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "core" / "application"


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_application_layer_does_not_import_ui_or_candidate_pipeline() -> None:
    for path in APP_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import streamlit" not in text
        assert "from streamlit" not in text
        assert "from ui.chat" not in text
        assert "from ui.chat_panel" not in text
        assert "from core.integrate.integration_pipeline" not in text
        assert "run_integration_pipeline(" not in text
        assert "process_user_prompt(" not in text
        assert "st.session_state" not in text


def test_headless_calls_production_routers_only() -> None:
    text = _read("core", "application", "headless.py")
    assert "route_single_prompt" in text
    assert "route_multi_prompt" in text
    assert "load_tabular" in text
    assert "use_profile" in text
    assert "from core.io.export_utils" in _read("core", "application", "artifacts.py")
    assert "run_integration_pipeline(" not in text
    assert "try_integrate_pipeline" not in text


def test_production_routers_unchanged_by_application_layer() -> None:
    single = _read("core", "routing", "route_single.py")
    multi = _read("core", "routing", "route_multi.py")
    assert "core.application" not in single
    assert "core.application" not in multi
    assert "run_integration_pipeline" not in multi
    assert "try_integrate_pipeline" in multi
    chat = _read("ui", "chat.py")
    assert "process_user_prompt" in chat
    assert "route_single_prompt" in chat
    assert "route_multi_prompt" in chat
    assert "from core.application" not in chat
    app = _read("app.py")
    assert "render_chat_panel" in app
    assert "core.application" not in app


def test_shadow_remains_off() -> None:
    assert load_shadow_config().enabled is False


def test_candidate_pipeline_not_wired_into_route_multi() -> None:
    multi = _read("core", "routing", "route_multi.py")
    assert "from core.integrate.integrate_pipeline import" in multi
    assert "from core.integrate.integration_pipeline" not in multi
