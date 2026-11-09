"""Research-only synthetic protocol fixtures; not physical Samsung measurements."""
import json
import threading
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from device import DeviceStore
from transport import make_device_server


class Clock:
    wall = 1_800_000_000_000
    mono = 100_000


def state(clock, seq=0, session="r1", on_body=True, collecting=True, index=False):
    return {
        "protocol_version": 2, "device_id": "watch7", "kind": "device_state",
        "source": "watch7_real", "session_id": session, "seq": seq,
        "captured_at_ms": clock.wall,
        "wear_state": "on_body" if on_body else "off_body",
        "online": collecting, "collecting": collecting,
        "recording_manifest_enabled": index and collecting and on_body,
    }


def scalar(clock, name="ibi_ms", value=845.0, unit="ms",
           seq=0, provider="samsung_sdk", wear="on_body", age=0):
    return {
        "protocol_version": 2, "device_id": "watch7", "kind": "summary",
        "source": "watch7_real", "session_id": "r1", "seq": seq,
        "captured_at_ms": clock.wall, "wear_state": wear,
        "metrics": {name: {
            "value": value, "quality": "valid", "unit": unit,
            "provider": provider, "age_ms": age,
        }},
    }


def manifest(clock, signal="ppg_green", seq=0):
    return {
        "protocol_version": 2, "device_id": "watch7",
        "kind": "signal_manifest", "source": "watch7_real",
        "session_id": "r1", "seq": seq, "captured_at_ms": clock.wall,
        "wear_state": "on_body", "recording_id": "demo-1",
        "signal": signal, "sample_count": 500,
        "sample_rate_hz": 25.0, "stored_on_watch": True,
    }


def test_research_sdk_scalars_require_provider_quality_and_live_session():
    clock = Clock()
    store = DeviceStore(wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono)
    assert store.ingest_v2(state(clock))["ok"]
    for n, (key, val, unit) in enumerate([
        ("ibi_ms", 850, "ms"), ("spo2_pct", 97, "%"),
        ("skin_temperature_c", 33.2, "degC"),
    ]):
        assert store.ingest_v2(scalar(clock, key, val, unit, seq=n))["ok"]
        assert store.metric_snapshot(key)["value"] == val
        assert store.metric_snapshot(key)["provider"] == "samsung_sdk"
    assert store.ingest_v2(scalar(clock, seq=3, provider="system"))["ok"]
    assert store.metric_snapshot("ibi_ms")["reason"] == "provider_not_supported"
    assert store.ingest_v2(scalar(clock, seq=4, age=18_000))["ok"]
    assert store.metric_snapshot("ibi_ms")["reason"] == "stale_or_simulated"
    assert store.ingest_v2(state(clock, seq=1, session="r2"))["ok"]
    assert store.metric_snapshot("ibi_ms")["reason"] == "device_state_mismatch_or_idle"


def test_research_unworn_and_stopped_rejected():
    clock = Clock()
    store = DeviceStore(wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono)
    assert store.ingest_v2(state(clock, on_body=False))["ok"]
    assert store.ingest_v2(scalar(clock, "spo2_pct", 97, "%",
                                 wear="off_body"))["reason"] == "bad_input"
    assert store.ingest_v2(scalar(clock))["ok"]
    assert store.metric_snapshot("ibi_ms")["reason"] == "not_worn"
    assert store.ingest_v2(state(clock, seq=1, collecting=False))["ok"]
    assert store.metric_snapshot("ibi_ms")["reason"] == "device_state_mismatch_or_idle"


def test_research_ppg_has_three_metadata_channels_and_revocation():
    clock = Clock()
    store = DeviceStore(allow_recording_manifests=True,
                        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono)
    assert store.ingest_v2(state(clock, index=True))["ok"]
    for n, signal in enumerate(("ppg_green", "ppg_red", "ppg_ir")):
        assert store.ingest_v2(manifest(clock, signal, n))["ok"]
    snapshot = store.recordings_snapshot()
    assert snapshot["ok"] and len(snapshot["recordings"]) == 3
    assert snapshot["raw_data_transferred"] is False
    assert "samples" not in json.dumps(snapshot)
    raw = manifest(clock, seq=3)
    raw["waveform"] = [1, 2]
    assert store.ingest_v2(raw)["reason"] == "bad_input"
    assert store.ingest_v2(state(clock, seq=1, index=False))["ok"]
    assert store.recordings_snapshot()["reason"] == "watch_recording_consent_required"
    assert store.ingest_v2(state(clock, seq=2, index=True))["ok"]
    assert store.recordings_snapshot()["recordings"] == []


def test_research_index_defaults_off_and_ecg_is_dropped():
    clock = Clock()
    store = DeviceStore(wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono)
    assert store.ingest_v2(state(clock, index=True))["ok"]
    assert store.ingest_v2(manifest(clock))["reason"] == "operator_disabled"
    allowed = DeviceStore(allow_recording_manifests=True,
                          wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono)
    assert allowed.ingest_v2(state(clock, index=True))["ok"]
    assert allowed.ingest_v2(manifest(clock, "ecg"))["reason"] == "signal_not_enabled"


def test_watch_http_requires_pairing_for_research_v2_messages():
    clock = Clock()
    store = DeviceStore(wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono)
    fixture_credential = str(uuid.uuid4())
    server = make_device_server("127.0.0.1", 0, store, fixture_credential)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def send(payload, paired):
        headers = {"Content-Type": "application/json"}
        if paired:
            headers["X-Wearable-Token"] = fixture_credential
        req = Request(f"http://127.0.0.1:{server.server_port}/v2/telemetry",
                      data=json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    try:
        try:
            send(state(clock), False)
            raise AssertionError("Missing pairing accepted")
        except HTTPError as err:
            assert err.code == 401
        assert send(state(clock), True)["ok"]
        assert send(scalar(clock), True)["ok"]
        assert store.metric_snapshot("ibi_ms")["value"] == 845.0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
