"""Company-compliant Watch7 MCP/ROS2 Driver with a separate Watch ingress.

MCP is loopback-only; the authenticated companion data plane is LAN-facing.
No Windows Relay, ADB, Agent Core patches, or research SDK required at runtime.
"""
from __future__ import annotations

import json
import os
import re
import signal
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from common.vendor_runtime import DriverBundle, make_handler, resolve_namespace
from device import DeviceStore
from haptics import HapticQueue
from sensors import build_plugins
from stream_store import StreamStore
from stream_protocol import STREAM_TYPES, KIND_TO_CARD
from transport import make_device_server

DRIVER_ID = "samsung-watch7-driver"


def resolve_mcp_server_name(config: dict) -> str:
    """Disambiguate parallel private lab Drivers in the real Canvas registry.

    The Core deduplicates MCP providers by server_name, even when URL and
    display name differ. The production identifier remains unchanged.
    """
    override = os.environ.get("WATCH7_TEST_MCP_SERVER_NAME", "").strip()
    if not override:
        return config.get("name", DRIVER_ID)
    if os.environ.get("WATCH7_PRIVATE_DOCKER_TEST") != "1":
        raise RuntimeError("Custom MCP server name is restricted to private tests")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", override):
        raise ValueError("Invalid private MCP server name")
    return override


def resolve_mcp_bind_host() -> str:
    """Only allow LAN-visible MCP in an explicitly isolated test network.

    Never publish an unauthenticated MCP port on a public or shared LAN.
    Production deployment should use the default loopback-only endpoint
    and an Agent Core sharing the host network namespace.
    """
    host = os.environ.get("WATCH7_MCP_BIND_HOST", "127.0.0.1").strip()
    if host == "127.0.0.1":
        return host
    if host == "0.0.0.0" and os.environ.get("WATCH7_PRIVATE_DOCKER_TEST") == "1":
        return host
    raise RuntimeError(
        "Non-loopback MCP requires WATCH7_PRIVATE_DOCKER_TEST=1 "
        "and an unpublished Docker-only bridge endpoint"
    )


def resolve_safe_namespace(config: dict) -> str:
    """ROS2 rejects a topic component beginning with a digit (Docker IDs)."""
    namespace = resolve_namespace(config)
    return namespace if re.match(r"^[a-zA-Z_]", namespace) else "watch7_" + namespace


def load_device_token() -> str:
    """Fail closed unless the operator provisioned an external device credential."""
    token = os.environ.get("WATCH7_DEVICE_TOKEN", "")
    filename = os.environ.get("WATCH7_DEVICE_TOKEN_FILE")
    if not token and filename:
        try:
            token = Path(filename).read_text(encoding="utf-8").strip()
        except OSError:
            raise RuntimeError("Watch7 device token file unavailable") from None
    if len(token) < 16:
        raise RuntimeError("Set WATCH7_DEVICE_TOKEN (>=16 characters) or token file")
    return token


