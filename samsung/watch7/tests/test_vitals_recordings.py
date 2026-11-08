"""Advanced Watch7 cards: bounded SDK scalars and metadata-only PPG index."""
import json

from common.vendor_runtime import DriverBundle
from device import DeviceStore, MAX_RECORDINGS
from sensors import build_plugins


class Clock:
    def __init__(self):
        self.wall = 1_800_000_000_000
        self.mono = 100_000

    def move(self, ms):
        self.wall += ms
        self.mono += ms


def heartbeat(clock, *, session="s1", seq=1, wear="on_body",
              recording_opt_in=False, collecting=True):
    return {
        "protocol_version": 2, "device_id": "watch7", "kind": "device_state",
        "source": "watch7_real", "session_id": session, "seq": seq,
        "captured_at_ms": clock.wall, "wear_state": wear,
        "online": True, "collecting": collecting,
        "recording_manifest_enabled": recording_opt_in,
    }


def metric(value, unit, *, provider="samsung_sdk", age=40, quality="valid"):
    return {
        "value": value if quality == "valid" else None,
        "unit": unit, "provider": provider, "quality": quality,
        "age_ms": age,
    }


def summary(clock, *, session="s1", seq=1, wear="on_body", metrics=None):
    return {
        "protocol_version": 2, "device_id": "watch7", "kind": "summary",
        "source": "watch7_real", "session_id": session, "seq": seq,
        "captured_at_ms": clock.wall, "wear_state": wear,
        "metrics": metrics if metrics is not None else {
            "ibi_ms": metric(875.0, "ms"),
            "spo2_pct": metric(97.2, "%"),
            "skin_temperature_c": metric(32.5, "degC"),
            "heart_rate_bpm": metric(68, "bpm", provider="health_services"),
        },
    }


def manifest(clock, *, session="s1", seq=1, signal="ppg_green",
             recording_id="r1", count=250):
    return {
        "protocol_version": 2, "device_id": "watch7",
        "source": "watch7_real", "kind": "signal_manifest",
        "session_id": session, "seq": seq, "captured_at_ms": clock.wall,
        "wear_state": "on_body", "recording_id": recording_id,
        "signal": signal, "sample_count": count,
        "sample_rate_hz": 25.0, "stored_on_watch": True,
    }


def make_cards(clock, *, recording_operator=False, with_recordings=True):
    sent = {}
    plugins = {}
    def publisher(topic):
        sent[topic] = []
        return sent[topic].append

    def on_metrics(data):
        if "watch_vitals" in plugins:
            plugins["watch_vitals"].publish(data)

    def on_manifest(data):
        if "watch_recordings" in plugins:
            plugins["watch_recordings"].publish(data)

    store = DeviceStore(
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
        on_metrics=on_metrics, on_manifest=on_manifest,
        allow_recording_manifests=recording_operator,
    )
    cfg = {"plugins": {"watch_vitals": {"enabled": True}}}
    if with_recordings:
        cfg["plugins"]["watch_recordings"] = {"enabled": True}
    bundle = DriverBundle(build_plugins(store, "lab", publisher, cfg))
    plugins.update({plugin.name: plugin for plugin in bundle.plugins})
    bundle.start_all()
    return store, bundle, sent


def test_vitals_schema_and_companion_not_claimed_integrated():
    clock = Clock()
    store, bundle, output = make_cards(clock, with_recordings=False)
    tool = bundle.get_all_tools()[0]
    assert tool["name"] == "watch_vitals"
    assert tool["type"] == "sensor"
    assert tool["multiInstance"] is False
    assert tool["inputSchema"]["properties"]["metric"]["enum"] == [
        "ibi_ms", "spo2_pct", "skin_temperature_c",
    ]
    assert tool["topic_out"] == [{
        "topic": "/lab/watch7/vitals", "format": "data/json"}]
    caps = bundle.dispatch("watch_vitals", {"action": "capabilities"})
    assert caps["companion"] == "research_sdk_not_yet_integrated"
    assert caps["raw_waveforms"] is False
    assert not caps["metrics"]["ibi_ms"]["companion_integrated"]
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ppg_green",
    })["reason"] == "unsupported_metric"
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ibi_ms",
    })["reason"] == "no_v2_summary"


