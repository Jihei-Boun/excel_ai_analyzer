"""Shadow Mode configuration — defaults OFF; kill switch via enabled=false."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.constants import PROJECT_ROOT

PIPELINE_VERSION = "phase35_semantic_escalation_v1"
SHADOW_SCHEMA_VERSION = 1


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ShadowConfig:
    """Operational controls only — not semantic routing."""

    enabled: bool = False
    sample_rate: float = 1.0
    max_concurrency: int = 1
    queue_size: int = 8
    timeout_sec: float = 600.0
    telemetry_dir: Path = PROJECT_ROOT / "data" / "shadow_telemetry"
    store_prompt: bool = False  # hash only by default; set true only if policy allows
    pipeline_version: str = PIPELINE_VERSION
    schema_version: int = SHADOW_SCHEMA_VERSION
    # Test-only: run shadow inline in the calling thread (still after legacy outcome built)
    inline_for_tests: bool = False

    def kill_switch_off(self) -> "ShadowConfig":
        return ShadowConfig(
            enabled=False,
            sample_rate=self.sample_rate,
            max_concurrency=self.max_concurrency,
            queue_size=self.queue_size,
            timeout_sec=self.timeout_sec,
            telemetry_dir=self.telemetry_dir,
            store_prompt=self.store_prompt,
            pipeline_version=self.pipeline_version,
            schema_version=self.schema_version,
            inline_for_tests=self.inline_for_tests,
        )


def load_shadow_config() -> ShadowConfig:
    """Read env. Default: MULTI_SHADOW_ENABLED=false (Shadow OFF)."""
    tel = os.environ.get("MULTI_SHADOW_TELEMETRY_DIR", "").strip()
    telemetry_dir = (
        Path(tel)
        if tel
        else PROJECT_ROOT / "data" / "shadow_telemetry"
    )
    return ShadowConfig(
        enabled=_env_bool("MULTI_SHADOW_ENABLED", False),
        sample_rate=max(0.0, min(1.0, _env_float("MULTI_SHADOW_SAMPLE_RATE", 1.0))),
        max_concurrency=max(1, _env_int("MULTI_SHADOW_MAX_CONCURRENCY", 1)),
        queue_size=max(1, _env_int("MULTI_SHADOW_QUEUE_SIZE", 8)),
        timeout_sec=max(1.0, _env_float("MULTI_SHADOW_TIMEOUT_SEC", 600.0)),
        telemetry_dir=telemetry_dir,
        store_prompt=_env_bool("MULTI_SHADOW_STORE_PROMPT", False),
        inline_for_tests=_env_bool("MULTI_SHADOW_INLINE_FOR_TESTS", False),
    )
