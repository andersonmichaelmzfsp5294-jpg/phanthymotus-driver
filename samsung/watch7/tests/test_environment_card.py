"""Company-compatible environment Card contracts and v2 telemetry integration."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from common.vendor_runtime import DriverBundle, make_handler
from device import DeviceStore
from sensors import build_plugins
from transport import make_device_server


class Clock:
    def __init__(self):
        self.wall = 1_800_000_000_000
        self.mono = 100_000

    def advance(self, ms):
        self.wall += ms
        self.mono += ms


def device_frame(clock, *, session="s1", seq=1, wear="on_body", online=True, collecting=True):
    return {
        "protocol_version": 2, "device_id": "watch7",
        "source": "watch7_real", "kind": "device_state",
        "session_id": session, "seq": seq, "captured_at_ms": clock.wall,
        "wear_state": wear, "online": online, "collecting": collecting,
    }


def metric(value, unit, *, quality="valid", age=50):
    return {
        "value": value if quality == "valid" else None,
        "unit": unit, "quality": quality,
        "provider": "android_sensor", "age_ms": age,
    }


def summary(clock, *, seq=1, session="s1", wear="on_body", values=None):
    return {
        "protocol_version": 2, "device_id": "watch7", "source": "watch7_real",
        "kind": "summary", "session_id": session, "seq": seq,
        "captured_at_ms": clock.wall, "wear_state": wear,
        "metrics": values if values is not None else {
            "pressure_hpa": metric(1012.8, "hPa"),
            "light_lux": metric(7.0, "lx"),
            "magnetic_field_uT": metric([18.0, -5.0, 30.0], "uT"),
            "accel_m_s2": metric([0.0, 0.0, 9.8], "m/s2"),
            "heart_rate_bpm": {
                "value": 72, "unit": "bpm", "quality": "valid",
                "provider": "health_services", "age_ms": 50,
            },
        },
    }


def make_fixture(clock, *, include_motion=False):
    emitted, plugins = {}, {}

    def publisher(topic):
        emitted[topic] = []
        return emitted[topic].append

    def dispatch_metrics(frame):
        for name in ("watch_motion", "watch_environment"):
            if name in plugins:
                plugins[name].publish(frame)

    store = DeviceStore(
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
        on_metrics=dispatch_metrics,
    )
    config = {"plugins": {"watch_environment": {"enabled": True}}}
    if include_motion:
        config["plugins"]["watch_motion"] = {"enabled": True}
    bundle = DriverBundle(build_plugins(store, "robot_lab", publisher, config))
    plugins.update({p.name: p for p in bundle.plugins})
    return store, bundle, emitted


def test_environment_schema_capabilities_and_lifecycle():
    clock = Clock()
    store, bundle, bus = make_fixture(clock)
    tool = bundle.get_all_tools()[0]
    assert tool["name"] == "watch_environment"
    assert tool["type"] == "sensor"
    assert tool["multiInstance"] is False
    assert tool["topic_out"] == [{
        "topic": "/robot_lab/watch7/environment", "format": "data/json"}]
    assert tool["inputSchema"]["properties"]["metric"]["enum"] == [
        "pressure_hpa", "light_lux", "magnetic_field_uT"]
    assert tool["inputSchema"]["x-action-params"]["read"]["params"] == ["metric"]
    assert bundle.dispatch("watch_environment", {"action": "read", "metric": "ecg"}) == {
        "ok": False, "reason": "unsupported_metric"}
    caps = bundle.dispatch("watch_environment", {"action": "capabilities"})
    assert caps["watch_sampling_controlled"] is False
    assert set(caps["metrics"]) == {"pressure_hpa", "light_lux", "magnetic_field_uT"}

    assert bundle.dispatch("watch_environment", {"action": "info"})["read_only"]
    result = bundle.dispatch("watch_environment", {"action": "start"})
    assert result == {"state": "running", "watch_capture_unchanged": True}
    assert store.ingest_v2(device_frame(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]

    published = [json.loads(s) for s in bus["/robot_lab/watch7/environment"]]
    assert len(published) == 1
    assert set(published[0]["metrics"]) == set(caps["metrics"])
    assert "heart_rate_bpm" not in published[0]["metrics"]
    assert "accel_m_s2" not in published[0]["metrics"]
    assert published[0]["metrics"]["magnetic_field_uT"]["value"] == [18, -5, 30]
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "pressure_hpa",
    })["value"] == 1012.8

    assert bundle.dispatch("watch_environment", {"action": "stop"})[
        "watch_capture_unchanged"] is True
    assert store.ingest_v2(summary(clock, seq=2))["ok"]
    assert len(bus["/robot_lab/watch7/environment"]) == 1


def test_environment_invalid_missing_stale_and_unavailable_values_not_fabricated():
    clock = Clock()
    store, bundle, bus = make_fixture(clock)
    bundle.start_all()
    assert store.ingest_v2(summary(clock))["ok"]
    assert not bus["/robot_lab/watch7/environment"]
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "light_lux",
    })["reason"] == "device_state_unavailable"

    assert store.ingest_v2(device_frame(clock))["ok"]
    values = {
        "pressure_hpa": metric(1010.0, "hPa", quality="unavailable"),
        "light_lux": metric(12.0, "lx", age=20_000),
        "magnetic_field_uT": metric([10, 20, 30], "uT", quality="low_confidence"),
    }
    assert store.ingest_v2(summary(clock, seq=2, values=values))["ok"]
    assert not bus["/robot_lab/watch7/environment"]
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "pressure_hpa",
    })["reason"] == "metric_unavailable"
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "light_lux",
    })["reason"] == "stale_or_simulated"

    assert store.ingest_v2(summary(clock, seq=2))["reason"] == "duplicate_or_out_of_order"
    invalid = summary(clock, seq=3, values={
        "light_lux": metric(-1.0, "lx"),
    })
    assert store.ingest_v2(invalid)["reason"] == "bad_input"
    assert not bus["/robot_lab/watch7/environment"]


def test_environment_off_body_allowed_but_session_change_and_idle_block():
    clock = Clock()
    store, bundle, bus = make_fixture(clock)
    bundle.start_all()
    assert store.ingest_v2(device_frame(clock, wear="off_body"))["ok"]
    # Ambient measurements do not require an on-wrist physiological reading.
    assert store.ingest_v2(summary(clock, wear="off_body", values={
        "light_lux": metric(3.0, "lx"),
    }))["ok"]
    got = bundle.dispatch("watch_environment", {
        "action": "read", "metric": "light_lux",
    })
    assert got["ok"] and got["wear_state"] == "off_body"

    assert store.ingest_v2(device_frame(clock, session="new"))["ok"]
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "light_lux",
    })["reason"] == "device_state_mismatch_or_idle"
    before = len(bus["/robot_lab/watch7/environment"])
    assert store.ingest_v2(summary(clock, seq=1, session="new", values={
        "light_lux": metric(5.0, "lx"),
    }))["ok"]
    assert len(bus["/robot_lab/watch7/environment"]) == before + 1
    assert store.ingest_v2(device_frame(clock, session="new", seq=2, collecting=False))["ok"]
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "light_lux",
    })["reason"] == "device_state_mismatch_or_idle"
    assert store.ingest_v2(summary(clock, seq=2, session="new", values={
        "light_lux": metric(6.0, "lx"),
    }))["ok"]
    assert len(bus["/robot_lab/watch7/environment"]) == before + 1

    clock.advance(15_001)
    assert bundle.dispatch("watch_environment", {
        "action": "read", "metric": "light_lux",
    })["reason"] == "stale_or_simulated"


def test_mcp_http_ingress_and_two_card_topic_isolation():
    import time

    clock = Clock()
    # Use current real time for HTTP ingress; no Watch is attached.
    clock.wall = int(time.time() * 1000)
    store, bundle, bus = make_fixture(clock, include_motion=True)
    bundle.start_all()
    token = "unit-test-" + "q" * 20
    ingress = make_device_server("127.0.0.1", 0, store, token)
    mcp = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: bundle, "Watch7", "samsung-watch7-driver"),
    )
    threads = [
        threading.Thread(target=s.serve_forever, daemon=True)
        for s in (ingress, mcp)
    ]
    for t in threads:
        t.start()

    def call(url, body, headers):
        request = Request(
            url, data=json.dumps(body).encode(), headers=headers,
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    try:
        base = f"http://127.0.0.1:{ingress.server_port}/v2/telemetry"
        head = {"Content-Type": "application/json", "X-Wearable-Token": token}
        assert call(base, device_frame(clock), head)["ok"]
        assert call(base, summary(clock), head)["ok"]

        tools_url = f"http://127.0.0.1:{mcp.server_port}/mcp"
        mcp_head = {"Content-Type": "application/json"}
        tools = call(tools_url, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        }, mcp_head)["result"]["tools"]
        assert {t["name"] for t in tools} == {"watch_motion", "watch_environment"}
        response = call(tools_url, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "watch_environment",
                       "arguments": {"action": "read", "metric": "magnetic_field_uT"}},
        }, mcp_head)
        result = json.loads(response["result"]["content"][0]["text"])
        assert result["ok"] and result["value"] == [18, -5, 30]

        env = json.loads(bus["/robot_lab/watch7/environment"][-1])
        motion = json.loads(bus["/robot_lab/watch7/motion"][-1])
        assert set(env["metrics"]) == {"pressure_hpa", "light_lux", "magnetic_field_uT"}
        assert set(motion["metrics"]) == {"accel_m_s2"}
        assert env["session_id"] == motion["session_id"]
    finally:
        for server in (mcp, ingress):
            server.shutdown()
            server.server_close()
        for t in threads:
            t.join(timeout=3)