def test_vitals_research_sdk_scalars_only_and_no_hr_leakage():
    clock = Clock()
    store, bundle, output = make_cards(clock, with_recordings=False)
    assert store.ingest_v2(heartbeat(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    snapshot = bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ibi_ms",
    })
    assert snapshot["ok"] and snapshot["value"] == 875.0
    assert snapshot["provider"] == "samsung_sdk"
    payload = json.loads(output["/lab/watch7/vitals"][0])
    assert set(payload["metrics"]) == {
        "ibi_ms", "spo2_pct", "skin_temperature_c"}
    assert "heart_rate_bpm" not in payload["metrics"]
    assert "samples" not in payload
    assert bundle.dispatch("watch_vitals", {"action": "stop"})[
        "watch_capture_unchanged"] is True
    assert store.ingest_v2(summary(clock, seq=2))["ok"]
    assert len(output["/lab/watch7/vitals"]) == 1


def test_vitals_fail_closed_on_provider_unknown_wear_and_stale():
    clock = Clock()
    store, bundle, output = make_cards(clock, with_recordings=False)
    assert store.ingest_v2(heartbeat(clock, wear="unknown"))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ibi_ms",
    })["reason"] == "not_worn"
    assert not output["/lab/watch7/vitals"]
    assert store.ingest_v2(heartbeat(clock, seq=2))["ok"]
    assert store.ingest_v2(summary(clock, seq=2, metrics={
        "ibi_ms": metric(930, "ms", provider="android_sensor"),
    }))["ok"]
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ibi_ms",
    })["reason"] == "provider_not_supported"
    assert not output["/lab/watch7/vitals"]
    assert store.ingest_v2(summary(clock, seq=3, metrics={
        "ibi_ms": metric(900, "ms", quality="unavailable"),
    }))["ok"]
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ibi_ms",
    })["reason"] == "metric_unavailable"
    clock.move(15_001)
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "ibi_ms",
    })["reason"] == "stale_or_simulated"


def test_vitals_no_cross_session_or_offbody_replay():
    clock = Clock()
    store, bundle, output = make_cards(clock, with_recordings=False)
    assert store.ingest_v2(heartbeat(clock))["ok"]
    assert store.ingest_v2(summary(clock))["ok"]
    assert len(output["/lab/watch7/vitals"]) == 1
    assert store.ingest_v2(heartbeat(clock, session="next"))["ok"]
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "spo2_pct",
    })["reason"] == "device_state_mismatch_or_idle"
    # A valid old summary cannot override a new off-wrist heartbeat.
    assert store.ingest_v2(summary(clock, session="next", seq=1))["ok"]
    assert store.ingest_v2(heartbeat(clock, session="next",
                                     seq=2, wear="off_body"))["ok"]
    assert bundle.dispatch("watch_vitals", {
        "action": "read", "metric": "spo2_pct",
    })["reason"] == "not_worn"
    assert len(output["/lab/watch7/vitals"]) == 2
    invalid = summary(clock, session="next", seq=2, wear="off_body")
    assert store.ingest_v2(invalid)["reason"] == "bad_input"


def test_recordings_default_off_and_missing_consent():
    clock = Clock()
    store, bundle, output = make_cards(clock)
    tool = [t for t in bundle.get_all_tools()
            if t["name"] == "watch_recordings"][0]
    assert tool["type"] == "sensor"
    assert tool["topic_out"] == [{
        "topic": "/lab/watch7/recordings", "format": "data/json"}]
    assert store.ingest_v2(heartbeat(clock, recording_opt_in=True))["ok"]
    assert store.ingest_v2(manifest(clock))["reason"] == "operator_disabled"
    assert bundle.dispatch("watch_recordings", {
        "action": "list",
    })["reason"] == "operator_disabled"
    assert not output["/lab/watch7/recordings"]

    store2, bundle2, bus2 = make_cards(clock, recording_operator=True)
    assert store2.ingest_v2(manifest(clock))["reason"] == "device_state_unavailable"
    assert store2.ingest_v2(heartbeat(clock))["ok"]
    assert store2.ingest_v2(manifest(clock))["reason"] == (
        "watch_recording_consent_required")
    assert not bus2["/lab/watch7/recordings"]


def test_recording_ppg_index_only_no_waveform_or_ecg():
    clock = Clock()
    store, bundle, output = make_cards(clock, recording_operator=True)
    assert store.ingest_v2(heartbeat(clock, recording_opt_in=True))["ok"]
    assert store.ingest_v2(manifest(clock))["ok"]
    recent = bundle.dispatch("watch_recordings", {"action": "list"})
    assert recent["ok"] and recent["raw_data_transferred"] is False
    assert len(recent["recordings"]) == 1
    assert recent["recordings"][0]["sample_count"] == 250
    event = json.loads(output["/lab/watch7/recordings"][-1])
    assert event["recording_id"] == "r1"
    assert event["raw_data_transferred"] is False
    assert "samples" not in event and "waveform" not in event
    assert store.ingest_v2(manifest(clock))["reason"] == "duplicate_or_out_of_order"
    assert store.ingest_v2(manifest(clock, seq=2, signal="ecg"))[
        "reason"] == "signal_not_enabled"
    assert store.ingest_v2(manifest(clock, seq=2, count=0))[
        "reason"] == "empty_recording"
    with_samples = manifest(clock, seq=2)
    with_samples["samples"] = [1.0] * 5
    assert store.ingest_v2(with_samples)["reason"] == "bad_input"
    assert len(output["/lab/watch7/recordings"]) == 1


