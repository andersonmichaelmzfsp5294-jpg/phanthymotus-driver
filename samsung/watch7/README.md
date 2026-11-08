# Samsung Galaxy Watch7 — read-only Sensor Driver

## PR and version boundaries

Company PR **#500** (`feat/watch7-heart-rate-driver`, commit `bc12b50`)
is the reviewed **two-Card baseline**: `heart_rate` and `device_state`.
This **separate personal-Fork follow-up**, `feat/watch7-motion-cards`,
adds one read-only `watch_motion` Sensor. It is NOT part of PR #500.

All three Cards use `std_msgs/String` JSON on ROS2 domain 42 with
`data/json`:

- `/{ros_namespace}/watch7/heart_rate`
- `/{ros_namespace}/watch7/device_state`
- `/{ros_namespace}/watch7/motion`

The ROS namespace defaults to the sanitized hostname. All Sensor `start`
and `stop` actions **only gate ROS2 publication in the Driver**; they do not
start, stop or reconfigure Watch Health Services or Android sensor trackers.
The wearer must start the Companion's foreground monitor explicitly.

## Required companion and ingress

The existing research companion APK sends `POST /v1/telemetry`, `POST
/v2/telemetry` and polls commands. The Driver uses the original v1/v2
protocol validators verbatim and exposes an authenticated watch-only HTTP
endpoint on port **15751**. The existing app can continue to upload data
without Windows Relay or ADB. The first PR and this motion follow-up intentionally return empty
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

Its Driver image contains only Python, ROS2, and the three read-only Sensor
Card plugins. This follow-up adds the `motion.py` module to the existing
Docker COPY list; it does not add apt/pip dependencies.
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

This is an offline-contract-tested implementation until a genuine Watch7
v2 motion summary, separate ROS2 subscriber and Dashboard Canvas run are
verified together. The earlier independent sensor probe does not prove this
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
