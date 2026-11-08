"""Watch7 motion Card: company contract, live telemetry, session and quality gates."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from common.vendor_runtime import DriverBundle, make_handler
from device import DeviceStore
from sensors import build_plugins
from transport import make_device_server


class Clock:
    wall = 1_800_000_000_000
    mono = 100_000

    def advance(self, ms):
        self.wall += ms
        self.mono += ms


def device_frame(clock, *, session="s1", seq=1, online=True, collecting=True):
    return {
        "protocol_version": 2, "device_id": "watch7",
        "source": "watch7_real", "kind": "device_state",
        "session_id": session, "seq": seq, "captured_at_ms": clock.wall,
        "wear_state": "on_body", "online": online, "collecting": collecting,
    }


def summary_frame(clock, *, session="s1", seq=1, quality="valid", age=100):
    metric = lambda value, unit: {
        "value": value if quality == "valid" else None,
        "unit": unit, "quality": quality, "provider": "android_sensor",
        "age_ms": age,
    }
    return {
        "protocol_version": 2, "device_id": "watch7",
        "source": "watch7_real", "kind": "summary",
        "session_id": session, "seq": seq, "captured_at_ms": clock.wall,
        "wear_state": "on_body",
        "metrics": {
            "accel_m_s2": metric([0.1, 0.2, 9.8], "m/s2"),
            "gyro_rad_s": metric([0.01, 0.0, 0.0], "rad/s"),
            "motion_score": metric(0.3, "m/s2"),
            "step_count": metric(121, "count"),
            "heart_rate_bpm": {
                "value": 70, "unit": "bpm", "quality": "valid",
                "provider": "health_services", "age_ms": age,
            },
        },
    }


def make_motion(clock):
    bus = {}
    plugins = {}
    def publisher(topic):
        bus[topic] = []
        return bus[topic].append

    store = DeviceStore(
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
        on_metrics=lambda data: plugins["watch_motion"].publish(data),
    )
    bundle = DriverBundle(build_plugins(store, "test_robot", publisher, {
        "plugins": {"watch_motion": {"enabled": True}}
    }))
    plugins.update({p.name: p for p in bundle.plugins})
    return store, bundle, bus


def test_motion_card_company_contract_and_publish_lifecycle():
    clock = Clock()
    store, bundle, bus = make_motion(clock)
    tool = bundle.get_all_tools()[0]
    assert tool["name"] == "watch_motion"
    assert tool["type"] == "sensor"
    assert tool["multiInstance"] is False
    assert tool["topic_out"] == [
        {"topic": "/test_robot/watch7/motion", "format": "data/json"}]
    assert tool["inputSchema"]["x-action-params"]["read"]["params"] == ["metric"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "bad"}) == {
        "ok": False, "reason": "unsupported_metric"}
    caps = bundle.dispatch("watch_motion", {"action": "capabilities"})
    assert set(caps["metrics"]) == {
        "motion_score", "accel_m_s2", "gyro_rad_s", "step_count"}
    assert caps["watch_sampling_controlled"] is False
    assert bundle.dispatch("watch_motion", {"action": "start"})[
        "watch_capture_unchanged"] is True

    assert store.ingest_v2(device_frame(clock))["ok"]
    assert store.ingest_v2(summary_frame(clock))["ok"]
    published = [json.loads(x) for x in bus["/test_robot/watch7/motion"]]
    assert len(published) == 1
    assert set(published[0]["metrics"]) == set(caps["metrics"])
    assert "heart_rate_bpm" not in published[0]["metrics"]
    assert published[0]["metrics"]["step_count"]["unit"] == "count"
    snapshot = bundle.dispatch("watch_motion", {"action": "read", "metric": "accel_m_s2"})
    assert snapshot["value"] == [0.1, 0.2, 9.8]
    assert snapshot["fresh"] and snapshot["source"] == "watch7_real"
    assert snapshot["wear_state"] == "on_body"
    assert bundle.dispatch("watch_motion", {"action": "stop"})[
        "watch_capture_unchanged"] is True
    assert store.ingest_v2(summary_frame(clock, seq=2))["ok"]
    assert len(bus["/test_robot/watch7/motion"]) == 1


def test_motion_data_fail_closed_when_unavailable_or_stale():
    clock = Clock()
    store, bundle, bus = make_motion(clock)
    bundle.start_all()
    assert store.ingest_v2(summary_frame(clock))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "accel_m_s2"})[
        "reason"] == "device_state_unavailable"
    assert not bus["/test_robot/watch7/motion"]

    assert store.ingest_v2(device_frame(clock))["ok"]
    assert store.ingest_v2(summary_frame(clock, seq=2, quality="unavailable"))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "gyro_rad_s"})[
        "reason"] == "metric_unavailable"
    assert not bus["/test_robot/watch7/motion"]
    assert store.ingest_v2(summary_frame(clock, seq=2))["reason"] == "duplicate_or_out_of_order"
    clock.advance(15_001)
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "step_count"})[
        "reason"] == "stale_or_simulated"
    assert store.ingest_v2(summary_frame(clock, seq=3, age=16_000))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "accel_m_s2"})[
        "reason"] == "device_state_unavailable"


def test_new_session_or_idle_watch_cannot_reuse_old_motion():
    clock = Clock()
    store, bundle, bus = make_motion(clock)
    bundle.start_all()
    assert store.ingest_v2(device_frame(clock))["ok"]
    assert store.ingest_v2(summary_frame(clock))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "step_count"})["ok"]
    assert store.ingest_v2(device_frame(clock, session="s2"))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "step_count"})[
        "reason"] == "device_state_mismatch_or_idle"
    assert store.ingest_v2(summary_frame(clock, session="s2"))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "step_count"})["ok"]
    assert store.ingest_v2(device_frame(clock, session="s2", seq=2, collecting=False))["ok"]
    assert bundle.dispatch("watch_motion", {"action": "read", "metric": "step_count"})[
        "reason"] == "device_state_mismatch_or_idle"
    assert store.ingest_v2(summary_frame(clock, session="s2", seq=2))["ok"]
    assert len(bus["/test_robot/watch7/motion"]) == 2


def test_motion_mcp_and_authenticated_telemetry_vertical_slice():
    clock = Clock()
    store, bundle, bus = make_motion(clock)
    bundle.start_all()
    watch_token = "unit-test-" + "x" * 20
    device = make_device_server("127.0.0.1", 0, store, watch_token)
    mcp = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: bundle, "Watch7", "samsung-watch7-driver"),
    )
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (device, mcp)
    ]
    for t in threads:
        t.start()

    def send_watch(frame):
        req = Request(
            f"http://127.0.0.1:{device.server_port}/v2/telemetry",
            data=json.dumps(frame).encode(),
            headers={"X-Wearable-Token": watch_token, "Content-Type": "application/json"},
        )
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    def call_mcp(method, params=None):
        req = Request(
            f"http://127.0.0.1:{mcp.server_port}/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                             "params": params or {}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    try:
        assert send_watch(device_frame(clock))["ok"]
        assert send_watch(summary_frame(clock))["ok"]
        tools = call_mcp("tools/list")["result"]["tools"]
        assert any(x["name"] == "watch_motion" for x in tools)
        response = call_mcp("tools/call", {
            "name": "watch_motion",
            "arguments": {"action": "read", "metric": "gyro_rad_s"},
        })
        result = json.loads(response["result"]["content"][0]["text"])
        assert result["ok"] and result["value"] == [0.01, 0.0, 0.0]
        assert len(bus["/test_robot/watch7/motion"]) == 1
    finally:
        for server in (mcp, device):
            server.shutdown()
            server.server_close()
        for t in threads:
            t.join(timeout=3)
