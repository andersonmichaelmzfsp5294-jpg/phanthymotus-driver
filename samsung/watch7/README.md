# Samsung Galaxy Watch7 — Sensor and opt-in Haptic Driver

## PR and version boundaries

Company PR **#500** (`feat/watch7-heart-rate-driver`, commit `bc12b50`)
is the reviewed **two-Card baseline**: `heart_rate` and `device_state`.
The **first** personal-Fork follow-up, `feat/watch7-motion-cards`,
adds the read-only `watch_motion` Sensor. The **second**, dependent
follow-up `feat/watch7-environment-cards`, adds `watch_environment`
and a shared read-only metric Card adapter. The third follow-up,
`feat/watch7-haptic-card`, adds a strictly gated `watch_haptic` Actuator.
This further **dependent** personal-Fork branch,
`feat/watch7-vitals-and-recordings`, adds `watch_vitals` (SDK scalar
summaries) and `watch_recordings` (PPG manifests only).
The next dependent branch, `feat/watch7-activity-card`, adds the
experimental read-only `watch_activity` Processor and fixes private
Docker test namespace/MCP identity isolation. The next dependent branch, `feat/watch7-orientation-card`, adds
`watch_orientation` for public Android orientation/gravity vectors.
None of these changes are part of company PR #500.

The seven Sensor Cards and one activity Processor use `std_msgs/String`
JSON on ROS2 domain 42 with
`data/json`:

- `/{ros_namespace}/watch7/heart_rate`
- `/{ros_namespace}/watch7/device_state`
- `/{ros_namespace}/watch7/motion`
- `/{ros_namespace}/watch7/environment`
- `/{ros_namespace}/watch7/vitals` (SDK summary values only)
- `/{ros_namespace}/watch7/recordings` (PPG metadata only, opt-in)
- `/{ros_namespace}/watch7/activity` (experimental motion state)
- `/{ros_namespace}/watch7/orientation` (public Android orientation/gravity vectors)

The ROS namespace defaults to the sanitized hostname. All Sensor `start`
and `stop` actions **only gate ROS2 publication in the Driver**; they do not
start, stop or reconfigure Watch Health Services or Android sensor trackers.
The wearer must start the Companion's foreground monitor explicitly.

## Required companion and ingress

The existing research companion APK sends `POST /v1/telemetry`, `POST
/v2/telemetry` and polls commands. The Driver uses the original v1/v2
protocol validators verbatim and exposes an authenticated watch-only HTTP
endpoint on port **15751**. The existing app can continue to upload data
without Windows Relay or ADB. The two Sensor follow-ups keep all
commands disabled. This Haptic follow-up can return **only** bounded v1
vibration commands when both the operator and wearer explicitly opt in.
The `/v2/commands` queue stays empty, and task proposals, ECG/BIA,
remote collection control and robot actions remain disabled.

MCP binds to **127.0.0.1:15750**. The Watch ingress binds to 0.0.0.0:15751
by default and requires `X-Wearable-Token`. Set `WATCH7_DEVICE_TOKEN`
to a unique random secret of at least 16 characters in the deployment
environment; the service deliberately **refuses to start** without it.
The secret must be configured manually in the Watch app.

**Security boundary:** the current companion transmits HTTP on Wi-Fi,
not authenticated TLS. Only operate on a controlled, isolated/trusted WLAN
or behind a TLS-protected reverse proxy; do not expose port 15751 to the
internet or an untrusted shared LAN. Before wider release, implement
TLS/pairing and secure token rotation. Do not include secret values in
Solutions, git commits, logs or documentation.

## Build & deploy

Company build entry point (from repository root):

```bash
./build.sh --mirror none samsung/watch7
```

**Warning:** the company's build script may push an image when registry
credentials are present. Do not execute it against a configured registry
merely to perform a local acceptance test. For no-push S5 tests, use the
adjacent project's `scripts/s5_build_local.py` (Docker Buildx `--load`);
the ARM64 build has passed against the company's ROS base image.

