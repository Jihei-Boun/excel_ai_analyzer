"""Phase 37: Multi-file Shadow Mode — observe candidate pipeline without user effect.

Invariant: legacy production path is the sole source of the user-facing response.
Shadow is disposable evidence collection only.
"""

from __future__ import annotations

from core.shadow.config import ShadowConfig, load_shadow_config
from core.shadow.hook import finish_with_shadow, maybe_build_shadow_snapshot

__all__ = [
    "ShadowConfig",
    "load_shadow_config",
    "finish_with_shadow",
    "maybe_build_shadow_snapshot",
]
