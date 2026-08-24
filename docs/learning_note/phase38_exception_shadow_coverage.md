# Phase 38 Narrow Implementation — Legacy Exception Shadow Coverage

## Summary

Post-snapshot uncaught legacy exceptions now schedule Shadow observationally once
(`observe_exception_with_shadow`) then re-raise the original exception.

- Catch: `Exception` (not `BaseException`) — KeyboardInterrupt/SystemExit skip Shadow
- Single-flight: request-local `shadow_scheduled` flag
- Schema: additive legacy fields only; no schema_version bump
- Default: `MULTI_SHADOW_ENABLED=false`

## Exception vs BaseException

Chose `Exception` to match repository convention and avoid scheduling Shadow on
interpreter shutdown / Ctrl+C.

## Recommendation

A — Coverage hardening validated; resume limited Phase 38 observation with Shadow still OFF by default.