def test_recordings_revocation_new_session_ttl_and_bounded_index():
    clock = Clock()
    store, bundle, output = make_cards(clock, recording_operator=True)
    assert store.ingest_v2(heartbeat(clock, recording_opt_in=True))["ok"]
    for i in range(MAX_RECORDINGS + 3):
        assert store.ingest_v2(manifest(clock, seq=i + 1,
                                       recording_id=f"r{i}"))["ok"]
    recent = bundle.dispatch("watch_recordings", {"action": "list"})
    assert len(recent["recordings"]) == MAX_RECORDINGS
    assert recent["recordings"][0]["recording_id"] == "r3"
    assert store.ingest_v2(heartbeat(clock, seq=2,
                                     recording_opt_in=False))["ok"]
    assert bundle.dispatch("watch_recordings", {"action": "list"})[
        "reason"] == "watch_recording_consent_required"
    # Re-authorization must not restore a previously revoked manifest.
    assert store.ingest_v2(heartbeat(clock, seq=3,
                                     recording_opt_in=True))["ok"]
    assert bundle.dispatch("watch_recordings", {"action": "list"})[
        "recordings"] == []
    assert store.ingest_v2(manifest(clock, seq=MAX_RECORDINGS + 4,
                                   recording_id="fresh"))["ok"]
    assert store.ingest_v2(heartbeat(clock, session="s2"))["ok"]
    assert bundle.dispatch("watch_recordings", {"action": "list"})[
        "reason"] == "watch_recording_consent_required"
    assert store.ingest_v2(heartbeat(clock, session="s2", seq=2,
                                     recording_opt_in=True))["ok"]
    assert bundle.dispatch("watch_recordings", {"action": "list"})[
        "recordings"] == []
    assert store.ingest_v2(manifest(clock, session="s2",
                                   recording_id="last"))["ok"]
    clock.move(15_001)
    assert bundle.dispatch("watch_recordings", {"action": "list"})[
        "reason"] == "device_state_unavailable"


def test_http_mcp_vitals_and_recording_metadata_vertical_slice():
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen

    from common.vendor_runtime import make_handler
    from transport import make_device_server

    clock = Clock()
    store, bundle, bus = make_cards(clock, recording_operator=True)
    token = "local-test-token-" + "z" * 20
    ingress = make_device_server("127.0.0.1", 0, store, token)
    mcp = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: bundle, "Watch7", "samsung-watch7-driver"),
    )
    workers = [threading.Thread(target=s.serve_forever, daemon=True)
               for s in (ingress, mcp)]
    for worker in workers:
        worker.start()

    def request(port, path, body, *, authorized=False):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["X-Wearable-Token"] = token
        req = Request(f"http://127.0.0.1:{port}{path}",
                      data=json.dumps(body).encode(), headers=headers)
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    def mcp_call(name, action, **arguments):
        res = request(mcp.server_port, "/mcp", {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": {"action": action, **arguments}},
        })
        return json.loads(res["result"]["content"][0]["text"])

    try:
        port = ingress.server_port
        assert request(port, "/v2/telemetry",
                       heartbeat(clock, recording_opt_in=True),
                       authorized=True)["ok"]
        assert request(port, "/v2/telemetry", summary(clock),
                       authorized=True)["ok"]
        assert request(port, "/v2/telemetry", manifest(clock),
                       authorized=True)["ok"]
        tools = request(mcp.server_port, "/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })["result"]["tools"]
        assert {t["name"] for t in tools} == {
            "watch_vitals", "watch_recordings"}
        assert mcp_call("watch_vitals", "read", metric="ibi_ms")["value"] == 875.0
        rows = mcp_call("watch_recordings", "list")
        assert rows["ok"] and rows["recordings"][0]["recording_id"] == "r1"
        assert rows["raw_data_transferred"] is False
        assert len(bus["/lab/watch7/vitals"]) == 1
        assert len(bus["/lab/watch7/recordings"]) == 1
    finally:
        for server in (mcp, ingress):
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join(timeout=3)
