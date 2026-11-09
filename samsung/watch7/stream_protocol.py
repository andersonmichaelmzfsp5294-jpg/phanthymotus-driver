"""Bounded Watch7 v3 research sensor transport. Deliberately separate from v2.

All inputs are authenticated at the HTTP layer. v3 additionally requires
a live wearer-granted stream_state and an explicit operator feature switch.
No remote actions; no SDK binaries; no permanent raw recordings.
"""
from __future__ import annotations

import base64
import binascii
import math
import time

STREAM_TYPES = {
    "watch_imu_stream": "imu_window",
    "watch_ppg_raw": "ppg_chunk",
    "watch_ecg": "ecg_chunk",
    "watch_bia": "bia_result",
    "watch_location": "location_fix",
    "watch_audio": "audio_chunk",
}
KIND_TO_CARD = {v: k for k, v in STREAM_TYPES.items()}
RAW_TYPES = frozenset(("watch_ppg_raw", "watch_ecg", "watch_audio"))
MAX_V3_BYTES = 24576
MAX_AGE_MS = 30000


def numeric(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_" + name)
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("out_of_range_" + name)
    return value


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_" + name)
    return value


def identity(value, name, length=96):
    if (not isinstance(value, str) or not 1 <= len(value) <= length
            or not value.isascii()
            or not all(ch.isalnum() or ch in "._:-" for ch in value)):
        raise ValueError("invalid_" + name)
    return value


def validate_v3(frame, now_ms=None):
    if not isinstance(frame, dict):
        raise ValueError("invalid_frame")
    kind = frame.get("kind")
    if kind not in set(KIND_TO_CARD) | {"stream_state"}:
        raise ValueError("unsupported_kind")
    if frame.get("protocol_version") != 3 or frame.get("device_id") != "watch7":
        raise ValueError("unsupported_protocol")
    if frame.get("source") != "watch7_real":
        raise ValueError("source_not_real")
    allowed = {
        "protocol_version", "device_id", "kind", "source", "session_id",
        "seq", "captured_at_ms", "wear_state", "online", "collecting",
        "enabled_streams", "samples", "sample_rate_hz", "recording_id",
        "stats", "lat", "lon", "accuracy_m", "provider", "speed_m_s",
        "altitude_m", "encoding", "sample_count", "pcm_b64", "duration_ms",
        "window_started_at_ms", "window_ended_at_ms",
    }
    if set(frame) - allowed:
        raise ValueError("unsupported_fields")
    session = identity(frame.get("session_id"), "session", 64)
    seq = integer(frame.get("seq"), "seq", 0, 2**63-1)
    ts = integer(frame.get("captured_at_ms"), "timestamp", 0, 2**63-1)
    now = int(time.time()*1000) if now_ms is None else now_ms
    if not now - MAX_AGE_MS <= ts <= now+5000:
        raise ValueError("stale_or_future")
    wear = frame.get("wear_state")
    if wear not in ("on_body", "off_body", "unknown"):
        raise ValueError("invalid_wear")
    out = {
        "protocol_version": 3, "device_id": "watch7", "kind": kind,
        "source": "watch7_real", "session_id": session, "seq": seq,
        "captured_at_ms": ts, "wear_state": wear,
    }
    if kind == "stream_state":
        if type(frame.get("online")) is not bool or type(frame.get("collecting")) is not bool:
            raise ValueError("invalid_stream_state")
        grants = frame.get("enabled_streams")
        if (not isinstance(grants, list) or len(grants)>6
                or len(set(map(str, grants)))!=len(grants)
                or any(x not in STREAM_TYPES for x in grants)):
            raise ValueError("invalid_stream_permissions")
        if frame["collecting"] and (wear != "on_body" or not frame["online"]):
            raise ValueError("cannot_collect_unworn")
        if not frame["collecting"] and grants:
            raise ValueError("stopped_state_has_grants")
        out.update(online=frame["online"], collecting=frame["collecting"],
                   enabled_streams=sorted(grants))
        return out
    if wear != "on_body":
        raise ValueError("not_worn")
    if kind in ("imu_window", "ppg_chunk", "ecg_chunk"):
        limits = {"imu_window": (128,7), "ppg_chunk": (96,4), "ecg_chunk": (128,2)}
        capacity, width = limits[kind]
        rate = numeric(frame.get("sample_rate_hz"), "rate", 1, 600)
        samples = frame.get("samples")
        if not isinstance(samples, list) or not 1 <= len(samples) <= capacity:
            raise ValueError("invalid_sample_count")
        previous = -1
        validated = []
        for sample in samples:
            if not isinstance(sample, list) or len(sample) != width:
                raise ValueError("invalid_sample_shape")
            t = integer(sample[0], "sample_offset", 0, 30000)
            if t <= previous:
                raise ValueError("nonmonotonic_samples")
            previous = t
            if kind=="imu_window":
                values=[numeric(v,"imu", -250 if i<3 else -50, 250 if i<3 else 50)
                        for i,v in enumerate(sample[1:])]
            elif kind=="ppg_chunk":
                values=[None if v is None else numeric(v,"ppg",-2**31,2**31-1)
                        for v in sample[1:]]
                if all(v is None for v in values):
                    raise ValueError("empty_ppg_sample")
            else:
                values=[numeric(sample[1],"ecg_mv",-50,50)]
            validated.append([t,*values])
        out.update(samples=validated,sample_count=len(validated),sample_rate_hz=rate)
        window_keys = ("window_started_at_ms", "window_ended_at_ms")
        if any(key in frame for key in window_keys):
            if not all(key in frame for key in window_keys):
                raise ValueError("incomplete_window_time")
            start = integer(frame["window_started_at_ms"], "window_start", 0, 2**63-1)
            end = integer(frame["window_ended_at_ms"], "window_end", 0, 2**63-1)
            if (start > end or start < now-MAX_AGE_MS
                    or end > ts+5000 or end < ts-MAX_AGE_MS):
                raise ValueError("invalid_window_time")
            out.update(window_started_at_ms=start, window_ended_at_ms=end)
        if kind in ("ppg_chunk","ecg_chunk"):
            out["recording_id"]=identity(frame.get("recording_id"),"recording_id")
        return out
    if kind=="bia_result":
        stats=frame.get("stats")
        limits={
            "body_fat_pct":(0,100), "body_fat_mass_kg":(0,200),
            "skeletal_muscle_kg":(0,200), "total_body_water_kg":(0,200),
            "impedance_ohm":(1,10000),
        }
        if (not isinstance(stats, dict) or not stats
                or set(stats)-set(limits)):
            raise ValueError("invalid_bia_stats")
        out["stats"]={key:numeric(v,key,*limits[key]) for key,v in stats.items()}
        out["recording_id"]=identity(frame.get("recording_id"),"recording_id")
        return out
    if kind=="location_fix":
        out["lat"]=numeric(frame.get("lat"),"latitude",-90,90)
        out["lon"]=numeric(frame.get("lon"),"longitude",-180,180)
        out["accuracy_m"]=numeric(frame.get("accuracy_m"),"accuracy",0,100000)
        if frame.get("provider") not in ("gps","fused","network"):
            raise ValueError("invalid_location_provider")
        out["provider"]=frame["provider"]
        for k,lo,hi in (("speed_m_s",0,150),("altitude_m",-1000,15000)):
            if k in frame:
                out[k]=numeric(frame[k],k,lo,hi)
        return out
    if kind=="audio_chunk":
        if frame.get("encoding") != "pcm_s16le_mono":
            raise ValueError("unsupported_audio_format")
        out["sample_rate_hz"]=integer(frame.get("sample_rate_hz"),"audio_rate",8000,16000)
        if out["sample_rate_hz"] not in (8000,16000):
            raise ValueError("unsupported_audio_rate")
        out["duration_ms"]=integer(frame.get("duration_ms"),"duration",1,1000)
        out["recording_id"]=identity(frame.get("recording_id"),"recording_id")
        payload=frame.get("pcm_b64")
        if not isinstance(payload,str) or len(payload)>11000:
            raise ValueError("audio_too_large")
        try:
            data=base64.b64decode(payload,validate=True)
        except (binascii.Error,ValueError):
            raise ValueError("invalid_audio_base64") from None
        if not 2<=len(data)<=8192 or len(data)%2!=0:
            raise ValueError("invalid_pcm")
        out.update(encoding="pcm_s16le_mono",pcm_b64=payload,
                   sample_count=len(data)//2)
        return out
    raise ValueError("unsupported_kind")