Its Driver image contains Python, ROS2, seven Sensor Card plugins,
the read-only `watch_activity` Processor and the opt-in `watch_haptic`
Actuator. Dependent feature branches
add `motion.py`, `environment.py`, `metric_cards.py`,
`vitals.py`, `recordings.py`, `activity.py`, `orientation.py`
and `haptics.py` without
adding apt/pip dependencies.
There is no Android SDK or Samsung proprietary SDK inside the Docker image.
The host needs the company's `/opt/phanthy-motus/dds-local.xml` loopback
DDS profile, ROS domain 42 and host network mode.

Deploying through the Dashboard uses `deploy/service.yml`; configure
the Watch's URL as `http://<robot-LAN-IP>:15751` and its device token to
match `WATCH7_DEVICE_TOKEN` on the robot. APK v1 and v2 telemetry are
accepted; local advanced Samsung measurement builds remain separate.

The Android companion source is maintained in the adjacent
`phanthymotus_wearable_card/heart-rate-wear-test` project, **not** this Driver PR.
Its **Standard 0.2.0** flavor builds without proprietary Samsung SDK dependencies
and installs alongside the legacy research app; **Research 0.2.0** includes
advanced Samsung Health Sensor SDK features and requires an independently
licensed local AAR. Both debug APK builds and manifest checks passed in S4.
Debug APK signing, HTTP device transport, and unpublished research SDK
license terms are not substitutes for an approved production distribution.

## Offline contract verification

```bash
python3 -m pytest samsung/watch7/tests -q
python3 scripts/check_service_yml.py samsung/watch7
```

### `watch_orientation` contract — public Android orientation vectors

Read-only Sensor Card; actions: `info`, `capabilities`, `read(metric)`,
`start`, `stop`. Names: `rotation_vector`, `game_rotation_vector`,
`geomagnetic_rotation_vector`, `gravity_m_s2`, `linear_accel_m_s2`.
Values are **raw sensor-frame vectors**, length 3–5 for rotation
vectors or exactly 3 for acceleration/gravity. Units are `unitless`
for rotation, `m/s2` for acceleration and gravity. The Card does not
convert to yaw/pitch/roll or compass heading, normalize quaternions,
infer gestures or trigger robot motion.

It reuses the existing foreground Android SensorManager sampling and
v2 summary cache; no Samsung SDK, subscriptions or remote triggers are
added. `read` and the ROS2 topic require a current real summary, live
same-session heartbeat and quality-valid value with provider
`android_sensor`. Invalid, stale or unavailable values are omitted,
never synthesized. Off-wrist vector readings may be reported with an
explicit `wear_state=off_body`, but no wearer-posture interpretation
is made. These are orientation sensor outputs, not absolute calibrated
human pose measurements.

### `watch_activity` contract — exploratory motion observations

Type: `processor`; actions `info`, `capabilities`, `read`, `start`, `stop`.
Consumes **only** fresh, validated on-body `motion_score` from the shared
Watch7 v2 summary cache, with optional cumulative `step_count` for a
nonnegative bounded inter-sample `step_delta`. A missing or reset step
counter yields `null` delta, never an invented count.

Algorithm `experimental_threshold_v0`: scores at or below 0.18 m/s²
are candidates for `still`; scores at or above 0.45 m/s² are candidates
for `moving`; between them and the first candidate sample returns
`unknown`. Two consecutive quality-valid same-session samples are
required before emitting a confirmed state. Hysteresis values are
**engineering defaults**, not experimentally tuned thresholds. No
walking/running recognition, fall detection, emotion detection,
health diagnosis or autonomous robot command is implied. The
Processor never starts or modifies Watch sensor sampling.

The Card requires a live real `device_state` heartbeat, same Watch
session, online, collecting and `wear_state=on_body`. No summary,
unworn state, stale metric or provider mismatch means no new
activity observation. A cross-session transition resets all confidence
and step baselines. `stop` gates ROS2 publication while on-Watch
sampling is unchanged. `read` rejects cached output unless it is from
the latest valid current-session motion summary.

### `watch_vitals` contract — SDK summaries

