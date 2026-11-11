"""Opt-in v3 Watch7 cards: bounded payload, consents, auth and MCP/ROS isolation.

Synthetic fixtures only. These checks do not claim real SDK/Android capture.
"""
import base64
import json
import threading
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from advanced_cards import AdvancedCard
from common.vendor_runtime import DriverBundle, make_handler
from device import DeviceStore
from sensors import build_plugins
from stream_protocol import STREAM_TYPES, KIND_TO_CARD, validate_v3
from stream_store import StreamStore
from transport import make_device_server


class Clock:
    wall=1_800_000_000_000
    mono=100_000

    def advance(self,ms):
        self.wall+=ms
        self.mono+=ms


def state(clock,seq=0,session="s1",grants=None,collecting=True):
    return {
        "protocol_version":3,"device_id":"watch7","source":"watch7_real",
        "kind":"stream_state","session_id":session,"seq":seq,
        "captured_at_ms":clock.wall,"wear_state":"on_body",
        "online":collecting,"collecting":collecting,
        "enabled_streams":list(grants or ()) if collecting else [],
    }


def frame(clock,kind,seq=0,session="s1"):
    f={
        "protocol_version":3,"device_id":"watch7","source":"watch7_real",
        "kind":kind,"session_id":session,"seq":seq,
        "captured_at_ms":clock.wall,"wear_state":"on_body",
    }
    if kind=="imu_window":
        f.update(sample_rate_hz=100,samples=[
            [i*10,0.01,0.02,9.8,0.1,0.2,0.3] for i in range(20)])
    elif kind=="ppg_chunk":
        f.update(sample_rate_hz=25,recording_id="ppg01",samples=[
            [i*40,100+i,110+i,120+i] for i in range(20)])
    elif kind=="ecg_chunk":
        f.update(sample_rate_hz=100,recording_id="ecg01",samples=[
            [i*10,i/100] for i in range(20)])
    elif kind=="bia_result":
        f.update(recording_id="bia01",stats={
            "body_fat_pct":19.5,"skeletal_muscle_kg":30.1,
            "total_body_water_kg":40.3,"impedance_ohm":480})
    elif kind=="location_fix":
        f.update(lat=18.2,lon=109.5,accuracy_m=8.5,provider="gps")
    elif kind=="audio_chunk":
        f.update(encoding="pcm_s16le_mono",sample_rate_hz=8000,
                 duration_ms=10,recording_id="audio01",
                 pcm_b64=base64.b64encode(b"\x01\x00"*80).decode())
    elif kind=="step_events":
        f.update(samples=[[0],[350],[720]],
                 window_started_at_ms=clock.wall-720,
                 window_ended_at_ms=clock.wall)
    elif kind=="sweat_loss_result":
        f.update(exercise_type="running",provider="samsung_sdk",
                 sdk_status=0,sweat_loss_ml=215.0,
                 exercise_duration_ms=480000,spm_samples=360,
                 distance_m=2200.0)
    return f


@pytest.mark.parametrize("card,kind",list(STREAM_TYPES.items()))
def test_all_cards_accept_only_double_gated_data(card,kind):
    c=Clock()
    events=[]
    store=StreamStore(enabled={card},wall_ms=lambda:c.wall,mono_ms=lambda:c.mono,
                      on_frame=lambda k,v:events.append((k,v)))
    assert store.ingest(frame(c,kind))["reason"]=="no_live_stream_consent"
    assert store.ingest(state(c,grants=[]))["ok"]
    assert store.ingest(frame(c,kind))["reason"]=="watch_consent_required"
    assert store.ingest(state(c,seq=1,grants=[card]))["ok"]
    assert store.ingest(frame(c,kind))["ok"]
    assert store.snapshot(card)["frame"]["kind"]==kind
    assert store.snapshot(card,mode="list")["count"]==1
    assert len(events)==1
    assert store.ingest(frame(c,kind))["reason"]=="duplicate_or_out_of_order"
    assert store.ingest(state(c,seq=2,grants=[],collecting=False))["ok"]
    assert store.snapshot(card)["reason"]=="stream_not_collecting"


