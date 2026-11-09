"""Opt-in, bounded volatile research streams; explicit session isolation."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from stream_protocol import KIND_TO_CARD, RAW_TYPES, STREAM_TYPES, validate_v3

SESSION_TTL_MS=15000
CHUNK_TTL_MS=30000
MAX_TOTAL_CHUNKS=64
PER_TYPE_CHUNKS=12


class StreamStore:
    def __init__(self, *, enabled=None, on_frame=None,
                 wall_ms=None, mono_ms=None):
        self.enabled=set(enabled or ())
        self._on_frame=on_frame or (lambda kind,frame:None)
        self._wall=wall_ms or (lambda:int(time.time()*1000))
        self._mono=mono_ms or (lambda:int(time.monotonic()*1000))
        self._lock=threading.RLock()
        self._state=None
        self._items=OrderedDict()
        self._sequences=OrderedDict()
        self._retired_sessions=OrderedDict()

    def _gate(self, card):
        if card not in self.enabled:
            return {"ok":False,"reason":"operator_disabled"}
        if not self._state or self._mono()-self._state[1]>SESSION_TTL_MS:
            self._items.clear()
            return {"ok":False,"reason":"no_live_stream_consent"}
        state=self._state[0]
        if not state["collecting"] or not state["online"] or state["wear_state"]!="on_body":
            return {"ok":False,"reason":"stream_not_collecting"}
        if card not in state["enabled_streams"]:
            return {"ok":False,"reason":"watch_consent_required"}
        return {"ok":True,"session_id":state["session_id"]}

    def _prune(self):
        now=self._mono()
        for key,(_,stamp) in list(self._items.items()):
            if now-stamp>CHUNK_TTL_MS:
                self._items.pop(key,None)

    def ingest(self, frame):
        try:
            clean=validate_v3(frame,now_ms=self._wall())
        except ValueError as exc:
            return {"ok":False,"reason":"bad_input","detail":str(exc)}
        kind=clean["kind"]
        with self._lock:
            seqkey=(clean["session_id"],kind)
            if clean["session_id"] in self._retired_sessions:
                return {"ok":False,"reason":"retired_session"}
            if clean["seq"]<=self._sequences.get(seqkey,-1):
                return {"ok":False,"reason":"duplicate_or_out_of_order"}
            if kind=="stream_state":
                # Security-relevant state transitions immediately revoke all prior streams.
                previous=self._state[0] if self._state else None
                if previous and previous["session_id"] != clean["session_id"]:
                    if clean["captured_at_ms"] < previous["captured_at_ms"]:
                        return {"ok":False,"reason":"older_session_state"}
                    self._retired_sessions[previous["session_id"]] = self._mono()
                    while len(self._retired_sessions) > 128:
                        self._retired_sessions.popitem(last=False)
                if (not clean["collecting"] or
                    previous and (previous["session_id"]!=clean["session_id"]
                    or set(previous["enabled_streams"])-set(clean["enabled_streams"]))):
                    self._items.clear()
                self._state=(clean,self._mono())
            else:
                card=KIND_TO_CARD[kind]
                gate=self._gate(card)
                if not gate["ok"]:
                    return gate
                if gate["session_id"]!=clean["session_id"]:
                    return {"ok":False,"reason":"session_mismatch"}
                self._prune()
                key=(clean["session_id"],kind,clean["seq"])
                self._items[key]=(clean,self._mono())
                self._items.move_to_end(key)
                same_kind=[x for x in self._items if x[1]==kind]
                for old_key in same_kind[:-PER_TYPE_CHUNKS]:
                    self._items.pop(old_key,None)
                while len(self._items)>MAX_TOTAL_CHUNKS:
                    self._items.popitem(last=False)
            self._sequences[seqkey]=clean["seq"]
            self._sequences.move_to_end(seqkey)
            while len(self._sequences)>256:
                self._sequences.popitem(last=False)
            # Prevent events being published after a concurrent revocation.
            if kind!="stream_state":
                self._on_frame(kind,dict(clean))
        return {"ok":True,"kind":kind,"seq":clean["seq"]}

    def snapshot(self,card,*,mode="latest"):
        if card not in STREAM_TYPES:
            return {"ok":False,"reason":"unknown_card"}
        with self._lock:
            gate=self._gate(card)
            if not gate["ok"]:
                return gate
            self._prune()
            kind=STREAM_TYPES[card]
            values=[frame for (session,k,_),(frame,_) in self._items.items()
                    if session==gate["session_id"] and k==kind]
            if mode=="list":
                return {"ok":True,"kind":kind,"count":len(values),
                        "seqs":[f["seq"] for f in values],
                        "session_id":gate["session_id"],"expires_after_ms":CHUNK_TTL_MS}
            if not values:
                return {"ok":False,"reason":"no_stream_samples"}
            # Read is guarded independently; no network payload escapes via logs.
            return {"ok":True,"frame":dict(values[-1]),
                    "session_id":gate["session_id"],"expires_after_ms":CHUNK_TTL_MS}