Read-only Sensor Card; actions: `info`, `capabilities`,
`read(metric)`, `start`, `stop`.
Supported v2 scalar summaries: `ibi_ms` (ms), `spo2_pct` (%),
`skin_temperature_c` (degC). The Card never returns ECG, raw PPG,
IBI time series, or any derived clinical interpretation. Heart rate
continues to use the existing `heart_rate` Card rather than duplicating
a second heart-rate source.

This is a **Driver contract integration only**: the Standard companion
does not produce those SDK signals, and the Research companion still
requires separate licensed SDK source integration to emit the matching
`v2/telemetry` summary values. The static capabilities response
explicitly reports `companion_integrated=false`; a successful synthetic
test is **not** real Samsung measurement validation.

The Driver accepts these summaries only from real Watch7 traffic
with a fresh same-session collecting/on-body heartbeat, quality `valid`,
an age of at most 15 seconds, and provider `samsung_sdk`. Missing,
unsupported or stale values return structured errors; nothing is
fabricated. These are research observations, **not medical diagnoses**.

### `watch_recordings` contract — PPG metadata only

Read-only Sensor Card; actions: `info`, `list`, `start`, `stop`.
Supports `signal_manifest` for `ppg_green`, `ppg_red`, `ppg_ir`.
Only `recording_id`, `signal`, `sample_count`,
`sample_rate_hz`, `stored_on_watch=true`, session and timestamps
are indexed (32-entry in-memory bound, 10-minute retention).
**No waveform samples, files, ECG or wearable recordings are downloaded.**

The API requires **two independent gates**: a deployment operator must
explicitly enable `WATCH7_RECORDING_INDEX_ENABLED=1` and a compatible
research companion must report `recording_manifest_enabled=true`
in a fresh, real, on-body v2 heartbeat. The company service YAML
fixes the operator flag to `0`. On current Standard/Research source
the new Watch flag is not emitted, so the index remains unavailable
until separately implemented with explicit user UI consent. Revoking
consent, stopping collection, removing the Watch or changing session
clears the index. Publishing always re-checks the live gate.

Research companion SDK binaries, recording contents, health secrets
and private signing material are **never** part of this Driver branch.
Actual PPG capture, UX consent, E2E upload, ROS2 and Bumi validation
are future tasks, not claimed as already verified.

### `watch_haptic` contract — third follow-up

Type: `actuator`; actions: `info`, `start`, `stop`,
`vibrate(pattern,event_id,reason)` and `status(request_id)`.
ROS2 ACK topic: `/{ros_namespace}/watch7/haptic_acks` as JSON.
Only `single`, `double`, `triple` patterns are supported.

**Fail closed by default:** merely enabling the Card in `config.yaml`
only makes it visible to MCP; it does not grant hardware execution.
An operator must deliberately set `WATCH7_HAPTIC_ACTIONS_ENABLED=1`,
and the user must start monitoring and explicitly enable
"授权/撤销当前监测会话的远程振动" in the matching Watch companion.
The v2 device heartbeat must be real, fresh (<15 s), collecting,
online, on-body, and contain `haptic_enabled=true`. This flag is
**independent** of `remote_control_enabled` (sensor collection) and
`agent_prompts_enabled` (task proposal notifications); neither grants
remote vibration. Old companion builds missing the new field are denied.

Commands are placed on the authenticated **`/v1/commands`** route,
with a 3,000 ms monotonic lease, 5,000 ms cooldown, bounded queue and
120 s event-id replay protection. `/v1/ack` requires a matching
`request_id`, `session_id`, and a valid device result. It is
idempotent, and only accepted Watch ACKs publish a ROS2 result.
A command being queued, HTTP-delivered, or having an MCP 200 response
**never** means the watch motor executed. Even an `executed` ACK is a
Watch App report, not an objective measurement of vibration amplitude.

On the Watch side, the opt-in is lost on foreground monitoring service
restart. Revocation is checked again immediately before vibrating,
and processed IDs are persisted before action to prevent duplicate
execution after a process crash. An expired, mismatched-session,
off-wrist or unconsented command must not vibrate.

