# Watch7 Research SDK → Driver v2 Uplink — implementation checkpoint

Date: 2026-11-09
Dependent branch: `feat/watch7-research-uplink`
Parent: `ae3b4cbd8071080d5f955ac28fd774d080e6c2f7`
Company PR #500: **unchanged**
Robot Bumi: **not touched**

## Problem solved in source

The Samsung Research flavor previously captured SDK observations into
on-Watch app-private JSONL files only. Standard mode's on-Watch telemetry
client depends on a separate foreground basic monitor, which must be
stopped before advanced Samsung measurements. The existing
`watch_vitals` and `watch_recordings` Driver Cards could therefore not
receive live research observations from the Research APK.

The local Research Android sources were modified to add an
**independent, foreground-UI-authorized, short-session uploader**.
It does not remotely initiate an SDK tracker; all measurements are still
started from the Watch UI by the wearer.

### Android project (local source, separate from Driver repository)

`E:\\常用开发文件夹\\实习\\phanthymotus_wearable_card\\heart-rate-wear-test`

* `app/src/research/java/com/example/heartrateweartest/ResearchTelemetryClient.kt`
  — new Research-only HTTP v2 client; a source-level copy is included
  in this branch under
  `samsung/watch7/docs/research-uplink/ResearchTelemetryClient.kt`.
  This copy is **documentation/source retention**, not a module copied
  into the Docker Driver image.
* `SamsungTrackerBridge.java` — extends the SDK listener with
  `onScalar(metric, value, sampleTimestampMs)` and
  `onManifest(localManifest)`. Emits only SDK-status-validated IBI,
  SpO2, skin-temperature values after local persistence; an ECG/BIA
  source never calls either export.
* `SamsungMeasurementActivity.kt` — adds unchecked, on-Watch opt-in
  toggles for SDK scalar sharing and PPG metadata only, uses the
  Android off-body detector, rejects missing pairing and off-body,
  and resets the toggles after SDK measurement ends.
* `SamsungSignalStore.kt` — unchanged, with 5 MiB per session,
  JSONL written to app-private storage and bounded retention.
* The Standard flavor is unaffected by these Research-only files.

## Research upload protocol

```text
Human starts permitted SDK tracker in Research Activity
 → Raw Samsung SDK DataPoints stored locally first
 → Validate Samsung status, timestamp and bounded scalar
 → ResearchTelemetryClient (separate on-Watch opt-in)
 → Authenticated /v2/telemetry HTTP device_state + summary
 → Linux Watch7 Driver DeviceStore + watch_vitals
 → ROS2 /{namespace}/watch7/vitals + Agent Core Dashboard
```

PPG manifest uses a separate operator gate plus Watch opt-in and
`kind=signal_manifest` for only `ppg_green/red/ir` metadata.
ECG/BIA and raw PPG/ECG waveform samples are never sent.

The SDK `DataPoint.getTimestamp()` API returns milliseconds; the
uploader rejects timestamps outside 0–15 s behind the Watch wall clock
and carries the real sample age into `age_ms`.
Samsung documents that IBI status `0` means normal and that
`SpO2Set.STATUS=2` indicates a completed SpO2 reading. A measured
skin-temperature value is skin/object temperature, not core-body temperature.

## Verified work, with strict claim boundaries

1. Research debug APK built successfully using the locally licensed,
   operator-provided Samsung SDK AAR in the **Research** flavor:
   `python -B build_s4.py --variant research --offline`.
   Subsequent source-only edits to the PPG state/Activity lifecycle
   still require a final rebuild.
2. Standard flavor build succeeded without a Samsung SDK:
   `python -B build_s4.py --variant standard --offline --no-samsung-sdk`.
3. The Python Driver suite, including five added synthetic Research
   uplink contract cases, was executed locally:
   **58 passed**, plus service YAML: **1 checked, 0 failed**.
4. The existing S6 isolated Docker Driver has already been verified
   through the **running Linux Agent Core and Dashboard backend**:
   9 Cards discovered; 9 `info` calls, bounded read/capabilities and
   real ROS2 topic registration. This predates the Research APK upload
   and is not evidence of research data reaching the Dashboard.
5. **Not completed:** installing the new Research APK on Watch7,
   on-Watch wearer-started IBI/SpO2/temperature measurement, PPG index
   opt-in, genuine `/v2/telemetry` transfer to S6, live
   `watch_vitals.read`, ROS2 payload observation and visible browser
   Canvas use. Do not relabel synthetic tests as real-device proof.

## Known quality limitations to resolve before claiming validated Cards

* **PPG channel counts:** the first Research adapter repeats the
  stored row count across green/red/IR manifests. The Watch SDK may
  mark individual channels invalid. Before statistical use, track
  status-validated per-channel counts and actual per-channel sampling
  intervals; do not claim three equal valid channels from global rows.
* **Manifest after capture:** the Driver's opt-in session gate
  intentionally clears its index when the Research uploader sends its
  terminal offline/idle heartbeat. The ROS2 event can be observed
  while accepted, but `watch_recordings.list` must not be described
  as a durable post-capture archive. Extend the explicit metadata
  sharing window with a separately reviewed consent/retention design.
* **Companion source reproducibility:** the complete modified local
  Android source tree is not checked into this Driver repo. This
  branch retains the new uploader source, Python contract tests and
  integration notes only. The three Android integration entry points
  must be independently versioned before a company PR.
* **Direct Windows LAN ingress and haptic consent persistence** were
  explicitly deferred by the owner, and remain unresolved. For
  controlled testing, a temporary ADB reverse to an isolated Linux
  Driver port can isolate the data pipeline without changing the
  production Docker deployment.
* Production `WATCH7_RECORDING_INDEX_ENABLED=0` and
  `WATCH7_HAPTIC_ACTIONS_ENABLED=0` remain unchanged.

## Next verification commands

```bash
# Windows Research companion root
python -B build_s4.py --variant research --offline
python -B build_s4.py --variant standard --offline --no-samsung-sdk

# Driver root
python -B -m pytest samsung/watch7/tests -q -p no:cacheprovider
python -X utf8 scripts/check_service_yml.py samsung/watch7

# E:/.../phanthymotus_wearable_card root
python -B scripts/s6_canvas_real_e2e.py check
python -B scripts/s6_canvas_ping_topics.py
```

Do not publish Research APKs, Samsung AAR, raw measurements,
device pairing tokens or signing keys into a public GitHub repository.
