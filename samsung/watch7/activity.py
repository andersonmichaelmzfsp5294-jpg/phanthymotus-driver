"""Conservative activity-state Processor Card for real Watch7 v2 motion.

This experimental signal is intentionally limited to still/moving/unknown.
It uses already validated on-wrist motion_score and an optional step delta,
never runs a new sensor tracker, detects falls, or commands the robot.
"""
from __future__ import annotations

import threading

from sensors import SensorPlugin

STILL_MAX = 0.18
MOVING_MIN = 0.45
CONFIRM_SAMPLES = 2
MAX_STEP_INTERVAL_MS = 10_000
MAX_STEP_DELTA = 50


class ActivityPlugin(SensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_activity",
            "Exploratory still/moving/unknown observations from quality-valid, "
            "same-session Watch7 motion. No walking/fall detection or robot control.",
            topic, device, ros_publisher,
        )
        self._state_lock = threading.Lock()
        self._session = None
        self._last_seq = -1
        self._candidate = None
        self._candidate_count = 0
        self._prev_steps = None
        self._prev_step_time = None
        self._latest = None

    def _reset(self, session: str) -> None:
        self._session = session
        self._last_seq = -1
        self._candidate = None
        self._candidate_count = 0
        self._prev_steps = None
        self._prev_step_time = None
        self._latest = None

    def get_tool(self) -> dict:
        return {
            "name": self.name, "type": "processor", "multiInstance": False,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["info", "capabilities", "read", "start", "stop"],
                    },
                },
                "required": ["action"],
                "x-action-params": {
                    action: {"params": []} for action in
                    ("info", "capabilities", "read", "start", "stop")
                },
            },
            "topic_out": [{"topic": self.topic, "format": "data/json"}],
        }

    def dispatch(self, action: str, args: dict) -> dict | None:
        if action == "capabilities":
            return {
                "ok": True, "algorithm": "experimental_threshold_v0",
                "states": ["still", "moving", "unknown"],
                "still_max_m_s2": STILL_MAX, "moving_min_m_s2": MOVING_MIN,
                "confirmation_samples": CONFIRM_SAMPLES,
                "requires": ["fresh_v2_device_state", "on_body",
                             "android_sensor.motion_score"],
                "step_delta_optional": True,
                "clinical_or_safety_validated": False,
                "fall_detection": False,
                "robot_action": False,
                "watch_capture_unchanged": True,
            }
        if action == "info":
            with self._lock:
                running = self._running
            return {
                "state": "running" if running else "idle",
                "read_only": True,
                "algorithm": "experimental_threshold_v0",
                "topic_out": [{"topic": self.topic, "format": "data/json"}],
                "on_watch_sampling_required": True,
            }
        if action == "read":
            sensor = self.device.metric_snapshot("motion_score")
            if not sensor.get("ok"):
                return {"ok": False, "reason": sensor.get("reason", "metric_unavailable")}
            state = self.device.device_snapshot()
            if (not state.get("available") or not state.get("online")
                    or not state.get("collecting") or state.get("source") != "watch7_real"
                    or state.get("wear_state") != "on_body"):
                return {"ok": False, "reason": "watch_not_worn_or_idle"}
            with self._state_lock:
                last = self._latest
                if (last is None or last["session_id"] != sensor["session_id"]
                        or last["seq"] != sensor["seq"]):
                    return {"ok": False, "reason": "activity_not_observed"}
                return {"ok": True, **dict(last)}
        if action in ("start", "stop"):
            return {**super().dispatch(action, args), "watch_capture_unchanged": True}
        return None

    def publish(self, frame: dict) -> None:
        if frame.get("kind") != "summary" or frame.get("source") != "watch7_real":
            return
        heartbeat = self.device.device_snapshot()
        if (not heartbeat.get("available") or not heartbeat.get("fresh")
                or heartbeat.get("source") != "watch7_real"
                or heartbeat.get("session_id") != frame.get("session_id")
                or not heartbeat.get("online") or not heartbeat.get("collecting")
                or heartbeat.get("wear_state") != "on_body"
                or frame.get("wear_state") != "on_body"):
            return

        motion = self.device.metric_snapshot("motion_score")
        if (not motion.get("ok") or motion.get("provider") != "android_sensor"
                or motion.get("seq") != frame.get("seq")
                or motion.get("session_id") != frame.get("session_id")):
            return
        score = motion["value"]
        if type(score) not in (int, float):
            return
        candidate = ("still" if score <= STILL_MAX
                     else "moving" if score >= MOVING_MIN else "unknown")
        steps = self.device.metric_snapshot("step_count")
        good_steps = (
            steps.get("ok") and steps.get("seq") == frame["seq"]
            and steps.get("session_id") == frame["session_id"]
            and steps.get("provider") == "android_sensor"
            and isinstance(steps.get("value"), (int, float))
        )
        now = frame["captured_at_ms"]
        with self._state_lock:
            if self._session != frame["session_id"]:
                self._reset(frame["session_id"])
            if frame["seq"] <= self._last_seq:
                return
            self._last_seq = frame["seq"]
            if candidate == "unknown":
                self._candidate, self._candidate_count = None, 0
            elif candidate == self._candidate:
                self._candidate_count = min(self._candidate_count + 1, CONFIRM_SAMPLES)
            else:
                self._candidate, self._candidate_count = candidate, 1

            confirmed = candidate != "unknown" and self._candidate_count >= CONFIRM_SAMPLES
            step_delta = None
            if good_steps:
                count = int(steps["value"])
                if (self._prev_steps is not None and self._prev_step_time is not None
                        and self._prev_step_time < now
                        and now - self._prev_step_time <= MAX_STEP_INTERVAL_MS):
                    delta = count - self._prev_steps
                    if 0 <= delta <= MAX_STEP_DELTA:
                        step_delta = delta
                self._prev_steps, self._prev_step_time = count, now
            else:
                self._prev_steps, self._prev_step_time = None, None

            event = {
                "kind": "activity_observation",
                "algorithm": "experimental_threshold_v0",
                "device_id": "watch7",
                "source": "watch7_real",
                "session_id": frame["session_id"],
                "seq": frame["seq"],
                "captured_at_ms": now,
                "wear_state": "on_body",
                "state": candidate if confirmed else "unknown",
                "confirmed": bool(confirmed),
                "motion_score": score,
                "motion_unit": "m/s2",
                "step_delta": step_delta,
                "watch_capture_unchanged": True,
                "robot_action": False,
            }
            self._latest = event
        super().publish(event)