**Companion code dependency:** The companion changes are located at
`../heart-rate-wear-test/app/src/main/java/com/example/heartrateweartest/`
(`MainActivity.kt`, `HealthMonitorService.kt`, `GatewayClient.kt`,
`DeviceStateRepository.kt`). That directory is **not part of the company Driver repository**.
The reproducible source-only diff is preserved in this personal-Fork
branch as `docs/companion-haptic-optin-2026-11-09.patch`. It has passed
`git apply --reverse --check` against the locally edited Android
source. Apply it only to the matching S4 base; do not apply twice.
No APK or proprietary AAR is uploaded. You must build/install the
matching Standard companion separately after review. An older APK cannot
consent to this new actuator, by design.

Testing the Python Card alone does **not** establish live Watch7 ACK
or actual vibration. Do not enable remote actions on Bumi until a
separate explicit deployment and on-Watch acceptance check.

### `watch_environment` contract

Actions: `info`, `capabilities`, `read(metric)`, `start`, `stop`.
Names and units: `pressure_hpa` (hPa), `light_lux` (lux), and
`magnetic_field_uT` (three-axis microtesla vector).
The Watch-side Standard companion already reads these optional public
Android `SensorManager` metrics; no Samsung proprietary SDK is used.
Data is returned only from the validated v2 `summary` cache.

Like the Motion Card, its `read` and ROS2 outputs require a fresh,
online and collecting v2 heartbeat in the same session, quality `valid`,
a sensor age of at most 15 seconds, and source `watch7_real`.
On-change light values may become stale without a new event; the Card
must then return `stale_or_simulated`, not a fabricated replacement.
Off-body state does not invalidate ambient readings, but is always
explicitly reported. Data values do not establish weather, altitude,
heading or environmental measurement accuracy.

The `environment` topic contains **only** the three allowed ambient
metrics. Heart rate, accelerometer and all other metrics are excluded.
The `capabilities` response describes implemented code paths only,
not a verified current sensor reading.

The two metric Cards share `metric_cards.py` to keep action schema,
freshness checks and output filtering consistent. The original two
baseline Sensor Cards remain unchanged in behavior.

### `watch_motion` contract

Actions: `info`, `capabilities`, `read(metric)`, `start`, `stop`.
Supported metric names: `motion_score` (m/s2), `accel_m_s2` (XYZ m/s2),
`gyro_rad_s` (XYZ rad/s), and `step_count` (cumulative Android counter).
The Step Counter requires Watch-side `ACTIVITY_RECOGNITION` permission.

The Card reads the existing validated v2 `summary` cache and only publishes
quality-valid motion metrics from the current live Watch session. A fresh,
online and collecting v2 `device_state` heartbeat is mandatory; missing,
stale, mismatched-session and idle data are refused. `off_body` is exposed
as context and must never be interpreted as on-wrist activity. The motion
ROS2 topic does not include heart-rate or any other health metric.
Static capabilities describe available code paths, not live hardware status.
No raw IMU waveform, long-term recording or activity diagnosis is provided.

These are offline-contract-tested implementations until genuine Watch7
v2 motion and environment summaries, separate ROS2 subscribers and
Dashboard Canvas runs are verified together. The earlier independent sensor probe does not prove this
Card's real-device acceptance.

No Watch is needed for these checks. True end-to-end verification requires
Watch7 on the same trusted network, an ARM64 Driver running on Linux and
a ROS2 subscriber. A successful stub test does not constitute live-device
acceptance.

## Disabled functionality (separate, reviewed PRs)

- Remote sampling start/stop/configure; task-bound haptic authorization
- Task proposals, consent/revocation, TTS and robots
- ECG/BIA/SpO2 automated measurement control and high-rate waveform transfers;
  PPG manifest *indexing* remains disabled by default
- Access from an untrusted network; automated APK distribution

Failing health permissions, stale samples, off-wrist physiology, expired
sessions, duplicate sequences and absent device telemetry never generate
fake health readings. Motion metrics may still be measured off-wrist, but
the Card exposes wear state and makes no on-wrist behavioral claim.
