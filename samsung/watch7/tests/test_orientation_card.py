"""Watch7 orientation Card: standard schema, sensor quality, session and MCP."""
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


def heartbeat(clock, session="s1", wear="on_body", seq=1, collecting=True):
    return {
        "protocol_version": 2, "device_id": "watch7", "kind": "device_state",
        "source": "watch7_real", "session_id": session, "seq": seq,
        "captured_at_ms": clock.wall, "wear_state": wear, "online": True,
        "collecting": collecting,
    }


def vector(values, unit, provider="android_sensor", quality="valid", age=50):
    return {
        "value": values if quality == "valid" else None, "unit": unit,
        "quality": quality, "provider": provider, "age_ms": age,
    }


def summary(clock, seq=1, session="s1", wear="on_body", metrics=None):
    return {
        "protocol_version": 2, "device_id": "watch7", "kind": "summary",
        "source": "watch7_real", "session_id": session, "seq": seq,
        "captured_at_ms": clock.wall, "wear_state": wear,
        "metrics": metrics if metrics is not None else {
            "rotation_vector": vector([0.01, 0.02, 0.03, 0.999], "unitless"),
            "game_rotation_vector": vector([0.01, 0.02, 0.03, 0.999], "unitless"),
            "geomagnetic_rotation_vector": vector([0.01, 0.02, 0.03], "unitless"),
            "gravity_m_s2": vector([0.0, 0.0, 9.8], "m/s2"),
            "linear_accel_m_s2": vector([0.1, 0.0, 0.0], "m/s2"),
            "heart_rate_bpm": {
                "value": 75 if wear == "on_body" else None, "unit": "bpm",
                "quality": "valid" if wear == "on_body" else "off_body",
                "provider": "health_services", "age_ms": 50,
            },
        },
    }


def fixture(clock):
    out = {}
    callbacks = {}
    def make_publisher(topic):
        out[topic] = []
        return out[topic].append
    device = DeviceStore(
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
        on_metrics=lambda event: callbacks["watch_orientation"].publish(event),
    )
    bundle = DriverBundle(build_plugins(device, "lab", make_publisher, {
        "plugins": {"watch_orientation": {"enabled": True}}
    }))
    callbacks.update({p.name: p for p in bundle.plugins})
    bundle.start_all()
    return device, bundle, out["/lab/watch7/orientation"]


def read(bundle, metric):
    return bundle.dispatch("watch_orientation", {
        "action": "read", "metric": metric,
    })


def test_orientation_card_contract_and_capabilities():
    clock = Clock()
    device, bundle, bus = fixture(clock)
    schema = bundle.get_all_tools()[0]
    assert schema["name"] == "watch_orientation"
    assert schema["type"] == "sensor"
    assert schema["topic_out"] == [
        {"topic": "/lab/watch7/orientation", "format": "data/json"}]
    caps = bundle.dispatch("watch_orientation", {"action": "capabilities"})
    assert caps["required_provider"] == "android_sensor"
    assert set(caps["metrics"]) == {
        "rotation_vector", "game_rotation_vector",
        "geomagnetic_rotation_vector", "gravity_m_s2", "linear_accel_m_s2"}
    assert read(bundle, "temperature")["reason"] == "unsupported_metric"
    assert bundle.dispatch("watch_orientation", {"action": "info"})["read_only"]


