"""Immutable request snapshot for Shadow workers (no session_state refs)."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:32]


@dataclass
class ShadowRequestSnapshot:
    """Copyable snapshot — DataFrames are deep-copied at construction."""

    request_id: str
    shadow_request_id: str
    user_prompt: str
    prompt_hash: str
    sources: dict[str, pd.DataFrame]
    file_count: int
    source_names: list[str]
    base_url: str
    model: str
    profile_name: str | None
    created_at_unix: float
    store_prompt: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def prompt_for_pipeline(self) -> str:
        return self.user_prompt

    def prompt_for_telemetry(self) -> str | None:
        if self.store_prompt:
            return self.user_prompt
        return None


def build_shadow_snapshot(
    *,
    prompt: str,
    named_frames: list[tuple[str, pd.DataFrame]],
    base_url: str,
    model: str,
    profile_name: str | None = None,
    request_id: str | None = None,
    store_prompt: bool = False,
) -> ShadowRequestSnapshot:
    rid = request_id or str(uuid.uuid4())
    sources: dict[str, pd.DataFrame] = {}
    for name, df in named_frames:
        # Isolation: deep copy so shadow mutation cannot affect legacy
        sources[str(name)] = df.copy(deep=True)
    return ShadowRequestSnapshot(
        request_id=rid,
        shadow_request_id=f"shadow-{rid}",
        user_prompt=prompt,
        prompt_hash=_prompt_hash(prompt),
        sources=sources,
        file_count=len(sources),
        source_names=list(sources.keys()),
        base_url=base_url,
        model=model,
        profile_name=profile_name,
        created_at_unix=time.time(),
        store_prompt=store_prompt,
    )
