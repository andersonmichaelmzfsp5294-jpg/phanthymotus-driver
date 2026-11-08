"""Watch7 haptic: deny by default, opt-in, bounded lease and actual Watch ACK."""
import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from common.vendor_runtime import DriverBundle, make_handler
from device import DeviceStore
from haptics import COOLDOWN_MS, TTL_MS, HapticQueue
from sensors import build_plugins
from transport import make_device_server


class Clock:
    def __init__(self):
        self.wall = 1_800_000_000_000
        self.mono = 100_000

    def advance(self, ms):
        self.wall += ms
        self.mono += ms


def state(clock, *, session="s1", seq=1, wear="on_body", opt_in=True,
          online=True, collecting=True):
    payload = {
        "protocol_version": 2, "device_id": "watch7",
        "source": "watch7_real", "kind": "device_state",
        "session_id": session, "seq": seq, "captured_at_ms": clock.wall,
        "wear_state": wear, "online": online, "collecting": collecting,
    }
    if opt_in is not None:
        payload["haptic_enabled"] = opt_in
    return payload


def fixture(*, enabled=True):
    clock = Clock()
    acks = []
    device = DeviceStore(
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
    )
    queue = HapticQueue(
        device, operator_enabled=enabled, on_ack=acks.append,
        wall_ms=lambda: clock.wall, monotonic_ms=lambda: clock.mono,
    )
    published = {}
    def publisher(topic):
        published[topic] = []
        return published[topic].append
    plugins = build_plugins(
        device, "bumi_test", publisher,
        {"plugins": {"watch_haptic": {"enabled": True}}}, haptic_queue=queue,
    )
    bundle = DriverBundle(plugins)
    bundle.start_all()
    return clock, device, queue, bundle, acks, published


def request_args(event_id="test.event1", pattern="double", reason="manual_test"):
    return {"action": "vibrate", "event_id": event_id, "pattern": pattern,
            "reason": reason}


def test_haptic_actuator_schema_and_default_off():
    clock, device, queue, bundle, acks, published = fixture(enabled=False)
    tool = bundle.get_all_tools()[0]
    assert tool["name"] == "watch_haptic"
    assert tool["type"] == "actuator"
    assert tool["multiInstance"] is False
    assert tool["topic_out"] == [{
        "topic": "/bumi_test/watch7/haptic_acks", "format": "data/json"}]
    assert tool["inputSchema"]["x-action-params"]["vibrate"]["params"] == [
        "pattern", "event_id", "reason"]
    assert device.ingest_v2(state(clock))["ok"]
    assert bundle.dispatch("watch_haptic", request_args()) == {
        "ok": False, "reason": "operator_disabled"}
    assert bundle.dispatch("watch_haptic", {"action": "info"})["operator_enabled"] is False
    assert queue.poll() == []
    assert acks == []


def test_consent_missing_off_body_offline_and_revocation():
    clock, device, queue, bundle, acks, published = fixture()
    assert bundle.dispatch("watch_haptic", request_args())["reason"] == (
        "no_fresh_watch_heartbeat")
    assert device.ingest_v2(state(clock, opt_in=None))["ok"]
    assert bundle.dispatch("watch_haptic", request_args())["reason"] == (
        "watch_haptic_consent_required")
    assert device.ingest_v2(state(clock, seq=2, opt_in=False))["ok"]
    assert bundle.dispatch("watch_haptic", request_args())["reason"] == (
        "watch_haptic_consent_required")
    assert device.ingest_v2(state(clock, seq=3, opt_in=True, wear="off_body"))["ok"]
    assert bundle.dispatch("watch_haptic", request_args())["reason"] == "watch_not_worn"
    assert device.ingest_v2(state(clock, seq=4, online=False))["ok"]
    assert bundle.dispatch("watch_haptic", request_args())["reason"] == (
        "watch_offline_or_idle")
    assert device.ingest_v2(state(clock, seq=5))["ok"]
    created = bundle.dispatch("watch_haptic", request_args())
    assert created["ok"] and created["state"] == "queued"
    assert len(queue.poll()) == 1
    assert device.ingest_v2(state(clock, seq=6, opt_in=False))["ok"]
    assert queue.poll() == []
    assert queue.status(created["request_id"])["command"]["state"] == "cancelled"
    assert queue.acknowledge({
        "request_id": created["request_id"], "session_id": "s1",
        "state": "executed",
    })["reason"] == "not_pending"
    assert not acks


