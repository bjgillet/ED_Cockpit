"""
ED Cockpit — Message Envelope Definitions
==========================================
Dataclasses representing every message that can travel over the
Agent ↔ Client WebSocket connection.

Direction legend
----------------
  Agent  → Client :  EventMessage, WelcomeMessage, RolesUpdatedMessage,
                     ErrorMessage
  Client → Agent  :  RegisterMessage, ActionMessage

Wire format
-----------
All messages are serialised as JSON.  Use ``to_dict()`` to serialise and
``from_dict()`` to deserialise.  The ``type`` field acts as the discriminator.

Example round-trip::

    msg = EventMessage(role="exobiology", event="ScanOrganic",
                       timestamp="2026-03-14T12:00:00Z",
                       data={"species": "Bacterium Nebulus"})
    raw = json.dumps(msg.to_dict())
    received = message_from_dict(json.loads(raw))

HMAC helper
-----------
``compute_action_hmac`` and ``verify_action_hmac`` live here (rather than in
``agent.network.auth``) so the client can sign ActionMessages without
importing any agent-side package.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from dataclasses import dataclass, field, asdict
from typing import Any


# ── Base ──────────────────────────────────────────────────────────────────────

@dataclass
class BaseMessage:
    type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ── Agent → Client ────────────────────────────────────────────────────────────

@dataclass
class WelcomeMessage(BaseMessage):
    """
    Sent by the agent immediately after a successful client registration.
    Carries the authoritative role list for the connecting client.
    """
    type:             str       = "welcome"
    assigned_roles:   list[str] = field(default_factory=list)
    protocol_version: str       = ""


@dataclass
class EventMessage(BaseMessage):
    """
    Carries a single game event destined for one or more client roles.
    The ``role`` field allows the client to route the event to the correct
    panel without parsing ``event`` or ``data``.
    """
    type:      str  = "event"
    role:      str  = ""   # canonical role name from shared.roles_def
    event:     str  = ""   # ED journal event name, e.g. "ScanOrganic"
    timestamp: str  = ""   # ISO-8601 UTC
    data:      dict = field(default_factory=dict)


@dataclass
class RolesUpdatedMessage(BaseMessage):
    """
    Sent when the agent operator changes a client's role assignment at
    runtime.  The client should redraw its panels without reconnecting.
    """
    type:           str       = "roles_updated"
    assigned_roles: list[str] = field(default_factory=list)


@dataclass
class ErrorMessage(BaseMessage):
    """
    Sent when the agent rejects a message or encounters a protocol error.
    ``fatal=True`` means the agent will close the connection after sending.
    """
    type:    str  = "error"
    code:    str  = ""    # machine-readable, e.g. "auth_failed"
    message: str  = ""    # human-readable description
    fatal:   bool = False


# ── Client → Agent ────────────────────────────────────────────────────────────

@dataclass
class RegisterMessage(BaseMessage):
    """
    First message sent by a client after the TLS handshake.
    ``proposed_roles`` is only used when the agent has no prior record for
    this client_id; otherwise the agent's stored assignment takes precedence.
    """
    type:           str       = "register"
    client_id:      str       = ""
    token:          str       = ""
    proposed_roles: list[str] = field(default_factory=list)


@dataclass
class ActionMessage(BaseMessage):
    """
    Requests the agent to perform a hardware-level action (e.g. key press).

    Security fields
    ---------------
    seq  — monotonically increasing per-client sequence number.  The agent
           rejects any message whose seq is not strictly greater than the
           last accepted seq for this client (replay protection).
    hmac — HMAC-SHA256 hex digest computed over:
               client_id + ":" + str(seq) + ":" + action + ":" + key
           using the shared token as the key.
    """
    type:   str = "action"
    action: str = ""    # "key_press"
    key:    str = ""    # logical key name, e.g. "next_firegroup"
    seq:    int = 0
    hmac:   str = ""


# ── Deserialisation helper ────────────────────────────────────────────────────

_TYPE_MAP: dict[str, type] = {
    "welcome":       WelcomeMessage,
    "event":         EventMessage,
    "roles_updated": RolesUpdatedMessage,
    "error":         ErrorMessage,
    "register":      RegisterMessage,
    "action":        ActionMessage,
}


def message_from_dict(data: dict[str, Any]) -> BaseMessage:
    """
    Deserialise a raw dict (parsed JSON) into the appropriate message dataclass.

    Raises
    ------
    ValueError
        If the ``type`` field is missing or unknown.
    """
    msg_type = data.get("type", "")
    cls = _TYPE_MAP.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type!r}")
    known_fields = {f for f in cls.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return cls(**filtered)


def message_from_json(raw: str) -> BaseMessage:
    """Deserialise a JSON string into the appropriate message dataclass."""
    return message_from_dict(json.loads(raw))


# ── HMAC helpers (shared so the client doesn't depend on agent packages) ──────

def compute_action_hmac(
    client_id: str,
    seq: int,
    action: str,
    key: str,
    token: str,
) -> str:
    """
    Compute the HMAC-SHA256 for an ActionMessage.

    Returns the hex digest that should appear in ``ActionMessage.hmac``.
    The payload is:  ``client_id:seq:action:key``
    The HMAC key is: the raw pre-shared token.
    """
    payload = f"{client_id}:{seq}:{action}:{key}".encode()
    return _hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()


def verify_action_hmac(
    client_id: str,
    seq: int,
    action: str,
    key: str,
    token: str,
    provided_hmac: str,
) -> bool:
    """Timing-safe check that the provided HMAC is correct."""
    expected = compute_action_hmac(client_id, seq, action, key, token)
    return _hmac.compare_digest(expected, provided_hmac)