def load_config(path: str | None = None) -> dict:
    import yaml

    filename = path or os.environ.get("CONFIG_PATH") or str(
        Path(__file__).with_name("config.yaml")
    )
    with open(filename, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def create_bundle(device: DeviceStore, namespace: str, publish_factory, config: dict,
                  haptic_queue=None, stream_store=None):
    plugins = build_plugins(device, namespace, publish_factory, config,
                            haptic_queue=haptic_queue, stream_store=stream_store)
    return DriverBundle(plugins), {plugin.name: plugin for plugin in plugins}


def run() -> None:
    token = load_device_token()

    # Avoid starting ROS2 or any socket if the required watch credential is missing.
    cfg = load_config()
    namespace = resolve_safe_namespace(cfg)
    mcp_port = int(cfg["mcp_port"])
    ingress_port = int(cfg["ingress_port"])
    if not (15700 <= mcp_port <= 15799 and 1 <= ingress_port <= 65535):
        raise ValueError("invalid MCP or ingress port")
    if mcp_port == ingress_port:
        raise ValueError("MCP and Watch ingress ports must differ")
    mcp_bind_host = resolve_mcp_bind_host()
    server_name = resolve_mcp_server_name(cfg)

    from common import logsafe
    logsafe.install()

    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String

    rclpy.init()
    node = rclpy.create_node("samsung_watch7_driver")
    publishers: dict[str, object] = {}

    def publish_factory(topic: str):
        pub = publishers.setdefault(
            topic, node.create_publisher(String, topic, qos_profile_sensor_data)
        )

        def publish_json(data: str) -> None:
            msg = String()
            msg.data = data
            pub.publish(msg)

        return publish_json

    plugins: dict[str, object] = {}

    def publish_metrics(sample: dict) -> None:
        for name in ("watch_motion", "watch_environment", "watch_vitals",
                     "watch_activity", "watch_orientation"):
            plugin = plugins.get(name)
            if plugin is not None:
                plugin.publish(sample)

    device = DeviceStore(
        allow_recording_manifests=os.environ.get("WATCH7_RECORDING_INDEX_ENABLED") == "1",
        on_manifest=lambda frame: plugins.get("watch_recordings") and plugins["watch_recordings"].publish(frame),
        on_heart=lambda sample: plugins.get("heart_rate") and plugins["heart_rate"].publish(sample),
        on_device=lambda sample: plugins.get("device_state") and plugins["device_state"].publish(sample),
        on_metrics=publish_metrics,
    )
    # Independent, explicit operator gates. Cards are visible even while
    # all sensitive stream ingestion is disabled by the deployment.
    flags = {
        "watch_imu_stream": "WATCH7_IMU_STREAM_ENABLED",
        "watch_ppg_raw": "WATCH7_PPG_RAW_ENABLED",
        "watch_ecg": "WATCH7_ECG_ENABLED",
        "watch_bia": "WATCH7_BIA_ENABLED",
        "watch_location": "WATCH7_LOCATION_ENABLED",
        "watch_audio": "WATCH7_AUDIO_ENABLED",
    }
    stream_store = StreamStore(
        enabled={card for card, flag in flags.items() if os.environ.get(flag) == "1"},
        on_frame=lambda kind, frame: (
            plugins[KIND_TO_CARD[kind]].publish_frame(kind, frame)
            if KIND_TO_CARD[kind] in plugins else None
        ),
    )
    # A remote command requires both this explicit deployment flag and
    # an opt-in heartbeat from a Watch user in the currently active session.
    ack_topic = f"/{namespace}/watch7/haptic_acks"
    ack_publisher = publish_factory(ack_topic)
    haptic_queue = HapticQueue(
        device,
        operator_enabled=os.environ.get("WATCH7_HAPTIC_ACTIONS_ENABLED") == "1",
        on_ack=lambda event: ack_publisher(
            json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        ),
    )
    bundle, plugins = create_bundle(
        device, namespace, publish_factory, cfg, haptic_queue=haptic_queue,
        stream_store=stream_store
    )
    bundle.start_all()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor_thread = threading.Thread(
        target=executor.spin, name="watch7-ros2-executor", daemon=True
    )
    watch_server = None
    mcp_server = None
    ingress_thread = None
    try:
        executor_thread.start()
        watch_server = make_device_server(
            os.environ.get("WATCH7_INGRESS_HOST", cfg.get("ingress_host", "0.0.0.0")),
            ingress_port, device, token, haptics=haptic_queue, streams=stream_store
        )
        mcp_server = ThreadingHTTPServer(
            (mcp_bind_host, mcp_port),
            make_handler(lambda: bundle, server_name, DRIVER_ID),
        )
        ingress_thread = threading.Thread(
            target=watch_server.serve_forever, name="watch7-http", daemon=True
        )
        ingress_thread.start()

        # Follow the company's existing registry contract. The connection stays
        # host-local and does not transmit the Watch device token.
        if os.environ.get("WATCH7_REGISTER_AGENT_CORE", "1") == "1":
            from common.vendor_runtime import start_registration
            start_registration(mcp_port, cfg, DRIVER_ID)

        stop_event = threading.Event()

        def stop(_signum, _frame):
            if not stop_event.is_set():
                stop_event.set()
                threading.Thread(target=mcp_server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        print(
            f"[watch7] mcp={mcp_bind_host}:{mcp_port}, ingress={watch_server.server_address}, "
            f"namespace={namespace}; cards={','.join(sorted(plugins))}",
            flush=True,
        )
        mcp_server.serve_forever(poll_interval=0.1)
    finally:
        if watch_server is not None:
            watch_server.shutdown()
            watch_server.server_close()
        if ingress_thread is not None:
            ingress_thread.join(timeout=3)
        if mcp_server is not None:
            mcp_server.server_close()
        bundle.stop_all()
        executor.shutdown(timeout_sec=2)
        if executor_thread.is_alive():
            executor_thread.join(timeout=3)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    run()
