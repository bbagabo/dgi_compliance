"""Technical identity of the gateway: an Ed25519 key pair generated ON the machine.

* Windows: the private key is encrypted with DPAPI (machine scope) before being written.
* Linux / other: the key file must be owned by the service account with mode 0600; a key
  file readable by the group or by others is refused (no group-shared secret).
The private key never leaves the machine; ERPNext only stores the public key.
"""
import base64
import os
import stat
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import signing


class IdentityError(Exception):
    pass


# --------------------------------------------------------------------------- DPAPI (Windows)
def _dpapi(data: bytes, protect: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = BLOB(len(data), buf), BLOB()
    CRYPTPROTECT_LOCAL_MACHINE = 0x4
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(blob_out))
    if not ok:
        raise IdentityError("DPAPI : chiffrement/dechiffrement de la cle impossible")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _write_secret(path: str, raw: bytes) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if sys.platform == "win32":  # pragma: no cover
        with open(path, "wb") as f:
            f.write(b"DPAPI1" + _dpapi(raw, True))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    os.chmod(path, 0o600)


def _read_secret(path: str) -> bytes:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"DPAPI1"):  # pragma: no cover
        return _dpapi(raw[6:], False)
    if sys.platform != "win32":
        mode = os.stat(path).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise IdentityError(f"{path} est lisible par d'autres comptes (mode {oct(mode & 0o777)}) : "
                                "corrigez avec chmod 600 puis relancez")
    return raw


class Identity:
    def __init__(self, gateway_id: str, private_key: Ed25519PrivateKey):
        self.gateway_id = gateway_id
        self._key = private_key

    @classmethod
    def load(cls, gateway_id: str, key_path: str) -> "Identity":
        if not os.path.exists(key_path):
            raise IdentityError("aucune cle : lancez d'abord l'enrolement de la passerelle")
        raw = _read_secret(key_path)
        return cls(gateway_id, Ed25519PrivateKey.from_private_bytes(raw))

    @classmethod
    def create(cls, gateway_id: str, key_path: str, overwrite: bool = False) -> "Identity":
        if os.path.exists(key_path) and not overwrite:
            raise IdentityError("une cle existe deja pour cette passerelle (utilisez --rotate pour la remplacer)")
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
        _write_secret(key_path, raw)
        return cls(gateway_id, key)

    @property
    def public_key_raw(self) -> bytes:
        return self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key_raw).decode()

    @property
    def fingerprint(self) -> str:
        return signing.fingerprint(self.public_key_raw)

    def sign_headers(self, method: str, path: str, body: bytes, *, now: float | None = None) -> dict:
        ts = int(now if now is not None else time.time())
        nonce = signing.new_nonce()
        sig = self._key.sign(signing.canonical(method, path, self.gateway_id, ts, nonce, body))
        return {signing.HEADER_GATEWAY: self.gateway_id, signing.HEADER_TIMESTAMP: str(ts),
                signing.HEADER_NONCE: nonce, signing.HEADER_SIGNATURE: base64.b64encode(sig).decode()}