def test_dedup_replay_conflict_cooldown_expiration_and_no_false_acks():
    clock, device, queue, bundle, acks, _ = fixture()
    assert device.ingest_v2(state(clock))["ok"]
    created = bundle.dispatch("watch_haptic", request_args())
    assert created["ok"] and not created["deduplicated"]
    same = bundle.dispatch("watch_haptic", request_args())
    assert same["deduplicated"] and same["request_id"] == created["request_id"]
    assert bundle.dispatch("watch_haptic", request_args(pattern="single"))[
        "reason"] == "event_id_conflict"
    assert bundle.dispatch("watch_haptic", request_args("test.event2"))[
        "reason"] == "cooldown"
    assert queue.acknowledge({
        "request_id": created["request_id"], "session_id": "s1",
        "state": "executed",
    })["reason"] == "not_authorized_or_delivered"
    assert queue.poll()[0]["ttl_remaining_ms"] == TTL_MS
    assert queue.acknowledge({
        "request_id": created["request_id"], "session_id": "other",
        "state": "executed",
    })["reason"] == "session_mismatch"
    ack = queue.acknowledge({
        "request_id": created["request_id"], "session_id": "s1",
        "state": "executed",
    })
    assert ack["ok"] and not ack["deduplicated"]
    assert len(acks) == 1 and acks[0]["evidence"] == "watch_app_ack"
    assert queue.acknowledge({
        "request_id": created["request_id"], "session_id": "s1",
        "state": "executed",
    })["deduplicated"]
    assert len(acks) == 1
    assert queue.poll() == []
    clock.advance(COOLDOWN_MS)
    assert device.ingest_v2(state(clock, seq=2))["ok"]
    pending = bundle.dispatch("watch_haptic", request_args("test.event2"))
    assert pending["ok"]
    assert queue.poll()[0]["request_id"] == pending["request_id"]
    clock.advance(TTL_MS + 1)
    assert queue.poll() == []
    assert queue.status(pending["request_id"])["command"]["state"] == "expired"
    assert queue.acknowledge({
        "request_id": pending["request_id"], "session_id": "s1",
        "state": "executed",
    })["reason"] == "not_pending"
    assert len(acks) == 1


def test_session_change_no_old_delivery_and_stop_cancels():
    clock, device, queue, bundle, acks, _ = fixture()
    assert device.ingest_v2(state(clock))["ok"]
    first = bundle.dispatch("watch_haptic", request_args())
    assert first["ok"]
    assert device.ingest_v2(state(clock, session="s2"))["ok"]
    assert queue.poll() == []
    assert queue.status(first["request_id"])["command"]["state"] == "cancelled"
    assert queue.acknowledge({
        "request_id": first["request_id"], "session_id": "s1", "state": "executed"
    })["reason"] == "not_pending"
    clock.advance(COOLDOWN_MS)
    assert device.ingest_v2(state(clock, session="s2", seq=2))["ok"]
    second = bundle.dispatch("watch_haptic", request_args())
    assert second["ok"] and second["session_id"] == "s2"
    assert bundle.dispatch("watch_haptic", {"action": "stop"})["state"] == "idle"
    assert queue.status(second["request_id"])["command"]["state"] == "cancelled"
    assert bundle.dispatch("watch_haptic", request_args("test.event3"))[
        "reason"] == "not_running"
    assert not acks


