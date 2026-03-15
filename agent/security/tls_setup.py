"""
ED Cockpit — TLS Setup
=======================
Utilities for generating and loading the self-signed TLS certificate used
by the agent's WebSocket server, and for building SSL contexts on both sides.

Agent side
----------
  ``generate_self_signed_cert()``  — called once to create agent.crt / agent.key
  ``build_server_ssl_context()``   — builds the SSLContext for WSServer

Client side
-----------
  ``cert_fingerprint()``           — computes the SHA-256 fingerprint of a cert
  ``build_client_ssl_context()``   — builds a context that accepts only a cert
                                     whose fingerprint matches a pinned value
                                     (Trust On First Use)

Trust On First Use (TOFU) model
--------------------------------
  1. Operator runs ``tools/gen_cert.py`` → agent.crt / agent.key are created.
     The fingerprint is printed to stdout.
  2. Operator shares the fingerprint with every client user (IM, paper, etc.)
     or copies agent.crt directly to the client machine.
  3. On first client connection the fingerprint is verified and saved in
     client.json.  All future connections are checked against that pinned value.

Files
-----
  Agent config dir (~/.config/ed-cockpit/ or %APPDATA%\\ed-cockpit\\)
    agent.crt   — PEM certificate   (share with clients or share fingerprint)
    agent.key   — PEM private key   (NEVER share)
  Client config dir (same default location)
    agent.crt   — PEM certificate copied from agent  (optional, for TOFU)

Dependencies
------------
  pip install cryptography   (cert generation only — not needed at runtime)
"""
from __future__ import annotations

import hashlib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ── Agent-side: certificate generation ───────────────────────────────────────

def generate_self_signed_cert(
    cert_path: Path,
    key_path:  Path,
    common_name:    str = "ed-agent",
    validity_days:  int = 3650,
) -> str:
    """
    Generate a self-signed RSA-2048 certificate and write PEM files.

    Parameters
    ----------
    cert_path : Path
        Output path for the certificate (agent.crt).
    key_path : Path
        Output path for the private key (agent.key).
    common_name : str
        CN field in the certificate subject (default ``"ed-agent"``).
    validity_days : int
        Certificate lifetime in days (default 10 years).

    Returns
    -------
    str
        The SHA-256 fingerprint of the generated certificate (hex, colon-
        separated bytes), suitable for sharing with clients.

    Raises
    ------
    ImportError
        If the ``cryptography`` package is not installed.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise ImportError(
            "The 'cryptography' package is required for cert generation.\n"
            "Install it with:  pip install cryptography"
        )

    # ── Generate RSA-2048 private key ─────────────────────────────────────
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # ── Build subject / issuer (self-signed → same) ───────────────────────
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName(common_name),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    # ── Write certificate ─────────────────────────────────────────────────
    cert_path.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    # ── Write private key (unencrypted) ───────────────────────────────────
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    # ── Set restrictive permissions on the key file (Linux/macOS only) ───
    if sys.platform != "win32":
        import os
        os.chmod(key_path, 0o600)

    return cert_fingerprint(cert_path)


# ── Agent-side: SSLContext for WSServer ───────────────────────────────────────

def build_server_ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """
    Build and return an ``ssl.SSLContext`` suitable for the WebSocket server.

    Parameters
    ----------
    cert_path : Path
        Path to the PEM certificate (agent.crt).
    key_path : Path
        Path to the PEM private key (agent.key).

    Raises
    ------
    FileNotFoundError
        If either file does not exist.
    ssl.SSLError
        If the files are invalid or mismatched.
    """
    if not cert_path.exists():
        raise FileNotFoundError(f"Certificate not found: {cert_path}")
    if not key_path.exists():
        raise FileNotFoundError(f"Private key not found: {key_path}")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    # Prefer modern TLS — drop obsolete versions
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ── Client-side: SSLContext with cert pinning ─────────────────────────────────

def build_client_ssl_context(
    pinned_fingerprint: Optional[str] = None,
    ca_cert_path:       Optional[Path] = None,
) -> ssl.SSLContext:
    """
    Build an ``ssl.SSLContext`` for the client WebSocket connection.

    Two pinning modes are supported (in priority order):

    1. **Fingerprint pinning** (``pinned_fingerprint`` provided):
       The server certificate is verified post-handshake by comparing its
       SHA-256 fingerprint against the stored value.  The standard CA chain
       validation is disabled because we use a self-signed cert.

    2. **Certificate file pinning** (``ca_cert_path`` provided):
       The agent's PEM certificate is loaded as the sole trusted CA.
       This is a stricter but equivalent check.

    3. **No pinning** (neither argument provided):
       TLS encryption is active but the server certificate is not verified.
       Suitable only for development / localhost testing.

    Parameters
    ----------
    pinned_fingerprint : str, optional
        Colon-separated SHA-256 hex fingerprint (as returned by
        ``cert_fingerprint()``).  Stored in client.json after first connect.
    ca_cert_path : Path, optional
        Path to the agent's PEM certificate on the client machine.

    Returns
    -------
    ssl.SSLContext
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    if ca_cert_path and ca_cert_path.exists():
        # Load agent cert as the only trusted CA
        ctx.load_verify_locations(cafile=str(ca_cert_path))
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = False   # self-signed — no hostname in CA chain
    elif pinned_fingerprint:
        # Fingerprint mode: disable chain validation, check fingerprint manually
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Fingerprint check is done post-handshake by WSConnection
    else:
        # Development mode: encrypt but don't verify
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    return ctx


