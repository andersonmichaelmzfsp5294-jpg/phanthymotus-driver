"""Shared read-only Card contract for fresh Watch7 v2 metric summaries.

The watch alone owns sensor subscriptions and consent. These Cards expose
the validated DeviceStore state and gate only ROS2 publication.
"""
from __future__ import annotations

from sensors import SensorPlugin


class MetricSensorPlugin(SensorPlugin):
    """Sensor Card for a fixed set of quality-checked v2 metrics."""

    def __init__(self, name: str, description: str, metrics: tuple[str, ...],
                 device, topic: str, ros_publisher, *, required_provider=None):
        if not metrics or len(metrics) != len(set(metrics)):
            raise ValueError("metric names must be nonempty and unique")
        super().__init__(name, description, topic, device, ros_publisher)
        self.metrics = metrics
        self.required_provider = required_provider

    def get_tool(self) -> dict:
        return {
            "name": self.name, "type": "sensor", "multiInstance": False,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["info", "start", "stop", "read", "capabilities"]},
                    "metric": {"type": "string", "enum": list(self.metrics)},
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
                "ok": True,
                "metrics": {name: declared[name] for name in self.metrics
                            if name in declared},
                "availability": "requires_fresh_device_heartbeat_and_valid_metric",
                "watch_sampling_controlled": False,
                "required_provider": self.required_provider,
            }
        if action == "read":
            metric = args.get("metric")
            if metric not in self.metrics:
                return {"ok": False, "reason": "unsupported_metric"}
            snapshot = self.device.metric_snapshot(metric)
            if (snapshot.get("ok") and self.required_provider is not None
                    and snapshot["provider"] != self.required_provider):
                return {"ok": False, "reason": "provider_not_supported"}
            return snapshot
        return None

    def publish(self, frame: dict) -> None:
        """Exclude every metric outside this Card's fixed allowlist.

        Values are never taken directly from callback frames: fresh DeviceStore
        snapshots provide one source of truth for quality, TTL and session gates.
        """
        if frame.get("kind") != "summary" or frame.get("source") != "watch7_real":
            return
        accepted = {}
        for name in self.metrics:
            snap = self.device.metric_snapshot(name)
            if (not snap.get("ok") or snap["session_id"] != frame.get("session_id")
                    or snap["seq"] != frame.get("seq")
                    or (self.required_provider is not None
                        and snap["provider"] != self.required_provider)):
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
