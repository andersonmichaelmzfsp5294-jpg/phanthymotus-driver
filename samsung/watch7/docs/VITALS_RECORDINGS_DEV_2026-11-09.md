# Watch7 P4 Driver — advanced vitals and PPG manifest metadata

Date: 2026-11-09
Personal Fork: `feat/watch7-vitals-and-recordings`
Base commit: `904ab87d467d4c7bc2d3c493c7e7f41d1aa784f0`

## What was actually implemented

1. `watch_vitals`: read-only Sensor Card `info/capabilities/read/start/stop`;
   access to bounded scalar `ibi_ms`, `spo2_pct`, `skin_temperature_c`
   via the existing protocol-v2 summary cache and MCP/ROS2 Card wrapper.
   On-body, live session, quality, age and Samsung SDK provider are mandatory.
2. `watch_recordings`: read-only Sensor Card `info/list/start/stop`.
   Optionally accepts and briefly indexes authenticated, validated
   `signal_manifest` messages for PPG green/red/IR **metadata only**.
   In-memory maximum 32, retention 10 minutes, no raw waveform/file access.
   All ECG manifest messages are currently denied.
3. A new optional v2 device heartbeat field
   `recording_manifest_enabled: boolean` distinguishes explicit
   device-side research consent from the earlier haptic/sensor-control
   permissions. Both a true field and operator environment flag
   `WATCH7_RECORDING_INDEX_ENABLED=1` are necessary.
   The deployment default is **0**, and the companion does not yet
   emit this opt-in signal. Empty/unauthorized queries must fail closed.
4. A separate v2 `on_manifest` callback feeds the metadata-only Card;
   `on_metrics` now routes to `watch_vitals` as well.
   Existing heart-rate, motion, environment and haptic paths remain.
5. Research-only backend integration is **NOT** included here.
   The Standard APK contains no Samsung SDK AAR and cannot generate these
   three research scalar metrics. This patch adds a Driver endpoint/schema
   for future separately consented Research app integration, not a
   completed on-watch data pipeline.

## Regression risks, test matrix and interpretation

The new `test_vitals_recordings.py` uses synthetic v2 frames to check:
- Company type/schema and MCP action parameters;
- SDK-only health provider and on-body checks;
- no heart-rate or motion fields in the vitals ROS2 topic;
- unavailable, invalid, stale and cross-session scalar refusals;
- operator default-off and missing companion consent for PPG index;
- signal allowlist; raw waveform payload / ECG / empty recording denials;
- finite size, opt-out and session reset of metadata;
- local authenticated HTTP ingress → Card MCP → JSON publisher integration.

**The source review and test cases do NOT establish that those tests
were executed**, and cannot be reported as passed until the local
workspace connection and Python test runner are available.
Actual SDK, Wear OS, native ROS2, Bumi and Dashboard tests remain pending.

## Required commands (driver repo root)

```sh
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
python -X utf8 scripts/check_service_yml.py samsung/watch7
```

## Privacy / company acceptance boundary

- Existing PPG real-device research probe collected sample values, but
  that does **not** prove companion-summary or manifest integration.
- Never include Samsung research AAR, proprietary source, raw ECG or
  PPG files, credentials, identifying user information or signing keys
  in the company Driver repository.
- No automated SpO2/ECG/BIA remote measurement is authorized.
- Do not represent these cards as medical-grade or eligible for medical
  interpretation, and do not enable PPG indexing by default.
- The two previously identified blockers — independent Windows LAN
  ingress and haptic consent persistence — are **deferred**, not fixed
  or declared resolved. No Bumi deployment is performed in this branch.
