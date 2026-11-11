"""Read-only Watch7 research sensor Cards backed by one gated data plane."""
from __future__ import annotations

import json
from sensors import SensorPlugin
from stream_protocol import STREAM_TYPES, RAW_TYPES

CARD_TEXT={
    "watch_imu_stream":"Bounded synchronized acceleration/gyro windows; wearer-started capture.",
    "watch_ppg_raw":"Research SDK green/red/infrared PPG windows with per-channel validity.",
    "watch_ecg":"Wearer-started Samsung ECG signal windows only; no automatic diagnosis.",
    "watch_bia":"Explicit on-Watch BIA completed result; implant contraindication gate.",
    "watch_location":"GPS position fixes with explicit on-Watch location permission.",
    "watch_audio":"Short user-triggered PCM microphone chunks; no always-on listening.",
    "watch_step_detector":"Per-step event timestamps from Android TYPE_STEP_DETECTOR; not cumulative count.",
    "watch_sweat_loss":"Samsung SDK run-only estimated sweat loss with status and exercise evidence.",
}


class AdvancedCard(SensorPlugin):
    def __init__(self,card,store,topic,publisher):
        super().__init__(card,CARD_TEXT[card],topic,store,publisher)
        self.stream=store

    def get_tool(self):
        return {
            "name":self.name,"type":"sensor","multiInstance":False,
            "description":self.description,
            "inputSchema":{
                "type":"object","properties":{
                    "action":{"type":"string","enum":[
                        "info","capabilities","list","read","start","stop"]}},
                "required":["action"],
                "x-action-params":{a:{"params":[]} for a in
                    ("info","capabilities","list","read","start","stop")},
            },
            "topic_out":[{"topic":self.topic,"format":"data/json"}],
        }

    def dispatch(self,action,args):
        if action=="capabilities":
            return {"ok":True,"card":self.name,"wire":"v3",
                    "kind":STREAM_TYPES[self.name],"operator_enabled":
                    self.name in self.stream.enabled,
                    "watch_consent_required":True,
                    "read_only":True,"local_capture_required":True,
                    "raw_chunk":self.name in RAW_TYPES,
                    "remote_capture":False,
                    "clinical_validation":False}
        if action=="info":
            with self._lock:
                running=self._running
            return {"state":"running" if running else "idle",
                    "device":"watch7","read_only":True,"local_capture_required":True,
                    "topic_out":[{"topic":self.topic,"format":"data/json"}]}
        if action in ("read","list"):
            return self.stream.snapshot(self.name,mode="list" if action=="list" else "latest")
        if action in ("start","stop"):
            return {**super().dispatch(action,args),"watch_capture_unchanged":True}
        return None

    def publish_frame(self,kind,frame):
        if kind!=STREAM_TYPES[self.name]:
            return
        # ROS2 data/json topics have broader subscriber visibility than
        # consent-gated MCP reads: remove raw waveforms, PCM, exact location
        # and BIA body-composition measurements from the event envelope.
        private_fields = {"samples", "pcm_b64", "stats", "lat", "lon",
                          "speed_m_s", "altitude_m", "sweat_loss_ml"}
        view = {k: v for k, v in frame.items() if k not in private_fields}
        view["data_redacted_on_ros2"] = True
        super().publish(view)
