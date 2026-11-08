"""Fail-closed Watch7 haptic actuator with session-bound, at-most-once commands.

The server operator AND the wearer must independently enable this feature.
An HTTP command delivery is never equated to a physical vibration. Only an
authenticated Watch companion ACK can report "executed".
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable

PATTERNS = frozenset(("single", "double", "triple"))
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TTL_MS = 3_000
COOLDOWN_MS = 5_000
REPLAY_MS = 120_000
MAX_COMMANDS = 128
MAX_EVENT_IDS = 256
TERMINAL = frozenset(("executed", "rejected", "failed", "expired", "cancelled"))


class HapticQueue:
    def __init__(
        self, device, *,
        operator_enabled: bool = False,
        on_ack: Callable[[dict], None] | None = None,
        wall_ms: Callable[[], int] | None = None,
        monotonic_ms: Callable[[], int] | None = None,
    ):
        self.device = device
        self.operator_enabled = operator_enabled
        self._ack = on_ack or (lambda data: None)
        self._wall = wall_ms or (lambda: int(time.time() * 1000))
        self._mono = monotonic_ms or (lambda: int(time.monotonic() * 1000))
        self._lock = threading.RLock()
        self._running = False
        self._commands: OrderedDict[str, dict] = OrderedDict()
        self._deadlines: dict[str, int] = {}
        self._events: OrderedDict[tuple[str, str], tuple[int, str]] = OrderedDict()
        self._last_command_ms = -10**15

    def start(self) -> None:
        with self._lock:
            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            for command in self._commands.values():
                if command["state"] in ("queued", "delivered"):
                    command["state"] = "cancelled"
                    command["cancelled_reason"] = "actuator_stopped"

    def _expire(self) -> None:
        now = self._mono()
        for identifier, cmd in self._commands.items():
            if cmd["state"] in ("queued", "delivered") and now >= self._deadlines[identifier]:
                cmd["state"] = "expired"
        for event_key, (created, _) in list(self._events.items()):
            if now - created > REPLAY_MS:
                self._events.pop(event_key, None)

    def _consent(self) -> dict:
        """The device owns the live heartbeat and on-Watch opt-in decision."""
        if not self.operator_enabled:
            return {"ok": False, "reason": "operator_disabled"}
        return self.device.haptic_gate()

    def info(self) -> dict:
        with self._lock:
            self._expire()
            gate = self._consent()
            return {
                "ok": True, "state": "running" if self._running else "idle",
                "operator_enabled": self.operator_enabled,
                "watch_opted_in": bool(gate.get("ok")),
                "gate_reason": "allowed" if gate.get("ok") else gate.get("reason"),
                "patterns": sorted(PATTERNS),
                "commands": len(self._commands),
                "ttl_ms": TTL_MS, "cooldown_ms": COOLDOWN_MS,
                "approval_scope": "wearer_explicit_foreground_session",
                "execution_evidence": "watch_ack_not_motor_measurement",
            }

    def queue(self, args: dict) -> dict:
        if not isinstance(args, dict):
            return {"ok": False, "reason": "invalid_arguments"}
        pattern = args.get("pattern")
        event_id = args.get("event_id")
        reason = args.get("reason", "requested_vibration")
        if pattern not in PATTERNS:
            return {"ok": False, "reason": "invalid_pattern"}
        if not isinstance(event_id, str) or not IDENTIFIER.fullmatch(event_id):
            return {"ok": False, "reason": "event_id_required"}
        if (not isinstance(reason, str) or not 1 <= len(reason) <= 100
                or any(ord(c) < 32 for c in reason)):
            return {"ok": False, "reason": "invalid_reason"}
        with self._lock:
            self._expire()
            if not self._running:
                return {"ok": False, "reason": "not_running"}
            gate = self._consent()
            if not gate.get("ok"):
                return gate
            session = gate["session_id"]
            key = (session, event_id)
            if key in self._events:
                cmd = self._commands.get(self._events[key][1])
                if cmd is not None:
                    if cmd["pattern"] != pattern or cmd["reason"] != reason:
                        return {"ok": False, "reason": "event_id_conflict"}
                    return {"ok": True, **dict(cmd), "deduplicated": True}
            now = self._mono()
            if now - self._last_command_ms < COOLDOWN_MS:
                return {"ok": False, "reason": "cooldown"}
            # Retain pending messages. Reject if queue is full rather than
            # silently evicting a command that may still reach the Watch.
            while len(self._commands) >= MAX_COMMANDS:
                first = next(iter(self._commands))
                if self._commands[first]["state"] not in TERMINAL:
                    return {"ok": False, "reason": "queue_full"}
                self._commands.pop(first)
                self._deadlines.pop(first, None)
            identifier = str(uuid.uuid4())
            cmd = {
                "request_id": identifier, "device_id": "watch7",
                "pattern": pattern, "reason": reason, "event_id": event_id,
                "session_id": session, "source": "watch7_real",
                "state": "queued", "created_at_ms": self._wall(),
                "expires_at_ms": self._wall() + TTL_MS,
            }
            self._commands[identifier] = cmd
            self._deadlines[identifier] = now + TTL_MS
            self._events[key] = (now, identifier)
            while len(self._events) > MAX_EVENT_IDS:
                self._events.popitem(last=False)
            self._last_command_ms = now
            return {"ok": True, **dict(cmd), "deduplicated": False}

    def _revoke_undeliverable(self, gate: dict) -> None:
        for cmd in self._commands.values():
            if cmd["state"] in ("queued", "delivered") and (
                not gate.get("ok") or cmd["session_id"] != gate.get("session_id")
            ):
                cmd["state"] = "cancelled"
                cmd["cancelled_reason"] = (
                    "session_changed" if gate.get("ok") else gate.get("reason", "no_consent")
                )

    def poll(self) -> list[dict]:
        with self._lock:
            self._expire()
            if not self._running:
                return []
            gate = self._consent()
            self._revoke_undeliverable(gate)
            if not gate.get("ok"):
                return []
            now = self._mono()
            ready = []
            for cmd in self._commands.values():
                if cmd["state"] not in ("queued", "delivered"):
                    continue
                remaining = self._deadlines[cmd["request_id"]] - now
                if remaining <= 0:
                    cmd["state"] = "expired"
                    continue
                cmd["state"] = "delivered"
                ready.append({
                    "request_id": cmd["request_id"], "pattern": cmd["pattern"],
                    "event_id": cmd["event_id"], "session_id": cmd["session_id"],
                    "expires_at_ms": cmd["expires_at_ms"],
                    "ttl_remaining_ms": remaining,
                })
            return ready

    def status(self, request_id: object) -> dict:
        if not isinstance(request_id, str) or not IDENTIFIER.fullmatch(request_id):
            return {"ok": False, "reason": "invalid_request_id"}
        with self._lock:
            self._expire()
            self._revoke_undeliverable(self._consent())
            cmd = self._commands.get(request_id)
            return (
                {"ok": True, "command": dict(cmd)}
                if cmd is not None else {"ok": False, "reason": "unknown_request_id"}
            )

    def acknowledge(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "reason": "bad_input"}
        req = payload.get("request_id")
        session = payload.get("session_id")
        outcome = payload.get("state")
        if (not isinstance(req, str) or not IDENTIFIER.fullmatch(req)
                or not isinstance(session, str) or not IDENTIFIER.fullmatch(session)
                or outcome not in ("executed", "rejected", "failed")):
            return {"ok": False, "reason": "bad_ack"}
        event = None
        with self._lock:
            self._expire()
            cmd = self._commands.get(req)
            if cmd is None:
                return {"ok": False, "reason": "not_pending"}
            if cmd["session_id"] != session:
                return {"ok": False, "reason": "session_mismatch"}
            if cmd["state"] == outcome:
                return {"ok": True, "request_id": req, "state": outcome, "deduplicated": True}
            if cmd["state"] not in ("queued", "delivered"):
                return {"ok": False, "reason": "not_pending"}
            # A matching "executed" ACK must also follow a verified
            # delivery to this live opted-in session; never fabricate one.
            if outcome == "executed":
                gate = self._consent()
                if (cmd["state"] != "delivered" or not gate.get("ok")
                        or gate.get("session_id") != session):
                    return {"ok": False, "reason": "not_authorized_or_delivered"}
            cmd["state"] = outcome
            cmd["ack_at_ms"] = self._wall()
            event = {
                "request_id": req, "event_id": cmd["event_id"],
                "session_id": session, "pattern": cmd["pattern"],
                "state": outcome, "source": "watch7_real",
                "timestamp_ms": cmd["ack_at_ms"],
                "evidence": "watch_app_ack",
            }
        # Never call the ROS publisher with the command lock held.
        self._ack(event)
        return {"ok": True, "request_id": req, "state": outcome, "deduplicated": False}


class HapticPlugin:
    def __init__(self, queue: HapticQueue, topic: str):
        self.name = "watch_haptic"
        self.queue = queue
        self.topic = topic

    def get_tool(self) -> dict:
        return {
            "name": self.name, "type": "actuator", "multiInstance": False,
            "description": (
                "Bounded Watch7 vibration with operator and on-Watch opt-in, "
                "session gate, cooldown, replay protection and device ACK."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["info", "start", "stop", "vibrate", "status"]},
                    "pattern": {"type": "string", "enum": sorted(PATTERNS)},
                    "event_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "reason": {"type": "string", "maxLength": 100},
                    "request_id": {"type": "string"},
                },
                "required": ["action"],
                "x-action-params": {
                    "info": {"params": []}, "start": {"params": []},
                    "stop": {"params": []},
                    "vibrate": {"params": ["pattern", "event_id", "reason"]},
                    "status": {"params": ["request_id"]},
                },
            },
            "topic_out": [{"topic": self.topic, "format": "data/json"}],
        }

    def start(self):
        self.queue.start()

    def stop(self):
        self.queue.stop()

    def dispatch(self, action: str, args: dict) -> dict | None:
        if action == "info":
            return self.queue.info()
        if action == "start":
            self.start()
            return self.queue.info()
        if action == "stop":
            self.stop()
            return self.queue.info()
        if action == "status":
            return self.queue.status(args.get("request_id"))
        if action == "vibrate":
            return self.queue.queue(args)
        return None
