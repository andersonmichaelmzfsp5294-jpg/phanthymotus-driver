# Watch7 v3 six research Sensor Cards — source implementation checkpoint

Date: 2026-11-09
Source workspace: `E:\\常用开发文件夹\\实习\\phanthymotus_wearable_card`

## Current engineering status

Six Card definitions and their authenticated v3 Driver transport are
implemented in the local `phanthymotus-driver/samsung/watch7` tree:

| Card | Android foreground capture | Wire kind | Data access |
| --- | --- | --- | --- |
| `watch_imu_stream` | Android SensorManager accelerometer+gyroscope, ~100 Hz requested | `imu_window` | bounded 7-field time-offset windows |
| `watch_ppg_raw` | wearer-started Samsung three-colour PPG tracker | `ppg_chunk` | status-valid green/red/IR sampled windows |
| `watch_ecg` | wearer-started Samsung ECG tracker | `ecg_chunk` | lead-check-gated millivolt windows |
| `watch_bia` | wearer-started Samsung BIA with user profile and contraindication confirmation | `bia_result` | completed, bounded body-composition fields |
| `watch_location` | one Android GPS current location (FINE_LOCATION) | `location_fix` | exact fix via read, redacted on ROS2 |
| `watch_audio` | user-clicked 3-second AudioRecord capture (RECORD_AUDIO) | `audio_chunk` | 8-kHz mono PCM16 chunks via read |

These are source-level implementations and **not six hardware-validated
capabilities**. The previously working nine Cards stay present.

## Android architecture

* Research flavor only: `ResearchV3Gateway.kt` (per-capture session,
  12-item upload queue cap, HTTPS or ADB-localhost HTTP only,
  bearer token, separate per-card permission and session expiry).
* `ResearchImuCapture.kt`: Android event timestamps, gyroscope temporal
  pairing, true measured sampling interval and local JSONL persistence
  before optional upload. Ends after 30 seconds or user stops.
* `SamsungV3Export.java`: creates PPG/ECG windows (48 rows maximum);
  filters invalid per-colour PPG and lead-off ECG, and emits BIA only
  after the SDK reports completion. Shares no Samsung proprietary AAR.
* `ResearchLocationCapture.kt`: one fresh GPS fix; closes the v3 session
  after success/failure.
* `ResearchAudioCapture.kt`: microphone permission, user-clicked 3-second
  microphone capture, private local file and bounded PCM chunks, then closes
  the v3 session.
* `SamsungMeasurementActivity.kt`: six individual entry points, on-Watch
  unchecked upload consent and clear stop/teardown. Also preserves the
  existing IBI/SpO2/skin-temperature and recording-index paths.
* `SamsungTrackerBridge.java`: raw SDK windows are emitted only **after**
  successful local persistence; full BIA profile and contraindication
  confirmation remain on-Watch.
* Both `ResearchV3Gateway` and `ResearchTelemetryClient` stop uploading
  upon off-body; no automatic sensor start by Agent/MCP.

Android source-of-truth:
`heart-rate-wear-test/app/src/research/java/com/example/heartrateweartest/`.
Source-only versioning snapshots in the personal Fork are under
`samsung/watch7/docs/research-source-v3/` (not Docker COPY inputs).

## Driver architecture

* New independently validated `POST /v3/telemetry` endpoint does **not**
  loosen any v1/v2 schema, body limits or haptic command controls.
* `stream_protocol.py` validates device/source/session/time, per-kind
  vector lengths, monotonic offsets, bounded values/PCM and optional
  acquisition window wall-clock times.
* `StreamStore` requires BOTH an explicit operator environment switch
  AND a fresh same-session, on-body `stream_state` consent heartbeat.
  15-second heartbeat TTL; per-kind 12 chunks; 64 chunks overall;
  30-second per-chunk TTL; revoked or retired sessions fail closed.
* `advanced_cards.py` publishes only **redacted** ROS2 event envelopes;
  no PPG/ECG/IMU samples, PCM, precise GPS coordinates, or BIA stats
  are broadcast on ROS2. The MCP `read` result is available only
  while the explicitly authorized session is live.
* Docker image includes three new v3 Python modules and
  `config.yaml` / `driver.yaml` discover all six Cards.
* All six operator feature flags default to `0` in
  `deploy/service.yml`. Existing S5 Driver, QQ Core and Bumi were
  not modified or restarted.

## Compilation, with explicit claim boundaries

The latest Research APK was compiled **locally**, without installation:

```bash
python -X utf8 -B build_s4.py --variant research --offline
# S4_BUILD_SUCCESS research
# app/build/outputs/apk/research/debug/app-research-debug.apk
# SHA-256 bcc227b5ed59ef3095089ea6e7beaa2cbfa836770c9ef366227f5d374dea47b9
```

No physical Watch7 recording, APK installation, live Linux Agent Core
deployment, live Dashboard capture, ROS2 payload test or Bumi execution
was performed in this feature round at the user's request.

The earlier **74/74 Driver pytest pass** and deployment config check
were recorded *before* the final v3 replay protection and ROS2
redaction edits. The current Driver post-edit suite remains **unrun**.

## Important data/completeness limits

- On-Watch JSONL is a bounded local source of truth; network chunks are
  best-effort, without durable retry, index replay or lossless
  transport guarantees. No claim of complete long recordings.
- `watch_ecg` / `watch_bia` provide session-scoped results/events.
  An explicit stop revokes MCP read access and clears volatile Driver
  chunks. Durable post-measurement review requires a separately approved
  retention/export workflow.
- Real sampling rate and Samsung SDK availability depend on device,
  firmware, permission and sensor state; requested 100-Hz IMU does not
  establish 100-Hz measured throughput.
- User raw/precise data require HTTPS remote ingress or loopback via
  ADB reverse. Production watch location/audio flags are off until
  operator deployment and account permission checks.
- The Research debug APK and proprietary Samsung SDK AAR must not be
  pushed into GitHub; only reviewed text source is versioned.

## Deferred acceptance stages (do not run until requested)

1. Source: Gradle Research/Standard build, Driver pytest, YAML checks,
   Python syntax compilation.
2. Device: wearer grants permissions and starts each sensor once;
   validate sample timestamps/status/actual Hz, BIA contraindication
   gating and short audio/local file.
3. Transport: temporary ADB reverse to isolated Linux ingress or TLS,
   one Watch-originated v3 stream_state + valid sample + stop per Card,
   unauthorized/replay/offbody/oversize rejection.
4. Dashboard: genuine six Card `tools/list` and `tools/call` via
   running Linux Agent Core; redacted ROS2 event topic and read TTL.
5. Company PR review/CI and eventual Bumi ARM64 operator approval,
   independent of the original company PR #500.
