"""Read-only Watch7 advanced vital-sign summary Card.

The Standard companion does not collect IBI, SpO2 or skin temperature.
No remote measurement, raw PPG waveform, HRV or diagnosis is offered.
"""
from __future__ import annotations

from metric_cards import MetricSensorPlugin

VITAL_METRICS = ("ibi_ms", "spo2_pct", "skin_temperature_c")


class VitalsPlugin(MetricSensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_vitals",
            "Read bounded, current on-wrist Samsung SDK IBI, SpO2 and skin "
            "temperature scalar summaries only. Research companion integration "
            "and wearer-started measurements required; no diagnostic claims.",
            VITAL_METRICS, device, topic, ros_publisher,
        )

    def dispatch(self, action: str, args: dict) -> dict | None:
        result = super().dispatch(action, args)
        if action == "capabilities" and isinstance(result, dict):
            result.update(
                ok=True,
                companion="research_sdk_not_yet_integrated",
                raw_waveforms=False,
                watch_user_measurement_required=True,
                diagnostic_use=False,
            )
        return result