@pytest.mark.parametrize("card,kind",list(STREAM_TYPES.items()))
def test_all_cards_operator_default_off(card,kind):
    c=Clock()
    store=StreamStore(wall_ms=lambda:c.wall,mono_ms=lambda:c.mono)
    assert store.ingest(state(c,grants=[card]))["ok"]
    assert store.ingest(frame(c,kind))["reason"]=="operator_disabled"
    assert store.snapshot(card)["reason"]=="operator_disabled"


def test_v3_invalid_waveform_sizes_nonmonotonic_and_unknown_keys():
    c=Clock()
    frame0=frame(c,"imu_window")
    frame0["samples"].append([0,0,0,0,0,0,0])
    with pytest.raises(ValueError,match="nonmonotonic"):
        validate_v3(frame0,now_ms=c.wall)
    bad=frame(c,"ecg_chunk")
    bad["samples"]=[[0,1]]*130
    with pytest.raises(ValueError,match="sample_count"):
        validate_v3(bad,now_ms=c.wall)
    bad=frame(c,"ppg_chunk")
    bad["samples"]=[[0,None,None,None]]
    with pytest.raises(ValueError,match="empty_ppg"):
        validate_v3(bad,now_ms=c.wall)
    bad=frame(c,"location_fix")
    bad["lat"]=91
    with pytest.raises(ValueError,match="out_of_range"):
        validate_v3(bad,now_ms=c.wall)
    bad=frame(c,"audio_chunk")
    bad["pcm_b64"]="not-base64"
    with pytest.raises(ValueError,match="invalid_audio"):
        validate_v3(bad,now_ms=c.wall)
    bad=frame(c,"imu_window")
    bad["email"]="private@localhost"
    with pytest.raises(ValueError,match="unsupported_fields"):
        validate_v3(bad,now_ms=c.wall)


def test_step_events_reject_duplicate_offsets_and_fake_time():
    c=Clock()
    frame0=frame(c,"step_events")
    frame0["samples"]=[[0],[0]]
    with pytest.raises(ValueError,match="nonmonotonic_steps"):
        validate_v3(frame0,now_ms=c.wall)
    frame0=frame(c,"step_events")
    frame0["window_started_at_ms"]=c.wall-200000
    with pytest.raises(ValueError,match="invalid_step_window"):
        validate_v3(frame0,now_ms=c.wall)


def test_sweat_sdk_status_never_invents_measured_milliliters():
    c=Clock()
    valid=frame(c,"sweat_loss_result")
    assert validate_v3(valid,now_ms=c.wall)["sweat_loss_ml"]==215.0
    bad=dict(valid,sdk_status=1)
    with pytest.raises(ValueError,match="unreliable_sweat_value"):
        validate_v3(bad,now_ms=c.wall)
    bad=dict(valid,sdk_status=1,sweat_loss_ml=None)
    assert validate_v3(bad,now_ms=c.wall)["sweat_loss_ml"] is None
    assert "duration_under_5_minutes" in validate_v3(
        dict(bad, sdk_status=1),now_ms=c.wall)["status_reasons"]
    assert "mean_spm_at_most_110" in validate_v3(
        dict(bad, sdk_status=64),now_ms=c.wall)["status_reasons"]
    assert validate_v3(dict(bad, sdk_status=-4),
                       now_ms=c.wall)["status_reasons"] == [
        "another_sweat_tracker_preempted"]
    bad=dict(valid,exercise_duration_ms=90000)
    with pytest.raises(ValueError,match="insufficient_running_evidence"):
        validate_v3(bad,now_ms=c.wall)
    bad=dict(valid,provider="android")
    with pytest.raises(ValueError,match="invalid_sweat_provider"):
        validate_v3(bad,now_ms=c.wall)


def test_invalid_consent_replay_session_expiry_and_bound():
    c=Clock()
    card="watch_imu_stream"
    store=StreamStore(enabled={card},wall_ms=lambda:c.wall,mono_ms=lambda:c.mono)
    bad=state(c,grants=[card,card])
    assert store.ingest(bad)["reason"]=="bad_input"
    assert store.ingest(state(c,grants=[card]))["ok"]
    for i in range(18):
        assert store.ingest(frame(c,"imu_window",seq=i))["ok"]
    assert store.snapshot(card,mode="list")["count"]==12
    assert store.ingest(frame(c,"imu_window",seq=19,session="other"))[
        "reason"]=="session_mismatch"
    c.advance(15001)
    assert store.snapshot(card)["reason"]=="no_live_stream_consent"
    assert store.ingest(state(c,seq=1,grants=[card]))["ok"]
    assert store.ingest(frame(c,"imu_window",seq=19))["ok"]
    assert store.ingest(state(c,seq=2,session="s2",grants=[card]))["ok"]
    assert store.snapshot(card)["reason"]=="no_stream_samples"


