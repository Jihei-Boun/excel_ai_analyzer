#!/usr/bin/env python3
"""Offline helper: regenerate Phase 34 canonical historical fixture from live JSON.

Does NOT run in CI. Review output before committing.
Requires local benchmark_results under LIVE_HARVEST_SOURCES.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.benchmark_multi.phase34_generalization import (
    CANONICAL_HISTORICAL_FIXTURE,
    LIVE_HARVEST_SOURCES,
    harvest_live_historical_plans,
)


def main() -> None:
    valid, type_c = harvest_live_historical_plans()
    if len(valid) < 20 or len(type_c) < 8:
        raise SystemExit(
            f"Insufficient harvest: valid={len(valid)} type_c={len(type_c)}. "
            f"Need live artifacts under {LIVE_HARVEST_SOURCES}"
        )
    KEEP = [
        "dataset_id",
        "source_kind",
        "case_id_analysis_only",
        "domain_analysis_only",
        "scenario_analysis_only",
        "user_prompt",
        "plan",
        "ops",
        "grain",
        "label",
    ]
    KEEP_C = KEEP + ["type_c_family"]

    def slim(item: dict, keys: list[str]) -> dict:
        return {k: item[k] for k in keys if k in item}

    prov = sorted(
        {
            v.get("source_file_analysis_only")
            for v in valid + type_c
            if v.get("source_file_analysis_only")
        }
    )
    fixture = {
        "fixture_id": "phase34_historical_plans_v1",
        "description": (
            "Canonical Phase 34 historical plans for hermetic unit/CI tests. "
            "Verifier receives only user_prompt + plan."
        ),
        "provenance": {
            "source_roots": [str(s) for s in LIVE_HARVEST_SOURCES],
            "source_files": prov,
            "extracted_historical_valid": len(valid),
            "extracted_historical_type_c": len(type_c),
        },
        "historical_valid": [slim(v, KEEP) for v in valid],
        "historical_type_c": [slim(c, KEEP_C) for c in type_c],
    }
    out = CANONICAL_HISTORICAL_FIXTURE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes) valid={len(valid)} type_c={len(type_c)}")


if __name__ == "__main__":
    main()