# ── Shared: fingerprint helpers ───────────────────────────────────────────────

def cert_fingerprint(cert_path: Path) -> str:
    """
    Compute the SHA-256 fingerprint of a PEM certificate file.

    Returns the fingerprint as uppercase colon-separated hex bytes, e.g.::

        "AB:CD:EF:..."

    This is the canonical human-readable format used by browsers and
    ``openssl x509 -fingerprint -sha256``.
    """
    pem = cert_path.read_bytes()
    der = _pem_to_der(pem)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i+2] for i in range(0, len(digest), 2))


def fingerprint_from_ssl_object(ssl_obj: ssl.SSLObject) -> str:
    """
    Extract the SHA-256 fingerprint of the certificate presented during
    an active TLS handshake.

    Parameters
    ----------
    ssl_obj : ssl.SSLObject
        The object returned by ``SSLSocket.getpeercert(binary_form=True)``.
        Pass the raw ``SSLSocket`` or ``SSLObject`` from the websockets library.

    Usage in WSConnection (post-handshake TOFU check)::

        der = ws.transport.get_extra_info("ssl_object").getpeercert(True)
        fp  = fingerprint_from_der(der)
    """
    raise NotImplementedError(
        "Use fingerprint_from_der(der) with the raw DER bytes from the socket."
    )


def fingerprint_from_der(der: bytes) -> str:
    """
    Compute the SHA-256 fingerprint from raw DER-encoded certificate bytes.

    Obtain the DER bytes during a TLS handshake via::

        der = ssl_object.getpeercert(binary_form=True)
    """
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i+2] for i in range(0, len(digest), 2))


def _pem_to_der(pem: bytes) -> bytes:
    """
    Convert PEM-encoded certificate bytes to DER (binary) form.

    Strips the ``-----BEGIN/END CERTIFICATE-----`` headers and base64-decodes
    the body.  Pure stdlib — no ``cryptography`` package required.
    """
    import base64
    lines = pem.decode("ascii").splitlines()
    b64 = "".join(
        line for line in lines
        if not line.startswith("-----")
    )
    return base64.b64decode(b64)


# ── Convenience: ensure cert exists, generate if missing ─────────────────────

def ensure_cert(
    cert_path: Path,
    key_path:  Path,
    common_name: str = "ed-agent",
) -> str:
    """
    Return the fingerprint of the agent certificate, generating it first
    if either file is missing.

    Intended to be called from ``EDApp.__init__`` so that setup is fully
    automatic on first run.

    Returns
    -------
    str
        SHA-256 fingerprint of the certificate.
    """
    if cert_path.exists() and key_path.exists():
        return cert_fingerprint(cert_path)
    return generate_self_signed_cert(cert_path, key_path, common_name)
