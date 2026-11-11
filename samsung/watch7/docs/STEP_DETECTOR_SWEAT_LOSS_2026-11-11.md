# Watch7 additional Sensor Cards: step events and Samsung sweat loss

**Status: locally coded, research APK builds, Driver synthetic unit tests pass.**
No Watch7 installation, on-body capture, production Docker deployment, live
Agent Core / ROS2 payload, or Bumi acceptance is claimed here.

Parent branch: `feat/watch7-v3-advanced-sensors`, head before this work
`d78aa540921a6cac3c72988621871bfe55c04e46`.
This work is isolated in `feat/watch7-step-sweat-sensors`.
Company PR #500, Bumi, QQ, and existing production containers untouched.

## New read-only Cards (17 total)

| Name | Origin | v3 kind | Core semantics |
|---|---|---|---|
| `watch_step_detector` | Android `TYPE_STEP_DETECTOR` | `step_events` | Real individual step events, monotonic event offsets and acquisition wall-clock interval, NOT cumulative steps |
| `watch_sweat_loss` | Samsung Health Sensor SDK SWEAT_LOSS plus Health Services RUNNING exercise SPM | `sweat_loss_result` | SDK estimated loss in mL only when status=0, actual running >=5 minutes, nonzero recorded SPM; otherwise status with null value |

Both use an independent `WATCH7_*_ENABLED` operator gate (default **0**),
a wearer-started, on-body v3 session and an authenticated `/v3/telemetry`
stream. MCP Card `read/list` is available only while the specific
research session grant remains live. ROS2 event envelopes redact step
offset arrays and sweat-loss amounts. Existing v1/v2 and other Cards
retain their schemas.

## Android implementation

Research-only additions under
`heart-rate-wear-test/app/src/research/java/com/example/heartrateweartest/`:

- `ResearchStepDetectorCapture.kt`: Android motion permission, genuine
  `TYPE_STEP_DETECTOR` events, bounded 32-event/20-second windows,
  dedicated HandlerThread, monotonic timestamp checks, local JSONL
  before optionally sending v3, stop/onDestroy cleanup.
- `SamsungSweatTracker.java`: capability-check Samsung `SWEAT_LOSS`,
  wearer profile, explicit `RUNNING` `ExerciseState.START/STOP`,
  real `STEP_PER_MINUTE` with Unix-epoch timestamps, final
  `SweatLossSet.STATUS`/result. No simulated quantities.
- `ResearchSweatRun.kt`: permissions, rejects any pre-existing
  Health Services exercise, checks RUNNING and SPM capability, tracks a
  user-started exercise and handles cancellation/restart callbacks
  using generation tokens. Runs in the foreground and does not
  override other apps' exercise sessions intentionally.
- `ResearchSweatKeepAliveService.kt`: Research-only user-started
  `health` foreground notification and lifecycle.
- `SamsungMeasurementActivity.kt`: wearer-start/stop controls, manual
  profile + consent, on-body abort, step detector runtime permission.
- `ResearchV3Gateway.kt`: adds two explicit stream kind allowlist
  mappings; HTTPS or ADB-loopback credential transport.

Research APK offline compilation:
```console
python -X utf8 -B build_s4.py --variant research --offline
S4_BUILD_SUCCESS research
S4_APK_SHA256 a1ca1c2925575a4073d4acac0537b0167915f71f56c0302e5d8533f545949e23
```
APK location on local Windows system:
`E:\\常用开发文件夹\\实习\\phanthymotus_wearable_card\\heart-rate-wear-test\\app\\build\\outputs\\apk\\research\\debug\\app-research-debug.apk`

## Driver protocol and validation

Files: `stream_protocol.py`, `main.py`, `advanced_cards.py`,
`config.yaml`, `driver.yaml`, `deploy/service.yml`.

Step event payload:
```json
{"protocol_version":3,"kind":"step_events","device_id":"watch7",
 "source":"watch7_real","session_id":"example","seq":0,
 "captured_at_ms":1800000000000,"wear_state":"on_body",
 "window_started_at_ms":1799999999000,
 "window_ended_at_ms":1800000000000,"samples":[[0],[350],[720]]}
```
This is an **illustrative synthetic fixture**, not a measured step trace.

Sweat result:
```json
{"protocol_version":3,"kind":"sweat_loss_result","device_id":"watch7",
 "source":"watch7_real","session_id":"example","seq":0,
 "captured_at_ms":1800000000000,"wear_state":"on_body",
 "exercise_type":"running","provider":"samsung_sdk",
 "sdk_status":0,"exercise_duration_ms":480000,"spm_samples":360,
 "sweat_loss_ml":215.0}
```
This also is a **synthetic fixture**. The real SDK estimate is not
direct measurement of hydration status or body water deficit.

Driver checks step sample shape and order, time window freshness,
explicit status, real exercise duration and SPM evidence. A nonzero
status is preserved with `sweat_loss_ml=null`; `status_reasons` is
decoded from documented Samsung status bits 0..6. No fake `0` status
or placeholder result is ever emitted. When result sharing was
explicitly opted in, an approximately 12-second post-run read window
permits MCP consumption before the Driver's live-session contract
revokes access. Full history and durable replay are outside scope.

## Locally run, not physical measurement

```console
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
80 passed
python -X utf8 scripts/check_service_yml.py samsung/watch7
1 checked, 0 failed, 0 known gaps
```
Tests include parameterized operator gate, watch consent, HTTP token
auth, per-session replay, sample shapes, SDK status reason decoding,
MCP tool schema and redacted ROS2 snapshots. Synthetic Python tests
do not validate that the Samsung Health Sensor Service produces a
status-zero result for a real run.

## Known next-stage physical validation and open risks

1. Confirm Watch7 exposes TYPE_STEP_DETECTOR after Wear OS runtime
   ACTIVITY_RECOGNITION grant; count detected steps against manual tally,
   check sample timestamps and loss.
2. On Watch7, ensure Samsung service lists SWEAT_LOSS, and that
   a real >=5 minute running session with sufficient pace, distance,
   heart-rate/PPG and step quality can produce SDK status 0.
   Samsung may require >=2 km or >90 kcal and mean SPM >110, as
   documented in the status bits; short walks should be rejected.
3. Confirm Android exercise foreground lifecycle, stopping an owned
   exercise only and not interrupting another app's exercise.
4. Verify the private HTTPS or ADB-reverse ingress, v3 session
   state grants, handoff window, MCP read and ROS2 redaction on
   running Linux Agent Core.
5. Confirm on-device AAR's runtime behavior; compile-time type checking
   is not evidence of sensor availability or usable SDK output.
6. Keep Samsung licensed AAR, personal measurements, credentials and
   APK artifacts out of GitHub and company PR.

Samsung documentation:
- https://developer.samsung.com/health/sensor/guide/tracking-sweat-loss.html
- https://developer.samsung.com/health/sensor/api-reference/com/samsung/android/service/health/tracking/data/ValueKey.SweatLossSet.html
- https://developer.android.com/health-and-fitness/health-services/active-data
