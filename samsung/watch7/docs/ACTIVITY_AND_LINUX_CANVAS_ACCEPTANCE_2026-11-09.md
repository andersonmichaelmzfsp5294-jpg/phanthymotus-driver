# Watch7 Activity Processor + real Linux Canvas API acceptance — 2026-11-09

## Provenance, target and boundaries

- Company PR #500 is still the two-Sensor baseline. It was not updated.
- Personal Fork development parent:
  `feat/watch7-vitals-and-recordings` @ `30a1d36366c7f3a20fa7209fa0f6e4426b44aa6c`.
- New dependent feature: `watch_activity` Processor with a deterministic,
  conservative still/moving/unknown heuristic, **not** an activity classifier
  validated on real users.
- Integration executed against actual Docker Ubuntu-24.04
  `phanthy-qq-official-canvas-test` Agent Core/Dashboard API and a fresh
  isolated `watch7-s6-canvas-driver` ROS2 image.
- No Robot Bumi, existing QQ containers, Watch7 system permissions,
  or production haptic controls were modified.

## Card implementation

- Company Card: `watch_activity` type `processor`.
- MCP actions: `info`, `capabilities`, `read`, `start`, `stop`.
- ROS2: `/{ros_namespace}/watch7/activity`, `std_msgs/String` JSON,
  `data/json`.
- Consumes only validated fresh v2 `motion_score` from `android_sensor`
  in a real, on-body, collecting, online, same-session heartbeat.
- Experimental algorithm: `score <= 0.18` m/s² suggests still;
  `score >= 0.45` suggests moving; two consecutive observations to confirm.
  Other conditions return unknown. No walking/running, gesture, fall or
  medical inference.
- Optional validated cumulative `step_count` produces an **interval delta**
  when the counter stays monotonic, gap <=10 seconds, delta 0..50.
  Missing/reset/implausible data returns null; never invent counts.
- `stop` gates ROS2 publishing only. State, sequence and step baseline
  are reset at a new Watch session; no robot action or wearer alerts.

## Actual executed checks

```text
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
# 47 passed on the final rerun (six new Activity tests, two new
# private Docker identity/namespace tests versus earlier 39).

python -X utf8 scripts/check_service_yml.py samsung/watch7
# 1 checked, 0 failed, 0 known gaps

python -B scripts/s5_build_local.py
# local Linux AMD64 image build PASS, no registry push.

python -B scripts/s6_canvas_real_e2e.py check
# real Linux Agent Core registered eight Cards and returned 200 for
# their info() calls and read-only capabilities/read/status/list probes.
# Core S6 entry: mcp-1794171679; S5 entry: mcp-1794076334.
```

The first Activity-expanded all-tests run observed an intermittent
Windows WinError 10053 in an **existing** haptic HTTP test:
46 passed / 1 failed. The isolated haptic test then passed 6/6,
and the subsequent full rerun passed 47/47. No hardware command was run.

### Real Canvas registry and ROS2

- Real Core Docker container reports tools:
  `device_state`, `heart_rate`, `watch_activity`,
  `watch_environment`, `watch_haptic`, `watch_motion`,
  `watch_recordings`, `watch_vitals`.
- Real Canvas tool-call proxy `POST /api/mcp/{id}/call` succeeded with
  `action=info` for all eight. Read-only `watch_activity.read`
  returns an unavailable result when no current v2 motion summary exists.
  This is the expected safe response, not a fabricated measurement.
- Real ROS2 CLI `ros2 topic list` from inside the running Driver showed
  all eight published topics including `/watch7_s6_lab/watch7/activity`.
- Real Core `POST /api/mcp/{id}/ping` and `GET /api/topics`
  displayed all eight topic definitions after an explicit ping.
- **Limitation:** These are the real Dashboard/Canvas **backend APIs**
  and real ROS2 topic discovery, not a GUI drag-and-drop/manual browser
  Canvas test, not proof of ROS2 data payload consumption, and not
  genuine wearable motion measurements in this S6 test container.
  Synthetic frames were used only in pytest integration tests.

### Isolation bug found and fixed

- Core deduplicates providers by **MCP serverInfo.name**, even if
  registration URL or display name differs. First S6 registration
  unintentionally upserted the original S5 registry entry.
- Original S5 registry `mcp-1794076334` was **restored** via real
  Core registry API to `Samsung Galaxy Watch7 Bundle` at the actual
  S5 Docker bridge endpoint `172.17.0.3:15750/mcp`, and its original
  two tools were verified.
- Private S6 now supplies unique `WATCH7_TEST_MCP_SERVER_NAME` with
  `WATCH7_PRIVATE_DOCKER_TEST=1`; S6 uses an independent entry
  `mcp-1794171679` at bridge endpoint `172.17.0.4:15750/mcp`.
- Docker randomly assigned hostnames may begin with digits; using
  their raw value as a ROS namespace crashes `rclpy` on topic creation.
  Fixed with a `watch7_` prefix fallback and an explicit test.
- S5 and S6 both appear separately in the real Dashboard registry
  after testing, with distinct topics and unchanged QQ Core container.

## Remaining true end-to-end gates

1. Real Standard Watch7 emits fresh v2 motion_score + step_count to
   the **new** isolated Driver without synthetic frames.
2. Independent ROS2 subscriber receives and inspects an actual activity
   observation payload, and the visible Dashboard Canvas displays it.
3. Research APK SDK telemetry is wired for IBI, SpO2, skin temperature,
   and optional PPG manifests. In this build, these remain contracts,
   not genuine real-time observations.
4. Direct Windows WLAN ingress and force-stop haptic opt-in persistence
   are previously identified, **deferred** and unchanged.
5. Bumi ARM64, machine owner deployment and company reviewer/CI are
   not part of this acceptance. The production operator flags for
   haptic action and PPG indexing stay disabled.
