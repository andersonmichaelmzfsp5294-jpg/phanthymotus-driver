"""Read-only, quality-gated Watch7 motion Sensor Card.

Subscriptions belong to the on-Watch foreground service. Card start/stop
only controls the Driver's ROS2 publishing lifecycle.
"""
from __future__ import annotations

from sensors import SensorPlugin

MOTION_METRICS = ("motion_score", "accel_m_s2", "gyro_rad_s", "step_count")


class MotionPlugin(SensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_motion",
            "Read fresh Watch7 motion metrics (IMU, motion score, cumulative steps). "
            "Does not remotely start sensor acquisition or infer activity diagnoses.",
            topic, device, ros_publisher,
        )

    def get_tool(self) -> dict:
        return {
            "name": self.name, "type": "sensor", "multiInstance": False,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["info", "start", "stop", "read", "capabilities"]},
                    "metric": {"type": "string", "enum": list(MOTION_METRICS)},
                },
                "required": ["action"],
                "x-action-params": {
                    "info": {"params": []},
                    "start": {"params": [],
                              "description": "Starts ROS2 publishing only, not Watch sampling."},
                    "stop": {"params": [],
                             "description": "Stops ROS2 publishing only, not Watch sampling."},
                    "read": {"params": ["metric"]},
                    "capabilities": {"params": []},
                },
            },
            "topic_out": [{"topic": self.topic, "format": "data/json"}],
        }

    def dispatch(self, action: str, args: dict) -> dict | None:
        if action in ("start", "stop"):
            return {**super().dispatch(action, args), "watch_capture_unchanged": True}
        if action == "info":
            with self._lock:
                running = self._running
            return {
                "state": "running" if running else "idle",
                "device": "watch7", "read_only": True,
                "on_watch_sampling_required": True,
                "topic_out": [{"topic": self.topic, "format": "data/json"}],
            }
        if action == "capabilities":
            declared = self.device.metric_capabilities()
            return {
                "ok": True, "metrics": {name: declared[name] for name in MOTION_METRICS},
                "availability": "requires_fresh_device_heartbeat_and_valid_metric",
                "watch_sampling_controlled": False,
            }
        if action == "read":
            metric = args.get("metric")
            if metric not in MOTION_METRICS:
                return {"ok": False, "reason": "unsupported_metric"}
            return self.device.metric_snapshot(metric)
        return None

    def publish(self, frame: dict) -> None:
        """Publish only fresh motion values from this exact session and sequence.

        Never forward the full v2 summary: it may contain HR or other health
        metrics unrelated to this Card.
        """
        if frame.get("kind") != "summary" or frame.get("source") != "watch7_real":
            return
        accepted = {}
        for name in MOTION_METRICS:
            snap = self.device.metric_snapshot(name)
            if (not snap.get("ok") or snap["session_id"] != frame.get("session_id")
                    or snap["seq"] != frame.get("seq")):
                continue
            accepted[name] = {
                "value": snap["value"], "unit": snap["unit"],
                "quality": snap["quality"], "provider": snap["provider"],
                "age_ms": snap["sensor_age_ms"],
            }
        if not accepted:
            return
        event = {
            "protocol_version": 2, "kind": "summary",
            "device_id": frame["device_id"], "source": frame["source"],
            "session_id": frame["session_id"], "seq": frame["seq"],
            "captured_at_ms": frame["captured_at_ms"],
            "wear_state": frame["wear_state"],
            "metrics": accepted,
        }
        super().publish(event)
