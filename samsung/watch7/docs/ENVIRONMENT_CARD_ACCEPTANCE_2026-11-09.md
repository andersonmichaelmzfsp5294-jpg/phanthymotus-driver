# Watch7 Environment Card — local acceptance (2026-11-09)

## Version provenance and scope

- Company upstream PR #500: `bc12b50243a63ef09af6237044f0fd5d45a22e59`
  (only `heart_rate` and `device_state`).
- First personal Fork follow-up: `feat/watch7-motion-cards`,
  `1f6f8262af7bad1efeec30e9616312d06741aff1`.
- Second personal Fork follow-up: `feat/watch7-environment-cards`,
  based on that motion commit.
- New `watch_environment` is a read-only standard PhanthyMotus Sensor
  Card. This changes no Agent Core, Watch APK, live Bumi, upstream PR #500
  or production Docker deployment.

## Supported metrics

| Metric | Unit | Provenance | Limitations |
|---|---|---|---|
| `pressure_hpa` | hPa | optional Android barometer | no altitude / weather claim |
| `light_lux` | lx | optional Android ambient-light sensor | on-change and possibly stale |
| `magnetic_field_uT` | uT (XYZ vector) | optional Android magnetometer | no calibrated heading claim |

The Watch7 research sensor probe observed all three signals during short
foreground sessions on 2026-11-03. That research evidence **does not**
establish Android Standard Companion -> official Driver -> native ROS2
-> Canvas acceptance.

This branch reuses the validated v2 `summary` cache in `DeviceStore`.
It factors Motion Card and Environment Card shared MCP/ROS2 logic into
`metric_cards.py`, preserving the original `watch_motion` methods
and semantics. The DeviceStore parser still bounds scalars/vectors,
validates units and rejects waveform payloads.

## Card and security behavior

- `watch_environment` type `sensor`; MCP actions: `info`,
  `capabilities`, `read(metric)`, `start` and `stop`.
- `/{ros_namespace}/watch7/environment` publishes `std_msgs/String`
  JSON with `data/json`. Only quality-valid, current environment
  metrics appear; no heart rate, PPG or IMU fields leak into this topic.
- A live v2 device heartbeat in the same session, marked online and
  collecting, is mandatory; source must be real. Sensor+receive ages
  must not exceed 15,000ms. Outdated or missing on-change metrics are
  not fabricated.
- Ambient measurements are *not* physiological, so they may be
  accepted off wrist; `wear_state` is still included as context.
- `start/stop` gate publishing in the Driver only, never start
  Watch sensor collection. No remote control, haptic or robot action.

## Executed tests

Run from the `phanthymotus-driver` root:

```bash
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
# 26 passed, exit 0 (final full-suite rerun)

python -X utf8 scripts/check_service_yml.py samsung/watch7
# 1 checked, 0 failed, 0 known gaps; exit 0
```

The initial full-suite run yielded **25 passed, 1 failure** in the
pre-existing `test_transport.py::test_watch_http_auth_and_disabled_commands`
because the local Windows socket was aborted (WinError 10053).
Immediately rerunning that file passed (2 passed); rerunning the entire
Watch7 suite passed (26 passed). The single transient is noted, not
silently erased. Tests are local in-process HTTP and simulated telemetry,
not real ROS2 middleware and not real Watch7 hardware.

## Remaining gates

- Same-WLAN Watch7 Standard App sends real ambient v2 summary to Driver.
- Native Linux/ARM64 container runs in a controlled test environment.
- Independent ROS2 subscription and Canvas Card discovery+call work.
- Behavior during screen-off and intermittent WLAN, optional sensors
  unavailable, and longer run stability are measured.
- Company code review and deploy approval are separate from this Fork.

## Rollback

Set `plugins.watch_environment.enabled: false` in
`samsung/watch7/config.yaml` to exclude the Card. To restore the
previous personal-Fork Motion baseline, reset only the independent
*remote feature branch* to parent commit `1f6f826` via a reviewed
revert; do not rewrite PR #500. Leave any unrelated local working-tree
changes intact.
