"""
ED Assist — Protocol Version
=============================
Single source of truth for the Agent ↔ Client wire protocol version.

Both the agent and client import this constant and include it in the
``WelcomeMessage``.  A client that receives a protocol version it does not
understand should log a warning and disconnect gracefully.

Versioning scheme:  MAJOR.MINOR
  MAJOR — incremented on any breaking change to the message envelope
  MINOR — incremented on backwards-compatible additions (new event fields, etc.)
"""

PROTOCOL_VERSION: str = "1.0"
