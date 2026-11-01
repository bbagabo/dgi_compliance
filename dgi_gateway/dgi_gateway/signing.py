"""Signature of gateway -> ERPNext requests.

The SAME format is implemented server side in dgi_compliance/gateway/signing.py; both files
must stay byte-for-byte compatible (a test compares them).

Canonical string (UTF-8), one field per line:
    <scheme: DGI-ED25519-V1 (service gateway) or DGI-ECDSA-P256-V1 (browser station, WebCrypto)>
    <HTTP method, upper case>
    <request path, e.g. /api/method/dgi_compliance.gateway.api.lease>
    <gateway id>
    <unix timestamp, seconds>
    <nonce, 32 hex chars>
    <sha256 hex of the raw request body>

Headers sent with every request: X-DGI-Gateway, X-DGI-Timestamp, X-DGI-Nonce,
X-DGI-Signature (base64 signature of the canonical string: Ed25519, or ECDSA P-256 / SHA-256 in
the IEEE P1363 form r||s produced by WebCrypto). The server picks the scheme from the key type it
stored at enrolment, never from the request (no downgrade).
"""
import hashlib
import secrets

SCHEME = "DGI-ED25519-V1"
SCHEME_ECDSA_P256 = "DGI-ECDSA-P256-V1"
HEADER_GATEWAY = "X-DGI-Gateway"
HEADER_TIMESTAMP = "X-DGI-Timestamp"
HEADER_NONCE = "X-DGI-Nonce"
HEADER_SIGNATURE = "X-DGI-Signature"
MAX_CLOCK_SKEW_S = 300


def body_digest(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def canonical(method: str, path: str, gateway_id: str, timestamp, nonce: str, body: bytes | None,
              scheme: str = SCHEME) -> bytes:
    return "\n".join([
        scheme, (method or "").upper(), path or "", gateway_id or "", str(int(timestamp)),
        nonce or "", body_digest(body),
    ]).encode("utf-8")


def fingerprint(public_key_raw: bytes) -> str:
    """Short, human-comparable fingerprint of a raw public key (XXXX-XXXX-XXXX-XXXX-XXXX)."""
    h = hashlib.sha256(public_key_raw or b"").hexdigest()[:20].upper()
    return "-".join(h[i:i + 4] for i in range(0, 20, 4))


def new_nonce() -> str:
    return secrets.token_hex(16)
