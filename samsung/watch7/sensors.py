"""Watch7 Sensor Card factory for base, motion and environment metrics."""
from __future__ import annotations

import json
import threading


class SensorPlugin:
    def __init__(self, name: str, description: str, topic: str, device, ros_publisher):
        self.name = name
        self.description = description
        self.topic = topic
        self.device = device
        self._publisher = ros_publisher
        self._lock = threading.Lock()
        self._running = False

    def get_tool(self) -> dict:
        return {
            "name": self.name, "type": "sensor", "multiInstance": False,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["info", "start", "stop"]}},
                "required": ["action"],
            },
            "topic_out": [{"topic": self.topic, "format": "data/json"}],
        }

    def start(self) -> None:
        with self._lock:
            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def dispatch(self, action: str, args: dict) -> dict | None:
        if action == "start":
            self.start()
            return {"state": "running"}
        if action == "stop":
            self.stop()
            return {"state": "idle"}
        if action == "info":
            with self._lock:
                running = self._running
            snapshot = (self.device.heart_snapshot() if self.name == "heart_rate"
                        else self.device.device_snapshot())
            return {
                "state": "running" if running else "idle",
                "device": "watch7", "snapshot": snapshot,
                "topic_out": [{"topic": self.topic, "format": "data/json"}],
                "on_watch_sampling_required": True,
            }
        return None

    def publish(self, event: dict) -> None:
        # Hold the same lifecycle lock through publication: no message can be
        # emitted after stop() has returned, including from concurrent HTTP calls.
        with self._lock:
            if not self._running:
                return
            self._publisher(json.dumps(event, separators=(",", ":"), ensure_ascii=False))


def build_plugins(device, namespace: str, publish_factory, config: dict,
                  haptic_queue=None) -> list:
    plugins = []
    specs = [
        ("heart_rate", "Live Watch7 heart-rate Sensor. No data is invented when offline.",
         f"/{namespace}/watch7/heart_rate"),
        ("device_state", "Watch7 device heartbeat, wear state, and battery.",
         f"/{namespace}/watch7/device_state"),
    ]
    enabled = config.get("plugins", {})
    for name, description, topic in specs:
        if enabled.get(name, {}).get("enabled", False):
            plugins.append(SensorPlugin(name, description, topic, device, publish_factory(topic)))
    if enabled.get("watch_motion", {}).get("enabled", False):
        # Import at construction time so the reusable base SensorPlugin can
        # also be imported directly without a circular module import.
        from motion import MotionPlugin
        topic = f"/{namespace}/watch7/motion"
        plugins.append(MotionPlugin(device, topic, publish_factory(topic)))
    if enabled.get("watch_environment", {}).get("enabled", False):
        from environment import EnvironmentPlugin
        topic = f"/{namespace}/watch7/environment"
        plugins.append(EnvironmentPlugin(device, topic, publish_factory(topic)))
    if enabled.get("watch_vitals", {}).get("enabled", False):
        from vitals import VitalsPlugin
        topic = f"/{namespace}/watch7/vitals"
        plugins.append(VitalsPlugin(device, topic, publish_factory(topic)))
    if enabled.get("watch_orientation", {}).get("enabled", False):
        from orientation import OrientationPlugin
        topic = f"/{namespace}/watch7/orientation"
        plugins.append(OrientationPlugin(device, topic, publish_factory(topic)))
    if enabled.get("watch_activity", {}).get("enabled", False):
        from activity import ActivityPlugin
        topic = f"/{namespace}/watch7/activity"
        plugins.append(ActivityPlugin(device, topic, publish_factory(topic)))
    if enabled.get("watch_recordings", {}).get("enabled", False):
        from recordings import RecordingsPlugin
        topic = f"/{namespace}/watch7/recordings"
        plugins.append(RecordingsPlugin(device, topic, publish_factory(topic)))
    if enabled.get("watch_haptic", {}).get("enabled", False) and haptic_queue is not None:
        from haptics import HapticPlugin
        topic = f"/{namespace}/watch7/haptic_acks"
        plugins.append(HapticPlugin(haptic_queue, topic))
    return plugins
