#!/usr/bin/env python3
"""Phase 36 stress runner: FP×1 + Type-C×3 + gate (frozen arch)."""

from __future__ import annotations

import json

from tests.benchmark_multi.phase36_pre_shadow_gate import (
    OUT,
    run_false_escalation_stress,
    run_offline_analysis,
    run_type_c_repeatability,
    write_gate,
)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("offline...", flush=True)
    offline = run_offline_analysis()

    print("fp stress x1...", flush=True)
    false_esc = run_false_escalation_stress(repeats=1, limit=None)
    slim = {k: v for k, v in false_esc.items() if k != "rows"}
    (OUT / "false_escalation_stress.json").write_text(
        json.dumps(slim, indent=2), encoding="utf-8"
    )
    (OUT / "harmful_false_escalation.json").write_text(
        json.dumps(
            {
                "overall_harmful": slim["overall"]["harmful_false_escalation"],
                "historical_harmful": slim["historical_real"][
                    "harmful_false_escalation"
                ],
                "synthetic_harmful": slim["synthetic_valid"][
                    "harmful_false_escalation"
                ],
                "rates": {
                    "overall": slim["overall"]["harmful_false_escalation_rate_pct"],
                    "historical": slim["historical_real"][
                        "harmful_false_escalation_rate_pct"
                    ],
                    "synthetic": slim["synthetic_valid"][
                        "harmful_false_escalation_rate_pct"
                    ],
                },
                "note": "single full pass over historical+synthetic VALID set",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(slim, indent=2), flush=True)

    print("type-c x3...", flush=True)
    type_c_rep = run_type_c_repeatability(repeats=3)
    slim_tc = {k: v for k, v in type_c_rep.items() if k != "rows"}
    (OUT / "type_c_repeatability.json").write_text(
        json.dumps(slim_tc, indent=2), encoding="utf-8"
    )
    print(json.dumps(slim_tc, indent=2), flush=True)

    gate = write_gate(offline, false_esc=false_esc, type_c_rep=type_c_rep)
    print("GATE", json.dumps(gate, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
