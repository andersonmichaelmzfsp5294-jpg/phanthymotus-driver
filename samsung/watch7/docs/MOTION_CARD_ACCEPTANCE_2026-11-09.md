# Watch7 motion Sensor Card — local acceptance (2026-11-09)

## Provenance and scope

- Upstream baseline: `4paradigm/phanthymotus-driver` PR #500,
  `bc12b50243a63ef09af6237044f0fd5d45a22e59` (two read-only Cards).
- Personal Fork development branch:
  `andersonmichaelmzfsp5294-jpg/phanthymotus-driver:feat/watch7-motion-cards`.
- This follow-up adds the **read-only** `watch_motion` Sensor Card. It does
  **not** change PR #500, Agent Core, any robot configuration or Watch APK.
- The Driver reuses the prior local, uncommitted `device.py` v2 summary
  cache and generic `metric_snapshot()` work. It is intentionally included
  in this follow-up, not transplanted into the reviewed baseline PR.

## Contract

- ROS2: `/{ros_namespace}/watch7/motion` as `std_msgs/String` JSON,
  `data/json`; MCP tool `watch_motion`, type `sensor`.
- Actions: `info`, `capabilities`, `read(metric)`, `start`, `stop`.
- The first four metrics: `motion_score`, `accel_m_s2`, `gyro_rad_s`,
  `step_count`. Step count is cumulative rather than a per-second step delta.
- Source is an authenticated, v2-validated `summary`, filtered to motion
  fields; heart rate and other physiological signals are never forwarded
  to the motion topic.
- Dynamic reads require a fresh v2 device heartbeat from the same session
  that is online and collecting. Reject stale, invalid, simulated and
  session-mismatched data. No zeros or inferred values are substituted.
- `start/stop` gates ROS2 publishing only. No remote Watch acquisition,
  force-start, haptics, Agent authorization or physical robot action is added.

## Tests actually executed

From repository root:

```bash
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
# 22 passed, exit 0

python -X utf8 scripts/check_service_yml.py samsung/watch7
# 1 checked, 0 failed, exit 0
```

New motion tests cover Card schema, metric filtering, cache freshness, quality,
sequence replay, session replacement, idle state, lifecycle stop, authenticated
HTTP ingress -> DeviceStore -> MCP read and ROS publication callback. These
use synthetic frames and an in-process HTTP server; they **do not constitute
true ROS2 middleware or physical-watch acceptance**.

## Hardware / deployment gates still NOT VERIFIED

- Physical Watch7 Standard APK emitting genuine v2 acceleration, gyro,
  step count and motion score while worn, including screen-off sampling.
- Native Linux/ARM64 image running on Bumi, ROS2 domain 42 independent
  subscriber receiving motion samples and Dashboard Canvas live wiring.
- Company build/review of this *new branch* and any machine-owner approval.
- Stable network, TLS pairing and production deployment security.

## Rollback and dependency

- Disable only the `watch_motion` plugin in `samsung/watch7/config.yaml`;
  the existing `heart_rate` and `device_state` Card contracts remain intact.
- Reverting this personal-Fork follow-up returns to PR #500's two-Card
  `bc12b50` baseline.
- Keep this branch explicitly dependent on PR #500 until the base is merged;
  do not submit it as an independent PR against an unrelated `main`.
