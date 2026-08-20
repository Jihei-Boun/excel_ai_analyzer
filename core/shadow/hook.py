"""Hook helpers for route_multi — schedule Shadow without affecting outcome."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from core.routing.route_types import SingleRouteOutcome
from core.shadow.config import ShadowConfig, load_shadow_config
from core.shadow.fingerprint import dataframe_fingerprint
from core.shadow.snapshot import ShadowRequestSnapshot, build_shadow_snapshot
from core.shadow.worker import schedule_shadow


def maybe_build_shadow_snapshot(
    *,
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
    base_url: str,
    model: str,
    profile_name: str | None = None,
    config: ShadowConfig | None = None,
) -> ShadowRequestSnapshot | None:
    """Build immutable snapshot if Shadow enabled; never raises."""
    try:
        cfg = config or load_shadow_config()
        if not cfg.enabled:
            return None
        if len(named_frames) < 2:
            return None
        return build_shadow_snapshot(
            prompt=prompt,
            named_frames=named_frames,
            base_url=base_url,
            model=model,
            profile_name=profile_name,
            store_prompt=cfg.store_prompt,
        )
    except Exception:  # noqa: BLE001
        return None


def legacy_observation_from_outcome(
    outcome: SingleRouteOutcome,
    *,
    legacy_latency_s: float | None = None,
) -> dict[str, Any]:
    df = outcome.dataframe
    success = df is not None or bool(
        (outcome.meta or {}).get("chart_path")
    ) or (
        outcome.operation_name
        in {"structured_integrate", "structured_integrate_failed"}
        and df is not None
    )
    # Broader: any non-empty reply with integrate success
    if outcome.operation_name == "structured_integrate":
        success = True
    if outcome.operation_name == "structured_integrate_failed":
        success = False
    return {
        "legacy_status": "success" if success else "failure_or_text_only",
        "legacy_success": bool(success and outcome.operation_name != "structured_integrate_failed"),
        "legacy_latency_s": legacy_latency_s,
        "legacy_result_type": type(df).__name__ if df is not None else "none",
        "legacy_operation_name": outcome.operation_name,
        "result_fingerprint": dataframe_fingerprint(df),
        "reply_len": len(outcome.reply or ""),
    }


def finish_with_shadow(
    outcome: SingleRouteOutcome,
    *,
    snapshot: ShadowRequestSnapshot | None,
    legacy_started_at: float | None = None,
    config: ShadowConfig | None = None,
) -> SingleRouteOutcome:
    """Attach Shadow schedule to a completed legacy outcome. Returns same outcome.

    Never modifies outcome fields. Never waits for Shadow completion.
    """
    if snapshot is None:
        return outcome
    try:
        latency = None
        if legacy_started_at is not None:
            latency = round(time.time() - legacy_started_at, 3)
        leg = legacy_observation_from_outcome(outcome, legacy_latency_s=latency)
        schedule_shadow(snapshot, legacy_observation=leg, config=config)
    except Exception:  # noqa: BLE001
        # Absolute isolation: shadow scheduling failures never affect legacy
        pass
    return outcome
