"""Activity Processor contract, no false activity, ROS/MCP through actual handlers."""
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
        self.wall, self.mono = 1_800_000_000_000, 100_000

    def advance(self, ms):
        self.wall += ms
        self.mono += ms


def hb(clock, session="s1", seq=1, wear="on_body", collecting=True):
    return {
        "protocol_version": 2, "kind": "device_state", "device_id": "watch7",
        "source": "watch7_real", "session_id": session,
        "seq": seq, "captured_at_ms": clock.wall, "wear_state": wear,
        "online": True, "collecting": collecting,
    }


def summary(clock, seq=1, score=0.1, steps=100, session="s1",
            wear="on_body", age=10, provider="android_sensor"):
    metrics = {
        "motion_score": {
            "value": score, "unit": "m/s2", "quality": "valid",
            "provider": provider, "age_ms": age,
        },
    }
    if steps is not None:
        metrics["step_count"] = {
            "value": steps, "unit": "count", "quality": "valid",
            "provider": "android_sensor", "age_ms": age,
        }
    return {
        "protocol_version": 2, "kind": "summary", "device_id": "watch7",
        "source": "watch7_real", "session_id": session,
        "seq": seq, "captured_at_ms": clock.wall, "wear_state": wear,
        "metrics": metrics,
    }


def fixture(clock):
    bus, plugins = {}, {}
    def publisher(topic):
        bus[topic] = []
        return bus[topic].append
    device = DeviceStore(
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
        on_metrics=lambda x: plugins["watch_activity"].publish(x),
    )
    bundle = DriverBundle(build_plugins(device, "test", publisher, {
        "plugins": {"watch_activity": {"enabled": True}}
    }))
    plugins.update({x.name: x for x in bundle.plugins})
    bundle.start_all()
    return device, bundle, bus["/test/watch7/activity"]


def read(bundle):
    return bundle.dispatch("watch_activity", {"action": "read"})


def test_company_processor_contract_and_declared_limits():
    clock = Clock()
    device, bundle, bus = fixture(clock)
    meta = bundle.get_all_tools()[0]
    assert meta["name"] == "watch_activity"
    assert meta["type"] == "processor"
    assert meta["multiInstance"] is False
    assert meta["topic_out"] == [{"topic": "/test/watch7/activity", "format": "data/json"}]
    assert set(meta["inputSchema"]["x-action-params"]) == {
        "info", "start", "stop", "read", "capabilities"}
    caps = bundle.dispatch("watch_activity", {"action": "capabilities"})
    assert caps["states"] == ["still", "moving", "unknown"]
    assert not caps["clinical_or_safety_validated"]
    assert not caps["fall_detection"] and not caps["robot_action"]
    assert bundle.dispatch("watch_activity", {"action": "info"})["read_only"]
    assert read(bundle)["reason"] == "no_v2_summary"


