# Watch7 Haptic Actuator — local acceptance (2026-11-09)

**Post-test update:** The initial sections below describe the earlier
software-only acceptance. A subsequent real Watch7 engineering E2E
**passed over a temporary ADB reverse tunnel** (one matched `executed`
Watch App ACK), but direct standalone WLAN-to-Windows HTTP was blocked.
The test cleaned up both the saved URL and temporary consent. See
[`HAPTIC_REAL_WATCH_E2E_2026-11-09.md`](HAPTIC_REAL_WATCH_E2E_2026-11-09.md).
Do not confuse the app ACK with physical motor instrumentation.

## Ownership and version

- Company upstream PR #500: `bc12b50243a63ef09af6237044f0fd5d45a22e59`.
- Motion personal-Fork commit: `1f6f8262af7bad1efeec30e9616312d06741aff1`.
- Environment personal-Fork commit: `b96c7c383091a46789616f88e411932b963d96a8`.
- New dependent personal-Fork branch: `feat/watch7-haptic-card`.
- This is the **first controlled Actuator Card**; prior four Sensor Cards
  remain read-only. Company PR #500 and Bumi were not changed.

## Driver implementation

- Standard Card `watch_haptic`, type `actuator`, actions:
  `info`, `start`, `stop`, `vibrate(pattern,event_id,reason)`,
  `status(request_id)`. ROS2 ACK topic
  `/{ros_namespace}/watch7/haptic_acks` (`std_msgs/String`, JSON).
- Allowed patterns: `single`, `double`, `triple`. No arbitrary timing,
  non-stop vibration, motor amplitude or raw health input.
- Separate operator flag `WATCH7_HAPTIC_ACTIONS_ENABLED`: **OFF by default**.
  Deployment YAML explicitly sets `WATCH7_HAPTIC_ACTIONS_ENABLED=0`.
  Card registration is not permission to execute.
- Companion must send a fresh real v2 `device_state` heartbeat with
  `haptic_enabled=true`, `wear_state=on_body`, `online=true`,
  `collecting=true`, same session and last heartbeat within 15 seconds.
  The flag is independent from `remote_control_enabled` and
  `agent_prompts_enabled`. Older Watch APKs omit it and cannot authorize
  an actuator.
- Commands are retrieved by the Watch via authenticated
  `GET /v1/commands`; ACKs are submitted via
  `POST /v1/ack` with request/session/state. Existing v2 control
  and task authorization endpoints stay disabled.
- Monotonic command TTL 3 seconds, 5-second cooldown,
  120-second replay window, idempotent `event_id`, bounded FIFO storage.
  Stop/revoke/offline/new session cancels queued/delivered commands
  before next delivery or ACK. A Watch ACK is required for executed
  state, and only an accepted ACK creates ROS2 output.
- HTTP queued/delivered and MCP request success are **not** actuator
  execution proof; an `executed` Watch ACK is an application report,
  not an independently instrumented vibrator measurement.

## Companion source changes — outside the Driver Fork

Local source only (not yet part of the driver GitHub commit):

`E:\\常用开发文件夹\\实习\\phanthymotus_wearable_card\\heart-rate-wear-test`

- `MainActivity.kt`: explicit on-Watch remote-haptic toggle with consent
  dialog, separate from remote sensor-control and task-notification toggles.
  It requires the user-started foreground monitor and enables v2 heartbeat.
- `HealthMonitorService.kt`: clear haptic consent at the beginning and end
  of each foreground monitor session.
- `DeviceStateRepository.kt`: emit `haptic_enabled` on v2 heartbeat.
- `GatewayClient.kt`: check consent and real wear-state immediately
  before execution, reject a cross-session command, include `session_id`
  in ACK, and drop stale pending ACKs after session changes.
- The full companion source is not included in the company Driver
  repository by design. The four-file source-only diff is retained as
  `docs/companion-haptic-optin-2026-11-09.patch` in the personal Fork;
  `git apply --reverse --check` against the locally edited app passed.
  A separately versioned Android source repository and a matching APK
  installation are still required for on-device acceptance.

## Executed verification

```bash
# Driver root
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
# 32 passed (all Driver tests including six new haptic tests)

python -X utf8 scripts/check_service_yml.py samsung/watch7
# 1 checked, 0 failed, 0 known gaps

# Companion project root (source-only build; no install)
python -B build_s4.py --variant standard --offline --no-samsung-sdk
# BUILD SUCCESSFUL; debug Standard APK produced
```

APK path:
`heart-rate-wear-test/app/build/outputs/apk/standard/debug/app-standard-debug.apk`

Built APK SHA256:
`bd202ca1b5043d3f19883e153ac08d72b092507fa79ec923b3352f449b59beab`

The first Standard build invocation timed out in Gradle project
configuration; the second completed successfully. No Research SDK
AAR is bundled into the Standard flavor.

## Not yet accepted

- No Watch7 install or user opt-in test of this new APK.
- No actual motor actuation or physical vibration observation.
- No Bumi deployment, ARM64 native runtime or actual ROS2 subscriber.
- No Dashboard Canvas discovery/call test of new actuator.
- HTTP+token is trusted LAN/dev-only, not a production encrypted
  deployment. Production needs TLS/pairing, rate/abuse monitoring,
  review of device-side consent UX and operator approval.
- The user must approve hardware test and Watch installation separately.
  Do not set the Docker service flag to 1 or remote-control the Watch
  by default.

## Next safe gate

1. Commit/version the companion source in a suitable personal repository.
2. With Watch7 user approval, install matching Standard APK and explicitly
   grant/revoke remote vibration inside the app.
3. On a strictly isolated trusted LAN, set the Driver operator flag to 1
   in a disposable local test instance only. Verify one short manual test,
   matching ACK, replay suppression, expired command, revocation and
   operator kill switch. Do not touch Bumi without separate approval.