def test_all_cards_mcp_contract_ros_redacts_sensitive_samples():
    c=Clock()
    sent={}
    cards={}
    def factory(topic):
        sent[topic]=[]
        return sent[topic].append
    store=StreamStore(enabled=set(STREAM_TYPES),wall_ms=lambda:c.wall,
        mono_ms=lambda:c.mono,on_frame=lambda kind,frame:
            cards[KIND_TO_CARD[kind]].publish_frame(kind,frame))
    bundle=DriverBundle(build_plugins(DeviceStore(),"lab",factory,{
        "plugins":{card:{"enabled":True} for card in STREAM_TYPES}
    },stream_store=store))
    cards.update({p.name:p for p in bundle.plugins})
    bundle.start_all()
    assert len(bundle.get_all_tools())==len(STREAM_TYPES)
    assert set(cards)==set(STREAM_TYPES)
    assert store.ingest(state(c,grants=list(STREAM_TYPES)))["ok"]
    for kind in STREAM_TYPES.values():
        assert store.ingest(frame(c,kind))["ok"]
    for name in STREAM_TYPES:
        tool=next(t for t in bundle.get_all_tools() if t["name"]==name)
        assert tool["type"]=="sensor"
        assert tool["topic_out"]==[{"topic":f"/lab/watch7/{name[6:]}",
                                   "format":"data/json"}]
        assert bundle.dispatch(name,{"action":"capabilities"})["ok"]
        item=bundle.dispatch(name,{"action":"read"})
        assert item["ok"]
        assert len(sent[f"/lab/watch7/{name[6:]}"])==1
        onbus=json.loads(sent[f"/lab/watch7/{name[6:]}"][0])
        assert "samples" not in onbus and "pcm_b64" not in onbus
        assert "sweat_loss_ml" not in onbus
        assert bundle.dispatch(name,{"action":"stop"})[
            "watch_capture_unchanged"] is True


def test_authenticated_http_v3_real_handler_and_mcp():
    from http.server import ThreadingHTTPServer
    c=Clock()
    store=StreamStore(enabled={"watch_imu_stream"},
            wall_ms=lambda:c.wall,mono_ms=lambda:c.mono)
    published=[]
    card=AdvancedCard("watch_imu_stream",store,"/lab/watch7/imu_stream",
                      lambda data:published.append(json.loads(data)))
    store._on_frame=card.publish_frame
    bundle=DriverBundle([card])
    bundle.start_all()
    token=str(uuid.uuid4())
    ingress=make_device_server("127.0.0.1",0,DeviceStore(),token,streams=store)
    mcp=ThreadingHTTPServer(("127.0.0.1",0),make_handler(
        lambda:bundle,"Watch7","samsung-watch7-driver"))
    workers=[threading.Thread(target=s.serve_forever,daemon=True)
             for s in (ingress,mcp)]
    for w in workers:w.start()
    def post(url,body,authorized=False):
        hdr={"Content-Type":"application/json"}
        if authorized:hdr["X-Wearable-Token"]=token
        req=Request(url,data=json.dumps(body).encode(),headers=hdr)
        with urlopen(req,timeout=4) as response:return json.load(response)
    url=f"http://127.0.0.1:{ingress.server_port}/v3/telemetry"
    try:
        with pytest.raises(HTTPError) as exc:
            post(url,state(c,grants=["watch_imu_stream"]))
        assert exc.value.code==401
        assert post(url,state(c,grants=["watch_imu_stream"]),True)["ok"]
        assert post(url,frame(c,"imu_window"),True)["ok"]
        reply=post(f"http://127.0.0.1:{mcp.server_port}/mcp",{
          "jsonrpc":"2.0","id":1,"method":"tools/call",
          "params":{"name":"watch_imu_stream","arguments":{"action":"read"}}})
        assert json.loads(reply["result"]["content"][0]["text"])["ok"]
        assert published and "samples" not in published[0]
    finally:
        for s in (mcp,ingress):s.shutdown();s.server_close()
        for w in workers:w.join(timeout=3)
