# Watch7 Haptic Actuator — real-device engineering E2E (2026-11-09)

## Scope, ownership and evidence limits

- Device verified with wireless ADB: **Samsung Galaxy Watch7 SM-L310**,
  previously paired, at `192.168.43.190:37157` (ephemeral ADB port).
- Windows test host WLAN: `192.168.43.98/24`.
- Existing Standard package: `com.phanthymotus.watch7`.
- Matching Standard Debug APK built offline with no Samsung proprietary
  SDK; SHA-256:
  `bd202ca1b5043d3f19883e153ac08d72b092507fa79ec923b3352f449b59beab`.
- `adb install -r` returned `Success`; package verified afterwards.
  No uninstall, no Research APK or personal measurements touched.
- Source under test: personal Fork `feat/watch7-haptic-card` based on
  `feat/watch7-environment-cards`. This is a **local engineering E2E**,
  not deployment on Bumi, company PR approval, or human-consent study.
- The Watch UI actions (start monitoring, open the haptic opt-in dialog,
  confirm the session option) were performed via **owner-delegated ADB
  UI input**. They exercised the normal on-screen controls without
  writing privileges, app preferences or system permissions directly.
  **ADB-simulated UI input does NOT prove a human physically consented.**

## Observed sequence

1. Standard app opened successfully and its monitoring foreground
   service appeared in `dumpsys activity services`, with
   `isForeground=true` and a foreground notification.
2. Read-only on-device preferences showed paired Driver URL
   `http://192.168.43.98:15751`, a credential of expected length,
   v2 telemetry enabled, and remote haptics initially unconfigured.
   The credential value was **never printed or uploaded**.
3. Windows local test ingress bound the intended WLAN port, and the
   Watch successfully pinged the computer (**2/2 ICMP replies**).
   However, direct Watch-to-Windows HTTP `/health` did **not**
   return a 200 response and the Watch app repeatedly logged
   `WearableGateway ... SocketException`. During the direct-LAN
   test window, the Driver received **zero** real heartbeat events.
   Therefore **standalone LAN E2E failed** on this test setup.
   The precise TCP block point (Windows firewall / network filtering /
   interface routing) is **not yet proven**; this is not a code pass.
4. To isolate transport from application behavior, a **temporary ADB
   reverse** `tcp:15751 -> tcp:15751` was set up and the app's
   existing endpoint was temporarily changed through the app's
   supported `gateway_url` Intent to `http://127.0.0.1:15751`.
   Existing pairing secret was preserved. The local test Driver
   used the existing secret from a private on-disk file without
   logging it. No ROS2, Agent Core or Bumi was started.
5. After the normal visible Start Monitoring UI control was triggered,
   real v2 device-state heartbeats were received continuously, and
   the Watch UI displayed a live heart rate. The Driver correctly
   rejected haptics while the wear state was `unknown`, and
   then rejected while `haptic_enabled` was missing/false.
6. The normal Watch Settings menu was navigated using on-device
   UI input to the independent **remote haptic authorization**
   option. The full dialog was displayed; the confirmation button
   was reachable after scrolling. A subsequent real v2 heartbeat
   gave `HAPTIC_GATE=allowed`.
7. Exactly **one** `single` pattern command was queued with a unique
   `event_id`. The Watch Companion fetched it via the authenticated
   `GET /v1/commands` route and sent a matching
   `POST /v1/ack` with `state=executed`.
   The local Driver reported `WATCH_ACK_STATES ['executed']` and
   `REAL_WATCH_APP_HAPTIC_ACK_EXECUTED_PASS`.
   **An application ACK confirms the Watch requested vibration; it is
   not an independent accelerometer or wearer-sensation measurement.**
8. Test server and command queue were stopped. The original WLAN URL
   `http://192.168.43.98:15751` was restored and verified through
   the app's existing settings; the ADB reverse tunnel was removed.
   This test never touched Bumi or the company PR.
9. Post-test read-only inspection found `enable_remote_haptics=true`
   persisted despite a `force-stop`. Because live heartbeat consent
   also checks `WatchRuntime.active` and the new foreground service
   clears consent at startup, this was **not** sufficient to create
   an executable new session. Nevertheless, retained local consent
   preference is a UX/hardening gap. It was **explicitly revoked
   through the Watch's visible UI**, and a final read confirmed
   `HAPTIC_OPT_IN_AFTER False`.

## Acceptance matrix

| Check | Result | Evidence |
|---|---|---|
| ADB pairing + SM-L310 identity | **PASS** | Shell model verification |
| Standard APK preserving package/data | **PASS** | `adb install -r` Success |
| Watch v2 actual heartbeat through ADB reverse | **PASS** | Repeated DeviceStore events |
| Watch on-body and session consent gating | **PASS** | Unknown -> consent-required -> allowed |
| MCP/HTTP Watch command and ACK | **PASS** | One Watch `executed` ACK |
| Independent vibrator motor measurement | **NOT VERIFIED** | App ACK is not physical sensing |
| Direct independent Wi-Fi to Windows port 15751 | **FAIL/BLOCKED** | /health no 200, SocketException |
| Native Bumi ARM64 deployment & ROS2 ACK subscriber | **NOT TESTED** | No Bumi operations |
| Real human on-Watch consent UI study | **NOT TESTED** | ADB input is automated |
| Test rollback + explicit revocation | **PASS** | Original endpoint and flag=false |

## Follow-up defects and hardening items

- **P0 — Direct WLAN:** diagnose the Windows inbound TCP/15751
  reachability issue (for example, isolate a source-IP-scoped firewall
  allowance for the Watch on a controlled hotspot). Do not declare
  independent Watch-to-Bumi networking accepted from this ADB-reverse
  test. Bumi may have different networking and needs its own check.
- **P1 — Stale consent preference:** do not rely solely on
  `onDestroy` to clear persistent consent, because Android
  `force-stop` does not guarantee that callback. Continue to require
  live `WatchRuntime.active`, fresh matching-session v2 heartbeat,
  owner opt-in and explicit Driver operator enable. Consider an
  in-memory/per-session consent authority with a revocable session ID.
- **P2 — Wear OS dialog UX:** the initial long text obscured the
  positive action until scrolling; verify accessibility at 480x480
  and shorten copy before wider testing.

## Environment left after testing

- Watch7: Standard Debug APK installed; original WLAN Driver URL
  restored; remote haptic preference **false**.
- Local temporary Driver: **stopped**; no pending command server.
- ADB reverse: **removed**.
- Bumi / QQ / Agent Core / company PR #500: **unmodified**.
- Production/deployment `WATCH7_HAPTIC_ACTIONS_ENABLED=0`
  is **unchanged**. No action is enabled persistently on Bumi.

Verification scripts are private local test harnesses in
`phanthymotus_wearable_card/scripts/s5_haptic_*.py` and intentionally
are not shipped in the Driver image.
