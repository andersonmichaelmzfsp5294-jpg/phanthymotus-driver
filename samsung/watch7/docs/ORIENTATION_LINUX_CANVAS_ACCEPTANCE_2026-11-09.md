# Watch7 Orientation Sensor — real Linux Agent Core/Dashboard acceptance (2026-11-09)

## Version and independent changes

- Personal Fork previous baseline: `feat/watch7-activity-card`,
  commit `714ca066426bde19e9bcdc726767c544e76fddb6`.
- New dependent follow-up: `feat/watch7-orientation-card`.
- Company PR #500, Bumi, the existing S5 Driver and QQ features are
  unchanged. This validation used only the isolated S6 Linux Driver and
  the **existing**, running Agent Core/Dashboard container.

## New Card

`watch_orientation`: standard `Sensor` Card. MCP actions `info`,
`capabilities`, `read(metric)`, `start`, `stop`; ROS2 output
`/{ros_namespace}/watch7/orientation`, JSON `std_msgs/String`.

Five permitted public Android SensorManager metrics:

| Metric | Unit | Shape |
|---|---|---|
| `rotation_vector` | unitless | vector of 3–5 components |
| `game_rotation_vector` | unitless | vector of 3–5 components |
| `geomagnetic_rotation_vector` | unitless | vector of 3–5 components |
| `gravity_m_s2` | m/s² | XYZ |
| `linear_accel_m_s2` | m/s² | XYZ |

The Standard Companion already subscribes to these optional sensors
only when the wearer starts foreground monitoring and places them in
the bounded v2 `summary` JSON. The Driver uses the existing
quality/TTL/session-checked `DeviceStore.metric_snapshot()` and
checks `provider=android_sensor`. No extra Watch sensor tracker,
Samsung SDK, orientation calibration, euler-angle conversion, heading
estimate or robot command has been added.

Environment/motion/vitals/recording fields are excluded from the
orientation topic by a strict allowlist. Off-wrist sensor values can
exist; they are reported with the original wear state and do not imply
wearer posture. Unavailable sensors remain unavailable.

## Tests actually executed

1. `python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider`:
   **53 passed** after correction of an off-body test fixture.
   Six orientation-specific tests cover schema, MCP, genuine v2
   validator shape, source checks, device session, stale/invalid input
   and publication stop.
2. `python -X utf8 scripts/check_service_yml.py samsung/watch7`:
   **1 checked, 0 failed, 0 known gaps**.
3. `python -B scripts/s5_build_local.py`:
   **local Linux AMD64 Docker image build succeeded**; no registry push.
4. On the real Ubuntu-24.04 Docker Core
   `phanthy-qq-official-canvas-test`, the actual
   `GET /api/mcp/{id}/tools` and `POST /api/mcp/{id}/call`
   returned **nine registered Watch7 Cards and nine successful
   `info` calls**, including the new `watch_orientation`.
   The optional orientation `capabilities`/empty `read` were
   also accepted. A missing live Watch summary is correctly
   unavailable rather than invented.
5. The actual Core Dashboard `POST /api/mcp/{id}/ping` and
   `GET /api/topics` returned **all nine S6 topic definitions**,
   including `/watch7_s6_lab/watch7/orientation`.
6. Native ROS2 CLI `ros2 topic list` inside the running Linux Driver
   displayed the nine S6 publisher topics. S5's two original topics
   remained present and the S5 registry identity stayed intact.

One first-run Windows full-suite test failed for an accidental valid
off-body HR test fixture, now fixed. An unrelated pre-existing HTTP
unit test also intermittently encountered Windows WinError 10053.
Subsequent full rerun passed 53/53. No test wrote a Bumi robot state.

## Acceptance distinction

**Verified:** local Card contracts; real running Linux Driver process;
real Agent Core/Dashboard *backend* discovery, calls and topic
registration; actual ROS2 publisher topic discovery.

**Still pending:** physical Watch7 submitting real orientation vectors
to this newly created S6 Linux Driver; independent ROS2 subscriber
receiving real live data; browser GUI Canvas drag-and-drop observation;
Android Research SDK advanced data pipeline; company code review/CI
and Bumi ARM64 deployment.

The two deferred issues (Windows direct LAN ingress and persistent
haptic consent across force-stop) are not changed by this patch.
Operator flags controlling haptics and PPG recording index remain off.
