"""Read-only Watch7 environmental Sensor Card.

All measurements are optional Android SensorManager signals, not remote
physical environment controls or medically meaningful measurements.
"""
from __future__ import annotations

from metric_cards import MetricSensorPlugin

ENVIRONMENT_METRICS = ("pressure_hpa", "light_lux", "magnetic_field_uT")


class EnvironmentPlugin(MetricSensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_environment",
            "Read fresh Watch7 ambient pressure, light and magnetic field. "
            "No weather forecast, absolute altitude or compass heading is inferred.",
            ENVIRONMENT_METRICS, device, topic, ros_publisher,
        )
