# Phase 39L — Live Verifier Payload Capture & Inference Instability Characterization

## 1. Executive Summary

Phase 39L adds **non-semantic observability** around the Semantic Verifier:

- exact pre-invocation payload capture (`MULTI_VERIFIER_CAPTURE_DIR`)
- deterministic raw/canonical payload fingerprints + prompt version hash
- raw model response + parsed verdict/reason trace
- exact-payload replay harness
- reconstructed live-like (`result=None`) instability stress (n=10)

**Results:**

| Question | Answer |
|---|---|
| Exact payload capture possible? | **Yes** (pre-`_chat_raw`) |
| Replay fidelity demonstrated? | **Yes — EXACT_REPLAY** for freshly captured calls |
| Offline instability on rename+join? | **Not observed** (P39J-05/06/07 PASS 10/10) |
| Historical P39J-06/07 exact replay? | **No — reconstruct only** |
| Hypothesis B signal? | **Yes** — `result=None` vs fingerprint hashes differ |
| Gate | **A** |
| Migration | **NOT APPROVED** |
| Shadow | remains **OFF** |

This Phase does **not** fix the verifier. It closes the observability gap so a future live instability Phase can capture the exact failing input.

---

## 2. Baseline Freeze

See `benchmark_results/multi/phase39l/baseline_freeze.json`.

- Entering official gate: **B** (post-39K)
- Materialization: V2.2 `final_schema_expr_partition`
- Roles: R-ROLE-B (non-authoritative)
- Models: verifier/planner `qwen2.5:7b`; strong `qwen3:32b`
- LLM options: `temperature=0`, `format=json`, timeout 300s
- Legacy primary / Shadow default OFF
- Semantic regression (39L capture + 39H + 34 + 33 + 35 + 38): **PASS**

---

## 3. Verifier Call Path

Documented in `verifier_call_path.json`:

1. `build_verifier_payload` — materialization
2. `run_semantic_verification` — user message assembly
3. **CAPTURE** (`build_invocation_record` / `persist_record`) — immediately before model call
4. `_chat_raw` / `chat_json` — invocation
5. `_extract_json_object` — raw → dict
6. `_normalize_verdict` — verdict/reason normalization
7. semantic escalation decision (+ optional capture attach)
8. Shadow worker (telemetry only)

**Live Shadow note:** `semantic_escalation` calls verifier with **`result=None`**. Phase 39K offline often passed a result fingerprint — a concrete Hypothesis B candidate.

---

## 4. Capture Schema

`capture_schema.json` / `core/integrate/verifier_invocation_capture.py`

Records:

- `exact_verifier_input` (system + verbatim user)
- `exact_payload_hash` / `canonical_payload_hash`
- `deterministic_evidence_snapshot`
- `prompt_version_hash`
- model / temperature / timeout / format_json / materialization_version
- `raw_model_response_text` + parsed fields
- escalation fields (best-effort)

Privacy: research/debug dir only; no raw workbook rows; not ordinary production logs.

---

## 5. Fingerprinting

- **Raw/exact hash:** sha256 of canonical JSON of `{system, user}` at capture point
- **Canonical hash:** sha256 of structured payload with sorted keys
- **Prompt version:** sha256(system + `---` + fixed user instruction prefix)
- Canonicalization only sorts keys / compact separators — does **not** drop or rewrite semantic fields

`payload_hash_tests.json`: same payload → same hash; live `result=None` vs fingerprint → **different hash** for P39J-05/06/07.

---

## 6. Replay Fidelity

| Case | Class |
|---|---|
| Fresh capture P39J-06 | **EXACT_REPLAY** |
| Fresh capture P39G-11 | **EXACT_REPLAY** |
| Historical P39J-06/07 live | **RECONSTRUCTED_REPLAY only** |

Harness replays captured verbatim user message through `_chat_raw` — it does **not** silently rebuild from plan when exact capture exists.

---

## 7. P39J Historical Limitation

**No — original P39J-06/07 live calls cannot be exactly replayed.**

They were not captured with Phase 39L fidelity. Only reconstructed live-like (`result=None`) or fingerprint-augmented offline payloads exist.

---

## 8. Instability Stress (n=10, reconstructed live-like)

| Case | Expect | pass/fail/uncertain | hash stable |
|---|---|---|---|
| P39J-05 | PASS | 10/0/0 | yes |
| P39J-06 | PASS | 10/0/0 | yes |
| P39J-07 | PASS | 10/0/0 | yes |
| P39G-11 | NON-PASS | 0/10/0 | yes |
| FD1 | NON-PASS | 0/10/0 | yes |
| GS1 | PASS | 10/0/0 | yes |
| C2-W1 | NON-PASS | 0/10/0 | yes |

Offline reconstructed stress did **not** reproduce the live false-fail. That does **not** prove the live problem disappeared — it strengthens the need for **live exact capture**.

---

## 9. Unsupported Structural Claims (manual)

Valid-case NON-PASS trials in this offline stress: **none**.

Therefore no new UNSUPPORTED_STRUCTURAL_CLAIM labels from 39L stress.

Historical 39J live notes (reference only, Korean):

- P39J-06: 검증기가 `total_stock` 붕괴를 주장했으나 결정적 final_schema/결과에 양측 메트릭이 유지됨 → 당시 수동 분류는 근거 없는 구조 주장.
- P39J-07: `total_use_kwh` 붕괴 주장도 동일하게 결정적 증거와 불일치.

---

## 10. Raw vs Parsed

Across stress trials: **raw vs parsed verdict mismatches = 0**.

`wrong_output_grain` is not introduced by the parser in these runs; when it appears (controls), it is present in the raw model JSON.

---

## 11. Runtime Configuration (observable)

- model: `qwen2.5:7b`
- temperature: `0.0` (`options.temperature` in `_chat_raw`)
- format_json: true
- timeout_s: 300
- materialization: `final_schema_expr_partition`
- live-like stress: `result_provided=false`
- backend: `core.llm_client._chat_raw+extract`

No production sampling knobs were changed.

---

## 12. Overhead / Isolation

- Capture default **OFF** unless `MULTI_VERIFIER_CAPTURE_DIR` set
- Telemetry I/O failures swallowed (unit-tested)
- Shadow remains fire-and-forget / not user-facing
- No Candidate fallback; Legacy semantics unchanged in regressions

---

## 13. Regression

`semantic_regression_results.json`: all targeted suites **ok** (39L capture, 39H, 34, 33, 35, 38).

---

## 14. Gate

**Gate A**

Evidence:

- faithful pre-invocation capture
- stable fingerprinting
- EXACT_REPLAY demonstrated on fresh captures
- raw/parsed traceability
- semantic behavior unchanged
- Legacy/Shadow isolation intact
- historical 39J gap explicitly documented (not overclaimed)

Gate A means: ready for a **focused live instability observation/replay Phase**.  
It does **not** mean verifier correctness is solved or migration is allowed.

---

## 15. Next Recommendation

Run a subsequent **focused live verifier instability observation/replay Phase**:

1. enable capture only on Shadow path with tiny sample rate
2. preserve exact failing payloads if FF reappears
3. EXACT_REPLAY under known runtime config
4. **do not** patch grain / add V2.3 / migrate mid-flight

**Capture first. Replay second. Characterize third. Fix later.**
