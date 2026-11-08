"""Metadata-only, consent-gated Watch7 PPG recordings index Card.

Never transfers a waveform or initiates any SDK measurement. Only opted-in
research builds may submit short manifests of recordings stored on the Watch.
"""
from __future__ import annotations

from sensors import SensorPlugin


class RecordingsPlugin(SensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_recordings",
            "Read bounded PPG recording manifests stored only on the Watch. "
            "Disabled by operator/Watch consent unless explicitly opted in.",
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
                               "enum": ["info", "list", "start", "stop"]},
                },
                "required": ["action"],
                "x-action-params": {
                    "info": {"params": []}, "list": {"params": []},
                    "start": {"params": []}, "stop": {"params": []},
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
                "raw_waveforms": False,
                "companion": "research_sdk_not_yet_integrated",
                "operator_and_watch_opt_in_required": True,
            }
        if action == "list":
            return self.device.recordings_snapshot()
        return None

    def publish(self, manifest: dict) -> None:
        if (manifest.get("kind") != "signal_manifest"
                or manifest.get("signal") not in ("ppg_green", "ppg_red", "ppg_ir")
                or manifest.get("source") != "watch7_real"):
            return
        # The ingest callback only runs after session/consent validation.
        # Re-check current consent to avoid publishing revoked metadata.
        snapshot = self.device.recordings_snapshot()
        if (not snapshot.get("ok") or snapshot["session_id"] != manifest["session_id"]):
            return
        allowed = (
            manifest["recording_id"], manifest["signal"],
        )
        if not any((row["recording_id"], row["signal"]) == allowed
                   for row in snapshot["recordings"]):
            return
        safe = {
            "device_id": "watch7", "kind": "signal_manifest",
            "source": "watch7_real", "session_id": manifest["session_id"],
            "seq": manifest["seq"], "recording_id": manifest["recording_id"],
            "signal": manifest["signal"], "sample_count": manifest["sample_count"],
            "sample_rate_hz": manifest["sample_rate_hz"],
            "stored_on_watch": True, "captured_at_ms": manifest["captured_at_ms"],
            "raw_data_transferred": False,
        }
        super().publish(safe)
