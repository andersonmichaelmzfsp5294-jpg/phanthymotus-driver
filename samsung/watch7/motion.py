"""Read-only Watch7 motion Sensor Card.

The subscription is owned by the user-started Watch foreground service;
Card start/stop only controls ROS2 publication.
"""
from __future__ import annotations

from metric_cards import MetricSensorPlugin

MOTION_METRICS = ("motion_score", "accel_m_s2", "gyro_rad_s", "step_count")


class MotionPlugin(MetricSensorPlugin):
    def __init__(self, device, topic: str, ros_publisher):
        super().__init__(
            "watch_motion",
            "Read fresh Watch7 motion metrics (IMU, motion score, cumulative steps). "
            "Does not remotely start sensor acquisition or infer activity diagnoses.",
            MOTION_METRICS, device, topic, ros_publisher,
        )