def test_bad_args_and_protocol_permissions_fail_closed():
    clock, device, queue, bundle, acks, _ = fixture()
    assert device.ingest_v2(state(clock, opt_in="yes"))["reason"] == "bad_input"
    assert device.ingest_v2(state(clock))["ok"]
    assert bundle.dispatch("watch_haptic", request_args(event_id=""))[
        "reason"] == "event_id_required"
    assert bundle.dispatch("watch_haptic", request_args(pattern="infinite"))[
        "reason"] == "invalid_pattern"
    assert bundle.dispatch("watch_haptic", request_args(reason="bad\nline"))[
        "reason"] == "invalid_reason"
    assert queue.acknowledge({"state": "executed", "request_id": "bad"})[
        "reason"] == "bad_ack"


def test_real_http_mcp_commands_and_authentication_are_separate():
    # In-process integration: not a proof of Watch vibrator movement.
    clock, device, queue, bundle, acks, published = fixture()
    clock.wall = int(time.time() * 1000)
    queue.start()
    mcp = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: bundle, "Watch7", "samsung-watch7-driver"),
    )
    token = "test-" + "o" * 25
    ingress = make_device_server("127.0.0.1", 0, device, token, haptics=queue)
    workers = [
        threading.Thread(target=s.serve_forever, daemon=True)
        for s in (mcp, ingress)
    ]
    for worker in workers:
        worker.start()

    def http(url, data=None, auth=None):
        headers = {"Content-Type": "application/json"}
        if auth is not None:
            headers["X-Wearable-Token"] = auth
        req = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers=headers)
        try:
            with urlopen(req, timeout=4) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            return e.code, json.load(e)

    base = f"http://127.0.0.1:{ingress.server_port}"
    mcp_url = f"http://127.0.0.1:{mcp.server_port}/mcp"
    try:
        assert http(base + "/v1/commands")[0] == 401
        assert http(base + "/v1/commands", auth="invalid")[0] == 401
        assert http(base + "/v1/commands", auth=token)[1]["commands"] == []
        assert http(base + "/v2/commands", auth=token)[1]["commands"] == []
        assert http(base + "/v2/task-prompts", auth=token)[1]["prompts"] == []
        assert http(base + "/v2/haptic-authorize",
                    {"request_id": "fake"}, token)[1]["allowed"] is False
        assert http(base + "/mcp", {}, token)[0] == 404

        assert http(base + "/v2/telemetry", state(clock), token)[1]["ok"]
        response = http(mcp_url, {
            "jsonrpc": "2.0", "id": 10, "method": "tools/list",
        })[1]
        assert [x["name"] for x in response["result"]["tools"]] == ["watch_haptic"]
        response = http(mcp_url, {
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "watch_haptic", "arguments": request_args()},
        })[1]
        result = json.loads(response["result"]["content"][0]["text"])
        assert result["ok"] and result["state"] == "queued"
        assert http(base + "/v1/commands", auth="invalid")[0] == 401
        assert http(base + "/v1/commands", auth=token)[1]["commands"][0][
            "request_id"] == result["request_id"]
        assert http(base + "/v1/ack", {
            "request_id": result["request_id"], "session_id": "wrong",
            "state": "executed",
        }, token)[1]["reason"] == "session_mismatch"
        status, done = http(base + "/v1/ack", {
            "request_id": result["request_id"], "session_id": "s1",
            "state": "executed",
        }, token)
        assert status == 200 and done["ok"]
        assert len(acks) == 1
        assert http(base + "/v1/commands", auth=token)[1]["commands"] == []
        assert http(base + "/v2/ack", {
            "request_id": result["request_id"], "session_id": "s1",
            "state": "executed",
        }, token)[1]["reason"] == "not_pending"
    finally:
        for server in (mcp, ingress):
            server.shutdown()
            server.server_close()
        for server in workers:
            server.join(timeout=3)