def test_real_protocol_summary_is_filtered_and_preserves_vector_shape():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(heartbeat(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    values = json.loads(bus[0])["metrics"]
    assert len(bus) == 1
    assert set(values) == {
        "rotation_vector", "game_rotation_vector",
        "geomagnetic_rotation_vector", "gravity_m_s2", "linear_accel_m_s2",
    }
    assert "heart_rate_bpm" not in values
    assert values["rotation_vector"]["value"] == [0.01, 0.02, 0.03, 0.999]
    assert values["geomagnetic_rotation_vector"]["value"] == [0.01, 0.02, 0.03]
    assert read(bundle, "gravity_m_s2")["value"] == [0.0, 0.0, 9.8]
    assert "heading" not in bus[0] and "euler" not in bus[0]


def test_invalid_provider_quality_replay_and_staleness_fail_closed():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(heartbeat(clock))["ok"]
    values = {
        "rotation_vector": vector(
            [0.1, 0.2, 0.3, 0.9], "unitless", provider="system"),
        "gravity_m_s2": vector(
            [0, 0, 9.8], "m/s2", quality="unavailable"),
    }
    assert store.ingest_v2(summary(clock, metrics=values))["ok"]
    assert read(bundle, "rotation_vector")["reason"] == "provider_not_supported"
    assert read(bundle, "gravity_m_s2")["reason"] == "metric_unavailable"
    assert not bus
    assert store.ingest_v2(summary(clock))["reason"] == "duplicate_or_out_of_order"
    invalid = summary(clock, seq=2, metrics={
        "rotation_vector": vector([1, 2], "unitless"),
    })
    assert store.ingest_v2(invalid)["reason"] == "bad_input"
    clock.advance(15_001)
    assert read(bundle, "rotation_vector")["reason"] == "stale_or_simulated"


def test_orientation_off_wrist_context_is_not_falsely_worn_and_session_gate():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(heartbeat(clock, wear="off_body"))["ok"]
    assert store.ingest_v2(summary(clock, wear="off_body"))["ok"]
    snap = read(bundle, "rotation_vector")
    # An Android sensor may report off wrist, so expose the state explicitly.
    assert snap["ok"] and snap["wear_state"] == "off_body"
    assert store.ingest_v2(heartbeat(clock, session="s2"))["ok"]
    assert read(bundle, "rotation_vector")["reason"] == "device_state_mismatch_or_idle"
    assert store.ingest_v2(summary(clock, session="s2"))["ok"]
    assert read(bundle, "rotation_vector")["ok"]
    assert store.ingest_v2(heartbeat(clock, session="s2", seq=2,
                                     collecting=False))["ok"]
    assert read(bundle, "gravity_m_s2")["reason"] == "device_state_mismatch_or_idle"


def test_stop_gates_ros_messages_but_not_wearable_sensor_sampling():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(heartbeat(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    assert bundle.dispatch("watch_orientation", {"action": "stop"}) == {
        "state": "idle", "watch_capture_unchanged": True,
    }
    assert store.ingest_v2(summary(clock, seq=2))["ok"]
    assert len(bus) == 1
    assert read(bundle, "rotation_vector")["ok"]


def test_authenticated_ingress_to_real_mcp_endpoint():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    key = "test-fixture-" + "x" * 20
    ingress = make_device_server("127.0.0.1", 0, store, key)
    mcp = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: bundle, "Watch7", "samsung-watch7-driver"),
    )
    workers = [threading.Thread(target=s.serve_forever, daemon=True)
               for s in (ingress, mcp)]
    for t in workers:
        t.start()

    def send(url, body, watch=False):
        headers = {"Content-Type": "application/json"}
        if watch:
            headers["X-Wearable-Token"] = key
        req = Request(url, data=json.dumps(body).encode(), headers=headers)
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    try:
        uri = f"http://127.0.0.1:{ingress.server_port}/v2/telemetry"
        assert send(uri, heartbeat(clock), watch=True)["ok"]
        assert send(uri, summary(clock), watch=True)["ok"]
        response = send(f"http://127.0.0.1:{mcp.server_port}/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "watch_orientation",
                       "arguments": {"action": "read", "metric": "gravity_m_s2"}},
        })
        answer = json.loads(response["result"]["content"][0]["text"])
        assert answer["ok"] and answer["value"] == [0.0, 0.0, 9.8]
        assert len(bus) == 1
    finally:
        for server in (mcp, ingress):
            server.shutdown()
            server.server_close()
        for t in workers:
            t.join(timeout=3)
