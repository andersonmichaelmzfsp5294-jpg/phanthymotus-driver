# Samsung Galaxy Watch7 — read-only Sensor Driver

## PR and version boundaries

Company PR **#500** (`feat/watch7-heart-rate-driver`, commit `bc12b50`)
is the reviewed **two-Card baseline**: `heart_rate` and `device_state`.
The **first** personal-Fork follow-up, `feat/watch7-motion-cards`,
adds the read-only `watch_motion` Sensor. The **second**, dependent
follow-up `feat/watch7-environment-cards`, adds `watch_environment`
and a shared read-only metric Card adapter. Neither is part of PR #500.

All four Cards use `std_msgs/String` JSON on ROS2 domain 42 with
`data/json`:

- `/{ros_namespace}/watch7/heart_rate`
- `/{ros_namespace}/watch7/device_state`
- `/{ros_namespace}/watch7/motion`
- `/{ros_namespace}/watch7/environment`

The ROS namespace defaults to the sanitized hostname. All Sensor `start`
and `stop` actions **only gate ROS2 publication in the Driver**; they do not
start, stop or reconfigure Watch Health Services or Android sensor trackers.
The wearer must start the Companion's foreground monitor explicitly.

## Required companion and ingress

The existing research companion APK sends `POST /v1/telemetry`, `POST
/v2/telemetry` and polls commands. The Driver uses the original v1/v2
protocol validators verbatim and exposes an authenticated watch-only HTTP
endpoint on port **15751**. The existing app can continue to upload data
without Windows Relay or ADB. The first PR and both read-only follow-ups intentionally return empty
`/v1/commands` and `/v2/commands` queues and refuse all execution
and task commands. No vibration, remote sampling, ECG/BIA or autonomous
robot action is exposed by this bundle.

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

Its Driver image contains only Python, ROS2, and four read-only Sensor
Card plugins. The two follow-ups add `motion.py`, `environment.py`
and the common `metric_cards.py` helper to the Docker COPY list; no
new apt/pip dependencies are introduced.
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

- Remote start/stop/configure, haptics, device execution ACKs
- Task proposals, consent/revocation, TTS and robots
- ECG/BIA/SpO2/PPG recording manifests and high-rate data
- Access from an untrusted network; automated APK distribution

Failing health permissions, stale samples, off-wrist physiology, expired
sessions, duplicate sequences and absent device telemetry never generate
fake health readings. Motion metrics may still be measured off-wrist, but
the Card exposes wear state and makes no on-wrist behavioral claim.