def test_classification_requires_two_consecutive_fresh_samples():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(hb(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    first = read(bundle)
    assert first["ok"] and first["state"] == "unknown"
    assert not first["confirmed"] and first["step_delta"] is None
    clock.advance(1000)
    assert store.ingest_v2(summary(clock, seq=2, steps=102))["ok"]
    second = read(bundle)
    assert second["confirmed"] and second["state"] == "still"
    assert second["step_delta"] == 2

    clock.advance(1000)
    assert store.ingest_v2(summary(clock, seq=3, score=0.9, steps=110))["ok"]
    assert read(bundle)["state"] == "unknown"
    clock.advance(1000)
    assert store.ingest_v2(summary(clock, seq=4, score=0.8, steps=113))["ok"]
    moving = read(bundle)
    assert moving["state"] == "moving" and moving["confirmed"]
    assert moving["step_delta"] == 3

    clock.advance(1000)
    assert store.ingest_v2(summary(clock, seq=5, score=0.3, steps=114))["ok"]
    assert read(bundle)["state"] == "unknown"
    assert len(bus) == 5
    assert all(json.loads(item)["robot_action"] is False for item in bus)


def test_step_delta_is_optional_and_rejects_counter_reset():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(hb(clock))["ok"]
    assert store.ingest_v2(summary(clock, steps=100))["ok"]
    clock.advance(2000)
    assert store.ingest_v2(summary(clock, seq=2, steps=10))["ok"]
    assert read(bundle)["step_delta"] is None
    clock.advance(2000)
    assert store.ingest_v2(summary(clock, seq=3, steps=500))["ok"]
    assert read(bundle)["step_delta"] is None
    clock.advance(2000)
    assert store.ingest_v2(summary(clock, seq=4, steps=None))["ok"]
    assert read(bundle)["step_delta"] is None
    clock.advance(2000)
    assert store.ingest_v2(summary(clock, seq=5, steps=550))["ok"]
    assert read(bundle)["step_delta"] is None


def test_fail_closed_when_offbody_idle_stale_or_not_from_android_sensor():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(summary(clock))["ok"]
    assert not bus
    assert read(bundle)["reason"] == "device_state_unavailable"
    assert store.ingest_v2(hb(clock, wear="off_body"))["ok"]
    assert store.ingest_v2(summary(clock, seq=2, wear="off_body"))["ok"]
    assert not bus
    assert read(bundle)["reason"] == "watch_not_worn_or_idle"
    assert store.ingest_v2(hb(clock, seq=2, wear="on_body"))["ok"]
    assert store.ingest_v2(summary(clock, seq=3, provider="samsung_sdk"))["ok"]
    assert not bus
    assert read(bundle)["reason"] == "activity_not_observed"
    assert store.ingest_v2(summary(clock, seq=4, age=18_000))["ok"]
    assert not bus
    clock.advance(15_100)
    assert read(bundle)["reason"] == "stale_or_simulated"


def test_cross_session_replay_blocked_and_stop_gates_publishing():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    assert store.ingest_v2(hb(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    assert store.ingest_v2(summary(clock))["reason"] == "duplicate_or_out_of_order"
    assert store.ingest_v2(hb(clock, session="s2"))["ok"]
    assert read(bundle)["reason"] == "device_state_mismatch_or_idle"
    assert store.ingest_v2(summary(clock, session="s2"))["ok"]
    assert read(bundle)["state"] == "unknown"
    assert read(bundle)["step_delta"] is None
    before = len(bus)
    assert bundle.dispatch("watch_activity", {"action": "stop"}) == {
        "state": "idle", "watch_capture_unchanged": True}
    clock.advance(1000)
    assert store.ingest_v2(summary(clock, session="s2", seq=2))["ok"]
    assert len(bus) == before
    assert read(bundle)["state"] == "still"


def test_live_http_ingress_mcp_and_json_publisher():
    clock = Clock()
    store, bundle, bus = fixture(clock)
    key = "fixture-only-" + "m" * 20
    ingress = make_device_server("127.0.0.1", 0, store, key)
    mcp = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: bundle, "Watch7", "samsung-watch7-driver"),
    )
    workers = [threading.Thread(target=s.serve_forever, daemon=True)
               for s in (ingress, mcp)]
    for worker in workers:
        worker.start()

    def send(url, payload, with_key=False):
        headers = {"Content-Type": "application/json"}
        if with_key:
            headers["X-Wearable-Token"] = key
        req = Request(url, data=json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    try:
        uri = f"http://127.0.0.1:{ingress.server_port}/v2/telemetry"
        assert send(uri, hb(clock), True)["ok"]
        assert send(uri, summary(clock), True)["ok"]
        clock.advance(1000)
        assert send(uri, summary(clock, seq=2), True)["ok"]
        mcpu = f"http://127.0.0.1:{mcp.server_port}/mcp"
        info = send(mcpu, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert info["result"]["tools"][0]["type"] == "processor"
        result = send(mcpu, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "watch_activity", "arguments": {"action": "read"}},
        })
        parsed = json.loads(result["result"]["content"][0]["text"])
        assert parsed["state"] == "still" and parsed["confirmed"]
        assert json.loads(bus[-1])["kind"] == "activity_observation"
    finally:
        for s in (mcp, ingress):
            s.shutdown()
            s.server_close()
        for w in workers:
            w.join(timeout=3)
