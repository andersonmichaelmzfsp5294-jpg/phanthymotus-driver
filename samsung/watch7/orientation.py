"""Read-only Watch7 orientation and gravity Sensor Card.

Returns public Android SensorManager vectors exactly as reported.
No Euler conversion, world-frame calibration, heading, gesture inference,
or new physical sensor subscription is performed by the Driver.
"""
from __future__ import annotations

from metric_cards import MetricSensorPlugin

ORIENTATION_METRICS = (
    "rotation_vector",
    "game_rotation_vector",
    "geomagnetic_rotation_vector",
    "gravity_m_s2",
    "linear_accel_m_s2",
)


class OrientationPlugin(MetricSensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_orientation",
            "Read fresh Watch7 Android orientation vectors, gravity and "
            "linear acceleration. Raw sensor-frame output only; no heading "
            "calibration, Euler conversion or gesture inference.",
            ORIENTATION_METRICS, device, topic, ros_publisher,
            required_provider="android_sensor",
        )
